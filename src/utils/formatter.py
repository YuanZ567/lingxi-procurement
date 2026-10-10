"""
formatter.py —— 查询结果的程序化渲染（问答溯源表格用）
====================================================================
为什么需要它：LLM 复述数据又慢又会走样（挑三四个字段念流水账），
             结构化数据应该由程序渲染成表格——数字零幻觉、格式永远整齐；
             LLM 只负责写总结句。SQL 查询结果和最终回答共用这一套渲染。
"""

import re

from decimal import Decimal
from datetime import date, datetime

# 常见列名 → 中文展示名（没映射到的列名原样保留，通用兜底）
COLUMN_LABELS = {
    "order_no": "订单号", "commodity_name": "物料", "commodity": "物料",
    "material_name": "物料", "supplier_id": "供应商ID", "supplier_name": "供应商",
    "order_num": "数量", "purchase_quantity": "数量", "unit_price": "单价(元)",
    "price": "价格(元)", "total_amount": "总金额(元)", "order_date": "日期",
    "record_date": "日期", "credit_status": "信用等级", "premium_rate": "溢价系数",
    "budget_limit": "预算(元)", "task_status": "状态", "task_name": "任务名",
    "stock_quantity": "库存量", "create_time": "创建时间", "reg_status": "登记状态",
    "province": "省份", "main_category": "主营品类",
    # ---- 供应商真实工商档案字段（白牛商查口径）----
    "credit_code": "统一社会信用代码", "legal_rep": "法定代表人",
    "reg_capital": "注册资本", "found_date": "成立日期",
    "industry": "国标行业", "address": "注册地址", "city": "城市",
}


def _format_value(col: str, value) -> str:
    """把单元格值转成适合展示的字符串"""
    if value is None:
        return "-"
    if isinstance(value, Decimal):                      # 金额：千分位 + 两位小数
        return f"{float(value):,.2f}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".")  # 浮点去尾零
    if isinstance(value, (date, datetime)):
        return str(value)[:10]                          # 日期只留年月日
    text = str(value)
    if col == "order_no" and len(text) > 12:            # 长订单号截成"ERP-…后6位"
        return text[:4] + "…" + text[-6:]
    if col == "reg_capital":                            # 注册资本清洗：'12000.000000万人民币'→'12,000万人民币'
        m = re.match(r"^([\d,]+(?:\.\d+)?)(.*)$", text)  # 数字部分 + 币种后缀
        if m:
            num = float(m.group(1).replace(",", ""))
            num_text = f"{num:,.0f}" if num == int(num) else f"{num:,.2f}".rstrip("0").rstrip(".")
            return num_text + m.group(2)
    return text


def render_rows_table(rows: list, max_rows: int = 10) -> str:
    """
    把 SQL 查询结果（字典列表）渲染成 Markdown 表格。
    单行长表（≥6列）自动转纵向键值卡——聊天窗里横表 14 列会挤成一条线。
    返回空字符串表示没有可渲染的数据。最多展示 max_rows 行，超出时附提示。
    """
    if not rows:
        return ""
    # 列取第一行的键并按映射中文名展示（保持查询返回的列顺序）
    cols = list(rows[0].keys())
    headers = [COLUMN_LABELS.get(c, c) for c in cols]

    # 单行 + 多列 → 纵向键值卡（详情查询场景：查某供应商/某任务的档案）
    if len(rows) == 1 and len(cols) >= 6:
        lines = ["| 字段 | 内容 |", "| --- | --- |"]
        for c in cols:
            if c == "id":                      # 主键对用户无意义，详情卡里省掉
                continue
            lines.append(f"| {COLUMN_LABELS.get(c, c)} | {_format_value(c, rows[0].get(c))} |")
        return "\n".join(lines)

    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows[:max_rows]:                           # 最多展示前 N 行
        lines.append("| " + " | ".join(_format_value(c, r.get(c)) for c in cols) + " |")

    table = "\n".join(lines)
    if len(rows) > max_rows:                            # 截断提示（诚实展示，不假装只有这些）
        table += f"\n（共 {len(rows)} 条，仅展示前 {max_rows} 条）"
    return table
