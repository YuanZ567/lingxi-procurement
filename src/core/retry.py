"""
作用：网络抖动这类"瞬时错误"不该一次失败就放弃。用 tenacity 库（requirements 已有）实现指数退避重试。
统一重试模块
基于 tenacity 库实现"指数退避"重试：第1次失败等1秒、第2次等2秒、第3次等4秒……
适用于：LLM 网络调用、MySQL 连接等瞬时性失败
不适用于：业务性失败（如超预算），那类错误重试多少次结果都一样
"""

import logging                  # 只是为了给 before_sleep_log 传级别常量

from tenacity import(           # tenacity：Python 最常用的重试库
    retry,                      # 核心重试装饰器
    stop_after_attempt,         # 停止条件：尝试 N 次后放弃
    wait_exponential,           # 等待策略：指数退避（1s→2s→4s…）
    before_sleep_log,           # 每次重试前自动打一条日志
)
from src.utils.config import get_settings       # 重试次数从配置中心读
from src.utils.logger import get_logger               # 重试日志用统一 logger

logger = get_logger("retry")                          # 本模块自己的日志器

def async_retry(operation_name:str):
    """异步函数重试装饰器工厂
    用法：
        @async_retry("LLM调用")
        async def call_llm(...):
            ...
    参数 operation_name 只是为了日志里能看出"谁在重试"
    """
    #先把配置读出来（重试次数)
    settings=get_settings()

    def decorator(func):
         # tenacity 的 @retry 同样支持 async 函数
        @retry(
            stop=stop_after_attempt(settings.RETRY_MAX_ATTEMPTS),# 最多尝试 3 次（含首次）
            wait=wait_exponential(multiplier=1,min=1,max=10),       # 等待 1s/2s/4s…封顶 10s
            before_sleep=before_sleep_log(logger,logging.WARNING),# 每次重试前打 WARNING 日志
            reraise=True,                   # 重试用完还失败：抛出原始异常（不吞）
        )
        async def wrapper(*args,**kwargs):          
            logger.info(f"[{operation_name}]开始执行")          # 记录每次执行入口
            return await func(*args,**kwargs)                   # 执行真正的函数并返回结果

        return wrapper               # 返回包好重试能力的新函数
    return decorator                    # 返回装饰器本身