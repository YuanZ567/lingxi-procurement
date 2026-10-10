"""
sse_api.py —— SSE 直播间（两条频道）
====================================================================
GET /api/sse/task/{task_id}?  采购任务频道：实时播报主图节点执行进度
GET /api/sse/qa?question=xxx  悬浮问答频道：回答+溯源推给前端
"""

import json         # 事件数据序列化成 JSON 字符串
from fastapi import APIRouter       # 路由组件
from sse_starlette.sse import EventSourceResponse  # SSE 响应类：把生成器变成"持续推送"
from src.api.task_api import TASK_REGISTRY       # 复用任务进度登记表
from src.graph.qa_graph import build_qa_graph    # 问答子图（阶段二产出）
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
async def qa_stream(question: str):
    """
    悬浮问答频道：跑一遍问答子图，把路由/回答/溯源按条推出
    前端用法：new EventSource('/api/sse/qa?question=预算超了怎么办')
    """
    async def qa_gen():
        graph=build_qa_graph()           # 每问编译一次（子图很轻，毫秒级）
        result=await graph.ainvoke({"question":question})
        yield {"data": json.dumps({"event": "route", "data": result["route"]}, ensure_ascii=False)}  # 推路由
        yield {"data": json.dumps({"event": "answer", "data": result["answer"]}, ensure_ascii=False)}  # 推回答
        yield {"data": json.dumps({"event": "sources", "data": result["sources"]}, ensure_ascii=False)}  # 推溯源
        yield {"event": "end", "data": "done"}   # 结束哨兵

    return EventSourceResponse(qa_gen())         # 交给 sse-starlette 播出去
     
