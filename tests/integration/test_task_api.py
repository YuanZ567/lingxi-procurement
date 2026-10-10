"""
tests/integration/test_task_api.py —— 采购任务接口集成测试
====================================================================
依赖：MySQL（Redis 可选）。不调用真实 LLM（LLM 由 conftest 全局拦截）。
边界：不测"创建任务成功→跑图"那条路径——那是 e2e 的活（要真 LLM、要几分钟）。
      这里只测接口契约、错误码、落库一致性，保证快且稳定。
"""
import uuid

import pytest

from src.db.models import ProcurementTask


def _make_task(db, task_id, status="pending", budget=2_100_000.0):
    """直接落库造一条任务（不走 /create，避免触发后台 LLM 流水线）"""
    task = ProcurementTask(
        id=task_id,
        task_name="集成测试临时单",
        material_name="螺纹钢",
        purchase_quantity=100,
        expect_delivery_date="2026-10-20",
        budget_limit=budget,
        task_status=status,
    )
    db.add(task)
    db.commit()
    return task


def _cleanup(db, task_id):
    t = db.get(ProcurementTask, task_id)
    if t:
        db.delete(t)
        db.commit()


# ---------- 参数校验：非法输入必须挡在门外（错误码 4001）----------

@pytest.mark.integration
@pytest.mark.parametrize("payload", [
    {"task_name": "坏单", "material_name": "螺纹钢", "purchase_quantity": 100,
     "expect_delivery_date": "2026-10-20", "budget_limit": 0},          # 预算=0
    {"task_name": "坏单", "material_name": "螺纹钢", "purchase_quantity": 100,
     "expect_delivery_date": "2026-10-20", "budget_limit": -100},       # 预算为负
    {"task_name": "坏单", "material_name": "螺纹钢", "purchase_quantity": 0,
     "expect_delivery_date": "2026-10-20", "budget_limit": 1000},       # 数量=0
])
def test_create_rejects_invalid_params(client, payload):
    r = client.post("/api/task/create", json=payload)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == 4001


@pytest.mark.integration
def test_create_rejects_missing_field(client):
    """缺 material_name → Pydantic 422，不会写出脏数据"""
    r = client.post("/api/task/create", json={"purchase_quantity": 1, "budget_limit": 1})
    assert r.status_code == 422


# ---------- 查询接口契约 ----------

@pytest.mark.integration
def test_get_task_404(client):
    """查不存在的单 → 404 + 错误码 4004，不裸抛堆栈"""
    r = client.get(f"/api/task/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == 4004


@pytest.mark.integration
def test_list_returns_array(client):
    r = client.get("/api/task/list")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.integration
def test_stats_month_contract(client, db):
    """统计接口契约：三个字段齐全且类型正确（前端面板直接消费）"""
    r = client.get("/api/task/stats/month")
    assert r.status_code == 200
    body = r.json()
    assert {"done_count", "total_saving", "avg_budget_ratio"} <= set(body.keys())
    assert isinstance(body["done_count"], int)
    assert isinstance(body["total_saving"], (int, float))


# ---------- 落库一致性与状态机 ----------

@pytest.mark.integration
def test_budget_red_line_persisted_untouched(client, db):
    """红线不丢失不篡改：库里的预算必须和写入值分毫不差（这是全链路约束的根基）"""
    tid = str(uuid.uuid4())
    budget = 1_234_567.89                       # 用"有零有整"的数，防巧合通过
    _make_task(db, tid, budget=budget)
    try:
        r = client.get(f"/api/task/{tid}")
        assert r.status_code == 200
        assert r.json()["db_status"] == "pending"
        t = db.get(ProcurementTask, tid)
        assert float(t.budget_limit) == budget  # Decimal 比较：精确相等
    finally:
        _cleanup(db, tid)


@pytest.mark.integration
def test_approve_rejects_non_manual_status(client, db):
    """状态机守护：只有 manual 态的单允许人工审批，pending 单直接 400（码 4002）"""
    tid = str(uuid.uuid4())
    _make_task(db, tid, status="pending")
    try:
        r = client.post(f"/api/task/{tid}/approve")
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == 4002
    finally:
        _cleanup(db, tid)


@pytest.mark.integration
def test_approve_404_for_missing_task(client):
    r = client.post(f"/api/task/{uuid.uuid4()}/approve")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == 4004


@pytest.mark.integration
def test_delete_task_removes_from_db(client, db):
    """删单：接口返回 200，库里真的没了，再次 GET 得 404"""
    tid = str(uuid.uuid4())
    _make_task(db, tid)
    assert client.delete(f"/api/task/{tid}").status_code == 200
    assert client.get(f"/api/task/{tid}").status_code == 404
