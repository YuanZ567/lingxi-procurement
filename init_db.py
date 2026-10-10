"""
数据库建表脚本（独立、需手动执行）

用法：
  python init_db.py           # 创建缺失的表（已存在的表自动跳过，绝不 DROP/重建）
  python init_db.py --check   # 只检查缺哪些表，不执行任何建表写入

说明：
  - 本脚本仅在【你明确下令】时才运行；src/db/models.py 本身不触发任何数据库写入。
  - 使用 SQLAlchemy 的 create_all，对已有表幂等跳过，不会破坏既有结构或数据。
"""
import sys

from src.db.session import engine
from src.db.models import Base


def missing_tables():
    """返回 Base 中定义、但 MySQL 里尚不存在的表名列表。"""
    from sqlalchemy import inspect
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    return [t for t in Base.metadata.tables.keys() if t not in existing]


def main():
    check_only = "--check" in sys.argv
    defined = sorted(Base.metadata.tables.keys())
    miss = missing_tables()

    print(f"已定义表（{len(defined)} 张）：{defined}")
    if miss:
        print(f"MySQL 中缺失（{len(miss)} 张）：{miss}")
    else:
        print("✅ MySQL 中表结构已齐全，无需创建。")

    if check_only:
        print("\n[--check 模式] 未执行任何建表写入。")
        return

    if not miss:
        print("\n无需建表，直接退出。")
        return

    Base.metadata.create_all(bind=engine)
    print(f"\n✅ 已补齐缺失表，当前共 {len(defined)} 张表。")


if __name__ == "__main__":
    main()
