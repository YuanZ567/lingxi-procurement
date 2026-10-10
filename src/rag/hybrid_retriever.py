# src/rag/hybrid_retriever.py
"""
混合检索模块（RAG 的"搜索队长"）
====================================================================
同时指挥两路检索：
  ① 向量路（语义匹配，vector_store.py 负责）
  ② BM25 路（关键词匹配，本文件用 jieba 分词 + rank_bm25 实现）
两路结果用 RRF 算法融合成一个最终排名。

RRF 是什么（大白话）：每路检索都给结果排了个名次，
RRF 说"别比分数了（两边分数没法比），比名次"——
一个片段在两路里名次都靠前，融合分就高。公式：融合分 = Σ 1/(60+名次)。
"""

import json                     # 读 jsonl 片段文件用
import jieba                    # 中文分词：把句子切成词语（BM25 只认词）
from rank_bm25 import BM25Okapi  # BM25 算法：经典的"关键词打分"检索算法
from src.rag.vector_store import vector_search  # 复用向量检索

# 语料片段的存放文件：建库时写入，检索时读出（和 build_kb.py 共用同一个路径）
CHUNKS_FILE = "chroma_db/chunks.jsonl"


def load_chunks() -> list:
    """从 jsonl 文件读出所有片段（BM25 索引要用）"""
    chunks = []                                      # 存读出来的片段
    with open(CHUNKS_FILE, "r", encoding="utf-8") as f:  # 打开片段文件
        for line in f:                               # 逐行读（jsonl 每行是一个 JSON）
            if line.strip():                         # 跳过空行
                chunks.append(json.loads(line))      # 反序列化成 dict 加进列表
    return chunks                                    # 交出全部片段


def bm25_search(query: str, top_k: int = 4) -> list:
    """
    BM25 关键词检索：谁包含的"查询词"多且稀少，谁得分高
    返回：[{"text":..., "source":..., "score":...}, ...] 按得分从高到低
    """
    chunks = load_chunks()                           # 读出全部片段
    tokenized = [list(jieba.cut_for_search(c["text"])) for c in chunks]  # 每个片段切成词列表
    bm25 = BM25Okapi(tokenized)                      # 构建 BM25 索引（语料小，现场建不费时）
    scores = bm25.get_scores(list(jieba.cut_for_search(query)))  # 给每个片段打分
    paired = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)  # 按分数降序配对排序
    return [{                                        # 整理成和向量检索相同的输出格式
        "text": c["text"],                           # 片段原文
        "source": c["source"],                       # 来源文件
        "score": round(s, 4),                        # BM25 分数（仅用于本路排名）
    } for c, s in paired[:top_k] if s > 0]           # 只取前 top_k 个、过滤掉 0 分的


def rrf_merge(list_a: list, list_b: list, k: int = 60) -> list:
    """
    RRF 融合：把两路检索的结果按"名次"合成一个排名
    list_a/list_b：两路各自排好序的结果列表；k：RRF 平滑常数（论文标准值60）
    返回：融合后的统一列表（每项多一个 rrf_score 字段）
    """
    pool = {}                                        # 融合池：key=片段原文，value=融合信息
    for rank, item in enumerate(list_a):             # 遍历 A 路结果（rank 从 0 开始）
        pool.setdefault(item["text"], {"item": item, "score": 0})  # 没见过的片段放进池子
        pool[item["text"]]["score"] += 1 / (k + rank + 1)          # A 路名次贡献融合分
    for rank, item in enumerate(list_b):             # 同样处理 B 路结果
        pool.setdefault(item["text"], {"item": item, "score": 0})  # 两路都命中的会在这里累加
        pool[item["text"]]["score"] += 1 / (k + rank + 1)          # B 路名次也贡献融合分
    merged = sorted(pool.values(), key=lambda x: x["score"], reverse=True)  # 按融合分降序
    out = []                                         # 存输出
    for v in merged:                                 # 遍历融合结果
        item = dict(v["item"])                       # 复制片段信息
        item["rrf_score"] = round(v["score"], 6)     # 附上融合分（调试/展示用）
        out.append(item)                             # 加进输出
    return out                                       # 交出融合后的统一排名


def hybrid_search(query: str, top_k: int = 4) -> list:
    """
    混合检索总入口（问答子图调用它）：向量路 + BM25 路 → RRF 融合 → 取前 top_k
    返回：[{"text":..., "source":..., "rrf_score":...}, ...]
    """
    vec_results = vector_search(query, top_k)        # 第1路：语义检索
    bm_results = bm25_search(query, top_k)           # 第2路：关键词检索
    merged = rrf_merge(vec_results, bm_results)      # 融合两路排名
    return merged[:top_k]                            # 交出最终的前 top_k 个片段
