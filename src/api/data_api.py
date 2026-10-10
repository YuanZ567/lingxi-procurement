"""
data_api.py —— 看板数据接口（行情 + 供应商）
====================================================================
GET /api/market?commodity=螺纹钢&limit=260   单品种行情序列（自动先刷新当天实时价）
GET /api/suppliers?region=天津市&category=螺纹钢   供应商档案（可按地区/品类筛）
让前端告别 public/*.json 静态文件，数据直接从 MySQL 来
"""

from fastapi import APIRouter,Query      # 路由 + 查询参数校验
from sqlalchemy import desc
from src.db.session import SessionLocal
from src.db.models import CommodityPrice,Supplier
from src.data.market_provider import refresh_market_price, backfill_history  # 实时刷新 + 历史回填

router=APIRouter(prefix="/api",tags=["看板数据"])

@router.get("/market")
async def get_market(
    commodity: str = Query("螺纹钢", description="品种中文名"),
    limit: int = Query(260, le=2603, description="返回最近多少个交易日")):
    """行情接口：先触发实时刷新（抓到当天价就写库），再查库返回完整序列。
    返回 {source, latest_date, series}，前端据此显示"数据截至"徽标。
    刷新内部自带容错：断网/接口挂了自动降级读库，绝不影响本接口出数。
    另有历史回填：库里最新日期落后于今天（系统几天没用）时，先从新浪日线把缺的交易日拉齐。"""
    backfill_history(commodity)                       # 有断档先补齐（幂等，无缺口时零开销）
    source = refresh_market_price(commodity)          # 再刷新：缓存/实时/降级，把最新价写进库里
    with SessionLocal() as db:                        # 开会话
        rows = (db.query(CommodityPrice)              # 查行情表
                  .filter(CommodityPrice.commodity_name == commodity)   # 只取该品种
                  .order_by(desc(CommodityPrice.record_date))           # 最新在前
                  .limit(limit).all())                # 截取条数
        rows.reverse()                                # 图表要"从旧到新"，把倒序翻回来
        return {                                      # 组装：序列 + 数据来源 + 最新日期
            "source": source,                         # "缓存"/"实时"/"数据库降级"/"无数据"
            "latest_date": str(rows[-1].record_date) if rows else None,   # 序列里最新交易日（给前端徽标）
            "series": [{                             # 前端图表直接吃的数组
                "date": str(r.record_date),           # 交易日
                "close": float(r.price)               # 收盘价（Decimal 转 float 才能进 JSON）
            } for r in rows]
        }

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