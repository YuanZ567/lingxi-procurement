"""
qa_tools.py —— 问答 Agent 的工具箱
====================================================================
大白话：Agent 光会想不行，还得有手。这里给 planner 备了三只手：
  search_policy     查制度（内部规章/流程文档，走混合检索+自动补检）
  query_database    查业务库（供应商/订单/库存/任务，走 NL2SQL，三道安全闸原样保留）
  get_market_price  查行情（实时刷新+近30个交易日走势，直连 market_provider，不绕 HTTP）
统一约定——每个工具返回一条"观察"：
  {"tool": 工具名, "args": 当时传入的参数, "note": 给调度脑看的一句话摘要, "data": 给答题人的结构化数据}
note 是关键：调度脑下一轮就靠读这些 note 判断"够不够、还缺啥"，所以 note 必须信息量足。
"""

from sqlalchemy import desc                          # 行情查询要按日期倒序取
from src.rag.agentic_retrieval import agentic_hybrid_search   # 制度混合检索（带自动补检）
from src.rag.nl2sql import run_nl2sql                # NL2SQL 总入口（内部自带三道安全闸）
from src.data.market_provider import backfill_history, refresh_market_price  # 行情回填+实时刷新
from src.db.session import SessionLocal              # 数据库会话
from src.db.models import CommodityPrice             # 行情表模型
from src.utils.logger import get_logger              # 统一日志

logger = get_logger("qa_tools")                      # 工具箱自己的日志器


# ---------- 工具1：查制度 ----------
async def tool_search_policy(args: dict) -> dict:
    """把检索词交给混合检索器，命中片段截短后装进观察返回"""
    query = (args or {}).get("query") or ""          # 取参数里的检索词；没传就空串
    chunks = await agentic_hybrid_search(query, top_k=4)   # 检索（内部：第一轮→自判断→不合格改写补检）
    data = [                                         # 只留答题需要的字段，片段截到200字防止状态爆炸
        {"text": c["text"][:200], "source": c["source"], "score": c.get("rrf_score", 0)}
        for c in chunks
    ]
    note = (                                         # 摘要：命中几条+最高分，调度脑靠它判断够不够
        f"检索到{len(data)}条制度片段，最高相关分{data[0]['score']:.3f}" if data
        else "制度库未命中任何内容，可能换了说法或该问题与制度无关"
    )
    return {"tool": "search_policy", "args": args, "note": note, "data": {"chunks": data}}


# ---------- 工具2：查业务库 ----------
async def tool_query_database(args: dict) -> dict:
    """自然语言问题交给 NL2SQL（翻译SQL→安全校验→只读执行），结果原样装进观察"""
    question = (args or {}).get("question") or ""    # 取参数里的一句数据问题
    result = await run_nl2sql(question)              # 内部已带三道安全闸和友好降级
    if "rows" in result:                             # 查成功：rows 在，sql 是查询凭证
        note = f"查询成功：返回{len(result['rows'])}行，SQL为 {result.get('sql', '')[:60]}"
    else:                                            # 查失败：error 里是友好原因（安全闸拦截也算这种）
        note = f"查询失败：{result.get('error', '未知原因')}"
    return {"tool": "query_database", "args": args, "note": note, "data": result}


# ---------- 工具3：查行情 ----------
async def tool_get_market_price(args: dict) -> dict:
    """刷新指定物料的行情并返回最新价+近期走势（流程与 /api/market 接口完全一致）"""
    commodity = (args or {}).get("commodity") or ""  # 取物料名，如"螺纹钢"
    if not commodity:                                # 物料名都没给：如实告诉调度脑缺参数
        return {"tool": "get_market_price", "args": args,
                "note": "缺少物料名参数，无法查询行情", "data": {}}

    backfill_history(commodity)                      # 先补历史缺口（幂等，没缺口零开销）
    source = refresh_market_price(commodity)         # 再刷新当天价（缓存/实时/降级，内部自带容错）
    with SessionLocal() as db:                       # 开会话查近30个交易日
        rows = (db.query(CommodityPrice)
                  .filter(CommodityPrice.commodity_name == commodity)   # 只取该品种
                  .order_by(desc(CommodityPrice.record_date))           # 最新在前
                  .limit(30).all())
    rows.reverse()                                   # 翻成"从旧到新"，算走势顺手

    if not rows:                                     # 库里压根没这物料：如实报告
        return {"tool": "get_market_price", "args": args,
                "note": f"库中没有「{commodity}」的行情数据", "data": {}}

    series = [{"date": str(r.record_date), "close": float(r.price)} for r in rows]
    latest = series[-1]["close"]                     # 最新收盘价
    prev = series[-2]["close"] if len(series) >= 2 else None   # 上一交易日价（算走势用）
    trend = ("上涨" if latest > prev else "下跌" if latest < prev else "平稳") if prev is not None else "未知"

    return {"tool": "get_market_price", "args": args,
            "note": f"「{commodity}」最新价{latest}元，近期走势{trend}（数据来源：{source}）",
            "data": {"commodity": commodity, "source": source,
                     "latest": latest, "trend": trend,
                     "series": series[-10:]}}        # 只留最近10天给答题人看，防止状态膨胀


# ---------- 注册表 + 统一入口 ----------
TOOL_REGISTRY = {                                    # 工具名 → 函数 的映射（planner 只许选这三个名字）
    "search_policy": tool_search_policy,
    "query_database": tool_query_database,
    "get_market_price": tool_get_market_price,
}


async def run_tool(name: str, args: dict) -> dict:
    """工具统一执行入口：查注册表→执行→任何异常都转成'失败观察'，绝不让工具异常炸掉整张图"""
    fn = TOOL_REGISTRY.get(name)                     # 按名字找工具
    if fn is None:                                   # 调度脑报了个不存在的工具名
        return {"tool": name, "args": args, "note": f"工具{name}不存在", "data": {}}
    try:                                             # 正常执行
        return await fn(args or {})
    except Exception as e:                           # 执行炸了：转成失败观察让调度脑自己看着办
        logger.warning(f"[qa_tools] 工具{name}执行异常：{e}")
        return {"tool": name, "args": args, "note": f"工具执行出错：{e}", "data": {}}


def build_sources_from_observations(observations: list) -> list:
    """从观察列表程序化生成溯源（不依赖 LLM，保证溯源永远准确——和 v1 同一设计哲学）"""
    sources = []                                     # 存溯源条目
    for obs in observations or []:                   # 逐条观察看是什么工具
        data = obs.get("data") or {}
        if obs.get("tool") == "search_policy":       # 制度路：每个命中片段都是一条溯源
            for c in data.get("chunks") or []:
                sources.append(f"来自《{c['source'].replace('.md', '')}》")
        elif obs.get("tool") == "query_database":    # 数据路：SQL 本身就是查询凭证
            if data.get("sql"):
                sources.append(f"来自数据库查询（{data['sql'][:60]}...）")
        elif obs.get("tool") == "get_market_price":  # 行情路：注明品种和数据来源
            if data.get("commodity"):
                sources.append(f"来自实时行情（{data['commodity']}，来源：{data.get('source', '')}）")
    return sources                                   # 交给 answer 节点写回状态
