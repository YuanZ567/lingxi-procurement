"""
tests/e2e/test_full_flow.py —— 端到端验收（对照 PRD §4.1~§4.5）
====================================================================
这是 acceptance_test.py 的 pytest 正式版：原脚本保留可手动跑，
本文件纳入 pytest 分层体系，命令：pytest -m e2e
前置：后端 8001 已启动、MySQL/Redis 正常、Chroma 已建库、.env 配置了真实 LLM key。
后端没起？自动 skip，不误伤其他层的测试结果。
"""
import json
import time
import uuid

import pytest
import requests

BASE = "http://localhost:8001"
POLL_TIMES, POLL_INTERVAL = 60, 8          # 最多等 60×8 秒（qwen-plus 一单 1~2 分钟）


def _backend_alive() -> bool:
    try:
        return requests.get(f"{BASE}/health", timeout=3).status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def _require_backend():
    """后端没起就整模块跳过（e2e 是显式验收，不是 CI 常规项）"""
    if not _backend_alive():
        pytest.skip("后端 8001 未启动，跳过 e2e 验收（启动后重跑：pytest -m e2e）")


@pytest.mark.e2e
@pytest.mark.slow
class TestPRDAcceptance:
    """PRD §4 五个验收面，每条对应一个 test 方法，跑完即验收报告"""

    def test_41_branch_b_reasonable_budget_completes(self):
        """§4.1 分支B：合理预算正常跑完、方案完整、成交额落库"""
        r = requests.post(f"{BASE}/api/task/create", json={
            "task_name": "验收-分支B合理预算", "material_name": "螺纹钢",
            "purchase_quantity": 500, "expect_delivery_date": "2026-10-20",
            "budget_limit": 2_100_000})
        tid = r.json()["task_id"]
        task = self._wait_terminal(tid)
        assert task["status"] in ("completed", "manual"), f"终态异常：{task['status']}"
        assert len(task.get("solution") or "") > 500, "方案字数不足，疑似空跑"
        # 成交额落库（查库为权威）
        from src.db.session import SessionLocal
        from src.db.models import ProcurementTask
        with SessionLocal() as db:
            t = db.get(ProcurementTask, tid)
            assert t is not None and t.final_amount is not None, "成交额未落库"
            assert float(t.budget_limit) == 2_100_000.0, "预算红线被篡改！"

    def test_41_branch_a_low_budget_blocked(self):
        """§4.1 分支A：恶意低预算必须被拦截（blocked），绝不硬出方案"""
        r = requests.post(f"{BASE}/api/task/create", json={
            "task_name": "验收-分支A超支拦截", "material_name": "铜材",
            "purchase_quantity": 100, "expect_delivery_date": "2026-10-20",
            "budget_limit": 1000})
        task = self._wait_terminal(r.json()["task_id"])
        assert task["status"] == "blocked", f"超支单未被拦截，终态={task['status']}"

    def test_42_negotiation_logs_persisted(self):
        """§4.2 多智能体留痕：谈判轮次落 negotiation_log，任务终态持久化"""
        tid = self._latest_completed_task_id()
        from src.db.session import SessionLocal
        from src.db.models import NegotiationLog
        with SessionLocal() as db:
            logs = db.query(NegotiationLog).filter(NegotiationLog.task_id == tid).all()
        assert len(logs) >= 1, "谈判过程未留痕，复盘/审计能力失效"

    def test_42_redis_checkpoint_recoverable(self):
        """§4.2 断点续跑：Redis 里存在该任务的 checkpoint 键（thread_id=task_id）"""
        tid = self._latest_completed_task_id()
        import redis
        r = redis.Redis.from_url("redis://127.0.0.1:6380/0", socket_connect_timeout=3)
        keys = r.keys(f"*{tid}*")
        assert len(keys) > 0, "Checkpoint 键不存在，中断恢复能力未生效"

    def test_43_rag_answer_has_sources(self):
        """§4.3 RAG 路：回答必须带溯源来源（防幻觉的核心证据）"""
        out = self._ask("预算超了怎么办")
        assert len(out.get("sources") or []) > 0, "RAG 回答无溯源"

    def test_43_nl2sql_returns_real_data(self):
        """§4.3 NL2SQL 路：溯源含真实订单号或 SQL，证明查的是真库不是编的"""
        out = self._ask("查一笔历史订单的金额")
        blob = json.dumps(out.get("sources", ""), ensure_ascii=False) + str(out.get("answer", ""))[:300]
        assert ("SO" in blob or "SELECT" in blob.upper()), "NL2SQL 回答缺真实数据凭证"

    def test_44_frontend_and_apis_reachable(self):
        """§4.4 前端与看板接口可达（交互项看手工清单）"""
        checks = [
            ("http://localhost:5174/", "看板页面"),
            (f"{BASE}/api/market?commodity=螺纹钢&limit=10", "行情接口"),
            (f"{BASE}/api/suppliers", "供应商接口"),
            (f"{BASE}/api/task/list", "任务列表接口"),
            (f"{BASE}/api/task/stats/month", "成本统计接口"),
        ]
        for url, name in checks:
            r = requests.get(url, timeout=10)
            assert r.status_code == 200, f"{name} 不可达：HTTP {r.status_code}"

    def test_45_engineering_fallbacks_in_place(self, project_root):
        """§4.5 工程化：降级/重试/合规数据在位"""
        mp = (project_root / "src/data/market_provider.py").read_text(encoding="utf-8")
        assert "读库" in mp or "fallback" in mp.lower(), "行情降级路径缺失"
        rp = (project_root / "src/core/retry.py").read_text(encoding="utf-8")
        assert "retry" in rp.lower(), "LLM 重试机制缺失"
        assert (project_root / "seed_data/supplier_profile.csv").exists(), "供应商档案种子缺失"
        assert (project_root / "seed_data/kb").exists(), "RAG 语料缺失"

    # ---------- 工具方法 ----------

    @staticmethod
    def _wait_terminal(task_id: str) -> dict:
        """轮询直到任务出终态（completed/manual/blocked/failed）"""
        for _ in range(POLL_TIMES):
            time.sleep(POLL_INTERVAL)
            task = requests.get(f"{BASE}/api/task/{task_id}", timeout=10).json()
            if task.get("status") not in ("pending", "running", None):
                return task
        pytest.fail(f"任务 {task_id} 在 {POLL_TIMES * POLL_INTERVAL}s 内未出终态")

    @staticmethod
    def _latest_completed_task_id() -> str:
        """取最近一单已完成任务，给留痕/Checkpoint 断言当样本"""
        rows = requests.get(f"{BASE}/api/task/list", timeout=10).json()
        done = [r for r in rows if r["status"] == "completed"]
        if not done:
            pytest.skip("没有已完成的任务可验证留痕（先跑分支B用例）")
        return done[0]["task_id"]

    @staticmethod
    def _ask(question: str) -> dict:
        """直调问答子图（比走 SSE 稳，不依赖前端在线）"""
        import asyncio
        from src.graph.qa_graph import build_qa_graph
        async def run():
            return await build_qa_graph().ainvoke({"question": question})
        return asyncio.run(run())
