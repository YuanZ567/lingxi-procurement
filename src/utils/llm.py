# src/llm/llm_client.py
"""
大模型统一客户端
对接阿里云百炼通义大模型，所有Agent统一调用这个实例
"""
import os
import json
import re
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from src.core.retry import async_retry                 # 统一重试装饰器（指数退避）
from src.utils.logger import get_logger                # 统一日志（降级时留痕）

#读取项目根目录.env环境变量文件
load_dotenv()

_llm_instance=None  #模块级单例，避免每个节点都新建客户端
_fast_llm_instance=None  # 快速模型单例（轻任务专用：分诊/总结等，快且省）


def get_llm(fast: bool = False):
    """
    获取LLM单例对象
    fast=False：主模型（.env 的 LLM_MODEL_NAME，重任务：SQL生成/谈判/方案）
    fast=True ：快速模型（LLM_FAST_MODEL_NAME，默认 qwen-turbo，轻任务：分诊/回答总结）
    返回 langchain ChatModel，可以直接给 LangGraph 节点 await ainvoke
    """
    global _llm_instance, _fast_llm_instance
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY，请检查 .env 配置")

    if fast:  # ---------- 快速模型通道 ----------
        if _fast_llm_instance is None:
            model_name = os.getenv("LLM_FAST_MODEL_NAME", "qwen-turbo")
            _fast_llm_instance = ChatOpenAI(
                api_key=api_key,
                base_url=os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                model=model_name,
                temperature=0.2,     # 业务决策场景，低温度减少幻觉
                max_tokens=1024,     # 轻任务输出短，512 不够写总结，1024 稳妥
                streaming=False,
                timeout=30,          # 快模型给更短超时，卡住尽快降级
            )
        return _fast_llm_instance

    # ---------- 主模型通道 ----------
    if _llm_instance is None:
        model_name = os.getenv("LLM_MODEL_NAME", "qwen3.8-max")

        _llm_instance = ChatOpenAI(
            api_key=api_key,
            base_url=os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            model=model_name,
            temperature=0.2,     # 业务决策场景，低温度减少幻觉
            max_tokens=2048,     # 单次输出最大 token
            streaming=False,     # MVP 先关闭流式，问答子系统再开
            timeout=45,          # 与 Harness 超时口径保持一致
        )
    return _llm_instance


def extract_json(text: str):
    """
    从 LLM 返回文本里抠出 JSON 对象
    模型偶尔会用 ```json ``` 围栏包裹、或前后带说明文字，这里统一兜底
    解析失败返回 None，由调用方走降级逻辑
    """
    if not text:
        return None
    text = text.strip()

    # 第一优先：剥掉 markdown 代码围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 第二优先：截取第一个 { 到最后一个 } 之间再试
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


_logger = get_logger("llm")                            # llm 模块自己的日志器


@async_retry("LLM调用")                                # 挂上重试：瞬时失败自动重试（默认3次）
async def _call_llm(system_prompt: str, user_prompt: str, fast: bool = False):
    """
    真正发起 LLM 调用的底层函数（被 ask_json 包着用）
    单独拆出来是为了只对"网络调用"这一段加重试，JSON解析失败不该触发重试
    fast=True 时走快速模型（分诊/总结等轻任务）
    """
    llm = get_llm(fast=fast)                           # 按任务轻重选模型
    resp = await llm.ainvoke([                         # 异步调用大模型
        ("system", system_prompt),                     # 系统提示词
        ("human", user_prompt),                        # 用户提示词
    ])
    return extract_json(resp.content)                  # 解析成 JSON 返回（失败返回 None，不抛异常）


async def ask_json(system_prompt: str, user_prompt: str, fast: bool = False):
    """
    Agent 节点统一调用入口：system+user → 调LLM → 解析JSON
    fast=True 走快速模型（轻任务提速降费）；与之前的行为差异：失败前会先自动重试；最终降级时会记 WARNING 日志（不再静默失败）
    """
    try:
        return await _call_llm(system_prompt, user_prompt, fast=fast)   # 带重试的真正调用
    except Exception as e:                             # 重试全部失败才会到这里
        # 记降级日志：异常类型+原因，事后可在 logs/lingxi.log 追溯
        _logger.warning(f"LLM调用最终失败，降级为模板兜底 | {type(e).__name__}: {e}")
        return None                                    # 返回 None，节点自动走模板文本