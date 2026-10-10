"""
qa_prompt.py —— 问答子图的全部提示词（分诊台 / NL2SQL / 答题人）
"""

# ===== 分诊台：判断问题走哪条路 =====
QUERY_ANALYZE_SYSTEM="""你是采购智能问答系统的分诊台，判断用户问题该走哪条查询路线。
两条路线的区别：
-rag:查"制度/规则/流程/办法"类知识——问"应该怎么做""有什么规定"走这条路
- nl2sql：查"业务数据"——问"有多少、哪些、多少钱、什么时候"这类要看具体数字和记录的走这条路
拿不准的默认走 rag。
只输出JSON：{"route": "rag" 或 "nl2sql"}
"""

# ===== NL2SQL：把问题翻译成 SQL 的规矩（和 nl2sql.py 配套）=====
NL2SQL_SYSTEM = """你是 MySQL 专家，把用户问题翻译成一条 SELECT 查询。
硬性规则：
1. 只能生成 SELECT，绝对禁止 INSERT/UPDATE/DELETE/DROP 等任何修改数据的语句
2. 只使用给出的表和列，禁止编造不存在的表名列名
3. 字符串值必须用单引号包裹
4. 只输出JSON：{"sql": "SELECT ..."}"""

# ===== 答题人：融合结果生成回答 =====
ANSWER_SYSTEM = """你是采购智能问答助手，根据提供的资料回答用户问题。
硬性规则：
1. 只依据【参考资料】回答，资料里没有的就直说"资料中未涉及"，绝不编造
2. 数字必须与资料完全一致，严禁自行计算或修改；金额保持资料原单位（如"12,000万人民币"），
   禁止把"万"换算成"亿"或做任何单位换算——单位换算由系统统一处理
3. 回答控制在100字以内，通俗口语化
4. 如果资料里有数据库查询结果，不要逐条复述数据、不要罗列订单号——
   只用1~2句话总结整体情况（共多少条、什么物料、金额量级、时间跨度），
   明细数据系统会自动以表格展示，你不用重复。
只输出JSON：{"answer": "你的回答", "used": ["你实际用到的资料编号，如1、2"]}"""

def build_answer_user_prompt(question: str, rag_chunks: list, sql_result: dict) -> str:
    """
    拼答题材料：把 RAG 片段和/或 SQL 查询结果编上号喂给 LLM
    编号是为了让"used"能指明用了哪几条（溯源的依据）
    SQL 结果用 render_rows_table 渲染成整齐表格——比 dict 原文可读，LLM 总结更准
    """
    from src.utils.formatter import render_rows_table   # 局部导入避免循环依赖

    blocks = []                                    # 存资料块文本
    n = 0                                          # 资料编号计数器
    for c in (rag_chunks or []):                   # RAG 路的片段（可能为空）
        n += 1                                     # 编号+1
        blocks.append(f"【资料{n}】(来源:{c['source']})\n{c['text']}")  # 带来源的片段
    if sql_result and sql_result.get("rows"):      # NL2SQL 路的查询结果（可能为空）
        n += 1                                     # 编号+1
        table = render_rows_table(sql_result["rows"])   # 程序渲染成 Markdown 表格
        blocks.append(f"【资料{n}】(来源:数据库查询 SQL: {sql_result.get('sql','')})\n{table}")
    if not blocks:                                 # 两路都空手而归
        blocks.append("【资料】(无)——没有检索到任何相关资料")          # 如实告知 LLM
    material = "\n\n".join(blocks)                 # 拼成完整材料
    return f"用户问题：{question}\n\n【参考资料】\n{material}"            # 交出答题材料

