"""
data_api.py —— 看板数据接口（行情 + 供应商）
====================================================================
GET /api/market?commodity=螺纹钢&limit=260   单品种行情序列（自动先刷新当天实时价）
GET /api/suppliers?region=天津市&category=螺纹钢   供应商档案（可按地区/品类筛）
让前端告别 public/*.json 静态文件，数据直接从 MySQL 来
"""

from fastapi import APIRouter,Query,BackgroundTasks    # 路由 + 查询参数校验 + 后台任务（慢活儿移出请求路径）
from sqlalchemy import desc
from src.db.session import SessionLocal
from src.db.models import CommodityPrice,Supplier
from src.data.market_provider import refresh_market_background, quick_source  # 后台刷新 + 快速徽标
from src.utils.logger import get_logger
logger = get_logger("data_api")                   # 本路由日志器（性能计时用）

router=APIRouter(prefix="/api",tags=["看板数据"])

@router.get("/market")
async def get_market(
    commodity: str = Query("螺纹钢", description="品种中文名"),
    limit: int = Query(260, le=2603, description="返回最近多少个交易日"),
    background_tasks: BackgroundTasks = None):
    """行情接口 v2（stale-while-revalidate：旧数据秒回，后台悄悄刷新）
    请求路径只做一件事：查库 → 立即返回（毫秒级，面板不再等 10 秒+）。
    "补历史断档 + 抓实时价"这两个慢活儿全部丢给 BackgroundTasks 在响应返回后执行；
    前端 60 秒轮询天然承担"下一次拿到新数据"的角色。
    source 徽标由 quick_source 毫秒级判定：缓存 / 实时（今天已落库）/ 数据库降级（今日价还没抓到）。"""
    import time as _t                                 # 计时器（性能排查用）
    _t0 = _t.time()                                   # 请求进入时刻
    with SessionLocal() as db:                        # 开会话
        rows = (db.query(CommodityPrice)              # 查行情表
                  .filter(CommodityPrice.commodity_name == commodity)   # 只取该品种
                  .order_by(desc(CommodityPrice.record_date))           # 最新在前
                  .limit(limit).all())                # 截取条数
        rows.reverse()                                # 图表要"从旧到新"，把倒序翻回来
    _t1 = _t.time()                                   # 查库完成时刻
    src = quick_source(commodity)                     # 毫秒级判徽标，不碰网络
    _t2 = _t.time()                                   # 徽标判定完成时刻
    payload = {                                       # 组装：序列 + 数据来源 + 最新日期
        "source": src,                                # "缓存"/"实时"/"数据库降级"
        "latest_date": str(rows[-1].record_date) if rows else None,   # 序列里最新交易日（给前端徽标）
        "series": [{                             # 前端图表直接吃的数组
            "date": str(r.record_date),           # 交易日
            "close": float(r.price)               # 收盘价（Decimal 转 float 才能进 JSON）
        } for r in rows]
    }
    if background_tasks is not None:                  # FastAPI 注入的后台任务队列（直调时可能没有）
        background_tasks.add_task(                    # 慢活儿全部排到响应之后执行：
            refresh_market_background, commodity)     # 补断档 + 抓实时 + 写库 + 更新缓存，不影响本次响应
    logger.info(f"[market计时] 总{_t.time()-_t0:.2f}s = 查库{_t1-_t0:.2f}s + 徽标{_t2-_t1:.2f}s（{commodity}，{len(rows)}行）")
    return payload                                    # 立即交出（库里有什么就先画什么）

@router.get("/suppliers")
async def get_suppliers(
    region: str = Query("", description="注册省份，空=全部"),
    category: str = Query("", description="主营品类，空=全部")):
    """供应商接口：从 supplier 表读真实工商档案+业务字段，支持地区/品类筛选"""
    with SessionLocal() as db:                        # 开会话
        q = db.query(Supplier)                        # 基础查询
        if region:                                    # 传了地区就加过滤
            q = q.filter(Supplier.province == region)
        if category:                                  # 传了品类就加过滤
            q = q.filter(Supplier.main_category == category)
        rows = q.all()                                # 执行查询
        return [{                                     # 组装返回（字段名与前端现用的一致，改造成本最低）
            "name": r.supplier_name,                  # 企业全名
            "credit": {"A": 95, "B": 75, "C": 55}.get(r.credit_status, 60),  # 等级转分数
            "premium": round((float(r.premium_rate) - 1) * 100, 2),           # 系数转溢价率%
            "region": r.province or "其他",           # 注册省份
            "category": r.main_category or "其他",    # 主营品类
        } for r in rows]