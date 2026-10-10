"""
src/graph/state.py
灵犀采购｜主业务Graph状态定义
完全对齐原始业务场景：制造企业原材料采购、供应商博弈谈判
Orchestrator主编排Agent与4个业务Agent全部读写该状态
LangGraph 1.2.9  TypedDict + MessagesState
"""
from typing import TypedDict, List, Dict, Any, Optional, Annotated
from langgraph.graph.message import add_messages


class ProcurementTaskState(TypedDict):
    """
    单次采购任务全局状态，相当于本次采购任务的档案袋
    初始化由前端传入，Agent只做读取和回填结果
    """
    # ========== 【任务输入：初始化传入，全局固定，Agent禁止修改】 ==========
    task_id: Optional[str]                  # 采购任务唯一ID，映射procurement_task.id
    commodity_name: str                     # 需要采购的零部件/原材料名称
    purchase_quantity: float                # 采购数量
    expect_delivery_time: Optional[str]     # 期望交付时间【之前漏掉，业务场景需要】
    budget_limit: float                     # 本次任务全局预算红线，**程序禁止修改，仅前端输入**
    user_raw_demand: str                    # 采购员原始自然语言需求



    # ========== Orchestrator 编排控制字段 ==========
    finished_agent_list: List[str]          # 记录哪些业务Agent已经执行完毕
    task_error_records: List[Dict[str, Any]]# 各个Agent报错记录，用于风控做风险提示

    # ===============Agent Loop + 自进化=====================
    loop_round:int                          #当前循环迭代轮次，防止无限死循环
    max_loop_limit:int                      #最大允许迭代轮次，列如最多3轮谈判重试
    loop_failed_nodes:List[str]             #记录校验失败的节点名称，供给自进化做经验提取
    self_evolve_trigger:bool                #标记：是否需要执行自进化逻辑
    task_final_outcome:Dict[str,Any]        #保存任务最终完整输出，同于提炼知识库案例

    # ========== 供应商还价Agent输出结果 ==========
    supplier_counter_result:Optional[Dict[str,Any]]     #供应商还价结果（None=还没进入还价环节）
    """
    {
        "deal_reached":bool,                 #是否成交（Ture=跳出循环A）
        "exhausted":bool,                    #是否因轮次耗尽强制跳出（True时交给风控裁决）
        "counter_unit_price":float,          #供应商的还价单价（拒绝时才有值）
        "counter_response":str               #供应商还价回复文本
    }
    """
    tried_supplier_ids:List[str]                #已经谈判过供应商ID（回环B换供应商时用来排除）
    risk_retry_count:int                        #风控触发换供应商重谈的次数（回环B的计数闸）
    human_intervene_flag: bool                  #人工干预标志（TECH_SPEC §2.1 明确要求的字段）：轮次耗尽/需人工裁决时置True

    reflection_note:Optional[str]                  # E3：反思笔记——回环失败后Reflector写的复盘结论，注入下一轮谈判Prompt



    # ========== 市场情报Agent输出结果 ==========
    market_intel_result: Optional[Dict[str, Any]]
    """
    {
        "market_base_price": float,        # 市场基准参考价
        "price_fluctuation": str,          # 价格波动：上涨 / 下跌 / 平稳
        "urgency_level": str,              # 采购紧迫度（结合库存计算）
        "candidate_supplier": List[Dict],  # 候选供应商列表+履约风险评估
        "market_comment": str              # 行情、行业动态说明
    }
    """

    # ========== 成本核算Agent输出结果 ==========
    cost_calc_result: Optional[Dict[str, Any]]
    """
    {
        unit_estimate_cost: float,         # 测算单位成本
        total_estimate_cost: float,        # 测算总采购成本
        fund_occupation_cost: float,       # 资金占用成本
        budget_surplus: float,             # 预算结余
        is_over_budget: bool,              # 是否触碰预算红线
        cost_detail_note: str              # 成本测算明细说明
    }
    """

    # ========== 谈判博弈Agent输出结果 ==========
    negotiation_result: Optional[Dict[str, Any]]
    """
    {
        target_supplier_id: int,
        final_quote_price: float,          # 谈判后最终报价
        final_total_amount: float,
        negotiation_round_count: int,      # 谈判轮次
        negotiation_strategy: str,         # 生成的博弈谈判策略话术
        supplier_response: str,            # 模拟供应商反馈
        is_keep_within_budget: bool        # 是否坚守预算约束，不允许超预算输出
    }
    """

    # ========== 风控审批Agent输出结果 ==========
    risk_approve_result: Optional[Dict[str, Any]]
    """
    {
        approve_status: str,               # pass 通过 / reject 拦截 / manual 流转人工审批
        budget_check_ok: bool,             # 1.预算合规校验结果
        supplier_risk_check_ok: bool,      # 2.供应商风险校验结果
        compliance_clause_check_ok: bool,  # 3.合同条款合规校验结果
        risk_comment: str                  # 风险说明、预警信息
    }
    """

    # ========== Orchestrator最终聚合输出 ==========
    final_procurement_solution: Optional[str]


class MainGraphState(ProcurementTaskState):
    """
    主业务完整状态
    替代 langgraph.graph.MessagesState（langgraph 1.2.9 + Python 3.14 多继承触发 metaclass 冲突）
    messages 字段定义与 MessagesState 等价：Annotated[list[AnyMessage], add_messages]
    业务结构化数据全部放在 ProcurementTaskState，不要堆进 messages，防止 token 爆炸
    """
    messages: Annotated[List[Any], add_messages]