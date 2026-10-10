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
from src.api.data_api import router as data_router   # 看板数据路由

logger=get_logger("api")            # 入口日志器

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

# ---- 注册业务路由 ----
app.include_router(task_router)                  # /api/task/*
app.include_router(sse_router)                   # /api/sse/*
app.include_router(data_router)                  # /api/market、/api/suppliers（P5 看板数据）

# ---- 全局异常兜底：任何没接住的异常都转成统一JSON，不向前端甩堆栈 ----
@app.exception_handler(Exception)
async def global_exception_handler(request:Request,exc:Exception):
    logger.error(f"[全局异常] {request.url.path}：{exc}")       # 服务端留痕（排查用）
     # TECH_SPEC §4.3 错误码 5001：Agent执行异常
    return JSONResponse(status_code=500, content={"code": 5001, "msg": "服务内部异常，请稍后重试"})
# ---- 健康检查：前端/运维探活用 ----
@app.get("/health")
async def health():
    return {"status": "ok"}                      # 能返回这个就说明服务活着