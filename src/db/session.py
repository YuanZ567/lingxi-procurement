##创建数据库连接引擎、会话工厂，提供给 FastAPI 获取数据库会话对象

import os  # 导入系统os模块，用来读取环境变量

# pathlib：用文件位置定位项目根，避免依赖当前工作目录
from pathlib import Path

# dotenv：读取项目根目录 .env 文件里面的配置
from dotenv import load_dotenv

# sqlalchemy：ORM数据库核心
from sqlalchemy import create_engine

# sessionmaker：生成数据库会话工厂，会话就是和数据库的连接会话
from sqlalchemy.orm import sessionmaker

# ---------------------- 1、加载 .env ----------------------
# 用 __file__ 定位项目根，绝对路径加载 .env
# 这样无论从哪个目录启动（IDE、uvicorn、命令行），都能正确读到配置
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

# 从环境变量读取数据库各项配置
DB_USER=os.getenv("DB_USER")  # mysql用户名 root
DB_PASSWORD=os.getenv("DB_PASSWORD")  # mysql密码
DB_HOST=os.getenv("DB_HOST")   # 数据库地址 localhost
DB_PORT=os.getenv("DB_PORT")   # 端口 3306
DB_NAME=os.getenv("DB_NAME")   # 数据库名 lingxi_procurement

# 防御性校验：配置缺失时立刻明确报错，
# 避免拼出 "None:None@None:None/None" 这种难以排查的 URL
_MISSING = [name for name, value in [
    ("DB_USER", DB_USER), ("DB_PASSWORD", DB_PASSWORD), ("DB_HOST", DB_HOST),
    ("DB_PORT", DB_PORT), ("DB_NAME", DB_NAME),
] if not value]
if _MISSING:
    raise RuntimeError(
        f"数据库配置缺失: {_MISSING}\n"
        f"请复制 .env.example 为 .env 并填写（已尝试读取: {BASE_DIR / '.env'}）"
    )

# ---------------------- 2、拼接数据库连接字符串 URL ----------------------
# 格式：mysql+pymysql://用户名:密码@主机:端口/数据库名
DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ---------------------- 3、创建数据库引擎 engine ----------------------
# engine：SQLAlchemy最核心对象，代表和MySQL数据库的连接池
engine=create_engine(
    DATABASE_URL,  
    echo=False,    # echo=True会打印全部执行SQL语句（调试打开；生产关闭False）
    pool_pre_ping=True,  # 连接池健康检查：取出连接前探测连接是否有效，防止MySQL断开闲置连接报错
    pool_recycle=300    # 连接回收时间，单位秒；5分钟强制回收旧连接，避免mysql长时间空闲断开
)


# ----------------------4、会话工厂 SessionLocal ----------------------
# sessionmaker：工厂函数，用来生成数据库会话(Session)
# autocommit=False：关闭自动提交；修改数据必须手动 db.commit() 才写入数据库
# autoflush=False：关闭自动刷新，避免查询时意外触发脏数据刷新
# bind=engine：会话绑定上面创建好的数据库引擎
SessionLocal=sessionmaker(autocommit=False,autoflush=False,bind=engine)

# ----------------------5、FastAPI依赖函数 get_db() 生成会话（生成器yield） ----------------------

def get_db():
    # 调用工厂，创建一个数据库会话，相当于拿到数据库连接
    db=SessionLocal()
    try:
         # yield：把db会话对象交给接口使用；函数暂停在这里，接口处理业务逻辑
        yield db
    finally:  
        # ✨无论接口业务正常执行、还是抛出异常报错，finally块一定会执行！
        # 请求结束后，关闭数据库会话，归还连接回连接池，防止连接泄露占满MySQL
        db.close()