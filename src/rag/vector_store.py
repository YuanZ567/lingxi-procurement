"""
向量库模块（RAG 的"语义检索员"）
====================================================================
干什么：把文本变成"语义指纹"（向量），存进 ChromaDB；
       查询时把用户问题也变成向量，找出语义最相近的文档片段。

向量是什么（大白话）：把一段文字压缩成一串数字（比如1024个），
意思相近的文字，这串数字就相近——"预算超支怎么办"和
《预算管理办法》里讲超支的段落，数字会离得很近。
"""

import chromadb         # ChromaDB：开源向量数据库，数据存本地文件夹
from langchain_openai import OpenAIEmbeddings     # 用百炼的 embedding 模型把文字变向量
from src.utils.config import get_settings              # 从配置中心拿 API Key

# 向量库的存放位置：项目根目录下的 chroma_db/ 文件夹（数据持久化在本地
CHROMA_DIR="chroma_db"

#集合（collection）名字：可以理解为向量库里的“一张表”
COLLECTION_NAME="lingxi_kb"

# 语义指纹生成器：调百炼的 text-embedding-v3 模型，把文字变成 1024 维向量
# check_embedding_ctx_length=False 必须加：告诉 langchain 别按 OpenAI 的方式预切分文本

_embedder=OpenAIEmbeddings(
    model="text-embedding-v3",
    api_key=get_settings().DASHSCOPE_API_KEY,
    base_url=get_settings().LLM_BASE_URL,
    dimensions=1024,                                # 输出向量的维度
    check_embedding_ctx_length=False                # 关键参数：关闭 OpenAI 专用的预切分检查
)

def embed_texts(texts:list)->list:
    """
    批量把文字变成向量（语义指纹）
    texts：["第一段话", "第二段话", ...]
    返回：[[0.1, 0.2, ...1024个数], [...], ...] 每段话对应一串数字
    """
    # 百炼 embedding 接口限制单批最多 10 条（9/10 实测 17 条直接 400 报错）
    # 所以这里手动按 10 条一批拆开调，结果按顺序拼回去
    all_vectors=[]                                 # 存全部向量的总列表
    for i in range(0,len(texts),10):               # 从0开始每次步进10：0,10,20...
        batch=texts[i:i+10]                        # 切出本批最多10条
        vectors=_embedder.embed_documents(batch)   # 本批调用一次接口
        all_vectors.extend(vectors)                # 拼接到总列表（顺序不会乱）
    return all_vectors                             # 交出与输入等长且顺序一致的向量列表


def embed_query(query:str)->list:
    """
    把"用户的一个问题"变成向量（检索时用）
    问题短，单独一个方法，接口也不同（embed_query）
    """
    return _embedder.embed_query(query)                   # 返回一串 1024 个的数字

def get_collection():
    """
    拿到向量库的集合对象（不存在就自动创建）
    PersistentClient：数据存到本地 chroma_db/ 文件夹，程序重启数据还在
    """

    client=chromadb.PersistentClient(path=CHROMA_DIR)       # 打开（或创建）本地向量库
    return client.get_or_create_collection(              # 拿到名为 lingxi_kb 的集合
        name=COLLECTION_NAME,
        metadata={"hnsw:space":"cosine"}            # 用"余弦相似度"衡量向量远近（文本检索标准做法）
    )

def rebuild_vector_store(chunks:list):
    """
    重建向量库：把所有文档片段连同向量一起存进去
    chunks：[{"id":"xxx","text":"段落内容","source":"文件名.md"}, ...]
    每次重建都清空旧数据（简单粗暴，语料少时重建只要几秒）
    """
    client=chromadb.PersistentClient(path=CHROMA_DIR)       # 打开本地向量库
    try:                                                    # 尝试删除旧集合
        client.delete_collection(COLLECTION_NAME)           # 有旧的就删掉（保证干净重建）
    except Exception:                                       # 第一次跑没有旧集合会报错
        pass                                                # 报错忽略即可（本来就没有）

    col=client.get_or_create_collection(                    # 重新创建空集合
        name=COLLECTION_NAME,
        metadata={"hnsw:space":"cosine"},                    # 同样用余弦相似度
    )

    texts=[c["text"] for c in chunks]                   # 抽出所有段落文字
    vectors=embed_texts(texts)                           # 一次性批量算语义指纹（省调用次数）
    col.add(                                            # 存入向量库
        ids=[c["id"] for c in chunks],                  # 每段的唯一编号
        documents=texts,                                # 每段的原文（检索到时原样返回）
        embeddings=vectors,                             # 每段的语义指纹
        metadatas=[{"source":c["source"]} for c in chunks],          # 每段的来源文件（溯源用）
    )
    print(f"向量库重建完成：{len(chunks)}个片段已入库->{CHROMA_DIR}")

def vector_search(query:str,top_k:int=4)->list:
     """
    语义检索：拿用户问题，找出语义最相近的 top_k 个片段
    返回：[{"text":段落内容, "source":来源文件, "score":相似度0~1}, ...] 按相似度从高到低
    """
     col=get_collection()                                # 打开向量库
     qv=embed_query(query)                               # 把问题变成向量
     res=col.query(query_embeddings=[qv],n_results=top_k)      # 向量库内部做相似度排序
     out=[]                                                 # 存整理后的结果
     for doc,meta,dist in zip(                               # zip 同时遍历三组返回值
         res["documents"][0],                                 # 命中的段落文字
         res["metadatas"][0],                                 # 段落来源
         res["distances"][0]                                  # 距离（越小越相似）
     ):
        out.append({                                      # 整理成统一格式
            "text": doc,                                  # 段落原文
            "source": meta.get("source", "未知"),          # 来源文件
            "score": round(1 - dist, 4),                  # 距离转相似度（余弦：相似度=1-距离）
        })
     return out  

