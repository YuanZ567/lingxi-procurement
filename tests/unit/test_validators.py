"""
tests/unit/test_validators.py —— 业务红线校验器单元测试
====================================================================
被测对象：src/core/validators.py::validate_negotiation_deal
为什么先测它：预算红线是本项目"程序判定、LLM 无判定权"的命门，
              这条逻辑一旦被 LLM 绕过，整个风控闭环就废了，所以必须参数化全覆盖。
特点：纯函数、不联网、不连库，毫秒级，CI 必跑。
"""
import pytest

from src.core.validators import validate_negotiation_deal


def _state(budget, total, unit, base=0):
    """拼一个最小可用的 state（只放校验器真正读的字段）"""
    return {
        "budget_limit": budget,
        "negotiation_result": {
            "final_total_amount": total,
            "final_quote_price": unit,
        },
        "market_intel_result": {"market_base_price": base},
    }


# ---------- 预算红线：本项目第一优先级约束 ----------

@pytest.mark.unit
@pytest.mark.parametrize("budget,total,unit,expect_ok,case", [
    (2_100_000, 1_800_000, 3600, True,  "正常成交：低于预算"),
    (2_100_000, 2_100_000, 4200, True,  "边界：恰好等于预算（不算超支）"),
    (2_100_000, 2_100_001, 4200, False, "边界+1元：必须拦截"),
    (2_100_000, 3_000_000, 6000, False, "明显超预算：必须拦截"),
    (1_000, 999_999, 9999, False, "恶意低预算：必须拦截"),
])
def test_budget_red_line(budget, total, unit, expect_ok, case):
    """预算红线不可突破：等于放行、超过必拦"""
    ok, reason = validate_negotiation_deal(_state(budget, total, unit))
    assert ok is expect_ok, f"{case}：期望 ok={expect_ok}，实际 {ok}，原因={reason}"
    if not expect_ok:
        assert "预算红线" in reason        # 失败原因要说清楚是预算问题


# ---------- 单价合法性 ----------

@pytest.mark.unit
@pytest.mark.parametrize("unit,expect_ok", [(0, False), (-100, False), (0.01, True)])
def test_unit_price_legality(unit, expect_ok):
    """单价必须为正数，0 和负数都是脏数据"""
    ok, reason = validate_negotiation_deal(_state(2_100_000, 1000, unit))
    assert ok is expect_ok
    if not expect_ok:
        assert "单价" in reason


# ---------- 异常低价（防"假低价骗过预算"）----------

@pytest.mark.unit
def test_abnormal_low_price_blocked():
    """单价低于市场基准价一半，且总价没被预算钳住 → 判为异常低价，拦截"""
    ok, reason = validate_negotiation_deal(_state(2_100_000, 500_000, 500, base=4000))
    assert ok is False
    assert "异常低价" in reason


@pytest.mark.unit
def test_budget_clamped_low_price_allowed():
    """总价被预算钳制（总价==预算上限）时产生的低价是诚实的预算约束，不能误杀"""
    ok, _ = validate_negotiation_deal(_state(2_100_000, 2_100_000, 500, base=4000))
    assert ok is True


@pytest.mark.unit
def test_skip_price_check_without_base_price():
    """没有市场基准价（情报节点没出数）时，第三检自动跳过，不误伤正常成交"""
    ok, _ = validate_negotiation_deal(_state(2_100_000, 500_000, 500, base=0))
    assert ok is True


# ---------- 健壮性 ----------

@pytest.mark.unit
def test_empty_negotiation_result_blocked():
    """谈判节点没产出结果（LLM 抽风/解析失败）→ 不能放行，必须显式失败"""
    ok, reason = validate_negotiation_deal({"budget_limit": 1000, "negotiation_result": None})
    assert ok is False
    assert "为空" in reason


@pytest.mark.unit
def test_validator_does_not_mutate_state():
    """校验器契约：只读判断，绝不修改 state（防止校验本身变成副作用源）"""
    state = _state(2_100_000, 1_800_000, 3600, base=4000)
    snapshot = _state(2_100_000, 1_800_000, 3600, base=4000)
    validate_negotiation_deal(state)
    assert state == snapshot
