"""
tests/integration/test_data_api.py —— 看板数据接口集成测试（行情 / 供应商）
====================================================================
依赖：MySQL。不调用真实 LLM。
注意：/api/market 正常会触发 akshare 实时抓取（慢且依赖外网），
      集成层把刷新动作 mock 掉，只验证"查库出数"的契约；联网刷新链路由 e2e 验收覆盖。
"""
import pytest

from src.db.models import Supplier


@pytest.mark.integration
def test_suppliers_returns_records(client, db):
    """供应商接口：有数据、字段齐全、溢价率是百分比数值"""
    r = client.get("/api/suppliers")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) > 0, "供应商表应为种子数据非空"
    for row in rows[:5]:
        assert {"name", "credit", "premium", "region", "category"} <= set(row.keys())
        assert 0 <= row["credit"] <= 100          # 信用分在合理区间


@pytest.mark.integration
def test_suppliers_filter_by_existing_category(client, db):
    """品类筛选：从库里取一个真实存在的品类再过滤，结果必须全中且非空"""
    sample = db.query(Supplier).filter(Supplier.main_category.isnot(None)).first()
    if not sample:
        pytest.skip("供应商种子数据未导入")
    r = client.get("/api/suppliers", params={"category": sample.main_category})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) > 0
    assert all(row["category"] == sample.main_category for row in rows)


@pytest.mark.integration
def test_suppliers_filter_by_existing_region(client, db):
    """地区筛选：同理取真实地区值，避免硬编码碰运气"""
    sample = db.query(Supplier).filter(Supplier.province.isnot(None)).first()
    if not sample:
        pytest.skip("供应商种子数据未导入")
    r = client.get("/api/suppliers", params={"region": sample.province})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) > 0
    assert all(row["region"] == sample.province for row in rows)


@pytest.mark.integration
def test_market_returns_series_contract(client, monkeypatch):
    """行情接口契约：mock 掉联网刷新后，返回 {source, latest_date, series} 且按日期升序"""
    monkeypatch.setattr("src.api.data_api.refresh_market_price", lambda c: "缓存")
    monkeypatch.setattr("src.api.data_api.backfill_history", lambda c: None)

    r = client.get("/api/market", params={"commodity": "螺纹钢", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert {"source", "latest_date", "series"} <= set(body.keys())
    assert body["source"] == "缓存"
    assert len(body["series"]) <= 5
    if body["series"]:
        dates = [d["date"] for d in body["series"]]
        assert dates == sorted(dates), "行情序列必须从旧到新（图表折线方向）"
        for d in body["series"]:
            assert {"date", "close"} <= set(d.keys())
            assert d["close"] > 0


@pytest.mark.integration
def test_market_limit_validation(client, monkeypatch):
    """limit 超过 2603（库里全部交易日数）→ 参数校验 422，防止恶意大查询"""
    monkeypatch.setattr("src.api.data_api.refresh_market_price", lambda c: "缓存")
    monkeypatch.setattr("src.api.data_api.backfill_history", lambda c: None)
    r = client.get("/api/market", params={"commodity": "螺纹钢", "limit": 99999})
    assert r.status_code == 422
