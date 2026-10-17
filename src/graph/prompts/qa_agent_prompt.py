"""
qa_prompt.py —— 问答子图的全部提示词（分诊台 / NL2SQL / 答题人）
"""

# ===== 分诊台：判断问题走哪条路 =====
QUERY_ANALYZE_SYSTEM="""你是采购智能问答系统的分诊台，判断用户问题该走哪条查询路线。
两条路线的区别：
-rag:查"制度/规则/流程/办法"类知识——问"应该怎么做""有什么规定"走这条路
- nl2sql：查"业务数据"——问"有多少、哪些、多少钱、什么时候"这类要看具体数字和记录的走这条路
拿不准的默认走 rag。
只输出JSON：{"route": "rag" 或 "nl2sql"}
"""

# ===== NL2SQL：把问题翻译成 SQL 的规矩（和 nl2sql.py 配套）=====
NL2SQL_SYSTEM = """你是 MySQL 专家，把用户问题翻译成一条 SELECT 查询。
硬性规则：
1. 只能生成 SELECT，绝对禁止 INSERT/UPDATE/DELETE/DROP 等任何修改数据的语句
2. 只使用给出的表和列，禁止编造不存在的表名列名
3. 字符串值必须用单引号包裹
4. 只输出JSON：{"sql": "SELECT ..."}"""

# ===== 答题人：融合结果生成回答 =====
ANSWER_SYSTEM = """你是采购智能问答助手，根据提供的资料回答用户问题。
硬性规则：
1. 只依据【参考资料】回答，资料里没有的就直说"资料中未涉及"，绝不编造
2. 数字必须与资料完全一致，严禁自行计算或修改；金额保持资料原单位（如"12,000万人民币"），
   禁止把"万"换算成"亿"或做任何单位换算——单位换算由系统统一处理
3. 回答控制在100字以内，通俗口语化
4. 如果资料里有数据库查询结果，不要逐条复述数据、不要罗列订单号——
   只用1~2句话总结整体情况（共多少条、什么物料、金额量级、时间跨度），
   明细数据系统会自动以表格展示，你不用重复。
只输出JSON：{"answer": "你的回答", "used": ["你实际用到的资料编号，如1、2"]}"""

def build_answer_user_prompt(question: str, rag_chunks: list, sql_result: dict) -> str:
    """
    拼答题材料：把 RAG 片段和/或 SQL 查询结果编上号喂给 LLM
    编号是为了让"used"能指明用了哪几条（溯源的依据）
    SQL 结果用 render_rows_table 渲染成整齐表格——比 dict 原文可读，LLM 总结更准
    """
    from src.utils.formatter import render_rows_table   # 局部导入避免循环依赖

    blocks = []                                    # 存资料块文本
    n = 0                                          # 资料编号计数器
    for c in (rag_chunks or []):                   # RAG 路的片段（可能为空）
        n += 1                                     # 编号+1
        blocks.append(f"【资料{n}】(来源:{c['source']})\n{c['text']}")  # 带来源的片段
    if sql_result and sql_result.get("rows"):      # NL2SQL 路的查询结果（可能为空）
        n += 1                                     # 编号+1
        table = render_rows_table(sql_result["rows"])   # 程序渲染成 Markdown 表格
        blocks.append(f"【资料{n}】(来源:数据库查询 SQL: {sql_result.get('sql','')})\n{table}")
    if not blocks:                                 # 两路都空手而归
        blocks.append("【资料】(无)——没有检索到任何相关资料")          # 如实告知 LLM
    material = "\n\n".join(blocks)                 # 拼成完整材料
    return f"用户问题：{question}\n\n【参考资料】\n{material}"            # 交出答题材料


# ============================================================
# 以下为 v2 Agent 化新增（2026-10 追加）：调度脑提示词 + Agent 材料拼接
# ============================================================

# ===== Agent 调度脑：决定调工具 / 直接答 / 反问 =====
QA_PLANNER_SYSTEM = """你是采购智能问答助手的调度脑（Agent），每一步只决定"下一步做什么"。
可用的工具（每步最多选一个）：
1. search_policy —— 查内部采购制度/规则/流程文档。参数：{"query": "检索词"}
2. query_database —— 查业务数据库（供应商/历史订单/库存/采购任务/价格明细）。参数：{"question": "一句自然语言数据问题"}
3. get_market_price —— 查某物料最新市场行情价与近期走势。参数：{"commodity": "物料名，如螺纹钢"}

决策规则（按顺序判断）：
1. 制度类问题必须查证：凡"怎么办/有什么规定/流程是什么"类问题，必须先调 search_policy 查证后再答，不许凭记忆作答
2. 缺关键参数才许反问：问题缺物料名/时间范围等关键信息、且对话历史里也找不到时，才允许反问（action=clarify）；
   能从对话历史里推出来的信息必须自己推，绝不偷懒反问
3. 信息不够就调工具：当前观察还不足以回答 → 选最合适的一个工具（action=tool）
4. 同一工具同样参数绝不重复调用；已经拿到足够材料 → 立即作答（action=answer）
只输出JSON，三选一：
{"action":"tool","tool":"工具名","args":{...}}
{"action":"answer"}
{"action":"clarify","question":"要反问用户的一句话"}"""


def build_planner_user_prompt(question: str, history: list, observations: list) -> str:
    """拼调度脑的"阅卷材料"：用户问题 + 最近对话历史 + 本轮已有观察摘要"""
    lines = [f"用户问题：{question}"]                 # 第一行永远是本轮问题
    if history:                                      # 有历史就带上（多轮指代全靠它：如"那铝材呢"）
        lines.append("\n【对话历史（最近在前）】")
        for h in history[:6]:                        # 最多带3轮6条，防提示词膨胀
            who = "用户" if h.get("role") == "user" else "助手"
            lines.append(f"{who}：{h.get('text', '')[:80]}")
    if observations:                                 # 有观察就逐条报 note——调度脑判断"够不够"的依据
        lines.append("\n【本轮已有观察】")
        for i, obs in enumerate(observations, 1):
            lines.append(f"{i}. [{obs.get('tool')}] {obs.get('note', '')}")
    else:
        lines.append("\n【本轮已有观察】（暂无，还没有调用任何工具）")
    lines.append("\n请决定下一步，只输出JSON。")
    return "\n".join(lines)


def build_agent_answer_user_prompt(question: str, observations: list, history: list) -> str:
    """拼答题材料：把观察里的结构化数据编号成"资料块"喂给答题人（SQL结果由程序渲染成表格防幻觉）"""
    from src.utils.formatter import render_rows_table   # 局部导入避免循环依赖（项目惯例）

    blocks = []                                      # 存资料块文本
    n = 0                                            # 资料编号计数器
    for obs in observations or []:                   # 逐条观察拆成资料块
        data = obs.get("data") or {}
        if obs.get("tool") == "search_policy":       # 制度片段：每条一块，带来源
            for c in data.get("chunks") or []:
                n += 1
                blocks.append(f"【资料{n}】(来源:{c['source']})\n{c['text']}")
        elif obs.get("tool") == "query_database":    # SQL结果：程序渲染表格，数字不经LLM的手
            if data.get("rows"):
                n += 1
                table = render_rows_table(data["rows"])
                blocks.append(f"【资料{n}】(来源:数据库查询 SQL: {data.get('sql', '')})\n{table}")
        elif obs.get("tool") == "get_market_price":  # 行情：最新价+近10天明细，一行一天
            if data.get("latest") is not None:
                n += 1
                lines = [f"{data.get('commodity')}最新价 {data['latest']} 元，近期走势{data.get('trend', '未知')}，数据来源：{data.get('source', '')}"]
                for p in (data.get("series") or []):
                    lines.append(f"{p['date']} 收盘 {p['close']} 元")
                blocks.append(f"【资料{n}】(来源:实时行情)\n" + "\n".join(lines))
    if not blocks:                                   # 一轮下来什么都没查到：如实告知
        blocks.append("【资料】(无)——本轮没有检索到任何可用资料")
    material = "\n\n".join(blocks)

    history_text = ""                                # 历史段：有才拼，让答题人能接住追问
    if history:
        recent = history[-6:]                        # 只看最近3轮
        history_text = "【对话历史】\n" + "\n".join(
            f"{'用户' if h.get('role') == 'user' else '助手'}：{h.get('text', '')[:80]}"
            for h in recent) + "\n\n"
    return f"{history_text}用户问题：{question}\n\n【参考资料】\n{material}"

