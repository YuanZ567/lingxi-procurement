"""
交易业务校验器集合（E1核心）
每个校验器签名统一：接收节点输出state，返回 (是否合格, 失败原因)
原则：校验器只读state做判断，绝不修改state；数值红线判定全在这里，不经过LLM
"""
from typing import Tuple                        # 校验器返回的二元组类型
from src.utils.logger import get_logger

logger = get_logger("validators")               # 本模块日志器


def validate_negotiation_deal(state: dict) -> Tuple[bool, str]:
    """
    谈判结果校验器（挂在negotiation_node上）
    三道检查：预算红线 → 单价合法性 → 价格合理性
    """
    nego = state.get("negotiation_result") or {}        # 谈判结果（防御None）
    if not nego:                                        # 结果为空说明节点内部出错了
        return False, "谈判结果为空，无法验收"            # 不合格+原因

    budget = state.get("budget_limit", 0)               # 预算红线（程序字段，不可信LLM）
    total = nego.get("final_total_amount", 0)           # 谈判后成交总金额
    unit = nego.get("final_quote_price", 0)             # 谈判后成交单价

    # 检查1：预算红线——任何谈判输出不允许超过预算（最高优先级）
    if total > budget:
        return False, f"谈判总金额{total}超出预算红线{budget}"

    # 检查2：单价合法性——单价必须为正数
    if unit <= 0:
        return False, f"谈判单价{unit}非法（必须大于0）"

    # 检查3：价格合理性——单价远低于市场基准价一半视为数据异常（防"假低价"）
    # 例外：总价被预算钳制（总价==预算上限）时产生的低价是诚实的预算约束，不算异常
    market = state.get("market_intel_result") or {}     # 市场情报
    base = market.get("market_base_price", 0)           # 市场基准价
    if base and unit < base * 0.5 and total < budget * 0.999:
        return False, f"谈判单价{unit}低于市场基准价{base}的一半，疑似异常低价"

    logger.info(f"谈判校验通过：单价{unit}，总额{total}，预算{budget}")   # 通过也留痕
    return True, ""                                     # 全部通过，返回二元组
