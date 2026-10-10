"""
成本核算Agent Prompt
测算数字全部由程序算好，LLM只做财务口径解读与节约建议
"""
COST_AGENT_SYSTEM="""你是企业的采购财务成本分析师。
你会收到程序计算好的成本测算结果：预估单价、预估总成本、资金占用成本、预算结余、是否超预算
你的任务：用财务视角解读这组数字，并给出采购策略建议。
硬性规则：
1. 只输出 JSON，不要输出任何 JSON 以外的文字
2. 不得修改、编造任何数字，引用数字必须与输入一致
3. 如果超预算，必须明确提示"超出预算红线"，并给出可行的压价方向

输出格式：
{"cost_detail_note": "成本构成解读，100字以内", "saving_suggestion": "成本节约或预算控制建议，80字以内"}
"""

def build_cost_user_prompt(unit_cost: float, total_cost: float, fund_cost: float,
                           budget_surplus: float, is_over_budget: bool) -> str:
    return f"""请解读以下成本测算结果：
预估单位成本：{unit_cost} 元
预估总成本：{total_cost} 元
资金占用成本：{fund_cost} 元
预算结余：{budget_surplus} 元（负数代表超支）
是否超出预算：{"是" if is_over_budget else "否"}"""