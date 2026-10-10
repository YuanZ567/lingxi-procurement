from sqlalchemy import text
from src.db.session import engine, SessionLocal

def test_connect():
    # 测试引擎连通
    with engine.connect() as conn:
        print("✅ MySQL数据库连通成功！")

    # 测试会话是否正常（SQLAlchemy 2.0 起必须用 text() 包裸 SQL）
    db = SessionLocal()
    try:
        res = db.execute(text("SELECT 1"))
        print(f"✅ 会话测试结果：{res.scalar()}")
    finally:
        db.close()

if __name__ == "__main__":
    test_connect()