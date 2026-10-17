"""
src/graph/qa_graph.py
灵犀采购｜问答子图 v2（Agent 形态）
====================================================================
v1 是"分诊→查一次→答题"的单遍流水线；v2 升级成 Agent：
  planner（LLM 调度脑）⇄ act（执行工具）来回循环，最多 3 轮；
  材料够了 → answer 融合作答；缺关键参数 → clarify 反问用户；
  每轮结束 finalize 把问答对写进 history，配 Redis checkpointer 实现"记得上一句"。
三道保险：
  ① 工具轮数硬闸 3 轮——LLM 陷入"查了又查"死循环时强制收卷；
  ② 保守反问代码级强制——已经有观察了就不许反问，只能答；
  ③ planner 的 LLM 挂了 → 自动降级成 v1 分诊模式（单工具+答题），系统不比旧版差。
"""

from contextlib import asynccontextmanager         # open_qa_graph 用它包成 async with
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.redis.aio import AsyncRedisSaver   # Redis 持久记忆（与主图同一组件）
from src.graph.qa_state import QAState
from src.graph.qa_tools import run_tool, build_sources_from_observations   # 工具箱+溯源生成
from src.utils.llm import ask_json                 # 统一 LLM 入口（带重试，失败返回 None）
from src.utils.formatter import render_rows_table  # 降级兜底时把行数据渲染成表格
from src.utils.logger import get_logger
from src.graph.prompts.qa_agent_prompt import (
    QA_PLANNER_SYSTEM,                             # v2 调度脑提示词
    QUERY_ANALYZE_SYSTEM,                          # v1 分诊提示词（降级路径用）
    ANSWER_SYSTEM, build_answer_user_prompt,       # 答题提示词 + v1 材料拼接（降级路径用）
    build_planner_user_prompt, build_agent_answer_user_prompt)  # v2 材料拼接

logger = get_logger("qa_graph")                    # 问答子图日志器

MAX_TOOL_ROUNDS = 3                                # 工具轮数硬闸：3 轮查不够也必须收卷（防死循环）


# ---------- 节点0：起跑线（每轮清零临时字段） ----------
def start_node(state: QAState):
    """挂了 checkpointer 后状态跨轮持久，上一轮的观察/轮数若不清零，下一轮会串味。
    本节点每轮第一个跑，把"每轮清零"字段全部重置；history 绝不碰（那是记忆本体）。"""
    new = dict(state)                              # 复制状态，不污染原对象（项目惯例）
    new["plan"] = None                             # 上轮 planner 的决定作废
    new["tool_observations"] = []                  # 上轮攒的材料作废（新问题重新查）
    new["iterations"] = 0                          # 工具轮数计数器归零
    new["steps"] = []                              # 执行轨迹清空（只进日志，不推前端）
    new["clarify_question"] = None                 # 反问内容清空
    new["rag_chunks"] = None                       # v1 兼容字段一并清
    new["sql_result"] = None                       # v1 兼容字段一并清
    return new


# ---------- 节点1：调度脑 ----------
async def planner_node(state: QAState):
    """LLM 决定下一步：调工具 / 直接答 / 反问。
    失败降级：planner 调不动时退回 v1 分诊逻辑，把分诊结果翻译成一次工具调用（系统不比旧版差）。"""
    new = dict(state)
    iterations = new.get("iterations", 0)          # 已调工具轮数

    # ---- 硬闸：轮数用完，不再问 LLM，强制收卷答题（省一次调用也防死循环） ----
    if iterations >= MAX_TOOL_ROUNDS:
        logger.warning(f"[qa-agent] 已达工具轮数上限{MAX_TOOL_ROUNDS}，强制转答题")
        new["plan"] = {"action": "answer"}
        return new

    # ---- 正常路径：问调度脑（走主模型：不带 fast=True，规划质量优先） ----
    result = await ask_json(
        QA_PLANNER_SYSTEM,
        build_planner_user_prompt(new["question"], new.get("history") or [],
                                  new.get("tool_observations") or [])
    )
    plan = None                                    # 先假设 planner 靠不住
    if result and result.get("action") in ("tool", "answer", "clarify"):
        plan = result                              # 动作合法，先收下
        if plan["action"] == "tool":               # 选了调工具：名字必须在注册表里
            from src.graph.qa_tools import TOOL_REGISTRY   # 局部导入避免循环依赖
            if plan.get("tool") not in TOOL_REGISTRY:
                plan = {"action": "answer"}        # 报错工具名 → 不敢用它，直接答
        if plan["action"] == "clarify" and new.get("tool_observations"):
            plan = {"action": "answer"}            # 保守反问的代码级强制：已经查过东西就不许反问

    if plan is None:                               # ---- 降级路径：planner 彻底调不动 ----
        legacy = await ask_json(QUERY_ANALYZE_SYSTEM, new["question"], fast=True)  # v1 分诊（快速模型）
        route = (legacy or {}).get("route", "rag") # 分诊结果；拿不到默认查制度（与 v1 同款安全默认）
        if route == "nl2sql":                      # 数据类问题 → 翻译成查库工具
            plan = {"action": "tool", "tool": "query_database", "args": {"question": new["question"]}}
        else:                                      # 制度类问题 → 翻译成查制度工具
            plan = {"action": "tool", "tool": "search_policy", "args": {"query": new["question"]}}
        logger.warning("[qa-agent] planner失败，降级为v1分诊单工具模式")

    new["plan"] = plan                             # 决定写回状态
    logger.info(f"[qa-agent] planner决定：{plan.get('action')} "
                f"{plan.get('tool', '')} {str(plan.get('args', ''))[:60]}")
    return new


def route_after_plan(state: QAState):
    """planner 后的条件路由：按 plan.action 指路（LangGraph 要求返回 path_map 的 key）"""
    action = (state.get("plan") or {}).get("action", "answer")   # 拿不到动作就当答题
    if action == "tool":                           # 要调工具 → act 节点
        return "act_node"
    if action == "clarify":                        # 要反问 → clarify 节点
        return "clarify_node"
    return "answer_node"                           # 其余（answer）→ 答题节点


# ---------- 节点2：执行工具 ----------
async def act_node(state: QAState):
    """执行 planner 选定的工具，观察写回状态，轮数+1；执行完固定回 planner（回路的另一半）"""
    new = dict(state)
    plan = new.get("plan") or {}                   # 取出本轮决定
    obs = await run_tool(plan.get("tool"), plan.get("args"))   # 统一入口执行（异常已兜成失败观察）
    new["tool_observations"] = (new.get("tool_observations") or []) + [obs]   # 观察入列
    new["iterations"] = new.get("iterations", 0) + 1                          # 轮数+1（计数闸在这拨动）
    new["steps"] = (new.get("steps") or []) + [f"{plan.get('tool')}：{obs['note']}"]  # 轨迹留痕
    logger.info(f"[qa-agent] 第{new['iterations']}轮工具 {plan.get('tool')} → {obs['note']}")
    return new                                     # 边表里 act→planner，自动回去再决策


# ---------- 节点3a：答题 ----------
async def answer_node(state: QAState):
    """把攒下的观察交给 LLM 融合作答；溯源由程序生成（不依赖LLM）；LLM挂了降级为观察摘要直拼"""
    new = dict(state)
    observations = new.get("tool_observations") or []   # 本轮攒的全部材料

    # ---- LLM 正常作答（快速模型：总结类轻任务，与 v1 同口径） ----
    prompt = build_agent_answer_user_prompt(new["question"], observations, new.get("history") or [])
    result = await ask_json(ANSWER_SYSTEM, prompt, fast=True)
    if result and result.get("answer"):            # 作答成功
        new["answer"] = result["answer"]
    else:                                          # LLM 挂了：把观察摘要+表格直接拼给用户（宁可朴素不能空手）
        parts = []
        for obs in observations:
            if obs.get("note"):
                parts.append("· " + obs["note"])   # 每条观察一行摘要
            rows = (obs.get("data") or {}).get("rows")
            if rows:
                parts.append(render_rows_table(rows, max_rows=5))   # 有查询结果贴前5行表格
        new["answer"] = "\n".join(parts) or "未检索到相关资料，请换个问法"
        logger.warning("[qa-agent] 答题LLM失败，降级为观察摘要")

    new["sources"] = build_sources_from_observations(observations)   # 程序化溯源（永远准确）
    new["route"] = "agent"                         # 标记：正常回答轮
    return new


# ---------- 节点3b：反问 ----------
async def clarify_node(state: QAState):
    """把 planner 的反问作为本轮输出。注意：能走到这必是'零观察'轮（代码级强制，见 planner）"""
    new = dict(state)
    plan = new.get("plan") or {}
    q = plan.get("question") or "能补充一下具体想查哪方面的信息吗？"   # 兜底话术
    new["clarify_question"] = q                    # 专用字段：SSE 靠它分流 clarify 事件
    new["answer"] = q                              # 同时放进 answer：不认识的旧调用方也能显示
    new["sources"] = []                            # 反问轮无溯源
    new["route"] = "agent_clarify"                 # 标记：反问轮
    logger.info(f"[qa-agent] 反问用户：{q[:40]}")
    return new


# ---------- 节点4：收尾（写记忆） ----------
def finalize_node(state: QAState):
    """把本轮问答对追加进 history（只留最近3轮防膨胀）。挂 checkpointer 时它自动持久到 Redis"""
    new = dict(state)
    history = (new.get("history") or [])[-6:]      # 只保留最近3轮（6条），记忆瘦身
    bot_text = new.get("clarify_question") or new.get("answer") or ""   # 反问轮也记（用户接着答时才有上下文）
    history += [{"role": "user", "text": new["question"]},
                {"role": "bot", "text": bot_text}]
    new["history"] = history
    return new


# ---------- 编译：无记忆版（e2e/离线直调兼容，签名保持 build_qa_graph() 不变） ----------
def build_qa_graph():
    """编译不带 checkpointer 的问答子图——e2e 的 build_qa_graph().ainvoke({...}) 原样可用"""
    return _compile(None)                          # checkpointer 传 None

# ---------- 编译：持久记忆版（SSE 接口用） ----------
@asynccontextmanager
async def open_qa_graph(redis_url: str):
    """编译挂 AsyncRedisSaver 的问答子图。用法（与主图同款）：
        async with open_qa_graph(REDIS_URL) as graph:
            await graph.ainvoke({"question": q}, config={"configurable": {"thread_id": tid}})
    with 块内 Redis 连接全程存活，退出自动释放。"""
    async with AsyncRedisSaver.from_conn_string(redis_url) as checkpointer:   # 打开 Redis 检查点连接
        await checkpointer.setup()                 # 建索引（幂等，可重复调）
        yield _compile(checkpointer)               # 交出编译好的图

def _compile(checkpointer):
    """真正建图：注册节点+连边。有 checkpointer 就挂上（记忆），没有就裸跑（兼容测试）"""
    builder = StateGraph(QAState)                  # 问答子图自己的状态

    builder.add_node("start_node", start_node)         # 起跑线：每轮清零临时字段
    builder.add_node("planner_node", planner_node)     # 调度脑：LLM 决定下一步
    builder.add_node("act_node", act_node)             # 干活的手：执行工具
    builder.add_node("answer_node", answer_node)       # 答题人
    builder.add_node("clarify_node", clarify_node)     # 反问人
    builder.add_node("finalize_node", finalize_node)   # 收尾：写记忆

    builder.add_edge(START, "start_node")              # 起点先进起跑线
    builder.add_edge("start_node", "planner_node")     # 清零完交给调度脑

    # 调度脑的三岔口：tool→干活 / clarify→反问 / answer→答题
    builder.add_conditional_edges(
        source="planner_node",
        path=route_after_plan,
        path_map={"act_node": "act_node", "answer_node": "answer_node", "clarify_node": "clarify_node"},
    )

    builder.add_edge("act_node", "planner_node")       # 工具干完回调度脑再决策（Agent 回路核心）
    builder.add_edge("answer_node", "finalize_node")   # 答完去写记忆
    builder.add_edge("clarify_node", "finalize_node")  # 反问也去写记忆
    builder.add_edge("finalize_node", END)             # 收尾即结束

    return builder.compile(checkpointer=checkpointer)  # 编译（checkpointer 可为 None）
