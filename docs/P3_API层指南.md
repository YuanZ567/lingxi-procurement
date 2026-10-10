# P3 · API 层代码指南：FastAPI 四件套（每行大白话注释版）

> 用途：照着在 VSCode 里敲。**4 个文件全部是空壳，从零填**，敲完前后端就能对接
> 这一层还顺带补掉 TECH_SPEC 的两个历史遗留：`task_status` 落库 + `negotiation_log` 谈判轮次落库

---

## 0. 先搞懂：API 层在整个系统里的角色

```
前端(Vue3) ──HTTP──→ FastAPI(本层) ──调──→ 主业务图(采购流水线)
   ↑                      │                      ↓
   └────SSE推送←──────────┘               读写业务库(任务表/谈判日志)
```

API 层是**前台接待**：不干活，只负责三件事——
① 接单（把前端传来的采购需求写进任务表、启动主图后台执行）
② 直播（把主图每个节点的执行进度用 SSE 实时推给前端）
③ 答问（把悬浮框的问题转给问答子图）

**SSE 是什么（大白话）**：普通 HTTP 是"一问一答"，SSE 是"问一次、持续播报"——
前端订阅一次接口，后端每执行完一个 Agent 就主动推一条消息，前端看板上的
"市场情报✅ → 成本核算⟳"就是靠它实时点亮的。比 WebSocket 轻量（单向够用）。

**数据流共识**（敲码前先记住）：每个任务的进度放在一个**内存登记表**里
（字典 + asyncio.Queue），SSE 从队列里读事件往外推——不做数据库读写，
因为进度是易逝的临时信息，任务结果才是要落库的。

---

## 1. `src/api/task_api.py`（新建：接单 + 后台执行 + 状态查询）

```python
# src/api/task_api.py
"""
task_api.py —— 采购任务的"接单口"和"查单口"
====================================================================
POST /api/task/create  接单：校验参数 → 写任务表(pending) → 后台启动主图 → 返回task_id
GET  /api/task/{id}    查单：返回任务状态和最终方案（前端轮询/刷新用）
顺带补齐 TECH_SPEC 遗留：task_status 落库 + negotiation_log 每轮落库
"""

import asyncio                                   # asyncio.create_task：把执行函数扔到后台跑
import uuid                                      # 生成全局唯一的 task_id
from fastapi import APIRouter, HTTPException     # 路由组件 + 业务错误抛出
from pydantic import BaseModel                   # 请求体校验模型
from src.db.session import SessionLocal          # 数据库会话
from src.db.models import ProcurementTask, NegotiationLog  # 任务表 + 谈判轮次日志表
from src.graph.main_graph import build_main_graph  # 主业务图（async 上下文管理器）
from src.utils.config import get_settings        # 拿 Redis 地址等配置
from src.utils.logger import get_logger          # 统一日志

router = APIRouter(prefix="/api/task", tags=["采购任务"])  # 本路由下所有接口共享前缀
logger = get_logger("task_api")                  # 本模块日志器
settings = get_settings()                        # 配置中心（REDIS_URL 在这里）

# ===== 进度登记表（内存字典）：task_id → 任务的运行时信息 =====
# 大白话：前台给每个进店的客人发个叫号牌，SSE 接口按号牌来读进度
TASK_REGISTRY: dict = {}                         # {task_id: {"queue":队列, "status":状态, "solution":方案}}


class CreateTaskRequest(BaseModel):
    """创建任务的请求体：前端 POST 过来的 JSON 自动按这些字段校验"""
    material_name: str                           # 采购物料名（如"螺纹钢"）
    purchase_quantity: int                       # 采购数量
    expect_delivery_date: str                    # 期望交付日期（如 2026-10-15）
    budget_limit: float                          # 预算红线


def _push_event(task_id: str, event: dict):
    """往某任务的进度队列里推一条事件（SSE 那头会收到）"""
    q = TASK_REGISTRY.get(task_id, {}).get("queue")  # 找到这个任务的队列
    if q:                                          # 队列还在（任务没被清理）才推
        q.put_nowait(event)                        # 非阻塞放入（队列满了会丢，进度允许丢）


def _update_task_status(task_id: str, status: str):
    """把任务状态写进数据库（TECH_SPEC §1.1 遗留的落库，在这里补上）"""
    with SessionLocal() as db:                     # 开会话
        task = db.get(ProcurementTask, task_id)    # 按主键查任务
        if task:                                   # 防御：任务被删了就不写
            task.task_status = status              # 更新状态
            db.commit()                            # 提交（不 commit 等于白写）


async def _run_task(task_id: str, payload: dict):
    """
    后台执行器：真正跑主业务图的人（接单后由 asyncio.create_task 派来）
    全程做四件事：改状态running → astream逐节点播报 → 谈判每轮落库 → 终态落库
    """
    registry = TASK_REGISTRY[task_id]              # 拿到本任务的登记信息
    try:
        _update_task_status(task_id, "running")    # 数据库状态：pending → running
        registry["status"] = "running"             # 内存状态同步改
        _push_event(task_id, {"event": "status", "data": "running"})  # 播报：开工了

        async with build_main_graph(settings.REDIS_URL) as graph:   # 打开主图（内含Redis连接）
            config = {"configurable": {"thread_id": task_id}}       # thread_id=任务id（断点续跑的钥匙）
            prev_round = 0                         # 记录上一轮谈判轮数（判断"出了新一轮"用）
            final_state = {}                       # 存最后一个节点的输出（里面有最终方案）

            # astream：每执行完一个节点就吐出一个 {节点名: 该节点输出} 的块
            async for chunk in graph.astream(payload, config=config):
                for node_name, output in chunk.items():     # 块里可能只有一个节点
                    _push_event(task_id, {"event": "node", "data": node_name})  # 播报：XX节点干完了
                    final_state.update(output)              # 把节点输出并进总状态（后写的覆盖先写的）

                    # ---- TECH_SPEC 遗留补齐：谈判每轮落 negotiation_log ----
                    nego = (output or {}).get("negotiation_result")     # 本块里有谈判结果吗
                    if nego:                                            # 有（谈判节点刚跑完）
                        rd = nego.get("negotiation_round_count", 0)     # 取本轮轮次
                        if rd > prev_round:                             # 轮次前进了 = 出了新一轮
                            with SessionLocal() as db:                  # 开会话写日志表
                                db.add(NegotiationLog(                  # 插一行谈判日志
                                    task_id=task_id,                    # 关联任务
                                    round_no=rd,                        # 第几轮
                                    agent_output=json.dumps(nego, ensure_ascii=False),  # 整轮结果存JSON
                                ))
                                db.commit()                             # 提交
                            prev_round = rd                             # 更新"上一轮"记号

        # ===== 图跑完了：根据风控结论决定终态 =====
        approve = (final_state.get("risk_approve_result") or {}).get("approve_status", "")  # 风控结论
        solution = final_state.get("final_procurement_solution", "")  # 最终采购方案（markdown文本）
        registry["solution"] = solution            # 存进登记表（查单接口给前端用）

        if approve == "pass":                      # 风控通过
            _update_task_status(task_id, "completed")               # 库状态：完成
            registry["status"] = "completed"                        # 内存状态：完成
        elif approve == "reject":                  # 风控拦截（超预算）
            _update_task_status(task_id, "blocked")                 # 库状态：已拦截
            registry["status"] = "blocked"
        else:                                      # manual（需人工）
            _update_task_status(task_id, "manual")                  # 库状态：待人工
            registry["status"] = "manual"

        _push_event(task_id, {"event": "done", "data": registry["status"]})  # 播报：干完了
        _push_event(task_id, None)                 # 推一个"哨兵"空事件：SSE 那头见它就收工

    except Exception as e:                         # 执行途中的任何意外（LLM挂/库断等）
        logger.error(f"[task {task_id}] 执行失败：{e}")
        _update_task_status(task_id, "failed")     # 库状态：失败
        registry["status"] = "failed"
        _push_event(task_id, {"event": "error", "data": str(e)[:200]})  # 播报失败原因
        _push_event(task_id, None)                 # 哨兵：让 SSE 收工


@router.post("/create")
async def create_task(req: CreateTaskRequest):
    """接单口：校验 → 落库 → 派后台任务 → 返回 task_id"""
    if req.budget_limit <= 0 or req.purchase_quantity <= 0:  # 参数合法性检查
        # TECH_SPEC §4.3 错误码 4001：预算参数非法
        raise HTTPException(status_code=400, detail={"code": 4001, "msg": "预算和数量必须大于0"})

    task_id = str(uuid.uuid4())                    # 生成全局唯一任务号

    with SessionLocal() as db:                     # 第一步：任务写进数据库（pending 态）
        db.add(ProcurementTask(
            id=task_id,                            # 主键直接用我们的任务号
            material_name=req.material_name,
            purchase_quantity=req.purchase_quantity,
            expect_delivery_date=req.expect_delivery_date,
            budget_limit=req.budget_limit,
            task_status="pending",                 # 初始状态：待执行
        ))
        db.commit()                                # 提交

    # 第二步：登记运行时信息（进度队列），第三步：派后台执行
    TASK_REGISTRY[task_id] = {"queue": asyncio.Queue(), "status": "pending", "solution": ""}
    asyncio.create_task(_run_task(task_id, {       # 关键：create_task 让图在后台跑，接口立刻返回
        "task_id": task_id,                        # astream 的输入载荷（和 run_purchase_task 一致）
        "commodity_name": req.material_name,
        "purchase_quantity": req.purchase_quantity,
        "expect_delivery_date": req.expect_delivery_date,
        "budget_limit": req.budget_limit,
        "user_raw_demand": f"需要采购{req.material_name}，尽量压低价格",
        "loop_round": 0,                           # E2 回环字段：初始值
        "max_loop_limit": 3,
        "tried_supplier_ids": [],
        "risk_retry_count": 0,
    }))

    return {"task_id": task_id}                    # 前端拿它去订阅 SSE 和查状态


@router.get("/{task_id}")
async def get_task(task_id: str):
    """查单口：返回任务当前状态 + 最终方案（前端轮询/刷新详情页用）"""
    reg = TASK_REGISTRY.get(task_id)               # 先查内存登记表（有运行时信息和方案）
    with SessionLocal() as db:                     # 再查数据库（有落库的权威状态）
        task = db.get(ProcurementTask, task_id)
    if not task and not reg:                       # 两边都没有 = 查无此单
        raise HTTPException(status_code=404, detail={"code": 4004, "msg": "任务不存在"})
    return {                                       # 组装返回（内存里有就用内存的，没有用库里的）
        "task_id": task_id,
        "status": reg["status"] if reg else (task.task_status if task else "unknown"),
        "solution": reg["solution"] if reg else "",
        "db_status": task.task_status if task else None,   # 数据库侧状态（对账用）
    }
```

> **别忘了一行**：文件顶部要 `import json`（`negotiation_log` 落库用了 `json.dumps`）。敲的时候放在 `import asyncio` 旁边。

---

## 2. `src/api/sse_api.py`（新建：两路 SSE 直播）

```python
# src/api/sse_api.py
"""
sse_api.py —— SSE 直播间（两条频道）
====================================================================
GET /api/sse/task/{task_id}?  采购任务频道：实时播报主图节点执行进度
GET /api/sse/qa?question=xxx  悬浮问答频道：回答+溯源推给前端
"""

import json                                      # 事件数据序列化成 JSON 字符串
from fastapi import APIRouter                    # 路由组件
from sse_starlette.sse import EventSourceResponse  # SSE 响应类：把生成器变成"持续推送"
from src.api.task_api import TASK_REGISTRY       # 复用任务进度登记表
from src.graph.qa_graph import build_qa_graph    # 问答子图（阶段二产出）
from src.utils.logger import get_logger

router = APIRouter(prefix="/api/sse", tags=["SSE推送"])   # 本路由共享前缀
logger = get_logger("sse_api")                   # 本模块日志器


@router.get("/task/{task_id}")
async def task_events(task_id: str):
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
    async def qa_gen():                          # 异步生成器：问答的"话筒"
        graph = build_qa_graph()                 # 每问编译一次（子图很轻，毫秒级）
        result = await graph.ainvoke({"question": question})   # 完整跑一遍问答子图
        yield {"data": json.dumps({"event": "route", "data": result["route"]}, ensure_ascii=False)}  # 推路由
        yield {"data": json.dumps({"event": "answer", "data": result["answer"]}, ensure_ascii=False)}  # 推回答
        yield {"data": json.dumps({"event": "sources", "data": result["sources"]}, ensure_ascii=False)}  # 推溯源
        yield {"event": "end", "data": "done"}   # 结束哨兵

    return EventSourceResponse(qa_gen())         # 交给 sse-starlette 播出去
```

---

## 3. `src/api/main.py`（新建：应用入口）

```python
# src/api/main.py
"""
main.py —— FastAPI 应用入口（整个后端服务的"大门"）
====================================================================
启动方式（项目根目录）：
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from fastapi import FastAPI, Request             # 应用本体 + 全局异常钩子要用的请求对象
from fastapi.middleware.cors import CORSMiddleware  # 跨域中间件（前端localhost:5173要访问后端8000）
from fastapi.responses import JSONResponse       # 统一JSON错误响应
from src.api.task_api import router as task_router   # 采购任务路由
from src.api.sse_api import router as sse_router     # SSE 路由
from src.utils.logger import get_logger          # 统一日志

logger = get_logger("api")                       # 入口日志器

app = FastAPI(                                   # 创建应用实例
    title="灵犀采购后端",                         # 文档页标题
    version="1.0",                               # 版本号
)

# ---- 跨域放行：开发期前端(5173)和后端(8000)端口不同，必须放行 ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],                         # 开发期全放行；上线要收紧成前端域名
    allow_methods=["*"],                         # 放行所有HTTP方法（GET/POST...）
    allow_headers=["*"],                         # 放行所有请求头
)

# ---- 注册两条业务路由 ----
app.include_router(task_router)                  # /api/task/*
app.include_router(sse_router)                   # /api/sse/*

# ---- 全局异常兜底：任何没接住的异常都转成统一JSON，不向前端甩堆栈 ----
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"[全局异常] {request.url.path}：{exc}")    # 服务端留痕（排查用）
    # TECH_SPEC §4.3 错误码 5001：Agent执行异常
    return JSONResponse(status_code=500, content={"code": 5001, "msg": "服务内部异常，请稍后重试"})

# ---- 健康检查：前端/运维探活用 ----
@app.get("/health")
async def health():
    return {"status": "ok"}                      # 能返回这个就说明服务活着
```

---

## 4. 验收（3 步，需要 MySQL + Redis 都在跑）

1. **启动服务**：`uvicorn src.api.main:app --port 8000 --reload` → 控制台无报错，浏览器打开 `http://127.0.0.1:8000/docs` 能看到接口文档页
2. **创建任务 + 订阅进度**（开两个终端）：
   - 终端A：`curl -N "http://127.0.0.1:8000/api/sse/task/先记下这步的task_id"`（先挂着等推流）
   - 终端B：`curl -X POST http://127.0.0.1:8000/api/task/create -H "Content-Type: application/json" -d "{\"material_name\":\"螺纹钢\",\"purchase_quantity\":500,\"expect_delivery_date\":\"2026-10-15\",\"budget_limit\":2500000}"`
   - 拿到 task_id 填进终端A的地址 → 终端A应持续吐出 `{"event":"node","data":"market_intelligence_node"}` 这样的进度行，最后出现 `event: end`
3. **问答频道**：`curl -N "http://127.0.0.1:8000/api/sse/qa?question=预算超了怎么办"` → 应推 answer 和 sources 事件

## 5. 注意事项与已知取舍

- **进度登记表是内存的**：服务重启后进行中的任务进度丢失（数据库里状态还在，不会错，只是 SSE 断了）——MVP 取舍；要强一致可改成 Redis 存进度，一行换一个存储
- **`negotiation_log` 落库在 API 层做**而不是图节点里做：图管决策、API 管持久化，职责分离；面试可讲"图的每个节点输出都能被 astream 事件捕获，持久化是消费方的事"
- **`final_procurement_solution` 只存在内存登记表**（procurement_task 表没有方案列）——重启后查不到方案文本，但 Checkpointer 里完整状态还在；要持久化加一列即可，属后续优化
- 生产部署记得把 CORS 的 `allow_origins=["*"]` 收紧成前端实际域名
- 敲完先 `python -m py_compile src/api/task_api.py src/api/sse_api.py src/api/main.py` 再启动 uvicorn
