"""
src/graph/main_graph.py
灵犀采购｜主业务Graph骨架
Orchestrator主编排架构：1主Agent +4业务Agent
节点仅空壳，业务逻辑后续填充；Redis Checkpoint持久任务状态记忆
业务对齐：原材料采购、供应商博弈谈判、三重风控审批
"""

from contextlib import asynccontextmanager
from langgraph.graph import StateGraph,START,END
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from src.graph.state import MainGraphState
from src.db.session import SessionLocal
from src.db.models import CommodityPrice, Inventory, Supplier
from src.utils.llm import ask_json
from src.graph.prompts.market_agent_prompt import MARKET_AGENT_SYSTEM, build_market_user_prompt
from src.graph.prompts.cost_agent_prompt import COST_AGENT_SYSTEM, build_cost_user_prompt
from src.graph.prompts.negotiation_agent_prompt import NEGOTIATION_AGENT_SYSTEM, build_negotiation_user_prompt
from src.graph.prompts.risk_agent_prompt import RISK_AGENT_SYSTEM, build_risk_user_prompt
from src.core.harness import AgentHarness                        # E1：可靠性外壳
from src.core.validators import validate_negotiation_deal        # E1：谈判校验器
from src.graph.prompts.supplier_counter_prompt import SUPPLIER_COUNTER_SYSTEM, build_supplier_counter_user_prompt  # 还价Agent的Prompt
from src.utils.logger import get_logger                          # 统一日志（回环留痕）
from src.graph.prompts.orchestrator_prompt import ORCHESTRATOR_SYSTEM, build_orchestrator_user_prompt  # 总裁总结的Prompt
from src.graph.prompts.reflector_prompt import REFLECTOR_SYSTEM, build_reflector_user_prompt  # E3：反思节点Prompt

logger = get_logger("main_graph")                                # 主图模块自己的日志器

async def orchestrator_node(state: MainGraphState):
     """
    Orchestrator主编排Agent
    1. 维护执行顺序：市场情报 → 成本核算 → 谈判 → 风控审批
    2. 记录已经完成的Agent
    3. 全部Agent执行完毕，聚合输出最终采购方案
    """
     pipeline=[                  #pipeline：流水线任务清单，写死业务执行顺序（节点名必须与 add_node 注册名完全一致）
         "market_intelligence_node",
         "cost_calculate_node",
         "negotiation_node",
         "risk_approval_node"
    ]
     finished=state.get("finished_agent_list",[])
     new_state=state.copy()   #复制一份状态，不直接修改原始 state

     next_node=None
     for node_name in pipeline:
          if node_name not in finished:
               next_node=node_name
               break

     #全部Agent执行完成，聚合最终采购方案
     if next_node is None:
          market=state.get("market_intel_result") or {}    #节点若曾异常未产出结果，兜底空dict，防止 .get 炸裂
          cost=state.get("cost_calc_result") or {}
          nego=state.get("negotiation_result") or {}
          approve=state.get("risk_approve_result") or {}

          solution_lines=[]
          solution_lines.append("#灵犀采购-最终采购方案")
          solution_lines.append(f"物料：{state['commodity_name']},采购数量：{state['purchase_quantity']}")
          solution_lines.append(f"预算上限：{state['budget_limit']},期望交付：{state.get('expect_delivery_time','无')}")
          solution_lines.append("## 市场情报摘要")
          solution_lines.append(f"基准价：{market.get('market_base_price')}，价格走势：{market.get('price_fluctuation')}，紧迫度：{market.get('urgency_level')}")
          cands=market.get('candidate_supplier') or []      # 候选供应商是dict列表，不能直接repr进报告
          if cands:                                          # 有候选才渲染表格（人看的排版）
               solution_lines.append("候选供应商（前10家）：")
               solution_lines.append("| 供应商 | 风险 | 溢价系数 |")
               solution_lines.append("|---|---|---|")
               for c in cands[:10]:                          # 报告最多列10家，全量数据在库里
                    solution_lines.append(f"| {c.get('supplier_name','')} | {c.get('risk','')} | {c.get('premium_rate','')} |")
          if market.get('market_comment'):
               solution_lines.append(f"行情说明：{market.get('market_comment')}")
          solution_lines.append("## 成本测算")
          solution_lines.append(f"预估单位成本：{cost.get('unit_estimate_cost')}")
          solution_lines.append(f"预估总成本：{cost.get('total_estimate_cost')}，资金占用成本：{cost.get('fund_occupation_cost')}")
          solution_lines.append(f"预算结余：{cost.get('budget_surplus')}，是否超预算：{cost.get('is_over_budget')}")
          solution_lines.append(f"成本解读：{cost.get('cost_detail_note')}")
          solution_lines.append("## 谈判博弈结果")
          solution_lines.append(f"目标供应商ID:{nego.get('target_supplier_id')}，成交总金额:{nego.get('final_total_amount')}")
          solution_lines.append(f"谈判轮次：{nego.get('negotiation_round_count')}，是否守住预算：{nego.get('is_keep_within_budget')}")
          solution_lines.append(f"谈判策略：{nego.get('negotiation_strategy')}，供应商反馈：{nego.get('supplier_response')}")
          solution_lines.append("## 风控审批结论")
          solution_lines.append(f"审批状态：{approve.get('approve_status')}")
          solution_lines.append(f"预算校验：{approve.get('budget_check_ok')}，供应商风险校验：{approve.get('supplier_risk_check_ok')}，合规条款校验：{approve.get('compliance_clause_check_ok')}")
          solution_lines.append(f"风险提示：{approve.get('risk_comment')}")

          #LLM总裁总结：4节点事实喂给LLM要一段决策者结论；失败自动降级（模板报告照常输出）
          try:  # try包住：LLM相关任何异常都不能影响模板报告的生成
               # 拼阅卷材料：任务信息 + 四节点结果，全都是程序算好的事实，LLM无计算权
               chief_prompt=build_orchestrator_user_prompt(
                    state["commodity_name"], state["purchase_quantity"], state["budget_limit"],
                    market, cost, nego, approve
               )
               # ask_json：统一LLM入口（内部自带重试3次+JSON解析），失败返回None
               chief_result=await ask_json(ORCHESTRATOR_SYSTEM, chief_prompt)
               if chief_result:  # LLM正常返回了
                    solution_lines.append("## AI总裁结论")                       # 加个报告章节标题
                    solution_lines.append(f"{chief_result.get('executive_summary','')}")  # 执行摘要
                    for risk in chief_result.get("key_risks",[]):              # 逐条列风险
                         solution_lines.append(f"{risk}")
                    for action in chief_result.get("next_actions",[]):         # 逐条列建议
                         solution_lines.append(f" {action}")
                    logger.info("orchestrator LLM总裁总结生成成功")            # 留痕：这次用了AI
               else:  # LLM返回None（重试全挂/JSON解析失败）——静默降级
                    logger.warning("orchestrator LLM总裁总结失败，使用纯模板报告")  # 留痕降级
          except Exception as e:  # 连prompt拼接都出意外的极端情况
               logger.warning(f"orchestrator总裁总结异常，跳过（不影响主流程）：{e}")  # 留痕但不炸

          new_state["final_procurement_solution"]="\n".join(solution_lines) #把列表里面每一行文本用换行符拼接成完整 markdown 大字符串。把完整报告存入新状态的final_procurement_solution字段。
          return new_state   #返回更新完的状态对象。
     
     



async def market_intelligence_node(state:MainGraphState):
     """市场情报Agent：行情解析、供应商背景与履约风险评估
     去 MySQL 数据库拉真实数据：物料行情价格、现有库存、全部供应商；
     算出采购紧迫程度；把整理好的情报结果塞进全局状态；
     把自己标记为 “已经干完”；
     出错就记录错误日志，不让整个流程直接崩掉。
     """
     new_state=state.copy()
     commodity_name=state["commodity_name"]
     error_records=new_state.get("task_error_records",[])

     try:
          with SessionLocal() as db:
               #1.查询物料最新行情（按行情日期倒序取两条：最新一条做基准价，两条对比算涨跌趋势）
               latest_two=db.query(CommodityPrice)\
               .filter(CommodityPrice.commodity_name==commodity_name)\
               .order_by(CommodityPrice.record_date.desc()).limit(2).all()
               #2.查询库存
               inv=db.query(Inventory).filter(Inventory.commodity_name== commodity_name).first()
               #3.查询全部供应商
               supplier_list=db.query(Supplier).all()

          #根据数据库真实数据组装情报结果
          latest_price=latest_two[0] if latest_two else None
          market_base_price=float(latest_price.price) if latest_price else 0.0
          stock_qty=float(inv.stock_quantity) if inv else 0

          #用最近两条行情对比得出涨跌趋势（模型无趋势列，只能从数据推导；仅一条数据时诚实标注未知）
          if len(latest_two) >= 2:
               price_diff=float(latest_two[0].price)-float(latest_two[1].price)
               price_trend="上涨" if price_diff>0 else ("下跌" if price_diff<0 else "平稳")
          else:
               price_trend="未知"

          #根据库存简单计算紧迫度
          if stock_qty < 100:
               urgency="高"
          elif stock_qty < 500:
               urgency="中等"
          else:
               urgency="低"

          #遍历数据库查出来的所有供应商对象
          #credit_status 在库里是 A/B/C 等级制，映射成风控节点消费的高/中/低
          risk_level_map={"A":"低","B":"中","C":"高"}   # credit_status A/B/C → 高/中/低 映射（原有行，保持不动）
          candidate_suppliers=[
               {"id":s.id, "supplier_name":s.supplier_name,
                "risk":risk_level_map.get(s.credit_status,"未知"),
                "premium_rate":float(s.premium_rate)}    # E2新增：溢价系数，还价底价模型要用
               for s in supplier_list
          ]

          """
          market_base_price：市场基准价格
          price_fluctuation：价格涨跌趋势；没行情就填 “未知”
          urgency_level：采购紧迫度（高 / 中等 / 低）
          candidate_supplier：候选供应商字典列表
          market_comment：备注文本，后面拼到最终采购方案里展示。
          """
          llm_result=await ask_json(
               MARKET_AGENT_SYSTEM,
               build_market_user_prompt(commodity_name, market_base_price,
                                        price_trend, urgency, stock_qty)
          )
          market_comment=(llm_result or {}).get("market_comment") \
               or f"当前库存:{stock_qty},读取数据库真实行情数据"

          new_state["market_intel_result"]={
               "market_base_price":market_base_price,
               "price_fluctuation": price_trend,
               "urgency_level": urgency,
               "candidate_supplier": candidate_suppliers,
               "market_comment": market_comment
          }
     except Exception as e:
          error_records.append({"node":"market_intelligence_node","error":str(e)})
          new_state["task_error_records"]=error_records

     finished=new_state.get("finished_agent_list",[])
     finished.append("market_intelligence_node")
     new_state["finished_agent_list"]=finished
     return new_state

     

async def cost_calculate_node(state:MainGraphState):
     """
     成本核算Agent：行情+库存+资金占用做多维度成本测算  
     上游依赖市场情报节点输出的行情数据，做整套采购成本数学运算，算出预估单价、总花费、资金占用、预算结余、是否超预算；
     结果写到状态；无论成功失败标记自己完成，报错就记录错误。
     """
     new_state=state.copy()
     market_info=state.get("market_intel_result",{})
     base_price=market_info.get("market_base_price",0)  #取出市场基准价（键名必须与市场情报节点写入的 market_base_price 一致）。
     qty=state["purchase_quantity"]                     #取出采购数量
     budget_limit=state["budget_limit"]                 #取出本次采购允许的预算上限。
     error_records=new_state.get("task_error_records",[])

     try:
        unit_estimate = base_price                 #预估单位成本直接等于市场情报给出来的基准价。
        total_estimate_cost = unit_estimate * qty   #预估总成本 = 预估单价 × 需要采购的数量。
        fund_occupation = total_estimate_cost * 0.005 # 资金占用简单系数0.5%
        budget_surplus = budget_limit - total_estimate_cost   #预算结余 = 预算上限 − 预估总成本。
        is_over_budget = total_estimate_cost > budget_limit   #布尔判断：预估总成本是否大于预算上限。


     #LLM增强：成本解读（失败自动降级为模板文本）
        llm_result=await ask_json(
            COST_AGENT_SYSTEM,
            build_cost_user_prompt(unit_estimate, total_estimate_cost,
                                   round(fund_occupation,2), round(budget_surplus,2),
                                   is_over_budget)
        )
        cost_note=(llm_result or {}).get("cost_detail_note") or "基于数据库行情真实计算"


        new_state["cost_calc_result"] = {           #round(xxx,2)：保留 2 位小数，金额，符合财务习惯。
            "unit_estimate_cost": unit_estimate,
            "total_estimate_cost": total_estimate_cost,
            "fund_occupation_cost": round(fund_occupation,2),
            "budget_surplus": round(budget_surplus,2),
            "is_over_budget": is_over_budget,
            "cost_detail_note":cost_note
        }
     except Exception as e:
          error_records.append({"node":"cost_calculate_node","error":str(e)})
          new_state["task_error_records"]=error_records

     finished=new_state.get("finished_agent_list",[])
     finished.append("cost_calculate_node")
     new_state["finished_agent_list"]=finished
     return new_state


async def negotiation_node(state:MainGraphState):
     """
     谈判博弈Agent：预算约束下生成多轮谈判策略与博弈话术
     负责算出谈判报价、锁定供应商、预算保护，目前是数值模拟，还没接入 LLM 谈判话术。
     """
     new_state=state.copy()
     cost_info=state.get("cost_calc_result",{})  #拿到上一步成本核算节点输出的结果字典
     market_info=state.get("market_intel_result",{})  #拿到第一步市场情报节点输出的情报字典，里面存放候选供应商列表。
     budget_limit=state["budget_limit"]  #取出采购预算上限。
     #从市场情报结果取出候选供应商列表；拿不到就返回空列表[]。
     candidate_suppliers = market_info.get("candidate_supplier",[])
     error_records=new_state.get("task_error_records",[])

     try:
          # ---- 选供应商：优先没谈过的（回环B换供应商时生效），否则维持原顺序 ----
          tried_ids=new_state.get("tried_supplier_ids") or []              # 已谈过的供应商ID
          untried=[s for s in candidate_suppliers if s["id"] not in tried_ids]  # 没谈过的
          pool=untried if untried else candidate_suppliers                  # 全谈过了就维持原列表
          target=pool[0]                                                    # 取第一个（MVP策略，智能选商留作扩展）
          target_supplier_id=target["id"]                                   # 本轮谈判目标供应商
          new_state["tried_supplier_ids"]=tried_ids+[target_supplier_id]    # 记入"已谈过"名单

          # ---- 定价：同一家供应商的还价才接受（预算内适当加价），换人重谈则按预估价98折重新开价 ----
          counter=new_state.get("supplier_counter_result") or {}            # 上轮还价结果
          counter_sid=counter.get("counter_from_supplier_id")               # 还价是哪家供应商给的
          if counter and counter.get("counter_unit_price") and counter_sid==target_supplier_id:  # 同一家：接受其底价
               target_unit=counter["counter_unit_price"]                    # 按该供应商底价成交
          else:                                                             # 首轮或换了供应商：重新按98折开价
               target_unit=(cost_info.get("unit_estimate_cost",0))*0.98     # 预估单价98折

          final_total=target_unit*state["purchase_quantity"]                # 总金额=单价×数量
          if final_total > budget_limit:                                    # 预算硬约束（程序兜底，不经LLM）
               final_total=budget_limit                                     # 超预算强制压到红线
               target_unit=final_total/state["purchase_quantity"]           # 单价同步修正

          final_unit_price=round(target_unit,2)                             # 成交单价（保留2位）
          final_total=round(final_total,2)                                  # 成交总额（保留2位）
          round_count=new_state.get("loop_round",0)+1                       # 谈判轮次=还价轮数+1

          # ---- LLM生成谈判策略与供应商回复（失败自动降级为占位文本）----
          llm_result=await ask_json(
               NEGOTIATION_AGENT_SYSTEM,
               build_negotiation_user_prompt(budget_limit, market_info.get("market_base_price",0),
                                             final_unit_price, final_total, target,
                                             reflection_note=new_state.get("reflection_note") or "")
          )
          strategy=(llm_result or {}).get("negotiation_strategy") or "待LLM生成谈判策略话术"
          supplier_reply=(llm_result or {}).get("supplier_response") or "待LLM模拟供应商回复"

          new_state["negotiation_result"] = {
            "target_supplier_id": target_supplier_id,      # 本轮目标供应商
            "final_quote_price": final_unit_price,         # 成交单价
            "final_total_amount": final_total,             # 成交总额
            "negotiation_round_count": round_count,        # 谈判轮次（含还价轮）
            "negotiation_strategy": strategy,              # LLM策略话术
            "supplier_response": supplier_reply,           # LLM供应商回复
            "is_keep_within_budget": final_total <= budget_limit   # 是否守住预算
        }
     except Exception as e:
          error_records.append({"node":"negotiation_node","error":str(e)})
          new_state["task_error_records"]=error_records

     finished = new_state.get("finished_agent_list", [])
     finished.append("negotiation_node")
     new_state["finished_agent_list"] = finished
     return new_state


     

async def risk_approval_node(state:MainGraphState):
     """
     风控审批Agent：三重校验：预算、供应商风险、合规条款
     做三重风险校验：预算校验、供应商风险等级校验、合规校验；
     自动给出审批结论：通过 / 需要人工介入；生成风控结果存入状态；
     无论成功失败标记自己完成，出错记录错误。
     """
     new_state=state.copy()
     retrying=False                   # E3修正：本次是否触发了回环B重派（触发时节点尾部不再把自己登记为完成）
     cost_info=state.get("cost_calc_result",{})
     nego_info=state.get("negotiation_result",{})
     market_info=state.get("market_intel_result",{})
     error_records=new_state.get("task_error_records",[])

     try:
          #三重校验
          budget_check_ok=not cost_info.get("is_over_budget",True)  #读取成本核算的 “是否超预算” 标记；拿不到数据默认给 True（默认判定为超预算）
          # 读取供应商风险
          supplier_list=market_info.get("candidate_supplier",[])
          supplier_risk_ok=True
          for s in supplier_list:
               if s["id"]==nego_info.get("target_supplier_id") and s.get("risk") == "高":
                    supplier_risk_ok=False   #供应商风险校验不通过
                    break
          compliance_ok = True

          # 谈判是否获得供应商确认：还价节点轮次耗尽（exhausted=True）说明供应商始终没点头
          counter_info=state.get("supplier_counter_result") or {}
          negotiation_confirmed=not counter_info.get("exhausted",False)

          if not budget_check_ok:
               #分支A【超支拦截】：成本测算超出预算红线，终止自动流程，需人工调整参数后重新发起
               approve_status="reject"
               risk_comment="成本测算超出预算红线，自动流程拦截终止，请调整预算上限或采购量后重新发起任务"
          elif not negotiation_confirmed:
               #分支C【谈判未收敛】：还价轮次耗尽仍未获供应商确认成交，不自动放行，流转人工决策
               approve_status="manual"
               risk_comment="谈判轮次耗尽仍未获供应商确认成交，需人工决策是否按当前价格执行或更换谈判对象"
          elif budget_check_ok and supplier_risk_ok and compliance_ok:
               #分支B【审批通过】：三重校验全部通过
               approve_status="pass"
               risk_comment="全部校验通过"
          else:
               #存在其他风险项（如供应商高风险），流转人工审批
               approve_status="manual"
               risk_comment="存在风险项，需要人工审批介入"


      #LLM增强：风险说明（只做解释建议，不得推翻程序结论；失败自动降级）
          llm_result=await ask_json(
               RISK_AGENT_SYSTEM,
               build_risk_user_prompt(approve_status, budget_check_ok,
                                      supplier_risk_ok, compliance_ok,
                                      nego_info.get("target_supplier_id",0))
          )
          risk_comment=(llm_result or {}).get("risk_comment") or risk_comment
          if approve_status!="pass":
               new_state["human_intervene_flag"]=True

          # ---- 回环B：供应商高风险导致manual时，踢掉该供应商换人重谈（最多1次） ----
          if approve_status=="manual" and not supplier_risk_ok:              # 只处理供应商风险这一种失败
               risk_retry_count=new_state.get("risk_retry_count",0)          # 已重试次数
               untried=[s for s in supplier_list if s["id"] not in (new_state.get("tried_supplier_ids") or [])]  # 还有没谈过的供应商吗
               if risk_retry_count<1 and untried:                            # 双重护栏：没重试过 且 有人可选
                    finished=new_state.get("finished_agent_list") or []      # 取完成清单
                    # 把negotiation和risk从完成清单里踢掉→orchestrator会重新派发它们
                    new_state["finished_agent_list"]=[f for f in finished if f not in ("negotiation_node","risk_approval_node")]
                    new_state["risk_retry_count"]=risk_retry_count+1         # 重试计数+1（第二次失败就不会再进了）
                    retrying=True                                            # 标记：尾部不要把自己登记回完成清单
                    risk_comment=risk_comment+"；已自动切换备用供应商重新谈判"   # 留痕
                    logger.warning("[risk_approval] 供应商高风险，触发回环B：换供应商重谈")

          new_state["risk_approve_result"] = {
            # approve_status：审批结果：pass 自动通过 /reject 超支拦截 /manual 人工审批
            # budget_check_ok：预算校验是否通过
            # supplier_risk_check_ok：供应商风险校验是否通过
            # compliance_clause_check_ok：合规校验是否通过
            # risk_comment：风控备注文本，后面拼进最终采购报告展示。
            # 注意：注释不能写进 dict 字面量内部——裸三引号字符串会与下一个键名拼接成超长 key，导致取值失败
            "approve_status": approve_status,
            "budget_check_ok": budget_check_ok,
            "supplier_risk_check_ok": supplier_risk_ok,
            "compliance_clause_check_ok": compliance_ok,
            "risk_comment": risk_comment
        }
     except Exception as e:
        error_records.append({"node":"risk_approval_node","error":str(e)})
        new_state["task_error_records"] = error_records

     # 尾部登记：正常情况把自己标记为完成；若触发了回环B（retrying=True）则不登记，
     # 让 Orchestrator 先重派 negotiation 换供应商，新一轮风控审批时再登记
     finished = new_state.get("finished_agent_list", [])
     if not retrying:
          finished.append("risk_approval_node")
     new_state["finished_agent_list"] = finished
     return new_state

async def supplier_counter_node(state:MainGraphState):
    """
    供应商还价节点（E2回环A的核心）
    用"底价模型"程序化判定是否成交：底价=市场基准价×该供应商溢价系数×0.98
    LLM只生成还价/接受的话术文本，判定权在程序手里
    未成交且轮次未满 → 路由回negotiation重新谈；否则放行给orchestrator→risk
    """
    new_state=state.copy()                                     # 复制state，不污染原状态
    nego=new_state.get("negotiation_result") or {}             # 谈判结果
    market_info=new_state.get("market_intel_result") or {}     # 市场情报
    candidates=market_info.get("candidate_supplier") or []     # 候选供应商（含溢价系数）
    base_price=market_info.get("market_base_price",0)          # 市场基准价
    loop_round=new_state.get("loop_round",0)                   # 当前还价轮数
    max_loop=new_state.get("max_loop_limit",3)                 # 轮数上限（防死循环硬闸）

    # 找到目标供应商的溢价系数（找不到默认1.0=无溢价）
    target_supplier=next((s for s in candidates if s["id"]==nego.get("target_supplier_id")),None)
    premium=target_supplier.get("premium_rate",1.0) if target_supplier else 1.0
    threshold_unit=base_price*premium*0.98                     # 供应商底价=基准价×溢价×0.98

    target_unit=nego.get("final_quote_price",0)                # 采购方本轮报价单价
    target_total=nego.get("final_total_amount",0)              # 采购方本轮报价总额

    if target_unit>=threshold_unit:                            # 判定1：报价≥底价 → 接受成交
        new_state["supplier_counter_result"]={                 # 写入成交结果
            "deal_reached":True,"exhausted":False,             # 成交、非耗尽
            "counter_unit_price":None,                         # 无需还价
        }
        # LLM生成接受话术（失败降级为模板文本）
        llm=await ask_json(SUPPLIER_COUNTER_SYSTEM,
            build_supplier_counter_user_prompt(target_unit,target_total,threshold_unit,"accept",loop_round+1))
        new_state["supplier_counter_result"]["counter_response"]= \
            (llm or {}).get("counter_response") or "我方接受贵司报价，可进一步商议付款条件。"

    elif loop_round>=max_loop:                                 # 判定2：轮次耗尽 → 强制跳出（防死循环硬闸）
        logger.warning(f"[supplier_counter] 还价轮数达到上限{max_loop}，强制跳出回环，交由风控裁决")
        new_state["supplier_counter_result"]={                 # 写入耗尽结果
            "deal_reached":True,"exhausted":True,              # deal_reached=True是为了跳出回环
            "counter_unit_price":threshold_unit,               # 记录最终底价供风控参考
        }
        new_state["human_intervene_flag"]=True                 # TECH_SPEC §2.1：需人工裁决，置人工干预标志
        new_state["loop_failed_nodes"]=(new_state.get("loop_failed_nodes") or [])+["supplier_counter_node"]  # 留痕：谈判未收敛

    else:                                                      # 判定3：拒绝还价 → 踢标记，回orchestrator重派
        new_state["loop_round"]=loop_round+1                   # 还价轮数+1（回环A的计数闸就在这里拨动）
        # 把negotiation从完成清单踢掉→orchestrator发现它未完成会自动重派（回环A统一走主编排调度）
        finished=new_state.get("finished_agent_list") or []
        new_state["finished_agent_list"]=[f for f in finished if f!="negotiation_node"]
        # LLM生成还价话术（失败降级为模板文本）
        llm=await ask_json(SUPPLIER_COUNTER_SYSTEM,
            build_supplier_counter_user_prompt(target_unit,target_total,threshold_unit,"counter",loop_round+1))
        new_state["supplier_counter_result"]={                 # 写入还价结果
            "deal_reached":False,"exhausted":False,            # 未成交、未耗尽→回环A继续
            "counter_from_supplier_id":nego.get("target_supplier_id"),  # 记录还价方，防止换供应商后误用上家底价
            "counter_unit_price":threshold_unit,               # 供应商底价，下轮谈判按此价成交
            "counter_response":(llm or {}).get("counter_response") or
                f"贵司报价{target_unit}元低于我方成本线，我方最低可接受{threshold_unit}元/单位。",
        }
        logger.info(f"[supplier_counter] 第{loop_round+1}轮还价：{target_unit}→{threshold_unit}元，交由orchestrator重派谈判")

    return new_state                                           # 返回更新后的state

async def reflector_node(state:MainGraphState):
     """
    反思节点（E3 任务内自进化核心）
    时机：每轮 supplier_counter 判定之后。判定为"拒绝还价"才触发 LLM 复盘，
    把失败归因和下一轮策略修正写进 reflection_note；
    判定为"成交/耗尽"则直接放行（谈判已结束，无需反思），并清空旧笔记防止污染后续任务
    本节点不改价格、不改路由，只写 reflection_note 一个字段——自进化不碰红线
    """
     new_state=state.copy()                                 # 复制state，不污染原状态
     counter=new_state.get("supplier_counter_result") or {}           # 本轮还价判定结果

     if counter.get("deal_reached"):                   # 成交或轮次耗尽：谈判流程已结束
          new_state["reflection_note"]=None            # 清空反思笔记，防止残留到后续环节
          return new_state                             # 直接放行，不消耗LLM调用

     # 走到这里 = 供应商拒绝了本轮报价，触发反思
     our_quote=(new_state.get("negotiation_result") or {}).get("final_quote_price",0) # 我方本轮报价
     bottom_price=counter.get("counter_unit_price",0)                 # 供应商底价
     round_no=new_state.get("loop_round",0)+1                   # 当前轮次（还价轮数+1）
     counter_response=counter.get("counter_response","")              # 供应商的拒绝话术

     #LLM复盘：失败归因 + 下一轮策略修正（失败自动降级为模板文本）
     llm=await ask_json(
          REFLECTOR_SYSTEM,
          build_reflector_user_prompt(our_quote,bottom_price, counter_response, round_no)
     )
     if llm:             # 反思成功：归因+建议拼接成笔记
          new_state["reflection_note"]=(llm.get("failure_reason") or "")+"；"+(llm.get("strategy_adjustment") or "")
          logger.info(f"[reflector] 第{round_no}轮反思完成：{new_state['reflection_note'][:50]}...")

     else:             # 反思失败：降级为模板笔记，保证下一轮仍有上下文
          new_state["reflection_note"]=f"第{round_no}轮报价{our_quote}元被拒，对方坚持{bottom_price}元，下一轮需调整话术策略"
          logger.warning(f"[reflector] 第{round_no}轮反思LLM失败，降级为模板笔记")

     return new_state                                           # 返回更新后的stat



def route_orchestrator(state: MainGraphState):
     """
        Orchestrator条件路由函数
        根据finished_agent_list 判断下一步执行哪个子agent
        返回节点名字字符串给到分支
        
     """
     pipeline=[
          "market_intelligence_node",
          "cost_calculate_node",
          "negotiation_node",
          "risk_approval_node"
     ]
     finished=state.get("finished_agent_list",[])
     for node_name in pipeline:
        if node_name not in finished:
            return node_name
     return "END"



# -------------------------- 构建编译Graph --------------------------
@asynccontextmanager
async def build_main_graph(redis_url:str):
     """
        构建主业务流程图（async context manager 形态）
        :param redis_url:redis连接地址，同于Checkpoint持久化记忆
        :yield:编译完成graph对象

        说明：
        1. langgraph-checkpoint-redis >= 0.5 的 from_conn_string 返回 context
           manager，直接赋值拿到 _GeneratorContextManager 传给 compile 会报
           Invalid checkpointer provided。
        2. 业务侧用 astream/aget_state 等 async 方法，必须用 AsyncRedisSaver
           （同步 RedisSaver 的 aget_tuple 抛 NotImplementedError）。
        3. 用法：
               async with build_main_graph(REDIS_URL) as graph:
                   await graph.ainvoke(...) / graph.astream(...)
           with 块内 Redis 连接全程存活，退出自动释放。
     """
     async with AsyncRedisSaver.from_conn_string(redis_url) as checkpointer:
          # 建立并校验 Redis 中的 checkpoint 索引（幂等，可重复调用）
          await checkpointer.setup()

          graph_builder = StateGraph(MainGraphState)

          # 注册全部节点
          harness=AgentHarness(max_retry=2,timeout=45)                # E1：创建Harness实例

          # 注册全部节点（negotiation用harness.wrap挂上交易校验器）
          graph_builder.add_node("orchestrator_node", orchestrator_node)
          graph_builder.add_node("market_intelligence_node", market_intelligence_node)
          graph_builder.add_node("cost_calculate_node", cost_calculate_node)
          graph_builder.add_node("negotiation_node",
              harness.wrap(negotiation_node,                          # E1：裸节点包上可靠性外壳
                  validator=validate_negotiation_deal,                # 挂谈判三道校验
                  node_name="negotiation_node"))
          graph_builder.add_node("supplier_counter_node", supplier_counter_node)  # E2：还价节点
          graph_builder.add_node("reflector_node", reflector_node)   # E3：反思节点
          graph_builder.add_node("risk_approval_node", risk_approval_node)
          

          # 流程起点进入主编排Agent
          graph_builder.add_edge(START, "orchestrator_node")

          # Orchestrator动态分发到各个业务Agent
          graph_builder.add_conditional_edges(
             source="orchestrator_node",
             path=route_orchestrator,
             path_map={
                 "market_intelligence_node": "market_intelligence_node",
                 "cost_calculate_node": "cost_calculate_node",
                 "negotiation_node": "negotiation_node",
                 "risk_approval_node": "risk_approval_node",
                 "END": END
             }
          )

          # 业务节点执行完回Orchestrator（全部节点统一回主编排，含还价节点——主编排统一调度）
          graph_builder.add_edge("market_intelligence_node", "orchestrator_node")
          graph_builder.add_edge("cost_calculate_node", "orchestrator_node")
          graph_builder.add_edge("negotiation_node", "supplier_counter_node")     # 谈判完→供应商判定
          graph_builder.add_edge("supplier_counter_node", "reflector_node")       # 判定完→先复盘（E3）
          graph_builder.add_edge("reflector_node", "orchestrator_node")           # 复盘完→回主编排（统一调度不变）
          graph_builder.add_edge("risk_approval_node", "orchestrator_node")


          graph = graph_builder.compile(checkpointer=checkpointer)
          yield graph