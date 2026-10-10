"""
task_api.py —— 采购任务的"接单口"和"查单口"
====================================================================
POST /api/task/create  接单：校验参数 → 写任务表(pending) → 后台启动主图 → 返回task_id
GET  /api/task/{id}    查单：返回任务状态和最终方案（前端轮询/刷新用）
顺带补齐 TECH_SPEC 遗留：task_status 落库 + negotiation_log 每轮落库
"""

import asyncio          # asyncio.create_task：把执行函数扔到后台跑
import time           # 取当前时间生成可读任务名
import traceback        # 打印完整堆栈（定位后台任务异常用）
import uuid           # 生成全局唯一的 task_id
import json           # 谈判日志序列化用
from fastapi import APIRouter,HTTPException         # 路由组件 + 业务错误抛出
from pydantic import BaseModel                      # 请求体校验模型
from src.db.session import SessionLocal          # 数据库会话
from src.db.models import ProcurementTask, NegotiationLog  # 任务表 + 谈判轮次日志表
from src.graph.main_graph import build_main_graph  # 主业务图（async 上下文管理器）
from src.utils.config import get_settings        # 拿 Redis 地址等配置
from src.utils.logger import get_logger          # 统一日志

router=APIRouter(prefix="/api/task",tags=["采购任务"])  # 本路由下所有接口共享前缀
logger=get_logger("task_api")
settings = get_settings()                        # 配置中心（REDIS_URL 在这里）

# ===== 进度登记表（内存字典）：task_id → 任务的运行时信息 =====
# 大白话：前台给每个进店的客人发个叫号牌，SSE 接口按号牌来读进度
TASK_REGISTRY: dict = {}                         # {task_id: {"queue":队列, "status":状态, "solution":方案}}

class CreateTaskRequest(BaseModel):
    """创建任务的请求体：前端 POST 过来的 JSON 自动按这些字段校验"""
    task_name:str=""                         # 任务名称（可读名，空则自动生成）
    material_name:str                       # 采购物料名（如"螺纹钢"）
    purchase_quantity:int                   # 采购数量
    expect_delivery_date:str                # 期望交付日期（如 2026-10-15）
    budget_limit:float                      # 预算红线

def _push_event(task_id:str,event:dict):
    
    """往某任务的进度队列里推一条事件（SSE 那头会收到）"""
    q=TASK_REGISTRY.get(task_id,{}).get("queue")    # 找到这个任务的队列
    if q:                                          # 队列还在（任务没被清理）才推
        q.put_nowait(event)                        # 非阻塞放入（队列满了会丢，进度允许丢）

def _update_task_status(task_id:str,status:str):
    """把任务状态写进数据库（TECH_SPEC §1.1 遗留的落库，在这里补上）"""
    with SessionLocal() as db:                     # 开会话
        task = db.get(ProcurementTask, task_id)    # 按主键查任务
        if task:                                   # 防御：任务被删了就不写
            task.task_status = status              # 更新状态
            db.commit()                            # 提交（不 commit 等于白写）

def _save_solution(task_id:str,solution:str):
    """最终方案落库：只存内存的话后端一重启就丢，历史任务就查不到结果了"""
    with SessionLocal() as db:
        task = db.get(ProcurementTask, task_id)
        if task:
            task.solution = solution or ""         # 空方案也存（标记"已生成过"）
            db.commit()

async def _run_task(task_id:str,payload:dict):
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
                    if output:                              # 9/14实测：LangGraph对个别节点会推{节点名:None}
                        final_state.update(output)          # 有输出才并入总状态（防御None）

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
        approve=(final_state.get("risk_approve_result") or {}).get("approve_status", "")
        # 风控结论
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

        _save_solution(task_id, solution)          # 方案落库（只存内存的话重启即丢）
        # 成交总金额落库：本月成本节约统计的数据源（从谈判结果取，取不到就跳过）
        final_amt = (final_state.get("negotiation_result") or {}).get("final_total_amount")
        if final_amt:                              # 谈出结果了才存
            with SessionLocal() as db:             # 开会话
                t = db.get(ProcurementTask, task_id)   # 找到本单
                if t:                              # 防御：单被删了不写
                    t.final_amount = float(final_amt)   # Decimal 兼容：转 float 再存
                    db.commit()                    # 提交
        _push_event(task_id, {"event": "done", "data": registry["status"]})  # 播报：干完了
        _push_event(task_id, None)                 # 推一个"哨兵"空事件：SSE 那头见它就收工

    except Exception as e:                         # 执行途中的任何意外（LLM挂/库断等）
        logger.error(f"[task {task_id}] 执行失败：{e}")
        logger.error(traceback.format_exc())   # 完整堆栈
        _update_task_status(task_id, "failed")     # 库状态：失败
        registry["status"] = "failed"
        _push_event(task_id, {"event": "error", "data": str(e)[:200]})  # 播报失败原因
        _push_event(task_id, None)                 # 哨兵：让 SSE 收工

@router.post("/create")
async def create_task(req:CreateTaskRequest):
    """接单口：校验 → 落库 → 派后台任务 → 返回 task_id"""
    if req.budget_limit <= 0 or req.purchase_quantity <= 0: # 参数合法性检查
        # TECH_SPEC §4.3 错误码 4001：预算参数非法
        raise HTTPException(status_code=400, detail={"code": 4001, "msg": "预算和数量必须大于0"})

    task_id=str(uuid.uuid4())               # 生成全局唯一任务号

    # 任务名称：前端传了就用，没传自动生成可读名（物料+日期时间），拒绝让用户看UUID
    readable_name = req.task_name.strip() or f"{req.material_name}采购单-{time.strftime('%m-%d %H:%M')}"

    with SessionLocal() as db:                  # 第一步：任务写进数据库（pending 态）
        db.add(ProcurementTask(
            id=task_id,                            # 主键直接用我们的任务号
            task_name=readable_name[:128],         # 可读任务名（截断防超长）
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

@router.get("/list")
async def list_tasks():
    """任务列表：最近20单（新→旧），给前端任务管理面板用。
    注意：本路由必须定义在 /{task_id} 之前，否则 'list' 会被当成 task_id 匹配"""
    with SessionLocal() as db:                     # 查库里最近20单
        rows = db.query(ProcurementTask).order_by(ProcurementTask.create_time.desc()).limit(20).all()
    out = []
    for t in rows:
        reg = TASK_REGISTRY.get(t.id)              # 内存登记表的状态比库新（running 中库里可能是 pending）
        out.append({
            "task_id": t.id,
            "task_name": t.task_name or f"{t.material_name}采购单",   # 老数据兜底
            "material_name": t.material_name,
            "status": reg["status"] if reg else t.task_status,
            "create_time": str(t.create_time or "")[:16],            # 截到分钟，列表更干净
        })
    return out

@router.get("/stats/month")
async def month_stats():
    """本月成本统计：本月完成的单数 + 总节约（预算−成交额）+ 平均预算占用率。
    注意：本路由也必须定义在 /{task_id} 之前，否则 'stats' 会被当成 task_id 匹配"""
    from datetime import date                          # 取本月起点用
    month_start = date.today().replace(day=1)          # 本月1号
    with SessionLocal() as db:                         # 开会话
        rows = (db.query(ProcurementTask)              # 查任务表
                  .filter(ProcurementTask.task_status == "completed",   # 只算完成的单
                          ProcurementTask.create_time >= month_start)  # 只算本月的
                  .all())
    done = [t for t in rows if t.final_amount]         # 有成交额的单才参与节约计算
    saving = sum((float(t.budget_limit) - float(t.final_amount)) for t in done)  # 逐单节约求和
    ratio = (sum(float(t.final_amount) / float(t.budget_limit) for t in done) / len(done) * 100) if done else 0
    return {                                           # 前端面板直接用的三个数
        "done_count": len(done),                       # 本月完成单数
        "total_saving": round(saving, 2),              # 总节约金额（元）
        "avg_budget_ratio": round(ratio, 1),           # 平均预算占用率（%）
    }

@router.post("/{task_id}/approve")
async def approve_task(task_id: str):
    """人工审批口：manual 状态的单，人工确认后改成 completed（方案已生成，直接可执行）。
    POST 方法与 GET /{task_id} 不冲突，但为保险起见放在它后面也没关系（方法+路径共同区分）"""
    reg = TASK_REGISTRY.get(task_id)                   # 先看内存登记表（任务可能还在本进程）
    with SessionLocal() as db:                         # 库是权威：无论如何都要改库
        task = db.get(ProcurementTask, task_id)        # 按主键查任务
        if not task:                                   # 查无此单
            raise HTTPException(status_code=404, detail={"code": 4004, "msg": "任务不存在"})
        if task.task_status != "manual":               # 只有"待人工"的单才需要审批
            raise HTTPException(status_code=400, detail={"code": 4002, "msg": f"当前状态 {task.task_status} 无需人工审批"})
        task.task_status = "completed"                 # 人工拍板：方案可用，状态改完成
        db.commit()                                    # 提交落库
    if reg:                                            # 内存登记还在（本进程跑的单）
        reg["status"] = "completed"                    # 同步内存状态
        _push_event(task_id, {"event": "done", "data": "completed"})  # 给还在听的 SSE 补终态
    logger.info(f"[task {task_id}] 人工审批通过 → completed")
    return {"task_id": task_id, "status": "completed"} # 前端拿它刷新界面

@router.get("/{task_id}")
async def get_task(task_id: str):
    """查单口：返回任务当前状态 + 最终方案（前端轮询/刷新详情页用）。
    方案来源：优先内存（本轮运行最新），内存没有（后端重启过）则读库（终态落库的）"""
    reg = TASK_REGISTRY.get(task_id)               # 先查内存登记表（有运行时信息和方案）
    with SessionLocal() as db:                     # 再查数据库（有落库的权威状态）
        task = db.get(ProcurementTask, task_id)
    if not task and not reg:                       # 两边都没有 = 查无此单
        raise HTTPException(status_code=404, detail={"code": 4004, "msg": "任务不存在"})
    solution = (reg["solution"] if reg else "") or (task.solution if task else "")  # 内存优先，库兜底
    return {
        "task_id": task_id,
        "status": reg["status"] if reg else (task.task_status if task else "unknown"),
        "solution": solution,
        "db_status": task.task_status if task else None,   # 数据库侧状态（对账用）
    }

@router.delete("/{task_id}")
async def delete_task(task_id: str):
    """删单：数据库任务记录（谈判日志级联删除）+ 内存登记一起清，前端任务列表的删除按钮用"""
    TASK_REGISTRY.pop(task_id, None)               # 内存登记先清
    with SessionLocal() as db:
        task = db.get(ProcurementTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail={"code": 4004, "msg": "任务不存在"})
        db.delete(task)                            # negotiation_log 外键 CASCADE 级联删除
        db.commit()
    return {"msg": "已删除", "task_id": task_id}
