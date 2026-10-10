"""
agentic_retrieval.py —— Agentic-RAG 迭代检索（RAG 的"不知疲倦的查找员"）
====================================================================
大白话：普通检索是"查一次就交卷"；迭代检索是"查一次，自己看一眼结果够不够好，
不够好就换个问法再查一次"。这就是 Agentic-RAG 的"自判断补检"。

判断标准（简单可靠）：RRF 融合分太低 = 两路检索都没找到像样的结果 = 换问法重查。
"""

from src.rag.hybrid_retriever import hybrid_search  # 阶段一做好的混合检索
from src.utils.llm import ask_json              # 用 LLM 把问题"换个问法"
from src.utils.logger import get_logger

logger=get_logger("agentic_retrieval")

# 及格线：RRF 单路命中约 0.016（1/61），两路都命中约 0.032
# 低于 0.02 说明连一路都没找到像样结果 → 触发补检
SCORE_THRESHOLD = 0.02

async def _rewrite_query(question:str)->str:
    """
     让 LLM 把原问题"换个问法"（扩词改写），提高第二次检索的命中率
    改写失败就返回原问题（降级：宁可少改写，不能崩）
    """
    result=await ask_json(
        "你是检索助手的查询改写器。把用户的采购相关问题改写成更适合文档搜索的查询："
        "补充同义词、展开缩写，但不得改变原意，30字以内。"
        '只输出JSON：{"rewritten": "改写后的查询"}',
        f"原问题：{question}"
    )
    if result and result.get("rewritten"):          # 改写成功
        return result["rewritten"]                  # 交出改写后的问法
    return question                                 # 改写失败：退回原问题（降级）

async def agentic_hybrid_search(question:str,top_k:int=4)->list:
    """
    对外总入口：第一轮检索 → 自判断 → 不合格则改写问题补检一轮
    返回：[{"text":..., "source":..., "rrf_score":...}, ...]（最多 top_k 个）
    """
    results=hybrid_search(question,top_k)               # 第一轮：原问题直接查

     # ---- 自判断：有结果且最高分及格 → 直接交卷 ----
    if results and results[0].get("rrf_score",0) >= SCORE_THRESHOLD:
        return results                          # 及格，不需要补检

     # ---- 补检：结果为空或分数不及格 → 改写问题再查一轮 ----
    logger.info(f"[agentic] 第一轮结果不足（{len(results)}条），改写问题补检")
    better_query=await _rewrite_query(question)            # 让 LLM 换个问法
    if better_query != question:                    # 改写出了新问法才值得再查
        second=hybrid_search(better_query,top_k)         # 第二轮：改写后的问题再查
        if second:
            logger.info(f"[agentic] 补检命中 {len(second)} 条")
            return second                          # 用第二轮的结果交卷

    return results                                 # 补检也没改善：交回第一轮结果（可能有空列表）