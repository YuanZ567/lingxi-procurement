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