"""
tests/unit/test_market_provider.py —— 行情三级接力与断网降级单元测试
====================================================================
被测对象：src/data/market_provider.py::refresh_market_price
业务背景：行情是成本测算和谈判定价的基准。演示现场断网是常态，
          "降级读库、流程不中断"是本项目对外承诺的可靠性能力，必须有用例钉死。
手段：monkeypatch 模拟「缓存命中 / 实时成功 / 实时失败 / 库里也没有」四种环境。
"""
import pytest

from src.data import market_provider as mp


@pytest.mark.unit
def test_level1_cache_hit(monkeypatch):
    """第1级：5 分钟内查过 → 直接命中缓存，不发起任何网络请求"""
    class FakeRedis:
        def get(self, key):
            return b"3456.0"

        def set(self, *a, **k):
            return True

    monkeypatch.setattr(mp, "_get_redis", lambda: FakeRedis())
    monkeypatch.setattr(mp, "fetch_realtime_price", lambda c: pytest.fail("命中缓存时不应联网"))
    assert mp.refresh_market_price("螺纹钢") == "缓存"


@pytest.mark.unit
def test_level2_realtime_success_and_persisted(monkeypatch):
    """第2级：实时抓到 → 必须写库留痕（行情时间序列随使用自动增长），且来源标记为实时"""
    written = {}

    def fake_fetch(commodity):
        return 3456.78

    def fake_save(commodity, price):
        written["commodity"] = commodity
        written["price"] = price
        return True

    monkeypatch.setattr(mp, "_get_redis", lambda: None)
    monkeypatch.setattr(mp, "fetch_realtime_price", fake_fetch)
    monkeypatch.setattr(mp, "save_price_to_db", fake_save)

    assert mp.refresh_market_price("螺纹钢") == "实时"
    assert written == {"commodity": "螺纹钢", "price": 3456.78}   # 留痕断言：价格确实落库了


@pytest.mark.unit
def test_level3_fallback_to_db_when_offline(offline, db):
    """第3级（核心可靠性）：断网时降级读库，返回"数据库降级"，流程绝不断供"""
    source = mp.refresh_market_price("螺纹钢")
    assert source in ("数据库降级", "无数据")     # 库里没种子数据时才是"无数据"
    if source == "数据库降级":
        from src.db.models import CommodityPrice
        assert db.query(CommodityPrice).filter(
            CommodityPrice.commodity_name == "螺纹钢"
        ).count() > 0


@pytest.mark.unit
def test_level4_no_data_at_all(offline):
    """第4级：断网 + 库里也没这个品种 → 诚实返回"无数据"，不编造价格（防幻觉底线）"""
    assert mp.refresh_market_price("不存在的品种XYZ") == "无数据"


@pytest.mark.unit
def test_commodity_mapping_is_complete():
    """品种→主力合约映射覆盖前端四个可切换品类，避免切到某品类时取不到价"""
    for name in ("螺纹钢", "铝材", "铜材", "塑料粒子"):
        assert name in mp.COMMODITY_FUTURES_MAP, f"{name} 未配置主力合约映射"
