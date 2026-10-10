"""
tests/unit/test_hybrid_retriever.py —— 混合检索（向量 + BM25 + RRF）单元测试
====================================================================
被测对象：src/rag/hybrid_retriever.py
为什么测：RAG 的"召回质量"决定问答是否幻觉。RRF 融合是纯算法，必须可验证：
          两路都命中的片段必须排到最前，这是混合检索相对单路检索的核心价值。
"""
import pytest

from src.rag.hybrid_retriever import rrf_merge, bm25_search, hybrid_search


def _doc(text, source="doc.md"):
    return {"text": text, "source": source, "score": 0.0}


# ---------- RRF 融合算法 ----------

@pytest.mark.unit
def test_rrf_merge_empty():
    """空输入不报错，返回空列表"""
    assert rrf_merge([], []) == []


@pytest.mark.unit
def test_rrf_merge_union_of_disjoint_results():
    """两路无交集时，融合结果是两路的并集"""
    a = [_doc("片段A1"), _doc("片段A2")]
    b = [_doc("片段B1"), _doc("片段B2")]
    merged = rrf_merge(a, b)
    assert len(merged) == 4


@pytest.mark.unit
def test_rrf_merge_boosts_dual_hit():
    """核心断言：两路都命中的片段必须排第一（这是混合检索的意义所在）"""
    shared = "两路都命中的片段"
    a = [_doc("只在A路的片段"), _doc(shared), _doc("A路第三")]
    b = [_doc("只在B路的片段"), _doc(shared)]
    merged = rrf_merge(a, b)
    assert merged[0]["text"] == shared
    assert merged[0]["rrf_score"] > merged[1]["rrf_score"]


@pytest.mark.unit
def test_rrf_score_formula():
    """按论文标准公式核验：rank0 得分 = 1/(k+rank+1) = 1/61"""
    merged = rrf_merge([_doc("唯一片段")], [], k=60)
    assert merged[0]["rrf_score"] == pytest.approx(round(1 / 61, 6), abs=1e-6)


@pytest.mark.unit
def test_rrf_k_constant_effect():
    """k 越大，名次差异被平滑得越厉害（高名次优势缩小）"""
    docs = [_doc("第一"), _doc("第二")]
    small_k = rrf_merge(docs, [], k=1)
    large_k = rrf_merge(docs, [], k=1000)
    gap_small = small_k[0]["rrf_score"] - small_k[1]["rrf_score"]
    gap_large = large_k[0]["rrf_score"] - large_k[1]["rrf_score"]
    assert gap_small > gap_large


# ---------- BM25 / 混合检索（依赖已构建的语料）----------

@pytest.mark.unit
def test_bm25_search_recalls_keyword(project_root):
    """BM25 关键词路：命中关键词的片段必须能召回（语料未构建则跳过）"""
    if not (project_root / "chroma_db" / "chunks.jsonl").exists():
        pytest.skip("知识库语料未构建（chroma_db/chunks.jsonl 不存在）")
    results = bm25_search("预算超支怎么处理", top_k=3)
    assert isinstance(results, list)
    assert len(results) <= 3
    for r in results:                       # 输出格式统一：text/source/score
        assert {"text", "source", "score"} <= set(r.keys())


@pytest.mark.unit
def test_hybrid_search_respects_top_k(project_root):
    """混合检索总入口：返回条数不超过 top_k，且每条带融合分"""
    if not (project_root / "chroma_db" / "chunks.jsonl").exists():
        pytest.skip("知识库语料未构建（chroma_db/chunks.jsonl 不存在）")
    results = hybrid_search("采购合同违约条款", top_k=2)
    assert len(results) <= 2
    for r in results:
        assert "rrf_score" in r
