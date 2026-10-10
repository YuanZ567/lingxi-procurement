from src.db.session import SessionLocal
from src.db.models import ProcurementTask

def test_orm():
    db = SessionLocal()
    try:
        # 查询表结构，验证ORM映射正常
        res = db.query(ProcurementTask).limit(1).all()
        print("✅ ORM模型加载成功，可正常查询procurement_task表")
    except Exception as e:
        print(f"❌ ORM异常：{e}")
    finally:
        db.close()

if __name__ == "__main__":
    test_orm()