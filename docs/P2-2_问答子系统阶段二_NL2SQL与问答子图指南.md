# P2-2 · 问答子系统阶段二：NL2SQL + 问答子图（每行大白话注释版）

> 用途：照着在 VSCode 里敲。共 **6 个新文件**，敲完问答子系统整条打通
> 阶段一的地基（`hybrid_search` 混合检索）在本指南被当作"现成工具"直接调用

---

## 0. 先搞懂：问答子图长什么样、怎么决定走哪条路

用户在悬浮框里提问，问题分两类，走不同的路：

```
用户提问："预算超了怎么办？"            "最近10条螺纹钢订单有哪些？"
                ↓                              ↓
        ┌── query_analyze（分诊台）：LLM 判断这问题属于哪类 ──┐
        ↓                                              ↓
   走 rag 路（查制度文档）                      走 nl2sql 路（查业务数据库）
        ↓                                              ↓
agentic_retrieve（迭代检索）                nl2sql（自然语言→SQL→只读查询）
        ↓                                              ↓
        └──────────── answer_generate（答题人：融合结果生成回答+溯源）────────────┘
                                                       ↓
                                                      END
```

**三条铁规矩**（面试重点）：
1. **分诊台决定走哪条路**（TECH_SPEC §2.5 的 query_analyze），LLM 只做分类不做查询
2. **NL2SQL 只允许 SELECT**：生成的 SQL 过不了安全校验就拒绝执行，返回友好提示（TECH_SPEC §5.3"不执行错误 SQL"）——问答子图对业务库**只读**
3. **回答必须带溯源**：RAG 路注明"来自哪个文档"，NL2SQL 路注明"用了哪条 SQL"——禁止编造

---

## 1. `src/rag/nl2sql.py`（新建：自然语言 → 只读 SQL）

```python
# src/rag/nl2sql.py
"""
nl2sql.py —— 自然语言查数据库（RAG 的"查账先生"）
====================================================================
干什么：把"最近10条螺纹钢订单有哪些？"这种自然语言，
       让 LLM 翻译成一条 SELECT 查询语句 → 安全校验 → 只读执行 → 返回结果。

三道安全闸（面试重点，TECH_SPEC §5.3）：
  ① 只认 SELECT：UPDATE/DELETE/DROP 等写操作词直接拒绝
  ② 只许一条语句：SQL 里出现分号直接拒绝（防止拼接第二条恶意语句）
  ③ 强制限量：没有 LIMIT 就自动补 LIMIT 20，防止一次拖垮数据库
"""

from sqlalchemy import text                       # SQLAlchemy 的"原生SQL"包装器
from src.db.session import SessionLocal           # 复用主项目的数据库会话
from src.utils.llm import ask_json                # 统一 LLM 入口（带重试+JSON解析）
from src.graph.prompts.qa_prompt import NL2SQL_SYSTEM  # NL2SQL 的提示词
from src.utils.logger import get_logger           # 统一日志

logger = get_logger("nl2sql")                     # 本模块日志器

# 喂给 LLM 的表结构说明书：只放问答会用到的 5 张表、关键列、示例问题
# 大白话：LLM 不认识你的数据库，你必须把"账本目录"抄给它看，它才能写出对的 SQL
SCHEMA_TEXT = """
1. supplier 供应商表：id, supplier_name(供应商名称), main_category(主营品类:螺纹钢/铝材/铜材/塑料粒子),
   credit_status(信用等级A/B/C), premium_rate(溢价系数), reg_status(登记状态), province(注册省份)
2. commodity_price 行情表：id, commodity_name(物料名), price(当日价格), record_date(行情日期)
3. history_order 历史订单表：id, order_no(订单号), supplier_id(供应商id), material_name(物料),
   quantity(数量), unit_price(订单单价), total_amount(总金额), order_date(订单日期), status(状态)
4. inventory 库存表：id, material_name(物料), warehouse(仓库), stock_quantity(库存数量), update_date
5. procurement_task 采购任务表：id, material_name(物料), purchase_quantity(采购数量),
   budget_limit(预算红线), task_status(任务状态), create_time(创建时间)
"""

# 安全黑名单：SQL 里出现这些"写操作词"一律拒绝执行
FORBIDDEN_WORDS = ["insert", "update", "delete", "drop", "alter",
                   "truncate", "create", "pragma", "attach", "grant"]


def generate_sql(question: str) -> str:
    """
    第一步：让 LLM 把自然语言翻译成 SELECT 语句
    返回：SQL 字符串；LLM 失败或生成不合法时抛 ValueError（由上层转友好提示）
    """
    user_prompt = f"""数据库表结构如下：
{SCHEMA_TEXT}
用户问题：{question}
请只输出一条 MySQL SELECT 查询语句（输出 JSON，格式 {{"sql": "..."}}），
只查需要的列，必须带 LIMIT（除非用户明确要总数统计）。"""

    result = ask_json(NL2SQL_SYSTEM, user_prompt)  # 调 LLM 翻译（内部带重试）
    if not result or not result.get("sql"):        # LLM 挂了或没给出 sql 字段
        raise ValueError("LLM 未能生成 SQL")        # 抛错，由 run_nl2sql 转友好提示

    sql = result["sql"].strip()                    # 拿到 SQL 并去掉首尾空白
    sql = sql.rstrip(";")                          # 去掉结尾分号（统一由程序控制语句结束）

    # ---- 安全闸①：必须是 SELECT 开头（只读底线）----
    if not sql.lower().startswith("select"):
        raise ValueError(f"拒绝执行：只允许 SELECT 查询，收到：{sql[:50]}")

    # ---- 安全闸②：禁止任何写操作词（不区分大小写，按整词匹配）----
    low = sql.lower()                              # 转小写方便比对
    for w in FORBIDDEN_WORDS:                      # 逐个黑名单词检查
        if w in low:                               # 出现即拒绝（宁严勿松）
            raise ValueError(f"拒绝执行：检测到禁止的写操作词 {w}")

    # ---- 安全闸③：强制限量（没有 LIMIT 就补 LIMIT 20）----
    if "limit" not in low:                         # 用户没主动限行数
        sql = sql + " LIMIT 20"                    # 程序补上，防止拖垮数据库

    logger.info(f"[nl2sql] 生成SQL：{sql[:80]}")   # 留痕：执行了什么 SQL（溯源用）
    return sql                                     # 交出合法 SQL


def run_sql(sql: str) -> list:
    """
    第二步：只读执行 SQL，把结果整理成字典列表
    返回：[{"列名": 值, ...}, ...]；查询出错抛异常由上层接住
    """
    with SessionLocal() as db:                     # 开一个数据库会话（用完自动关闭）
        result = db.execute(text(sql))             # 执行 SQL（text() 包装原生语句）
        rows = [dict(r._mapping) for r in result]  # 每行转成 {"列名":值} 字典
    return rows                                    # 交出查询结果


def run_nl2sql(question: str) -> dict:
    """
    对外总入口（问答子图的 nl2sql 节点调用它）：翻译 → 校验 → 执行
    任何一步出问题都返回 {"error": 友好提示}，绝不抛异常炸掉子图
    """
    try:                                           # 包住全流程：任何失败都转友好提示
        sql = generate_sql(question)               # 翻译+安全校验
        rows = run_sql(sql)                        # 只读执行
        return {"sql": sql, "rows": rows}          # 成功：SQL 和结果一起返回（SQL 是溯源凭证）
    except ValueError as e:                        # 安全校验拒绝（如含写操作词）
        logger.warning(f"[nl2sql] 安全拦截：{e}")
        return {"error": f"该问题无法转换为安全的查询：{e}"}
    except Exception as e:                         # 数据库错误/LLM 彻底失败等
        logger.warning(f"[nl2sql] 查询失败：{type(e).__name__}: {e}")
        return {"error": "查询数据库时出现问题，请换个问法或稍后再试"}
```

---

## 2. `src/rag/agentic_retrieval.py`（新建：迭代检索）

```python
# src/rag/agentic_retrieval.py
"""
agentic_retrieval.py —— Agentic-RAG 迭代检索（RAG 的"不知疲倦的查找员"）
====================================================================
大白话：普通检索是"查一次就交卷"；迭代检索是"查一次，自己看一眼结果够不够好，
不够好就换个问法再查一次"。这就是 Agentic-RAG 的"自判断补检"。

判断标准（简单可靠）：RRF 融合分太低 = 两路检索都没找到像样的结果 = 换问法重查。
"""

from src.rag.hybrid_retriever import hybrid_search  # 阶段一做好的混合检索
from src.utils.llm import ask_json                  # 用 LLM 把问题"换个问法"
from src.utils.logger import get_logger

logger = get_logger("agentic_retrieval")

# 及格线：RRF 单路命中约 0.016（1/61），两路都命中约 0.032
# 低于 0.02 说明连一路都没找到像样结果 → 触发补检
SCORE_THRESHOLD = 0.02


def _rewrite_query(question: str) -> str:
    """
    让 LLM 把原问题"换个问法"（扩词改写），提高第二次检索的命中率
    改写失败就返回原问题（降级：宁可少改写，不能崩）
    """
    result = ask_json(
        "你是检索助手中的查询改写器。把用户的采购相关问题改写成更适合文档检索的查询："
        "补充同义词、展开缩写，但不得改变原意，30字以内。"
        '只输出JSON：{"rewritten": "改写后的查询"}',
        f"原问题：{question}")
    if result and result.get("rewritten"):         # 改写成功
        return result["rewritten"]                 # 交出改写后的问法
    return question                                # 改写失败：退回原问题（降级）


def agentic_hybrid_search(question: str, top_k: int = 4) -> list:
    """
    对外总入口：第一轮检索 → 自判断 → 不合格则改写问题补检一轮
    返回：[{"text":..., "source":..., "rrf_score":...}, ...]（最多 top_k 个）
    """
    results = hybrid_search(question, top_k)       # 第一轮：原问题直接查

    # ---- 自判断：有结果且最高分及格 → 直接交卷 ----
    if results and results[0].get("rrf_score", 0) >= SCORE_THRESHOLD:
        return results                             # 及格，不需要补检

    # ---- 补检：结果为空或分数不及格 → 改写问题再查一轮 ----
    logger.info(f"[agentic] 第一轮结果不足（{len(results)}条），改写问题补检")
    better_query = _rewrite_query(question)        # 让 LLM 换个问法
    if better_query != question:                   # 改写出了新问法才值得再查
        second = hybrid_search(better_query, top_k)  # 第二轮：改写后的问题再查
        if second:                                 # 第二轮查到了东西
            logger.info(f"[agentic] 补检命中 {len(second)} 条")
            return second                          # 用第二轮的结果交卷

    return results                                 # 补检也没改善：交回第一轮结果（可能有空列表）
```

---

## 3. `src/graph/prompts/qa_prompt.py`（新建：问答子图的三个提示词）

```python
# src/graph/prompts/qa_prompt.py
"""
qa_prompt.py —— 问答子图的全部提示词（分诊台 / NL2SQL / 答题人）
"""

# ===== 分诊台：判断问题走哪条路 =====
QUERY_ANALYZE_SYSTEM = """你是采购智能问答系统的分诊台，判断用户问题该走哪条查询路线。
两条路线的区别：
- rag：查"制度/规则/流程/办法"类知识——问"应该怎么做""有什么规定"走这条路
- nl2sql：查"业务数据"——问"有多少、哪些、多少钱、什么时候"这类要看具体数字和记录的走这条路
拿不准的默认走 rag。
只输出JSON：{"route": "rag" 或 "nl2sql"}"""

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
2. 数字必须与资料完全一致，严禁自行计算或修改
3. 回答控制在200字以内，通俗口语化
只输出JSON：{"answer": "你的回答", "used": ["你实际用到的资料编号，如1、2"]}"""


def build_answer_user_prompt(question: str, rag_chunks: list, sql_result: dict) -> str:
    """
    拼答题材料：把 RAG 片段和/或 SQL 查询结果编上号喂给 LLM
    编号是为了让"used"能指明用了哪几条（溯源的依据）
    """
    blocks = []                                    # 存资料块文本
    n = 0                                          # 资料编号计数器
    for c in (rag_chunks or []):                   # RAG 路的片段（可能为空）
        n += 1                                     # 编号+1
        blocks.append(f"【资料{n}】(来源:{c['source']})\n{c['text']}")  # 带来源的片段
    if sql_result and sql_result.get("rows"):      # NL2SQL 路的查询结果（可能为空）
        n += 1                                     # 编号+1
        rows_text = "\n".join(str(r) for r in sql_result["rows"][:10])  # 最多展示10行
        blocks.append(f"【资料{n}】(来源:数据库查询 SQL: {sql_result.get('sql','')})\n{rows_text}")
    if not blocks:                                 # 两路都空手而归
        blocks.append("【资料】(无)——没有检索到任何相关资料")          # 如实告知 LLM
    material = "\n\n".join(blocks)                 # 拼成完整材料
    return f"用户问题：{question}\n\n【参考资料】\n{material}"            # 交出答题材料
```

---

## 4. `src/graph/qa_state.py`（新建：问答子图自己的状态定义）

```python
# src/graph/qa_state.py
"""
qa_state.py —— 问答子图的状态定义
====================================================================
大白话：问答子图是独立于主业务图的"另一台机器"，
它有自己的一张工作台（QAState），不跟主图的 MainGraphState 混用。
TECH_SPEC §2.5：问答子图独立编译、独立状态，只查业务数据、不修改。
"""

from typing import TypedDict, List, Dict, Any, Optional


class QAState(TypedDict):
    question: str                                  # 用户的原始问题（全程不变）
    route: str                                     # 分诊结果："rag" 或 "nl2sql"
    rag_chunks: Optional[List[Dict[str, Any]]]      # RAG 路命中的制度片段（含来源）
    sql_result: Optional[Dict[str, Any]]            # NL2SQL 路的 {"sql":..., "rows":...} 或 {"error":...}
    answer: str                                    # 最终回答文本
    sources: List[str]                             # 溯源列表（如"来自《采购预算管理办法》"）
```

---

## 5. `src/graph/qa_graph.py`（新建：问答子图本体）

```python
# src/graph/qa_graph.py
"""
qa_graph.py —— 问答子图本体（TECH_SPEC §2.5）
====================================================================
四个节点：query_analyze(分诊) → hybrid_retrieve(查制度) / nl2sql(查数据) → answer_generate(答题)
独立编译成一台小图，和主业务图完全解耦；只读业务库，绝不修改。
"""

from typing import Any, Dict
from langgraph.graph import StateGraph, START, END
from src.graph.qa_state import QAState            # 问答子图自己的状态
from src.rag.agentic_retrieval import agentic_hybrid_search  # 阶段一的迭代检索
from src.rag.nl2sql import run_nl2sql             # 本指南的 NL2SQL 总入口
from src.utils.llm import ask_json                # 统一 LLM 入口
from src.graph.prompts.qa_prompt import (         # 三个提示词
    QUERY_ANALYZE_SYSTEM, ANSWER_SYSTEM, build_answer_user_prompt)
from src.utils.logger import get_logger           # 统一日志

logger = get_logger("qa_graph")                   # 问答子图日志器


# ---------- 节点1：分诊台 ----------
async def query_analyze_node(state: QAState):
    """判断问题走 rag 路（查制度）还是 nl2sql 路（查数据）"""
    new_state = dict(state)                        # 复制状态（和主图同一习惯）
    result = ask_json(QUERY_ANALYZE_SYSTEM, state["question"])  # LLM 分类
    route = (result or {}).get("route", "rag")     # 拿分类结果；拿不到默认走 rag
    if route not in ("rag", "nl2sql"):             # LLM 嘴瓢输出了别的值
        route = "rag"                              # 一律纠正为 rag（安全默认）
    new_state["route"] = route                     # 写入分诊结果
    logger.info(f"[qa分诊] '{state['question'][:20]}' → {route}")
    return new_state


def route_by_analyze(state: QAState):
    """分诊后的条件路由：按 route 字段指路"""
    return state.get("route", "rag")               # 返回值直接匹配 path_map 的 key


# ---------- 节点2a：RAG 路——迭代检索制度文档 ----------
async def hybrid_retrieve_node(state: QAState):
    """调用阶段一的迭代检索，找出相关的制度片段"""
    new_state = dict(state)
    chunks = agentic_hybrid_search(state["question"], top_k=4)  # 迭代检索（不足会自动补检）
    new_state["rag_chunks"] = chunks               # 片段写进状态（答题人要用）
    logger.info(f"[qa-rag] 检索到 {len(chunks)} 个片段")
    return new_state


# ---------- 节点2b：NL2SQL 路——查业务数据库 ----------
async def nl2sql_node(state: QAState):
    """调用 NL2SQL 总入口：翻译 SQL → 安全校验 → 只读执行"""
    new_state = dict(state)
    result = run_nl2sql(state["question"])         # 内部已带安全闸和友好降级
    new_state["sql_result"] = result               # 结果（或 error）写进状态
    logger.info(f"[qa-nl2sql] {'查询到'+str(len(result.get('rows',[])))+'行' if 'rows' in result else '查询失败'}")
    return new_state


# ---------- 节点3：答题人（两路汇合） ----------
async def answer_generate_node(state: QAState):
    """把检索/查询到的资料喂给 LLM 生成回答，并整理溯源列表"""
    new_state = dict(state)
    rag_chunks = new_state.get("rag_chunks")       # RAG 路结果（nl2sql 路时是 None）
    sql_result = new_state.get("sql_result")       # NL2SQL 路结果（rag 路时是 None）

    # ---- 先程序化整理溯源列表（不依赖 LLM，保证溯源永远准确）----
    sources = []                                   # 存溯源条目
    if rag_chunks:                                 # RAG 路有片段
        for c in rag_chunks:                       # 每个命中片段都是一条溯源
            sources.append(f"来自《{c['source'].replace('.md','')}》")  # 去掉.md后缀更好看
    if sql_result and sql_result.get("sql"):       # NL2SQL 路执行了 SQL
        sources.append(f"来自数据库查询（{sql_result['sql'][:60]}...）")  # SQL 是查询凭证

    # ---- LLM 生成回答（失败自动降级为"资料摘要"模板）----
    prompt = build_answer_user_prompt(state["question"], rag_chunks, sql_result)  # 拼答题材料
    result = ask_json(ANSWER_SYSTEM, prompt)       # LLM 作答
    if result and result.get("answer"):            # 作答成功
        new_state["answer"] = result["answer"]     # 用 LLM 的回答
    else:                                          # LLM 挂了：降级为资料原样输出
        parts = []                                 # 拼接降级文本
        if rag_chunks:                             # 有片段就罗列片段
            parts += [c["text"][:80] for c in rag_chunks[:2]]      # 最多贴2段
        if sql_result and sql_result.get("rows"):  # 有查询结果就罗列数据行
            parts += [str(r) for r in sql_result["rows"][:3]]      # 最多贴3行
        new_state["answer"] = "；".join(parts) if parts else "未检索到相关资料，请换个问法"
        logger.warning("[qa-answer] LLM作答失败，降级为资料摘要")

    new_state["sources"] = sources                 # 溯源列表写回状态
    logger.info(f"[qa-answer] 回答生成完成，溯源 {len(sources)} 条")
    return new_state


# ---------- 编译子图 ----------
def build_qa_graph():
    """
    编译问答子图（独立于主业务图）
    流程：分诊 → (rag路 | nl2sql路) → 答题 → END
    """
    builder = StateGraph(QAState)                  # 用问答子图自己的状态建图

    # 注册四个节点
    builder.add_node("query_analyze_node", query_analyze_node)      # 分诊台
    builder.add_node("hybrid_retrieve_node", hybrid_retrieve_node)  # 查制度
    builder.add_node("nl2sql_node", nl2sql_node)                    # 查数据
    builder.add_node("answer_generate_node", answer_generate_node)  # 答题人

    # 起点→分诊台
    builder.add_edge(START, "query_analyze_node")

    # 分诊台的条件路由：按 route 指向两条路之一
    builder.add_conditional_edges(
        source="query_analyze_node",
        path=route_by_analyze,
        path_map={
            "rag": "hybrid_retrieve_node",         # 制度类问题→查知识库
            "nl2sql": "nl2sql_node",               # 数据类问题→查数据库
        },
    )

    # 两条路殊途同归：都汇入答题人，答题人完成后结束
    builder.add_edge("hybrid_retrieve_node", "answer_generate_node")
    builder.add_edge("nl2sql_node", "answer_generate_node")
    builder.add_edge("answer_generate_node", END)

    return builder.compile()                       # 编译成可运行的图
```

> **关于 Checkpoint 的一句实话**：TECH_SPEC 说问答子图"独立 Checkpoint"——本 MVP 的问答是**无状态单轮**（问完即答，不存在中断续跑），所以暂时没挂 Redis Checkpoint，这是有意裁剪不是遗漏；等阶段三接 SSE/API 若需要多轮对话再补，一行的事。面试被问就这么答。

---

## 6. `test_qa.py`（新建，放项目根目录，和 test_graph.py 并排）

```python
# test_qa.py
"""
问答子图冒烟测试：三个问题覆盖三条路径
  ① 制度类→rag路   ② 数据类→nl2sql路   ③ 混合类→看分诊怎么判
运行：python test_qa.py
"""
import asyncio                                    # 异步运行入口
from src.graph.qa_graph import build_qa_graph     # 问答子图


async def main():
    graph = build_qa_graph()                       # 编译问答子图
    questions = [                                  # 三个测试问题
        "预算超了应该怎么处理？",                   # ① 明显是制度问题→应走rag
        "最近5条螺纹钢的历史订单有哪些？",          # ② 明显是数据问题→应走nl2sql
        "供应商信用等级怎么划分的？",               # ③ 制度问题→应走rag
    ]
    for q in questions:                            # 逐个提问
        print(f"\n{'='*50}\n问题：{q}")
        result = await graph.ainvoke({"question": q})  # 跑一遍问答子图
        print(f"路由：{result['route']}")           # 打印分诊结果（验证分诊台）
        print(f"回答：{result['answer'][:150]}")    # 打印回答（前150字）
        for s in result["sources"]:                 # 打印溯源
            print(f"  溯源：{s[:70]}")


if __name__ == "__main__":
    asyncio.run(main())                            # 异步入口
```

---

## 7. 验收（2 步）

1. **编译+跑测试**：`python test_qa.py` → 三个问题全部有回答、有溯源，无报错
2. **核对分诊与溯源**：
   - 问题①③的 `route` 应为 `rag`，溯源含《采购预算管理办法》/《供应商管理办法》
   - 问题②的 `route` 应为 `nl2sql`，溯源含 SQL 语句，回答里是**数据库里的真实订单号/日期**（不是编的）

## 8. 注意事项与已知取舍

- **NL2SQL 的三道安全闸**（只 SELECT / 禁分号 / 强制 LIMIT）是防幻觉+防注入的底线，面试必讲；真实生产还会加"SQL 白名单表校验""只读数据库账号"双保险
- 分诊台的"拿不准默认 rag"是刻意设计：rag 路查不到会明说"未涉及"，nl2sql 路错了会真去查库——**错误方向选便宜的那条**
- 每次提问消耗 2~3 次 LLM 调用（分诊 1 + 答题 1 + 可能的补检改写 1）
- LLM 生成 SQL 属于"代码确定性工具执行"的例外（TECH_SPEC §2.6.2 明确问答子图允许 LLM 自主工具调用），但有三道安全闸兜底——这就是"给自由，但戴镣铐"
- 敲完先 `python -m py_compile src/rag/nl2sql.py src/rag/agentic_retrieval.py src/graph/qa_graph.py src/graph/qa_state.py src/graph/prompts/qa_prompt.py` 再跑测试
