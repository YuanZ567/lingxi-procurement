"""
run_purchase_task.py
模拟发起一次采购任务，驱动完整主Graph流转
mock输入参数，测试整条流水线是否可以跑完闭环
用法：
    python run_purchase_task.py                # 默认预算250万，正常审批分支
    python run_purchase_task.py 1000000        # 低预算，超支拦截分支（PRD分支A）
"""
import os
import sys
import uuid
from dotenv import load_dotenv
from src.graph.main_graph import build_main_graph

load_dotenv()
REDIS_URL = os.getenv("REDIS_URL")

async def run_one_task():
    # 预算支持命令行传入：用于双分支验收（正常审批 / 超支拦截）
    budget_limit = float(sys.argv[1]) if len(sys.argv) > 1 else 2500000.0
    task_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": task_id}}

    input_payload = {
        "task_id": task_id,
        "commodity_name": "螺纹钢",
        "purchase_quantity": 500,
        "expect_delivery_time": "2026‑10‑15",
        "budget_limit": budget_limit,
        "user_raw_demand": "制造车间需要采购一批螺纹钢用于生产，希望尽量压低价格",
        "loop_round": 0,             # E2：还价轮数从0开始
        "max_loop_limit": 3,         # E2：还价轮数上限（防死循环硬闸）
        "tried_supplier_ids": [],    # E2：已谈判供应商名单，初始为空
        "risk_retry_count": 0        # E2：风控换商重谈次数，初始为0
    }

    # build_main_graph 是 async context manager：块内 Redis 连接存活，退出自动释放
    async with build_main_graph(REDIS_URL) as graph:
        print(f"=====启动采购任务 task_id:{task_id} | 预算红线:{budget_limit} =====")
        async for chunk in graph.astream(input_payload, config=config):
            for node_name, output in chunk.items():
                print(f"\n👉执行节点：{node_name}")

        final_state = await graph.aget_state(config)
        values = final_state.values
        solution = values.get("final_procurement_solution")
        print("\n=========最终采购方案输出==========")
        print(solution)

        # 双分支验收断言：预算红线全程不丢失、不篡改
        assert values.get("budget_limit") == budget_limit, "预算红线被篡改！"
        print(f"\n=====验收：budget_limit 全程保持 {values.get('budget_limit')}，未被篡改 =====")

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_one_task())
