"""
tests/conftest.py —— pytest 全局夹具（测试工程的地基）
====================================================================
三件事：
  ① 把项目根目录塞进 sys.path，保证 `from src.xxx import yyy` 在任何目录跑都能找到
  ② 提供分层夹具：db（数据库会话）、client（FastAPI 测试客户端）、mock_llm（假 LLM）
  ③ 安全兜底：非 e2e 用例一律禁止真调 LLM——防止单测偷偷联网烧 token、也保证结果确定
"""
import sys                                     # 动态改模块搜索路径
from pathlib import Path                        # 路径处理

import pytest                                   # pytest 本体

# ---------- ① 项目根目录入 path（tests/ 的上级就是项目根）----------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 加载 .env（LLM key、Redis 地址等），加载失败不致命（单测不需要）
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass


# ---------- ② 分层夹具 ----------

@pytest.fixture(scope="session")
def project_root() -> Path:
    """项目根目录路径（读种子数据、源码文件用）"""
    return ROOT


@pytest.fixture
def db():
    """数据库会话夹具：连不上就 skip，不把测试染红（CI 没库时优雅跳过）"""
    from sqlalchemy import text
    from src.db.session import SessionLocal

    session = SessionLocal()
    try:
        session.execute(text("SELECT 1"))       # 探活：能查 1 说明库活着
    except Exception as e:                      # MySQL 没起 / 库名不对
        session.close()
        pytest.skip(f"MySQL 不可用，跳过：{str(e)[:80]}")
    try:
        yield session
    finally:
        session.close()                         # 用完必关，防止连接泄漏


@pytest.fixture
def client():
    """FastAPI 测试客户端：不真起 uvicorn，进程内直接打接口（快且稳定）"""
    from fastapi.testclient import TestClient
    from src.api.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_llm(monkeypatch):
    """
    把 LLM 入口换成假实现：返回预设结果，零延迟、零费用、结果确定。
    用法：mock_llm({"sql": "SELECT * FROM supplier LIMIT 5"})
    说明：nl2sql 里是 `from src.utils.llm import ask_json`（模块级 import），
          所以除了 src.utils.llm 本体，还要把引用方 src.rag.nl2sql 也打补丁。
    """
    def _install(return_value):
        async def fake_ask_json(system_prompt: str, user_prompt: str):
            return return_value                 # 固定返回，绝不定网络

        monkeypatch.setattr("src.utils.llm.ask_json", fake_ask_json)
        try:
            monkeypatch.setattr("src.rag.nl2sql.ask_json", fake_ask_json)
        except AttributeError:                  # 该模块没导入进来就算了
            pass
        return fake_ask_json

    return _install


@pytest.fixture
def offline(monkeypatch):
    """
    模拟"断网"：让行情实时抓取直接抛异常，用于验证降级路径真的能兜住。
    """
    def _boom(*args, **kwargs):
        raise RuntimeError("模拟断网：实时行情接口不可达")

    monkeypatch.setattr("src.data.market_provider.fetch_realtime_price", _boom)
    monkeypatch.setattr("src.data.market_provider._get_redis", lambda: None)   # 同时禁掉缓存命中
    return None


# ---------- ③ 安全兜底：非 e2e 用例禁止真调 LLM ----------

@pytest.fixture(autouse=True)
def _work_from_project_root(monkeypatch):
    """
    统一把工作目录切到项目根：项目里不少模块用相对路径读数据
    （如 chroma_db/chunks.jsonl、seed_data/*.csv），不切目录会随机 FileNotFoundError。
    """
    monkeypatch.chdir(ROOT)


@pytest.fixture(autouse=True)
def _block_real_llm(request, monkeypatch):
    """
    所有用例默认拦截真实 LLM 调用，只有打了 @pytest.mark.e2e 的才放行。
    目的：单测必须"确定性 + 零成本"，任何偷偷联网的调用都会被这条拦下报错。
    """
    if "e2e" in request.keywords:               # e2e 允许真跑 LLM
        return

    async def boom(*args, **kwargs):
        raise RuntimeError(
            "测试环境禁止调用真实 LLM（单测/集成测请 mock_llm；确需真跑请打 @pytest.mark.e2e）"
        )

    for target in ("src.utils.llm.ask_json", "src.utils.llm._call_llm", "src.rag.nl2sql.ask_json"):
        try:
            monkeypatch.setattr(target, boom)
        except (AttributeError, ImportError):   # 目标不存在就跳过，别把收集阶段搞崩
            pass
