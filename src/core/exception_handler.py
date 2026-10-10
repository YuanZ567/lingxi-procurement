"""
统一异常体系
把错误分成两类，决定系统怎么应对：
  retryable=True  → 瞬时错误（网络抖动等）：值得重试，重试失败再降级
  retryable=False → 确定性错误（配置缺失/超预算等）：重试没意义，直接走终止/人工
每个异常都有 error_code，将来 P3 API 层凭编号给前端返回不同错误提示
"""
from src.utils.logger import get_logger  #记录异常日志
logger=get_logger("exception")              #本模块自己的日志器

class LingXiError(Exception):
    """本项目所有自定义异常的基类"""
    error_code:str="LINGXI_ERROR"           #异常编号，子类各自覆盖
    retryable:bool=False                    #是否值得重试，子类各自覆盖

    def __init__(self, message:str):
        # 把 message 交给父类 Exception，这样 str(exc) 就能拿到这句话
        super().__init__(message)
        # 立刻记一条 ERROR 日志：异常在哪发生、编号是什么、说了什么
        logger.error(f"[{self.error_code}]{message}")


class ConfigError(LingXiError):
    """配置错误：如 .env 缺少 API Key。重试不可能变好，不可重试"""
    error_code:str="CONFIG_ERROR"
    retryable:bool=False

class LLMServiceError(LingXiError):
    """LLM 服务错误：如网络超时、限流。通常是瞬时的，值得重试"""
    error_code:str="LLM_SERVICE_ERROR"
    retryable:bool=True

class DatabaseServiceError(LingXiError):
    """数据库错误：如连接抖动。通常是瞬时的，值得重试"""
    error_code:str="DATABASE_SERVICE_ERROR"
    retryable: bool = True

class BudgetViolationError(LingXiError):
    """预算红线违规：业务最高级错误，任何情况下都不允许重试或自动放行"""
    error_code:str = "BUDGET_VIOLATION"
    retryable:bool = False

def classify_exception(exc:Exception)->str:
    """异常分类器：判断一个异常属于哪类，返回分类字符串
    调用方据此决定：继续重试 / 降级 / 终止任务
    """
    if isinstance(exc,LingXiError):         # 是我们自定义的异常
        return "retryable" if exc.retryable else "fatal"    # 看它的 retryable 标记
    if isinstance(exc,(TimeoutError,ConnectionError)):       # 原生的超时/连接类错误（含拒绝/重置/中断）
        return "retryable"                  # 大概率是瞬时抖动，可重试
    return "fatal"                          # 其他未知错误一律按严重处理


def build_error_record(node_name:str,exc:Exception)->dict:
    """
    构造标准化的错误记录（写入 state 的 task_error_records 列表）
    比现在只存 {"node":..., "error":...} 多了错误类型和分类两个字段
    供风控节点提示风险、供自进化（E4）提取失败经验用
    """
    return {
        "node":node_name,                       # 哪个节点出的错
        "error":str(exc),                       # 错误信息原文
        "error_type":type(exc).__name__,            # 异常类名，如 LLMServiceError
        "classification":classify_exception(exc),       # 分类：retryable / fatal
    }
