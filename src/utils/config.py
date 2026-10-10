"""作用：全项目只有一个地方读 .env，别的文件都来找它要配置。
统一配置中心
全项目所有配置（数据库/Redis/LLM/日志/重试）都从这里读
好处：改配置只动这一个文件，避免 os.getenv 散落各处
用 pydantic-settings 库实现（requirements 里已有，支持 .env 自动加载和类型校验）
"""

from functools import lru_cache    # lru_cache：缓存装饰器，让配置只创建一次
from pydantic_settings import BaseSettings,SettingsConfigDict # pydantic-settings 的核心类

class Settings(BaseSettings):
    """配置类：每个属性对应.env里的一个配置项，名字必须一致"""

# ---------- 读取 .env 的设置 ----------
    model_config=SettingsConfigDict(
        env_file=".env",            #指定从项目根目录的.env文件读配置
        extra="ignore",             #.env里多出来的配置项不报错，直接忽略
    )

    #---------- MySQL 数据库配置 ----------
    DB_HOST:str="localhost"         # 数据库地址，.env 没写就用默认值 localhost
    DB_PORT:int=3306                # 数据库端口，MySQL 默认 3306
    DB_USER:str="root"              # 数据库用户名
    DB_PASSWORD:str=""              # 数据库密码（敏感信息只放 .env，不写死在代码里）
    DB_NAME: str = "lingxi_procurement" # 数据库名

    # ---------- Redis 配置 ----------
    REDIS_URL: str = "redis://127.0.0.1:6380/0"  # LangGraph Checkpoint 专用地址（6380 是 Redis Stack）

     # ---------- LLM 大模型配置 ----------
    DASHSCOPE_API_KEY: str = ""                       # 百炼 API 密钥，必填（没配会在启动时被发现）
    LLM_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 百炼的 OpenAI 兼容接口地址
    LLM_MODEL_NAME: str = "qwen3.8-max"               # 模型名
    LLM_TIMEOUT: int = 45                             # 单次 LLM 调用超时时间（秒），和 Harness 保持一致

    # ---------- 重试配置 ----------
    RETRY_MAX_ATTEMPTS: int = 3                       # 瞬时失败最多重试几次（含第一次）

    # ---------- 日志配置 ----------
    LOG_LEVEL: str = "INFO"                           # 日志级别：DEBUG/INFO/WARNING/ERROR


@lru_cache                       # 缓存：第一次调用创建 Settings 对象，之后直接返回同一个，不再重复读 .env
def get_settings()->Settings:    # 注意：这个函数必须在类的外面（模块级），不能缩进进类里
    """
    获取全局唯一配置对象
    任何文件需要配置都调用这个函数：from src.utils.config import get_settings
    """
    return Settings()            # 实例化时会自动读 .env 并做类型转换/校验
