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
