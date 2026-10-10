from sqlalchemy import Column, BigInteger, Integer, String, Numeric, Date, DateTime, Text, UniqueConstraint, ForeignKey
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import func

# 所有 Decimal 金额字段都用 Numeric（SQLAlchemy 2.0 移除 Decimal 别名，统一用 Numeric）
Decimal = Numeric

# 所有ORM模型的基类，SQLAlchemy2.0标准写法
class Base(DeclarativeBase):
    pass

# 1. 大宗商品行情表 commodity_price
class CommodityPrice(Base):
    __tablename__="commodity_price"

    id=Column(BigInteger,primary_key=True,autoincrement=True,comment="主键")
    commodity_name=Column(String(128),nullable=False,comment="物料名称")
    price=Column(Decimal(16,4),nullable=False,comment="当日基准价格")
    record_date=Column(Date,nullable=False,comment="行情日期")
    create_time=Column(DateTime,default=func.now(),comment="创建时间")

# 2. 供应商表 supplier
class Supplier(Base):
    __tablename__="supplier"

    id=Column(BigInteger,primary_key=True,autoincrement=True,comment="主键")
    supplier_name=Column(String(128),nullable=False,comment="供应商名称")
    credit_status=Column(String(32),nullable=False,comment="信用风险等级")
    premium_rate=Column(Decimal(8,4),nullable=False,comment="供应商溢价系数")
    # ========== 9/8新增：真实工商档案列（来源：白牛商查API批量导出，公示系统口径）==========
    # 这些字段是"这家公司是谁"的官方登记信息，可去国家企业信用信息公示系统逐家核验
    main_category=Column(String(32),nullable=True,comment="主营品类(螺纹钢/铝材/铜材/塑料粒子)，与行情表物料名对齐")
    credit_code=Column(String(32),nullable=True,comment="统一社会信用代码，18位唯一身份编码")
    legal_rep=Column(String(64),nullable=True,comment="法定代表人姓名")
    reg_capital=Column(String(64),nullable=True,comment="注册资本(带币种原文，如'15000万人民币')")
    reg_status=Column(String(64),nullable=True,comment="登记状态(存续/在营等)，非存续企业在风控节点应降级")
    found_date=Column(Date,nullable=True,comment="成立日期，可推算企业经营年限")
    province=Column(String(32),nullable=True,comment="注册省份")
    city=Column(String(32),nullable=True,comment="注册城市")
    industry=Column(String(64),nullable=True,comment="国标行业大类(如批发业/有色金属冶炼和压延加工业)")
    address=Column(String(255),nullable=True,comment="注册地址")
    create_time=Column(DateTime,default=func.now(),comment="创建时间")

# 3. 采购主任务表 procurement_task【核心表】
class ProcurementTask(Base):
    __tablename__="procurement_task"

    id=Column(String(64),primary_key=True,comment="task_id全局唯一主键")
    task_name=Column(String(128),nullable=True,comment="任务名称（前端可读名，如'9月螺纹钢紧急采购'）")
    material_name=Column(String(128),nullable=False,comment="物料")
    purchase_quantity=Column(Decimal(16,4),nullable=False,comment="采购数量")
    expect_delivery_date=Column(Date,nullable=True,comment="期望交付时间")

     # 重点约束：数据库层面注释标记，程序代码禁止修改此字段，仅允许前端人工编辑
    budget_limit=Column(Decimal(16,4),nullable=False,comment="本次任务预算红线，全局刚性约束；程序禁止修改此字段，仅允许前端人工编辑")
    task_status=Column(String(32),nullable=False,default="pending",comment="任务状态:pending/running/blocked/success/failed")
    solution=Column(Text,nullable=True,comment="最终采购方案全文（终态落库，后端重启不丢）")
    final_amount=Column(Decimal(16,4),nullable=True,comment="成交总金额（谈判终态落库，成本节约统计用）")
    create_time=Column(DateTime,default=func.now(),comment="创建时间")


# 4. 谈判轮次日志 negotiation_log
class NegotiationLog(Base):
    __tablename__="negotiation_log"

    id=Column(BigInteger,primary_key=True,autoincrement=True,comment="主键")
    task_id=Column(String(64),ForeignKey("procurement_task.id",ondelete="CASCADE"), nullable=False, comment="关联采购任务")
    round_no=Column(Integer,nullable=False,comment="谈判轮数")
    agent_output=Column(Text,nullable=True,comment="Agent输出策略、话术")
    create_time = Column(DateTime, default=func.now(), comment="记录时间")


# 5. 成本测算方案表 cost_plan
class CostPlan(Base):
    __tablename__="cost_plan"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    task_id=Column(String(64),ForeignKey("procurement_task.id", ondelete="CASCADE"), nullable=False, comment="关联任务")
    plan_type=Column(String(16),nullable=False,comment="once一次性 / batch分批采购")
    total_cost=Column(Decimal(16,4),nullable=False,comment="测算总成本")
    material_cost=Column(Decimal(16,4),nullable=False,comment="物料成本")
    storage_cost=Column(Decimal(16,4),nullable=False,comment="仓储成本")
    capital_occupy_cost=Column(Decimal(16,4),nullable=False,comment="资金占用成本")
    surplus_or_over=Column(Decimal(16,4),nullable=False,comment="预算结余/超支金额")
    create_time=Column(DateTime,default=func.now(),comment="创建时间")

# 6. 历史订单表 history_order（仿真ERP）
class HistoryOrder(Base):
    __tablename__="history_order"
    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    order_no=Column(String(64),nullable=False,comment="订单编号")
    commodity_name=Column(String(128),nullable=False,comment="物料名称")
    supplier_id=Column(BigInteger,ForeignKey("supplier.id"),nullable=False,comment="供应商ID")
    order_num=Column(Decimal(16,4),nullable=False,comment="采购数量")
    unit_price=Column(Decimal(16,4),nullable=False,comment="单价")
    total_amount=Column(Decimal(16,4),nullable=False,comment="订单总金额")
    order_date=Column(Date,nullable=False,comment="下单日期")
    create_time=Column(DateTime,default=func.now(),comment="创建时间")

    __table_args__=(
        UniqueConstraint("order_no",name="uk_order_no"),
    )


# 7. 库存表 inventory（仿真ERP）
class Inventory(Base):
    __tablename__="inventory"

    id=Column(BigInteger,primary_key=True,autoincrement=True,comment="主键")
    commodity_name=Column(String(128),nullable=False,comment="物料名称")
    stock_quantity=Column(Decimal(16,4),nullable=False,comment="现有库存数量")
    update_time=Column(DateTime,default=func.now(),onupdate=func.now(), comment="更新时间")

    __table_args__=(
        UniqueConstraint("commodity_name",name="uk_commodity_name"),
    )


# 8. RAG知识库元数据表 kb_document
class KbDocument(Base):
    __tablename__="kb_document"

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="主键")
    doc_title=Column(String(256),nullable=False,comment="文档标题")
    doc_source=Column(String(256),nullable=True,comment="文档来源，用于问答溯源")
    vector_doc_id=Column(String(128),nullable=True,comment="对应Chroma向量文档id")
    create_time=Column(DateTime,default=func.now(),comment="创建时间")