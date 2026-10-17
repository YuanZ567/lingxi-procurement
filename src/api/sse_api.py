"""
sse_api.py —— SSE 直播间（两条频道）
====================================================================
GET /api/sse/task/{task_id}?  采购任务频道：实时播报主图节点执行进度
GET /api/sse/qa?question=xxx  悬浮问答频道：回答+溯源推给前端
"""

import json         # 事件数据序列化成 JSON 字符串
import uuid         # thread_id 没传时自动生成用
from fastapi import APIRouter       # 路由组件
from sse_starlette.sse import EventSourceResponse  # SSE 响应类：把生成器变成"持续推送"
from src.api.task_api import TASK_REGISTRY       # 复用任务进度登记表
from src.utils.config import get_settings        # 读 REDIS_URL（checkpoint 专用地址）
from src.graph.qa_graph import open_qa_graph, build_qa_graph   # 带记忆子图 + 无记忆子图（降级用）
from src.utils.logger import get_logger

router = APIRouter(prefix="/api/sse", tags=["SSE推送"])   # 本路由共享前缀
logger = get_logger("sse_api")  

@router.get("/task/{task_id}")
async def task_events(task_id:str):
     """
    采购任务进度频道：从登记表队列里读事件，一条条推给前端
    前端用法：new EventSource('/api/sse/task/xxx')，监听 message 即可
    """
     reg = TASK_REGISTRY.get(task_id)             # 找到任务的登记信息
     if not reg:                                  # 没这个任务（或已过有效期）
        return {"msg": "任务不存在或已结束"}       # SSE 建立前就返回普通响应

     async def event_gen():                       # 异步生成器：SSE 的"话筒"
        while True:                              # 循环读队列
            event = await reg["queue"].get()     # 阻塞等下一条事件（没事件就挂着，不耗CPU）
            if event is None:                    # 读到哨兵（None）= 后台说"播完了"
                yield {"event": "end", "data": "done"}   # 推一条结束事件
                break                            # 退出循环，SSE 连接关闭
            yield {"data": json.dumps(event, ensure_ascii=False)}  # 正常事件转JSON推出

     return EventSourceResponse(event_gen())      # 把生成器交给 sse-starlette 去播

@router.get("/qa")
async def qa_stream(question: str, thread_id: str = ""):
    """
    悬浮问答频道 v2：Agent 形态（planner⇄工具回路+多轮记忆+反问）
    前端用法：new EventSource('/api/sse/qa?question=预算超了怎么办&thread_id=xxx')
    thread_id 是"记忆抽屉号"：同一个 thread_id 连续提问 = 同一段对话（能追问、能接指代）。
    不传 thread_id 也兼容：后端自动生成一次性 ID（行为等同旧版单问单答）。
    """
    settings = get_settings()                        # 读 REDIS_URL（与主图共用同一 Redis）
    tid = (thread_id or "").strip() or uuid.uuid4().hex   # 前端带了就用它的，没带现造一个

    async def qa_gen():
        try:                                         # 整张图跑挂（如 Redis 断连）也不能让 SSE 干挂着
            try:                                     # 第一选择：带记忆模式（Agent 完整体）
                async with open_qa_graph(settings.REDIS_URL) as graph:
                    result = await graph.ainvoke(
                        {"question": question},      # 输入只给本轮问题（history 靠 checkpointer 自己续）
                        config={"configurable": {"thread_id": tid}},   # thread_id=记忆抽屉号
                    )
            except Exception as e:                   # Redis/checkpoint 挂了：降级无记忆模式（Agent 照常，只是不记得上一句）
                logger.error(f"[sse-qa] 带记忆模式异常，降级为无记忆单问单答：{e}")
                graph = build_qa_graph()             # 裸子图：不挂 checkpointer，本轮跑完即弃
                result = await graph.ainvoke({"question": question})
        except Exception as e:                       # 连降级模式都炸了（如 LLM 全挂）：友好话术收场
            logger.error(f"[sse-qa] 问答彻底失败：{e}")
            yield {"data": json.dumps({"event": "answer", "data": "哎呀，我这会儿出了点故障，请稍后再问"}, ensure_ascii=False)}
            yield {"event": "end", "data": "done"}
            return

        if result.get("clarify_question"):           # ---- 反问轮：单独事件类型，前端好做样式 ----
            yield {"data": json.dumps({"event": "route", "data": "agent_clarify"}, ensure_ascii=False)}
            yield {"data": json.dumps({"event": "clarify", "data": result["clarify_question"]}, ensure_ascii=False)}
            yield {"data": json.dumps({"event": "sources", "data": []}, ensure_ascii=False)}
        else:                                        # ---- 正常回答轮：与 v1 事件结构完全一致 ----
            yield {"data": json.dumps({"event": "route", "data": result.get("route", "agent")}, ensure_ascii=False)}
            yield {"data": json.dumps({"event": "answer", "data": result.get("answer", "")}, ensure_ascii=False)}
            yield {"data": json.dumps({"event": "sources", "data": result.get("sources", [])}, ensure_ascii=False)}
        yield {"event": "end", "data": "done"}       # 结束哨兵（前端靠它关连接）

    return EventSourceResponse(qa_gen())             # 交给 sse-starlette 播出去
     
