"""
import_csv.py
导入CSV种子数据：大宗商品行情、供应商基础数据 (功能)
存放路径：src/data/import_csv.py
csv数据源目录：项目根目录 ./seed_data/  或  ./.seed_data/
"""
import os
from pathlib import Path
import pandas as pd

# 项目内部导入
from src.db.session import SessionLocal
from src.db.models import CommodityPrice, Supplier

# ---------------------- 路径配置 ----------------------
# 获取项目根目录
BASE_DIR = Path(__file__).parent.parent.parent
# csv种子文件目录（兼容非隐藏 seed_data/ 与 隐藏 .seed_data/ 两种命名）
SEED_DATA_DIR = BASE_DIR / "seed_data"
if not SEED_DATA_DIR.exists() and (BASE_DIR / ".seed_data").exists():
    SEED_DATA_DIR = BASE_DIR / ".seed_data"

COMMODITY_CSV_PATH = SEED_DATA_DIR / "commodity_price.csv"
SUPPLIER_CSV_PATH = SEED_DATA_DIR / "supplier.csv"
SUPPLIER_PROFILE_CSV_PATH = SEED_DATA_DIR / "supplier_profile.csv"  # 真实工商档案表（白牛商查API导出）


def import_commodity_price():
    """导入大宗商品行情表 commodity_price"""
    if not os.path.exists(COMMODITY_CSV_PATH):
        print(f" 文件不存在：{COMMODITY_CSV_PATH}，跳过行情导入")
        return

    # 读取csv（utf-8-sig：自动兼容带BOM头和不带BOM头的csv，防止首列名读成 \ufeffcommodity_name）
    df = pd.read_csv(COMMODITY_CSV_PATH, encoding="utf-8-sig")
    db = SessionLocal()
    try:
        insert_count = 0
        for _, row in df.iterrows():
            # 查重：同一物料+同一日期，视为重复记录，跳过
            exist = db.query(CommodityPrice).filter(
                CommodityPrice.commodity_name == row["commodity_name"],
                CommodityPrice.record_date == row["record_date"]
            ).first()
            if exist:
                continue

            entity = CommodityPrice(
                commodity_name=row["commodity_name"],
                price=row["price"],
                record_date=row["record_date"]
            )
            db.add(entity)
            insert_count += 1

        db.commit()
        print(f"commodity_price 导入完成，新增 {insert_count} 条记录")
    except Exception as e:
        db.rollback()
        print(f" commodity_price导入失败：{str(e)}")
        raise e
    finally:
        db.close()


def import_supplier():
    """导入供应商表 supplier"""
    if not os.path.exists(SUPPLIER_CSV_PATH):
        print(f" 文件不存在：{SUPPLIER_CSV_PATH}，跳过供应商导入")
        return

    # utf-8-sig：同上，兼容BOM头
    df = pd.read_csv(SUPPLIER_CSV_PATH, encoding="utf-8-sig")
    db = SessionLocal()
    try:
        insert_count = 0
        for _, row in df.iterrows():
            # 查重：供应商名称相同则跳过
            exist = db.query(Supplier).filter(Supplier.supplier_name == row["supplier_name"]).first()
            if exist:
                continue

            entity = Supplier(
                supplier_name=row["supplier_name"],
                credit_status=row["credit_status"],
                premium_rate=row["premium_rate"]
            )
            db.add(entity)
            insert_count += 1

        db.commit()
        print(f" supplier 导入完成，新增 {insert_count} 条记录")
    except Exception as e:
        db.rollback()
        print(f" supplier导入失败：{str(e)}")
        raise e
    finally:
        db.close()


def import_supplier_profile():
    """
    回填供应商真实工商档案（9/8新增）
    数据来源：supplier_profile.csv（白牛商查API批量导出的公示系统口径数据）
    逻辑：supplier 表里已有 80 家真实供应商（import_supplier 导入的骨架：名字+信用+溢价系数），
          本函数按"供应商名称"逐家匹配，把信用代码/法人/注册资本/省市/行业等真实档案字段补上。
          匹配不上的跳过并打印警告（骨架表和档案表理论上一一对应，不该出现）。
    """
    if not os.path.exists(SUPPLIER_PROFILE_CSV_PATH):
        print(f" 文件不存在：{SUPPLIER_PROFILE_CSV_PATH}，跳过工商档案回填")
        return

    # utf-8-sig：兼容带BOM头的csv
    df = pd.read_csv(SUPPLIER_PROFILE_CSV_PATH, encoding="utf-8-sig")
    db = SessionLocal()
    try:
        update_count = 0  # 数一数成功回填了几家
        for _, row in df.iterrows():
            # 按"供应商名称"找到骨架表里对应的供应商记录
            sup = db.query(Supplier).filter(Supplier.supplier_name == row["supplier_name"]).first()
            if not sup:
                # 档案里有但库里没有：多半是档案和骨架不同步，警告但不中断
                print(f" 警告：档案中的供应商在库里找不到，跳过 → {row['supplier_name']}")
                continue

            # 逐字段回填真实档案（字符串统一转 str 并去掉首尾空格；日期直接给 Date 列）
            sup.main_category = str(row["main_category"]).strip()      # 主营品类
            sup.credit_code = str(row["credit_code"]).strip()          # 统一社会信用代码
            sup.legal_rep = str(row["legal_rep"]).strip()              # 法定代表人
            sup.reg_capital = str(row["reg_capital_wan"]).strip()      # 注册资本（原文带币种）
            sup.reg_status = str(row["reg_status"]).strip()            # 登记状态（存续/在营）
            found = pd.to_datetime(row["found_date"], errors="coerce") # 成立日期：解析失败就给空，不让脏数据崩掉导入
            sup.found_date = found.date() if pd.notna(found) else None
            sup.province = str(row["province"]).strip()                # 注册省份
            city = row["city"]                                         # 城市可能为空（直辖市没有城市名）
            sup.city = str(city).strip() if pd.notna(city) else None
            sup.industry = str(row["industry"]).strip()                # 国标行业大类
            sup.address = str(row["address"]).strip()[:250]            # 注册地址（截断防超长）
            update_count += 1

        db.commit()
        print(f" supplier 工商档案回填完成，更新 {update_count} 家")
    except Exception as e:
        db.rollback()
        print(f" supplier_profile 回填失败：{str(e)}")
        raise e
    finally:
        db.close()


def run_all_import():
    """执行全部csv导入入口"""
    print("===== 开始导入CSV种子数据 =====")
    import_commodity_price()
    import_supplier()
    import_supplier_profile()  # 9/8新增：骨架导入完立刻回填真实档案
    print("===== CSV导入全部结束 =====")


if __name__ == "__main__":
    run_all_import()