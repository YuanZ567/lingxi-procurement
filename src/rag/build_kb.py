# src/data/build_kb.py
"""
build_kb.py —— 知识库建库脚本（RAG 的"图书管理员"）
====================================================================
干什么：把 seed_data/kb/ 下的制度文档读出来 → 切成小片段 →
       ① 算向量存进 ChromaDB（语义检索用） ② 写成 jsonl（BM25 检索用）
手动运行一次即可：python -m src.data.build_kb
文档改了就重跑一遍（重建式更新，语料小所以很快）
"""

import json                                        # 写 jsonl 片段文件用
from pathlib import Path                           # 跨平台路径处理
from src.rag.vector_store import rebuild_vector_store  # 向量库写入
from src.rag.hybrid_retriever import CHUNKS_FILE   # 片段文件路径（和检索端共用同一个）

# 语料目录：seed_data/kb/ 下的所有 .md 文件
KB_DIR = Path(__file__).parent.parent.parent / "seed_data" / "kb"
# 每个片段的最大字数：太长的段落检索不精准，超长要再切
MAX_CHUNK_CHARS = 400


def split_doc(text: str) -> list:
    """
    把一篇文档切成检索片段
    切法（简单有效）：先按空行切段；超过400字的段再按句号切成两半
    """
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]  # 按空行切段、去空白
    chunks = []                                   # 存最终片段
    for p in paras:                               # 遍历每个段落
        if len(p) <= MAX_CHUNK_CHARS:             # 段落不长，整段就是一个片段
            chunks.append(p)
        else:                                     # 段落超长：按句号二分（粗但够用）
            mid = len(p) // 2                     # 找中间位置
            cut = p.find("。", mid)               # 从中间往后找第一个句号
            cut = cut + 1 if cut != -1 else len(p)  # 找到就在句号后切，找不到就硬切
            chunks.append(p[:cut])                # 前半段
            chunks.append(p[cut:])                # 后半段
    return [c for c in chunks if len(c) >= 20]    # 太碎的（<20字）不要，检索出来没意义


def run():
    """建库主流程：读文档 → 切片 → 编号 → 入向量库 → 写 jsonl"""
    chunks = []                                   # 存所有片段
    for md_file in sorted(KB_DIR.glob("*.md")):   # 遍历 kb 目录下每个制度文档
        text = md_file.read_text(encoding="utf-8")  # 读出全文
        pieces = split_doc(text)                  # 切成片段
        for i, piece in enumerate(pieces):        # 给每个片段编号
            chunks.append({                       # 片段的三个要素：编号/内容/来源
                "id": f"{md_file.stem}-{i+1:02d}",   # 编号如"采购预算管理办法-03"
                "text": piece,                    # 片段内容
                "source": md_file.name,           # 来源文件名（溯源卡片显示用）
            })
        print(f"已切分 {md_file.name}：{len(pieces)} 个片段")

    if not chunks:                                # 一个片段都没有说明语料目录是空的
        print("警告：seed_data/kb/ 下没有找到语料！")
        return                                    # 直接结束

    rebuild_vector_store(chunks)                  # ① 向量入库（内部会调 embed_texts 算指纹）

    with open(CHUNKS_FILE, "w", encoding="utf-8") as f:  # ② 同一批片段写成 jsonl
        for c in chunks:                          # 逐行写（jsonl 格式：每行一个 JSON）
            f.write(json.dumps(c, ensure_ascii=False) + "\n")  # ensure_ascii 保留中文原文
    print(f"BM25 语料写入完成：{CHUNKS_FILE}")


if __name__ == "__main__":
    run()                                         # 直接运行：python -m src.data.build_kb
