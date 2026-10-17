# src/graph/qa_state.py
"""
qa_state.py —— 问答子图的状态定义（v2：Agent 形态）
====================================================================
大白话：这张"工作台"比 v1 多了三样东西——
多轮记忆(history)、工具观察(tool_observations)、反问(clarify_question)。
TECH_SPEC §2.5 仍然成立：问答子图独立编译、独立状态，只查业务数据、绝不修改。
分区规则很重要：
  「每轮清零」字段由 start_node 在每轮开头强制重置——因为挂了 checkpointer 之后，
   状态是跨轮持久的，上一轮的工具观察/轮数计数如果不清零，下一轮会"串味"；
  「跨轮持久」字段谁都不许清，这就是记忆本体。
"""

from typing import TypedDict, List, Dict, Any, Optional


class QAState(TypedDict):
    # ---------- 用户输入（每轮覆盖） ----------
    question: str                                     # 用户的原始问题（每轮新值直接覆盖旧值）

    # ---------- 每轮清零的临时字段（start_node 每轮开头重置） ----------
    plan: Optional[Dict[str, Any]]                    # planner 的决定：{"action":"tool/answer/clarify", ...}
    tool_observations: Optional[List[Dict[str, Any]]] # 工具观察列表：[{tool,args,note,data}, ...]（本轮攒的材料）
    iterations: int                                   # 本轮已调工具次数（回环计数闸，够 3 次强制收卷）
    rag_chunks: Optional[List[Dict[str, Any]]]        # v1 兼容字段：RAG 路命中片段（降级老路还用）
    sql_result: Optional[Dict[str, Any]]              # v1 兼容字段：NL2SQL 查询结果（降级老路还用）

    # ---------- 输出（每轮覆盖） ----------
    route: str                                        # "agent"=正常回答 / "agent_clarify"=反问轮
    answer: str                                       # 最终回答文本（反问轮里放反问话术）
    sources: List[str]                                # 溯源列表（如"来自《采购预算管理办法》"）
    clarify_question: Optional[str]                   # 反问内容；非反问轮为 None（SSE 靠它分流事件）

    # ---------- 跨轮持久字段（checkpointer 负责，代码里绝不主动清） ----------
    history: Optional[List[Dict[str, str]]]           # 多轮对话记忆 [{role:"user"/"bot", text}, ...]
