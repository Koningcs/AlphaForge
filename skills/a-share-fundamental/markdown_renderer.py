from __future__ import annotations

from typing import Any


PROFILE_COLUMNS = [
    ("stock_code", "股票代码"),
    ("stock_name", "股票简称"),
    ("company_name", "公司名称"),
    ("exchange", "上市交易所"),
    ("industry", "所属行业"),
    ("main_business", "主营业务"),
    ("listing_date", "上市日期"),
]

INCOME_COLUMNS = [
    ("period", "报告期"),
    ("revenue", "营业收入"),
    ("operating_cost", "营业成本"),
    ("gross_profit", "毛利"),
    ("gross_margin", "毛利率"),
    ("selling_expense", "销售费用"),
    ("admin_expense", "管理费用"),
    ("rd_expense", "研发费用"),
    ("financial_expense", "财务费用"),
    ("operating_profit", "营业利润"),
    ("net_profit_parent", "归母净利润"),
    ("deducted_net_profit", "扣非归母净利润"),
    ("net_margin", "净利率"),
]

BALANCE_COLUMNS = [
    ("period", "报告期"),
    ("total_assets", "总资产"),
    ("cash", "货币资金"),
    ("accounts_receivable", "应收账款"),
    ("inventory", "存货"),
    ("fixed_assets", "固定资产"),
    ("construction_in_progress", "在建工程"),
    ("goodwill", "商誉"),
    ("short_term_debt", "短期借款"),
    ("long_term_debt", "长期借款"),
    ("total_liabilities", "总负债"),
    ("asset_liability_ratio", "资产负债率"),
    ("shareholder_equity", "股东权益"),
]

CASHFLOW_COLUMNS = [
    ("period", "报告期"),
    ("operating_cashflow", "经营活动现金流量净额"),
    ("investing_cashflow", "投资活动现金流量净额"),
    ("financing_cashflow", "筹资活动现金流量净额"),
    ("capex", "资本开支"),
    ("free_cashflow", "自由现金流"),
    ("operating_cashflow_to_net_profit", "经营现金流/净利润"),
]

INDICATOR_COLUMNS = [
    ("period", "报告期"),
    ("roe", "ROE"),
    ("roa", "ROA"),
    ("gross_margin", "毛利率"),
    ("net_margin", "净利率"),
    ("asset_liability_ratio", "资产负债率"),
    ("current_ratio", "流动比率"),
    ("quick_ratio", "速动比率"),
    ("inventory_turnover", "存货周转率"),
    ("ar_turnover", "应收账款周转率"),
    ("rd_expense_ratio", "研发费用率"),
    ("selling_expense_ratio", "销售费用率"),
    ("admin_expense_ratio", "管理费用率"),
    ("financial_expense_ratio", "财务费用率"),
]

VALUATION_COLUMNS = [
    ("date", "日期"),
    ("close_price", "收盘价"),
    ("market_cap", "总市值"),
    ("float_market_cap", "流通市值"),
    ("pe_ttm", "PE(TTM)"),
    ("pe_static", "PE(静)"),
    ("pb", "PB"),
    ("peg", "PEG"),
    ("pcf", "PCF"),
    ("ps_ttm", "PS(TTM)"),
    ("ev_ebitda", "EV/EBITDA"),
    ("industry_rank", "行业估值排名"),
]

RATIO_FIELDS = {
    "gross_margin",
    "net_margin",
    "asset_liability_ratio",
    "operating_cashflow_to_net_profit",
    "roe",
    "roa",
    "rd_expense_ratio",
    "selling_expense_ratio",
    "admin_expense_ratio",
    "financial_expense_ratio",
}


def render_fundamental_markdown(data: dict[str, Any]) -> str:
    metadata = data["metadata"]
    profile = data["company_profile"]
    analysis = data.get("analysis_summary", {})
    title = f"# {metadata.get('stock_code', '')} {metadata.get('stock_name') or ''} 基本面数据".strip()
    lines = [
        title,
        "",
        f"- 生成时间: {metadata.get('generated_at', '')}",
        f"- 数据来源: {metadata.get('data_source', '')}",
        f"- 年数: {metadata.get('years', '')}",
        "",
        "## 分析摘要",
        "",
        render_overall_summary(analysis),
        "",
        "## 1. 公司概况",
        "",
        render_key_value_table(profile, PROFILE_COLUMNS),
        "",
        "## 2. 利润表",
        "",
        render_table(data.get("income_statement", []), INCOME_COLUMNS),
        "",
        "## 3. 资产负债表",
        "",
        render_table(data.get("balance_sheet", []), BALANCE_COLUMNS),
        "",
        "## 4. 现金流量表",
        "",
        render_table(data.get("cashflow_statement", []), CASHFLOW_COLUMNS),
        "",
        "## 5. 核心财务指标",
        "",
        render_table(data.get("financial_indicators", []), INDICATOR_COLUMNS),
        "",
        "## 6. 估值快照",
        "",
        render_key_value_table(data.get("valuation", {}), VALUATION_COLUMNS),
        "",
        "## 7. 数据质量检查",
        "",
        render_quality_section(data.get("data_quality", {})),
        "",
    ]
    return "\n".join(lines)


def render_analysis_markdown(data: dict[str, Any]) -> str:
    metadata = data["metadata"]
    analysis = data.get("analysis_summary", {})
    title = f"# {metadata.get('stock_code', '')} {metadata.get('stock_name') or ''} 基本面分析".strip()
    lines = [
        title,
        "",
        f"- 生成时间: {metadata.get('generated_at', '')}",
        f"- 数据来源: {metadata.get('data_source', '')}",
        f"- 说明: {analysis.get('note', '规则化摘要仅用于梳理财务数据，不构成投资建议。')}",
        "",
        "## 总览",
        "",
        render_overall_summary(analysis),
        "",
        render_analysis_section("增长能力", analysis.get("growth", {})),
        "",
        render_analysis_section("盈利能力", analysis.get("profitability", {})),
        "",
        render_analysis_section("现金流质量", analysis.get("cashflow_quality", {})),
        "",
        render_analysis_section("资产质量", analysis.get("asset_quality", {})),
        "",
        render_analysis_section("偿债能力", analysis.get("solvency", {})),
        "",
        render_analysis_section("估值", analysis.get("valuation", {})),
        "",
    ]
    return "\n".join(lines)


def render_data_quality_markdown(data_quality: dict[str, list[str]]) -> str:
    return "\n".join(
        [
            "# 数据质量检查",
            "",
            render_quality_section(data_quality),
            "",
        ]
    )


def render_overall_summary(analysis: dict[str, Any]) -> str:
    overall = analysis.get("overall", {}) if analysis else {}
    lines = []
    for title, key in [
        ("优势", "strengths"),
        ("风险", "risks"),
        ("关注项", "watch_items"),
    ]:
        values = overall.get(key) or []
        lines.append(f"### {title}")
        lines.append("")
        if values:
            lines.extend(f"- {value}" for value in values)
        else:
            lines.append("- 无")
        lines.append("")
    return "\n".join(lines).rstrip()


def render_analysis_section(title: str, section: dict[str, Any]) -> str:
    lines = [f"## {title}", ""]
    comments = section.get("comments") or []
    if comments:
        lines.extend(f"- {comment}" for comment in comments)
    else:
        lines.append("- 暂无足够数据生成结论")
    lines.append("")

    for label, key in [
        ("优势", "positives"),
        ("风险", "risks"),
        ("关注项", "watch_items"),
    ]:
        values = section.get(key) or []
        if values:
            lines.append(f"- {label}: " + "；".join(values))
    return "\n".join(lines).rstrip()


def render_key_value_table(row: dict[str, Any], columns: list[tuple[str, str]]) -> str:
    lines = ["| 字段 | 值 |", "| --- | --- |"]
    for key, label in columns:
        lines.append(f"| {escape_cell(label)} | {escape_cell(format_value(row.get(key), key))} |")
    return "\n".join(lines)


def render_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "_无数据_"
    header = "| " + " | ".join(escape_cell(label) for _, label in columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append(
            "| "
            + " | ".join(escape_cell(format_value(row.get(key), key)) for key, _ in columns)
            + " |"
        )
    return "\n".join([header, divider, *body])


def render_quality_section(data_quality: dict[str, list[str]]) -> str:
    lines: list[str] = []
    for title, key in [
        ("缺失字段", "missing_fields"),
        ("异常值", "abnormal_values"),
        ("警告", "warnings"),
    ]:
        values = data_quality.get(key) or []
        lines.append(f"### {title}")
        lines.append("")
        if values:
            lines.extend(f"- {value}" for value in values)
        else:
            lines.append("- 无")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_value(value: Any, key: str) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if key in RATIO_FIELDS:
            return f"{value * 100:.2f}%"
        if abs(value) >= 10000:
            return f"{value:,.2f}"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def escape_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")
