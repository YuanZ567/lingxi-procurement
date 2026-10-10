"""
generate_erp.py
仿真生成ERP模拟数据：历史订单 history_order、库存 inventory
存放路径：src/data/generate_erp.py
前置条件：必须先运行 import_csv.py，数据库存在供应商、物料数据
"""
import random
import uuid
from datetime import datetime, timedelta
from src.db.session import SessionLocal
from src.db.models import Supplier, CommodityPrice, HistoryOrder, Inventory


def random_date(start_date: datetime, end_date: datetime) -> datetime.date:
    """生成起止范围内随机日期工具函数"""
    delta = end_date - start_date
    random_days = random.randint(0, delta.days)
    return (start_date + timedelta(days=random_days)).date()


def generate_inventory(db):
    """
    生成库存数据 inventory
    根据已有的物料列表生成库存；物料已存在则跳过，不会覆盖
    """
    # 从行情表获取全部物料名称集合
    commodity_rows = db.query(CommodityPrice.commodity_name).distinct().all()
    commodity_list = [r.commodity_name for r in commodity_rows]

    insert_cnt = 0
    for commodity in commodity_list:
        # 如果物料库存记录已经存在，跳过
        inv = db.query(Inventory).filter(Inventory.commodity_name == commodity).first()
        if inv:
            continue
        # 仿真随机库存数量
        stock_qty = round(random.uniform(100, 10000), 4)
        new_inv = Inventory(
            commodity_name=commodity,
            stock_quantity=stock_qty
        )
        db.add(new_inv)
        insert_cnt += 1
    db.commit()
    print(f" inventory库存生成完成，新增 {insert_cnt} 条")


def get_price_on_date(db, commodity_name, order_date):
    """
    查某个物料在指定日期的真实行情价（9/8新增，替换原来的随机数定价）
    取价规则：优先取订单当天的行情；当天没有（周末/节假日不开盘）就取
    订单日期之前最近的一次收盘价——和真实采购"按最近市价成交"的逻辑一致。
    返回：行情价（float）；连历史行情都没有时返回 None（调用方跳过这条订单）。
    """
    # 第一步：先精确查订单当天的行情（比如订单日期是周二，直接拿周二收盘价）
    price_row = (
        db.query(CommodityPrice)  # 查行情表
        .filter(CommodityPrice.commodity_name == commodity_name)  # 物料名对上
        .filter(CommodityPrice.record_date == order_date)  # 日期=订单当天
        .first()  # 拿一条（没有就返回 None）
    )
    if price_row:  # 当天正好有行情，直接用
        return float(price_row.price)

    # 第二步：当天没有（非交易日），往前找最近的一次收盘价
    # 比如订单日期是周日，就拿上周五的收盘价——真实采购也是这么定价的
    price_row = (
        db.query(CommodityPrice)  # 查行情表
        .filter(CommodityPrice.commodity_name == commodity_name)  # 物料名对上
        .filter(CommodityPrice.record_date <= order_date)  # 只要订单当天之前的行情
        .order_by(CommodityPrice.record_date.desc())  # 按日期从新到旧排
        .first()  # 取最近的那条
    )
    if price_row:  # 找到了历史行情
        return float(price_row.price)

    # 连历史行情都没有（比如物料名打错、行情表没这个品类），返回 None 让调用方跳过
    return None


def generate_history_orders(db, order_count: int = 300):
    """
    仿真生成历史订单 history_order
    9/8改版：订单单价不再随机摇数，改为【锚定真实行情】——
      订单单价 = 订单日期当天的真实期货收盘价 × 供应商溢价系数
    这样生成的订单价格走势和真实市场完全同步（2024年铜价什么样，订单价就什么样），
    逻辑上无懈可击，面试可讲："历史订单按真实行情基准+供应商溢价系数建模生成"。
    :param db: 数据库会话
    :param order_count: 需要生成多少条仿真订单，默认300条
    """
    # 获取全部供应商
    supplier_list = db.query(Supplier).all()
    if len(supplier_list) == 0:
        raise Exception("数据库供应商为空，请先运行 import_csv.py 导入供应商csv！")

    # 获取物料列表
    commodity_rows = db.query(CommodityPrice.commodity_name).distinct().all()
    commodity_list = [r.commodity_name for r in commodity_rows]
    if len(commodity_list) == 0:
        raise Exception("物料行情为空，请先运行 import_csv.py！")

    start_dt = datetime.now() - timedelta(days=365)
    end_dt = datetime.now()

    insert_cnt = 0   # 数一数真正插进去多少条
    skip_cnt = 0     # 数一数因为缺行情被跳过多少条
    # 按品类给供应商分组：铜材订单只能从铜业公司里挑——
    # 否则会生成"云南铜业卖铝材"这种错配订单，谈判 Agent 引用历史成交时穿帮
    from collections import defaultdict
    suppliers_by_category = defaultdict(list)
    for s in supplier_list:
        suppliers_by_category[(s.main_category or "").strip()].append(s)

    for _ in range(order_count):
        commodity = random.choice(commodity_list) # 先挑物料
        # 只从主营品类匹配的供应商里挑（该品类没档案供应商时才退回全表，防御）
        candidates = suppliers_by_category.get(commodity.strip()) or supplier_list
        supplier = random.choice(candidates)      # 品类内随机挑一家供应商
        order_date = random_date(start_dt, end_dt)  # 随机一个过去一年内的日期

        # ===== 9/8核心改动：锚定真实行情定价 =====
        # 查该物料在订单日期的真实行情价（当天没有就取之前最近的交易日）
        market_price = get_price_on_date(db, commodity, order_date)
        if market_price is None:  # 连行情都没有（不该发生，防御一下）
            skip_cnt += 1  # 这条不生成，跳过
            continue

        # 订单单价 = 真实行情价 × 供应商溢价系数
        # premium_rate 是 Decimal（数据库 Numeric 列），必须先转 float 才能相乘
        # 逻辑：供应商在市场基准价上加自己的溢价（A类≈97折~112折，C类加价更多）
        real_unit_price = round(market_price * float(supplier.premium_rate), 4)

        order_num = round(random.uniform(10, 2000), 4)  # 采购数量：10~2000 之间随机
        total_amt = round(real_unit_price * order_num, 4)  # 总金额 = 单价 × 数量
        order_no = f"ERP-{uuid.uuid4().hex[:16].upper()}"  # 订单编号：ERP- + 16位随机码，保证唯一

        order_entity = HistoryOrder(
            order_no=order_no,
            commodity_name=commodity,
            supplier_id=supplier.id,
            order_num=order_num,
            unit_price=real_unit_price,   # 锚定真实行情的订单单价
            total_amount=total_amt,
            order_date=order_date
        )
        db.add(order_entity)
        insert_cnt += 1

    db.commit()
    print(f" history_order历史订单生成完成，新增 {insert_cnt} 条锚定行情的订单（跳过 {skip_cnt} 条缺行情的）")


def run_generate_erp(order_num=80):
    """入口函数：生成库存+历史订单"""
    print("===== 开始仿真ERP数据生成 =====")
    db = SessionLocal()
    try:
        generate_inventory(db)
        generate_history_orders(db, order_count=order_num)
    except Exception as e:
        db.rollback()
        print(f" ERP仿真生成失败：{e}")
        raise e
    finally:
        db.close()
    print("===== ERP仿真数据全部完成 =====")


if __name__ == "__main__":
    # 直接运行脚本生成订单：`python -m src.data.generate_erp 300` 就生成300条
    # 命令行第一个参数当数量，不传就用默认80条
    import sys  # 用来读命令行参数
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 80  # 有参数用参数，没有用默认值
    run_generate_erp(order_num=count)  # 按指定数量生成