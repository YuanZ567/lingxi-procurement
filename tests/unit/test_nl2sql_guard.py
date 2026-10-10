"""
tests/unit/test_nl2sql_guard.py —— NL2SQL 三道安全闸单元测试
====================================================================
被测对象：src/rag/nl2sql.py（generate_sql / run_nl2sql）
为什么重要：NL2SQL 是"让 LLM 写 SQL 直连生产库"，是整套系统风险最高的口子。
            一旦被提示词注入带出 DELETE，就是删库事故。所以安全闸必须有对抗性用例。
手段：mock LLM 直接喂"恶意 SQL"，验证程序侧（不是 LLM 侧）能不能兜住。
"""
import pytest

from src.rag import nl2sql


# ---------- 安全闸①：只认 SELECT ----------

@pytest.mark.unit
async def test_reject_non_select(mock_llm):
    """写操作语句必须被拒——哪怕 LLM 被诱导生成了 UPDATE"""
    mock_llm({"sql": "UPDATE supplier SET credit_status='A'"})
    with pytest.raises(ValueError, match="只允许SELECT"):
        await nl2sql.generate_sql("把所有供应商改成A级")


# ---------- 安全闸②：写操作词黑名单（防分号拼接注入）----------

@pytest.mark.unit
@pytest.mark.parametrize("evil_sql", [
    "SELECT * FROM supplier; DROP TABLE supplier",
    "SELECT * FROM supplier WHERE name='x' OR 1=1 -- '; DELETE FROM supplier",
    "SELECT * FROM supplier; TRUNCATE history_order",
    "SELECT * FROM supplier; ALTER TABLE supplier ADD COLUMN x INT",
])
async def test_reject_forbidden_words(mock_llm, evil_sql):
    """提示词注入 / 分号拼接 / DDL 语句，一律拦在程序侧"""
    mock_llm({"sql": evil_sql})
    with pytest.raises(ValueError) as exc:
        await nl2sql.generate_sql("查供应商")
    msg = str(exc.value)
    assert "写操作词" in msg or "只允许SELECT" in msg, f"未拦截危险 SQL：{evil_sql}"


@pytest.mark.unit
async def test_union_query_is_known_gap(mock_llm):
    """
    已知边界（诚实记录，不是 bug 掩饰）：
    纯 UNION SELECT 跨表探测当前是放行的——它以 SELECT 开头、不含写操作词，三道闸拦不住。
    缓解措施：给 LLM 的 SCHEMA_TEXT 只暴露 5 张业务表，且只读库账号原则上只授 SELECT。
    后续加固方向：SQL 解析白名单表（只允许 SCHEMA_TEXT 里声明的表名出现）。
    """
    mock_llm({"sql": "SELECT * FROM supplier UNION SELECT user FROM mysql.user"})
    sql = await nl2sql.generate_sql("查供应商")
    assert sql.upper().startswith("SELECT")      # 现状：放行
    assert "LIMIT 20" in sql.upper()             # 但至少被强制限量，拖不垮库


# ---------- 安全闸③：强制限量 ----------

@pytest.mark.unit
async def test_auto_append_limit(mock_llm):
    """没写 LIMIT 的查询自动补 LIMIT 20，防止一次拖垮数据库"""
    mock_llm({"sql": "SELECT order_no, total_amount FROM history_order"})
    sql = await nl2sql.generate_sql("查历史订单")
    assert sql.upper().endswith("LIMIT 20")


@pytest.mark.unit
async def test_keep_existing_limit(mock_llm):
    """用户/LLM 已带 LIMIT 时不重复追加"""
    mock_llm({"sql": "SELECT order_no FROM history_order LIMIT 5"})
    sql = await nl2sql.generate_sql("查最近5条订单")
    assert sql.upper().count("LIMIT") == 1
    assert "LIMIT 5" in sql.upper()


# ---------- 正常路径 ----------

@pytest.mark.unit
async def test_happy_path(mock_llm):
    """合法只读查询原样放行，结尾分号被统一剥掉（语句结束由程序控制）"""
    mock_llm({"sql": "SELECT order_no FROM history_order LIMIT 10;"})
    sql = await nl2sql.generate_sql("查订单号")
    assert sql == "SELECT order_no FROM history_order LIMIT 10"
    assert not sql.endswith(";")


# ---------- 异常与降级 ----------

@pytest.mark.unit
async def test_llm_return_no_sql(mock_llm):
    """LLM 没给出 sql 字段 → 明确抛错，不能带着空 SQL 去查库"""
    mock_llm({})
    with pytest.raises(ValueError, match="未能生成SQL"):
        await nl2sql.generate_sql("随便问点啥")


@pytest.mark.unit
async def test_run_nl2sql_never_raise(mock_llm):
    """对外总入口契约：任何失败都转成 {'error': ...}，绝不把异常抛给上层炸掉问答子图"""
    mock_llm({"sql": "DROP TABLE supplier"})
    result = await nl2sql.run_nl2sql("删库跑路")
    assert "error" in result
    assert "无法转换为安全的查询" in result["error"]


@pytest.mark.unit
async def test_run_nl2sql_returns_sql_for_traceability(mock_llm, monkeypatch):
    """成功时把 SQL 一并返回——这是前端"溯源卡片"的凭证（防幻觉的关键留痕）"""
    monkeypatch.setattr(nl2sql, "run_sql", lambda sql: [{"order_no": "SO20250101001"}])
    mock_llm({"sql": "SELECT order_no FROM history_order LIMIT 1"})
    result = await nl2sql.run_nl2sql("查一笔订单")
    assert result["rows"][0]["order_no"] == "SO20250101001"
    assert "SELECT" in result["sql"].upper()
