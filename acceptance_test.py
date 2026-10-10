"""
acceptance_test.py —— P5 全流程验收脚本（对照 PRD §4.1~§4.5）
====================================================================
跑法（项目根目录）：python acceptance_test.py
前置：后端 8001 已启动（LLM_MODEL_NAME=qwen-plus 建议）、MySQL/Redis 正常、Chroma 已建库
每通过一项打 ✅，失败打 ❌；最后汇总通过率，全过退出码 0
"""

import json, time, sys                                 # 基础库
import requests                                        # HTTP 客户端（调本地后端）

BASE = "http://localhost:8001"                         # 后端地址
results = []                                           # 收集每条断言结果

def check(name, ok, note=""):                          # 统一记录断言的小工具
    tag = "✅" if ok else "❌"                          # 通过/失败图标
    results.append((name, ok))                        # 存进总表
    print(f"{tag} {name}" + (f" —— {note}" if note else ""))  # 打印带备注

# ========== §4.1 业务流程验收：双分支 + 预算红线 ==========
def accept_business():
    print("\n—— §4.1 业务流程 ——")
    # 分支B：合理预算 → 应该跑完且方案完整
    r = requests.post(f"{BASE}/api/task/create", json={
        "task_name": "验收-分支B合理预算", "material_name": "螺纹钢",
        "purchase_quantity": 500, "expect_delivery_date": "2026-10-20", "budget_limit": 2100000})
    tid_ok = r.json()["task_id"]                      # 合理预算单
    # 分支A：恶意低预算 → 应该被拦截而不是硬出方案
    r = requests.post(f"{BASE}/api/task/create", json={
        "task_name": "验收-分支A超支拦截", "material_name": "铜材",
        "purchase_quantity": 100, "expect_delivery_date": "2026-10-20", "budget_limit": 1000})
    tid_bad = r.json()["task_id"]                     # 超支单
    # 轮询两单直到终态（qwen-plus 一般 1~2 分钟，给足 8 分钟）
    a, b = {}, {}
    for _ in range(60):                               # 最多 60×8 秒
        time.sleep(8)
        a = requests.get(f"{BASE}/api/task/{tid_ok}").json()
        b = requests.get(f"{BASE}/api/task/{tid_bad}").json()
        if a["status"] not in ("pending", "running") and b["status"] not in ("pending", "running"):
            break                                     # 两单都出终态
    check("分支B：合理预算正常跑完", a.get("status") in ("completed", "manual"), f"终态={a.get('status')}")
    check("分支B：方案已生成（>500字）", len(a.get("solution") or "") > 500, f"方案{len(a.get('solution') or '')}字")
    check("分支B：成交额已落库", _get_final_amount(tid_ok) is not None)
    check("分支A：超支被自动拦截", b.get("status") == "blocked", f"终态={b.get('status')}")
    # 预算红线不篡改：直接查库对比
    from src.db.session import SessionLocal           # 复用项目会话（需在项目根目录跑）
    from src.db.models import ProcurementTask
    with SessionLocal() as db:                        # 开会话
        t = db.get(ProcurementTask, tid_ok)           # 查合理预算单
        check("预算红线全程不丢失不篡改", t is not None and float(t.budget_limit) == 2100000.0)
    return tid_ok                                     # 给 §4.2 复用

def _get_final_amount(tid):                            # 查某单成交额的小工具
    from src.db.session import SessionLocal
    from src.db.models import ProcurementTask
    with SessionLocal() as db:
        t = db.get(ProcurementTask, tid)
        return float(t.final_amount) if t and t.final_amount else None

# ========== §4.2 多智能体验收：留痕 + 持久化 + Checkpoint ==========
def accept_agents(tid):
    print("\n—— §4.2 多智能体 ——")
    from src.db.session import SessionLocal
    from src.db.models import NegotiationLog, ProcurementTask
    with SessionLocal() as db:                        # 开会话
        logs = db.query(NegotiationLog).filter(NegotiationLog.task_id == tid).all()  # 该单谈判日志
        t = db.get(ProcurementTask, tid)              # 该单任务
    check("谈判过程留痕（negotiation_log 有轮次）", len(logs) >= 1, f"{len(logs)} 轮")
    check("任务状态持久化（库里可查）", t is not None and t.task_status in ("completed", "manual", "blocked"))
    try:                                              # Redis 可能没起，别让它炸掉整个验收
        import redis                                  # 项目依赖里已有
        r = redis.Redis.from_url("redis://127.0.0.1:6380/0")   # Checkpoint 专用 Redis
        keys = r.keys(f"*{tid}*")                     # 找该任务的 checkpoint 键
        check("Redis Checkpoint 可恢复（键存在）", len(keys) > 0, f"{len(keys)} 个键")
    except Exception as e:                            # Redis 连不上
        check("Redis Checkpoint 可恢复", False, str(e)[:50])

# ========== §4.3 检索问答验收：RAG 溯源 + NL2SQL 无幻觉 ==========
def accept_qa():
    print("\n—— §4.3 检索问答 ——")
    import asyncio                                    # 子图是异步的
    from src.graph.qa_graph import build_qa_graph     # 直接调子图（比走 SSE 稳）
    async def ask(q):                                 # 异步跑一次问答
        g = build_qa_graph()
        return await g.ainvoke({"question": q})
    # 问1（RAG 路）：答案要带溯源
    r1 = asyncio.run(ask("预算超了怎么办"))
    check("RAG：回答有溯源来源", len(r1.get("sources") or []) > 0, f"{len(r1.get('sources') or [])} 条来源")
    # 问2（NL2SQL 路）：回答要含真实数据（订单号或 SQL）
    r2 = asyncio.run(ask("查一笔历史订单的金额"))
    src_text = json.dumps(r2.get("sources", ""), ensure_ascii=False) + str(r2.get("answer", ""))[:300]
    check("NL2SQL：溯源含真实数据（订单号/SQL）", ("SO" in src_text or "SELECT" in src_text.upper()), src_text[:80])

# ========== §4.4 前端验收：页面与数据可达（自动部分） ==========
def accept_frontend():
    print("\n—— §4.4 前端（自动部分，交互项看手工清单）——")
    FE = "http://localhost:5174"                      # 前端地址（5173 被 LegalEye 占用）
    for path, name in [(FE + "/", "看板页面"),
                       (BASE + "/api/market?commodity=螺纹钢&limit=10", "行情接口"),
                       (BASE + "/api/suppliers", "供应商接口"),
                       (BASE + "/api/task/list", "任务列表接口"),
                       (BASE + "/api/task/stats/month", "成本统计接口")]:
        try:
            code = requests.get(path, timeout=5).status_code
            check(f"{name} 可访问", code == 200, f"HTTP {code}")
        except Exception as e:                        # 服务没起也不崩脚本
            check(f"{name} 可访问", False, str(e)[:50])

# ========== §4.5 工程化验收：降级路径 + 重试 + 合规数据在位 ==========
def accept_engineering():
    print("\n—— §4.5 工程化 ——")
    from pathlib import Path                          # 读源码确认关键路径存在
    mp = Path("src/data/market_provider.py").read_text(encoding="utf-8")
    check("行情断网降级读库（代码路径存在）", "读库" in mp or "fallback" in mp.lower())
    rp = Path("src/core/retry.py").read_text(encoding="utf-8")
    check("LLM 重试机制存在", "retry" in rp.lower())
    check("数据合规（供应商档案种子在位）", Path("seed_data/supplier_profile.csv").exists())
    check("知识库语料在位（RAG 可复现）", Path("seed_data/kb").exists())

# ========== 主流程 ==========
if __name__ == "__main__":
    print("=" * 50 + "\n灵犀采购 · P5 全流程验收\n" + "=" * 50)
    tid = accept_business()                           # §4.1（最慢，先跑）
    accept_agents(tid)                                # §4.2
    accept_qa()                                       # §4.3
    accept_frontend()                                 # §4.4
    accept_engineering()                              # §4.5
    passed = sum(1 for _, ok in results if ok)        # 统计
    print("\n" + "=" * 50)
    print(f"验收结果：{passed}/{len(results)} 通过")
    sys.exit(0 if passed == len(results) else 1)      # 全过退出码 0
