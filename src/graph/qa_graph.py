"""
qa_graph.py —— 问答子图本体
====================================================================
四个节点：query_analyze(分诊) → hybrid_retrieve(查制度) / nl2sql(查数据) → answer_generate(答题)
独立编译成一台小图，和主业务图完全解耦；只读业务库，绝不修改。
（QAState 状态定义在 qa_state.py，提示词在 prompts/qa_prompt.py）
"""

from typing import Any, Dict
from langgraph.graph import StateGraph, START, END
from src.graph.qa_state import QAState            # 问答子图自己的状态（QAState 定义在 qa_state.py）
from src.rag.agentic_retrieval import agentic_hybrid_search  # 阶段一的迭代检索
from src.rag.nl2sql import run_nl2sql             # 本指南的 NL2SQL 总入口
from src.utils.llm import ask_json                # 统一 LLM 入口
from src.graph.prompts.qa_agent_prompt import (    # 三个提示词
    QUERY_ANALYZE_SYSTEM, ANSWER_SYSTEM, build_answer_user_prompt)
from src.utils.logger import get_logger           # 统一日志
from src.utils.formatter import render_rows_table # 查询结果表格化（数字程序渲染防幻觉）

logger = get_logger("qa_graph")                   # 问答子图日志器


# ---------- 节点1：分诊台 ----------
async def query_analyze_node(state:QAState):
    """判断问题走rag路（查制度）还是 nl2sql 路（查数据）"""
    new_state=dict(state)               # 复制状态（和主图同一习惯）
    result=await ask_json(QUERY_ANALYZE_SYSTEM, state["question"], fast=True)  # LLM 分类（轻任务走快速模型）
    route=(result or {}).get("route","rag")             # 拿分类结果；拿不到默认走 rag
    if route not in ("rag","nl2sql"):                   # LLM 嘴瓢输出了别的值
        route="rag"                                     # 一律纠正为 rag（安全默认）
    new_state["route"]=route                             # 写入分诊结果
    logger.info(f"[qa分诊] '{state['question'][:20]}' → {route}")
    return new_state


def route_by_analyze(state:QAState):
    """分诊后的条件路由：按 route 字段指路"""
    return state.get("route","rag")             # 返回值直接匹配 path_map 的 key

# ---------- 节点2a：RAG 路——迭代检索制度文档 ----------
async def hybrid_retrieve_node(state: QAState):
    """调用阶段一的迭代检索，找出相关的制度片段"""
    new_state=dict(state)
    chunks=await agentic_hybrid_search(state["question"],top_k=4)  # 迭代检索（不足会自动补检）
    new_state["rag_chunks"]=chunks              # 片段写进状态（答题人要用）
    logger.info(f"[qa-rag]检索到{len(chunks)}个片段")
    return new_state


# ---------- 节点2b：NL2SQL 路——查业务数据库 ----------
async def nl2sql_node(state: QAState):
    """调用 NL2SQL 总入口：翻译 SQL → 安全校验 → 只读执行"""
    new_state = dict(state)
    result = await run_nl2sql(state["question"])    # 内部已带安全闸和友好降级（异步）
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

    # ---- LLM 生成回答（失败自动降级为"资料摘要"模板）---
    prompt = build_answer_user_prompt(state["question"], rag_chunks, sql_result)  # 拼答题材料
    result = await ask_json(ANSWER_SYSTEM, prompt, fast=True)   # LLM 作答（总结类轻任务走快速模型）
    if result and result.get("answer"):            # 作答成功
        new_state["answer"] = result["answer"]     # 用 LLM 的回答
    else:                                          # LLM 挂了：降级为资料原样输出
        parts = []                                 # 拼接降级文本
        if rag_chunks:                             # 有片段就罗列片段
            parts += [c["text"][:80] for c in rag_chunks[:2]]      # 最多贴2段
        if sql_result and sql_result.get("rows"):  # 有查询结果就贴表格（程序渲染）
            parts += [render_rows_table(sql_result["rows"], max_rows=5)]
        new_state["answer"] = "\n".join(p for p in parts if p) if parts else "未检索到相关资料，请换个问法"
        logger.warning("[qa-answer] LLM作答失败，降级为资料摘要")

    # ---- 数据类回答追加程序化表格：数字由程序渲染，LLM 只写总结（防幻觉+防流水账）----
    if sql_result and sql_result.get("rows"):
        table = render_rows_table(sql_result["rows"])
        if table and table not in new_state["answer"]:     # 降级分支已含表格则不重复
            new_state["answer"] = new_state["answer"] + "\n\n" + table

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