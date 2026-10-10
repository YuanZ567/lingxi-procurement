"""
谈判博弈Agent Prompt
报价数字由程序在预算约束内算好（0.98折策略），LLM围绕目标价生成策略话术与供应商回复
"""
NEGOTIATION_AGENT_SYSTEM ="""你是经验丰富的工业品采购谈判专家。
你会收到：采购预算上限、市场基准价、程序算好的谈判目标价、目标供应商资料。
你的任务：生成一套可执行的多轮谈判策略话术，并模拟该供应商的回复。
硬性规则：
1. 只输出 JSON，不要输出任何 JSON 以外的文字
2. 所有报价相关表述不得高于预算上限，目标价以程序给定为准，不得自己改价
3. 谈判策略要具体可执行：开场策略、让步节奏、筹码（长期订单、现款结算、账期等）、底线
4. 供应商回复要符合其风险等级与溢价特征，可以适当还价但最终接近目标价

输出格式：
{"negotiation_strategy": "分轮次的谈判策略与话术，300字以内", "supplier_response": "供应商针对我方报价的回复，150字以内"}
"""

def build_negotiation_user_prompt(budget_limit: float, market_base_price: float,
                                  target_unit_price: float, target_total: float,
                                  supplier_info: dict, reflection_note: str = "") -> str:
    supplier_desc = "未知" if not supplier_info else \
        f"名称：{supplier_info.get('supplier_name')}，风险等级：{supplier_info.get('risk')}"
    # E3新增：反思笔记段——上一轮失败后Reflector写的复盘结论，有就带上
    reflection_section = ""
    if reflection_note:                                      # 非空说明上一轮谈判失败过
        reflection_section = f"""
复盘教练的上一轮反思与策略修正建议（务必在本轮话术中体现这些建议）：
{reflection_note}"""
    return f"""请生成谈判策略与供应商回复，背景如下：
预算上限（绝对红线）：{budget_limit} 元
市场基准价：{market_base_price} 元/单位
我方谈判目标单价：{target_unit_price} 元/单位
我方谈判目标总金额：{target_total} 元
目标供应商：{supplier_desc}{reflection_section}"""