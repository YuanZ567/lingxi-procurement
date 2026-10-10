"""
Agent Harness 可靠性外壳 v2（E1 交易场景升级）
升级点：
1. 校验器支持 (是否合格, 失败原因) 二元组——失败原因可写回state
2. 校验失败不再静默重试——每次都记WARNING日志
3. 最终失败时结构化写回：task_error_records + loop_failed_nodes（供回环/自进化消费）
4. 新增 wrap()：把裸节点函数包成带校验的节点，注册进LangGraph
"""

import asyncio                      #异步超时控制
from typing import Callable,Any,Dict,Tuple,Optional
from src.utils.logger import get_logger         #统一日志
from src.core.exception_handler import build_error_record     #标准化错误记录

logger=get_logger("harness")                #harness模块自己的日志器

class AgentHarness:
    def __init__(
            self,
            max_retry:int=2,            #业务校验失败最多重试2次
            timeout:int=45              #单个Agent最大执行超时，单位秒
    ):
        self.max_retry=max_retry
        self.timeout=timeout

    async def run(
        self,
        agent_func: Callable,                 # 真正要执行的Agent函数（async）
        state: Dict[str, Any],                # LangGraph传入的当前状态
        validator: Optional[Callable] = None, # 业务校验器：输出state -> bool 或 (bool, 失败原因)
        node_name: str = "agent"              # 节点名，用于日志和错误记录
    ) -> Dict[str, Any]:
        attempt = 0                           # 当前已尝试次数
        last_reason = ""                      # 最近一次校验失败的原因
        last_err: Optional[Exception] = None  # 最近一次异常（如有）
        out_state: Optional[Dict[str, Any]] = None  # 最近一次节点输出（降级兜底用）

        while attempt <= self.max_retry:      # 重试循环，耗尽后走降级
            try:
                #超时保护：超过timeout秒直接抛TimeoutError
                out_state=await asyncio.wait_for(agent_func(state),timeout=self.timeout)

                if validator is None:                # 没配校验器：输出直接放行
                    return out_state

                result=validator(out_state)  #执行业务校验
                if isinstance(result,tuple):          # 支持二元组 (是否合格, 失败原因)
                    ok,reason=result
                else:                                   # 也兼容纯bool的老写法
                    ok,reason=bool(result),""
                if ok:                                  # 也兼容纯bool的老写法
                    return out_state

                # 校验不通过：记日志（不再是静默重试），准备下一轮
                last_reason=reason
                logger.warning(f"[{node_name}] 第{attempt+1}次业务失败：{reason}")
                attempt += 1

            except asyncio.TimeoutError as e:   # 超时也是一种失败，进入重试
                last_err=e
                logger.error(f"[{node_name}] 执行超时（>{self.timeout}s）")
                attempt += 1
            except Exception as e:           # 其他所有异常：记录后重试
                last_err=e
                logger.error(f"[{node_name}] 执行异常：{type(e).__name__}: {e}")
                attempt += 1

        # ===== 重试耗尽，执行结构化降级 =====
        # 失败原因优先取校验原因，其次取异常信息
        fail_msg=last_reason or (f"{type(last_err).__name__}: {last_err}" if last_err else "未知原因")
        logger.error(f"[Harness降级] {node_name} 重试耗尽，降级放行 | {fail_msg}")

        # 在最终输出上追加失败痕迹（结构化，供回环和自进化读取）
        merged = dict(out_state if out_state is not None else state)  # 有输出用输出，全炸了用原state兜底

        error_records=merged.get("task_error_records") or []     # 错误记录列表（防御None）
        error_records.append(build_error_record(node_name, RuntimeError(fail_msg)))
        failed_nodes = merged.get("loop_failed_nodes") or []         # 失败节点列表（防御None）
        failed_nodes.append(node_name)
        merged["task_error_records"] = error_records                 # 写回错误记录
        merged["loop_failed_nodes"] = failed_nodes                   # 写回失败节点
        merged["__agent_failed"] = True                              # 失败标记（老约定保留）
        merged["__agent_error_msg"] = fail_msg                       # 失败原因（老约定保留）
        return merged

    def wrap(
        self,
        agent_func: Callable,                  # 裸节点函数
        validator: Optional[Callable] = None,  # 该节点的业务校验器
        node_name: str = "agent"
    ) -> Callable:
        """
        把裸节点包成带校验的节点，返回值可直接喂给 graph_builder.add_node
        用法：graph_builder.add_node("negotiation_node", harness.wrap(negotiation_node, validator=..., node_name="negotiation_node"))
        """
        async def wrapped(state: Dict[str, Any]) -> Dict[str, Any]:
            return await self.run(agent_func, state, validator, node_name)  # 全部逻辑委托给run
        return wrapped
