"""
test_graph.py
测试主Graph编译是否正常
仅做结构校验，不运行Agent业务逻辑
build_main_graph 是 async context manager（AsyncRedisSaver 生命周期要求），
必须用 async with 包住，不能直接赋值（会拿到 _GeneratorContextManager）
放在项目根目录
"""
import os
import asyncio
from dotenv import load_dotenv
from src.graph.main_graph import build_main_graph

# 加载环境变量
load_dotenv()

REDIS_URL = os.getenv("REDIS_URL")


async def test_build_graph():
    print("===== 开始编译主业务Graph =====")
    try:
        # with 块内 Redis 连接全程存活，退出自动释放
        async with build_main_graph(redis_url=REDIS_URL) as graph:
            print("✅ Graph编译成功！")
            # 输出图结构简单预览
            print(f"Graph节点列表：{list(graph.get_graph().nodes.keys())}")
    except Exception as e:
        print(f"❌ Graph编译失败：{str(e)}")
        raise e


if __name__ == "__main__":
    asyncio.run(test_build_graph())
