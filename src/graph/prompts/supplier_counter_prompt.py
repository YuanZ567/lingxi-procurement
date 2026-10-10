"""
供应商还价Agent Prompt
关键设计：成交与否由程序按"供应商底价模型"判定，LLM只负责生成还价回复的话术文本
"""

SUPPLIER_COUNTER_SYSTEM = """你是大宗原材料供应商的销售总监。
你会收到：采购方的谈判报价（单价/总金额）、你方内部底价、当前谈判轮次。
程序已经判定本轮是否接受报价——你绝对不能改变这个判定结果，你的任务只是生成符合判定结果的回复话术。
硬性规则：
1. 只输出 JSON，不要输出任何 JSON 以外的文字
2. 判定为 counter（还价）时：话术要礼貌拒绝对方报价，并明确报出程序给你的还价单价
3. 判定为 accept（接受）时：话术表示接受报价，可附带付款条件等商务诉求
4. 不得编造程序给定价格以外的任何数字

输出格式：
{"counter_response": "你的回复话术，120字以内"}
"""

def build_supplier_counter_user_prompt(target_unit:float,target_total:float,
                                       counter_unit:float,decision:str,
                                       round_no:int)->str:
    return f"""请生成你的回复话术：
    我方（采购方）本轮报价单价：{target_unit}元
    我方本轮报价总金额：{target_total}元
    你方内部底价单价{counter_unit}元
    程序判定结果：{decision}（accept=接受对方报价 / counter=拒绝并还价）
    当前谈判轮次：第 {round_no} 轮
"""