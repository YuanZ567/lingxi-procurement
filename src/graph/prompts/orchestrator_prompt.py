"""
orchestrator_prompt.py —— Orchestrator 主编排 Agent 的提示词
====================================================================
Orchestrator 是"采购总监"：4个业务Agent干完活，它看全部结果做最终裁决。
关键设计：LLM 只做"总裁总结"，不做任何计算——所有数字都是程序算好喂给它的，
硬性规则禁止它编造数字（防幻觉：数字错了整份方案就不可信了）。
"""
# 系统提示词：告诉大模型"你是谁、你的规矩是什么"
ORCHESTRATOR_SYSTEM = """你是一家制造企业的采购总监，正在审核一份完整的智能采购方案。
你会收到四个执行环节的结果：市场情报、成本测算、谈判结果、风控审批。
你的任务：站在决策者角度，输出一份简明的总裁结论。

硬性规则：
1.只输出JSON，不要输出JSON以外的任何文字
2.严禁编造或修改任何数字！所有数字必须与输入完全一致，拿不准就不写数字
3.executive_summary不超过150字，key_risks每条不超过40字，next_actions每条不超过40字
4.如果风控审批未通过，next_actions第一条必须是整改方向

输出格式：
{"executive_summary": "一段总裁执行摘要", "key_risks": ["风险1", "风险2"],
 "next_actions": ["建议1", "建议2"]}
"""

def build_orchestrator_user_prompt(commodity_name, purchase_quantity, budget_limit,
                                   market, cost, nego, approve):
    """
    拼总裁结论的"阅卷材料"：把4个节点的结果事实原样喂给LLM。
    参数全是各节点写入 state 的结果字典，直接 .get() 取，缺了就是"无"。
    """
    return f"""请审核以下智能采购全流程结果并给出总裁结论：

【本次任务】
采购物料：{commodity_name}
采购数量：{purchase_quantity}
预算红线：{budget_limit}

【市场情报】
市场基准价：{market.get('market_base_price')} 元，价格趋势：{market.get('price_fluctuation')}
采购紧迫度：{market.get('urgency_level')}

【成本测算】
预估单位成本：{cost.get('unit_estimate_cost')} 元
预估总成本：{cost.get('total_estimate_cost')} 元
是否超预算：{cost.get('is_over_budget')}

【谈判结果】
目标供应商ID：{nego.get('target_supplier_id')}
成交总金额：{nego.get('final_total_amount')} 元
谈判轮次：{nego.get('negotiation_round_count')}

【风控审批】
审批状态：{approve.get('approve_status')}
预算校验：{approve.get('budget_check_ok')}，供应商风险校验：{approve.get('supplier_risk_check_ok')}"""