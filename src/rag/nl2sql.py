"""
nl2sql.py —— 自然语言查数据库（RAG 的"查账先生"）
====================================================================
干什么：把"最近10条螺纹钢订单有哪些？"这种自然语言，
       让 LLM 翻译成一条 SELECT 查询语句 → 安全校验 → 只读执行 → 返回结果。

三道安全闸：
  ① 只认 SELECT：UPDATE/DELETE/DROP 等写操作词直接拒绝
  ② 只许一条语句：SQL 里出现分号直接拒绝（防止拼接第二条恶意语句）
  ③ 强制限量：没有 LIMIT 就自动补 LIMIT 20，防止一次拖垮数据库
"""

from sqlalchemy import text         # SQLAlchemy 的"原生SQL"包装器
from src.db.session import SessionLocal         # 复用主项目的数据库会话
from src.utils.llm import ask_json               # 统一 LLM 入口（带重试+JSON解析）
from src.graph.prompts.qa_agent_prompt import NL2SQL_SYSTEM # NL2SQL 的提示词
from src.utils.logger import get_logger                     # 统一日志

logger = get_logger("nl2sql")                     # 本模块日志器

# 喂给 LLM 的表结构说明书：只放问答会用到的 5 张表、关键列、示例问题
# 大白话：LLM 不认识你的数据库，你必须把"账本目录"抄给它看，它才能写出对的 SQL
SCHEMA_TEXT="""
1. supplier 供应商表：id, supplier_name(供应商名称), main_category(主营品类:螺纹钢/铝材/铜材/塑料粒子),
   credit_status(信用等级A/B/C), premium_rate(溢价系数), reg_status(登记状态), province(注册省份), city(注册城市),
   credit_code(统一社会信用代码), legal_rep(法定代表人), reg_capital(注册资本,如'15000万人民币'),
   found_date(成立日期), industry(国标行业大类), address(注册地址)
   —— 查"供应商信息/资质/背景/档案"时，应把名称、品类、信用、溢价、法人、注册资本、成立日期、地址等一并查出
2. commodity_price 行情表：id, commodity_name(物料名), price(当日价格), record_date(行情日期)
3. history_order 历史订单表：id, order_no(订单号), commodity_name(物料名), supplier_id(供应商id),
   order_num(采购数量), unit_price(订单单价), total_amount(总金额), order_date(订单日期)
4. inventory 库存表：id, commodity_name(物料名), stock_quantity(库存数量), update_time(更新时间)
5. procurement_task 采购任务表：id, material_name(物料), purchase_quantity(采购数量),
   budget_limit(预算红线), task_status(任务状态), create_time(创建时间)
"""

# 安全黑名单：SQL 里出现这些"写操作词"一律拒绝执行
FORBIDDEN_WORDS=["insert","update","delete","drop","alter",
                 "truncate","create","pragma","attach","grant"]

async def generate_sql(question:str)->str:
    """
    第一步：让 LLM 把自然语言翻译成 SELECT 语句
    返回：SQL 字符串；LLM 失败或生成不合法时抛 ValueError（由上层转友好提示）
    """
    user_prompt = f"""数据库表结构如下：
    {SCHEMA_TEXT}
用户问题：{question}
请只输出一条 MySQL SELECT 查询语句（输出 JSON，格式 {{"sql": "..."}}），
只查需要的列，必须带 LIMIT（除非用户明确要总数统计）。"""

    result=await ask_json(NL2SQL_SYSTEM,user_prompt)   # 调 LLM 翻译（异步：必须await）
    if not result or not result.get("sql"):         # LLM 挂了或没给出 sql 字段
        raise ValueError("LLM未能生成SQL")          # 抛错，由 run_nl2sql 转友好提示

    sql=result["sql"].strip()                    # 拿到 SQL 并去掉首尾空白
    sql=sql.rstrip(";")                     # 去掉结尾分号（统一由程序控制语句结束）

    # ---- 安全闸①：必须是 SELECT 开头（只读底线）----
    if not sql.lower().startswith("select"):
        raise ValueError(f"拒绝执行：只允许SELECT查询，收到：{sql[:50]}")

     # ---- 安全闸②：禁止任何写操作词（不区分大小写，按整词匹配）----
    low=sql.lower()                  # 转小写方便比对
    for w in FORBIDDEN_WORDS:           # 逐个黑名单词检查
        if w in low:                    # 出现即拒绝（宁严勿松）
            raise ValueError(f"拒绝执行：检测到禁止的写操作词{w}")

     # ---- 安全闸③：强制限量（没有 LIMIT 就补 LIMIT 20）----
    if "limit" not in low:                   # 用户没主动限行数
        sql=sql+" LIMIT 20"                       # 程序补上，防止拖垮数据库

    logger.info(f"[nl2sql]生成SQL：{sql[:80]}")         # 留痕：执行了什么 SQL（溯源用）
    return sql

def run_sql(sql:str)->list:
    """
    第二步：只读执行 SQL，把结果整理成字典列表
    返回：[{"列名": 值, ...}, ...]；查询出错抛异常由上层接住
    """
    with SessionLocal() as db:           # 开一个数据库会话（用完自动关闭）
        result = db.execute(text(sql))             # 执行 SQL（text() 包装原生语句）
        rows = [dict(r._mapping) for r in result]  # 每行转成 {"列名":值} 字典
    return rows                                    # 交出查询结果

async def run_nl2sql(question: str) -> dict:
    """
    对外总入口（问答子图的 nl2sql 节点调用它）：翻译 → 校验 → 执行
    任何一步出问题都返回 {"error": 友好提示}，绝不抛异常炸掉子图
    """
    try:                                           # 包住全流程：任何失败都转友好提示
        sql = await generate_sql(question)          # 翻译+安全校验（异步）
        rows = run_sql(sql)                        # 只读执行
        return {"sql": sql, "rows": rows}          # 成功：SQL 和结果一起返回（SQL 是溯源凭证）
    except ValueError as e:                        # 安全校验拒绝（如含写操作词）
        logger.warning(f"[nl2sql] 安全拦截：{e}")
        return {"error": f"该问题无法转换为安全的查询：{e}"}
    except Exception as e:                         # 数据库错误/LLM 彻底失败等
        logger.warning(f"[nl2sql] 查询失败：{type(e).__name__}: {e}")
        return {"error": "查询数据库时出现问题，请换个问法或稍后再试"}

