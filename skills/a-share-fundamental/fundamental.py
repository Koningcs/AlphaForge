from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import json

from akshare_provider import (
    fetch_company_profile,
    fetch_financial_indicators,
    fetch_financial_report,
    fetch_valuation,
    normalize_stock_code,
    pick_value,
)
from analysis import build_analysis_summary
from markdown_renderer import render_analysis_markdown, render_data_quality_markdown, render_fundamental_markdown
from quality_check import check_data_quality


INCOME_FIELDS = {
    "revenue": ["营业收入", "营业总收入", "主营业务收入"],
    "operating_cost": ["营业成本", "主营业务成本"],
    "selling_expense": ["销售费用"],
    "admin_expense": ["管理费用"],
    "rd_expense": ["研发费用"],
    "financial_expense": ["财务费用"],
    "operating_profit": ["营业利润"],
    "net_profit_parent": ["归属于母公司所有者的净利润", "归属于母公司股东的净利润", "归属母公司股东的净利润", "净利润"],
    "deducted_net_profit": ["扣除非经常性损益后的净利润", "扣非归母净利润"],
}

BALANCE_FIELDS = {
    "total_assets": ["资产总计", "总资产"],
    "cash": ["货币资金"],
    "accounts_receivable": ["应收账款", "应收帐款"],
    "inventory": ["存货"],
    "fixed_assets": ["固定资产净额", "固定资产", "固定资产合计"],
    "construction_in_progress": ["在建工程"],
    "goodwill": ["商誉"],
    "short_term_debt": ["短期借款"],
    "long_term_debt": ["长期借款"],
    "total_liabilities": ["负债合计", "总负债"],
    "shareholder_equity": ["归属于母公司股东权益合计", "所有者权益(或股东权益)合计", "股东权益合计", "所有者权益合计"],
}

CASHFLOW_FIELDS = {
    "operating_cashflow": ["经营活动产生的现金流量净额", "经营活动现金流量净额"],
    "investing_cashflow": ["投资活动产生的现金流量净额", "投资活动现金流量净额"],
    "financing_cashflow": ["筹资活动产生的现金流量净额", "筹资活动现金流量净额"],
    "capex": [
        "购建固定资产、无形资产和其他长期资产支付的现金",
        "购建固定资产、无形资产和其他长期资产所支付的现金",
        "资本性支出",
        "资本开支",
    ],
}

INDICATOR_FIELDS = {
    "roe": ["净资产收益率(%)", "加权净资产收益率(%)", "净资产报酬率(%)"],
    "roa": ["总资产净利润率(%)", "总资产利润率(%)", "资产报酬率(%)"],
    "gross_margin": ["销售毛利率(%)", "主营业务利润率(%)"],
    "net_margin": ["销售净利率(%)"],
    "asset_liability_ratio": ["资产负债率(%)"],
    "current_ratio": ["流动比率"],
    "quick_ratio": ["速动比率"],
    "inventory_turnover": ["存货周转率(次)", "存货周转率"],
    "ar_turnover": ["应收账款周转率(次)", "应收帐款周转率(次)", "应收账款周转率"],
}

PERCENT_INDICATORS = {"roe", "roa", "gross_margin", "net_margin", "asset_liability_ratio"}


def get_fundamental(
    stock_code: str,
    years: int = 5,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    code = normalize_stock_code(stock_code)
    years = max(1, int(years))
    warnings: list[str] = []

    profile = fetch_company_profile(code, warnings)
    income_raw = fetch_financial_report(code, "利润表", years, warnings)
    balance_raw = fetch_financial_report(code, "资产负债表", years, warnings)
    cashflow_raw = fetch_financial_report(code, "现金流量表", years, warnings)
    indicator_raw = fetch_financial_indicators(code, years, warnings)
    valuation = fetch_valuation(code, warnings)

    income = normalize_income(income_raw)
    balance = normalize_balance(balance_raw)
    cashflow = normalize_cashflow(cashflow_raw, income)
    indicators = normalize_indicators(indicator_raw, income, balance)

    data = {
        "metadata": {
            "stock_code": code,
            "stock_name": profile.get("stock_name"),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "years": years,
            "data_source": "akshare",
            "warnings": warnings,
        },
        "company_profile": profile,
        "income_statement": income,
        "balance_sheet": balance,
        "cashflow_statement": cashflow,
        "financial_indicators": indicators,
        "valuation": valuation,
        "analysis_summary": {},
        "data_quality": {},
    }
    data["analysis_summary"] = build_analysis_summary(data)
    data["data_quality"] = check_data_quality(data, years)

    project_root = Path(__file__).resolve().parents[2]
    output_root = Path(output_dir) if output_dir else project_root / "outputs"
    stock_output_dir = output_root / code
    stock_output_dir.mkdir(parents=True, exist_ok=True)

    json_path = stock_output_dir / "fundamental.json"
    markdown_path = stock_output_dir / "fundamental.md"
    analysis_path = stock_output_dir / "analysis.md"
    data_quality_path = stock_output_dir / "data_quality.md"

    json_path.write_text(json.dumps(json_safe(data), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_fundamental_markdown(data), encoding="utf-8")
    analysis_path.write_text(render_analysis_markdown(data), encoding="utf-8")
    data_quality_path.write_text(render_data_quality_markdown(data["data_quality"]), encoding="utf-8")

    return {
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "analysis_path": str(analysis_path),
        "data_quality_path": str(data_quality_path),
        "data": data,
    }


def normalize_income(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        item = {"period": row.get("_period")}
        for key, candidates in INCOME_FIELDS.items():
            item[key] = pick_value(row, candidates)
        item["gross_profit"] = safe_sub(item["revenue"], item["operating_cost"])
        item["gross_margin"] = safe_div(item["gross_profit"], item["revenue"])
        item["net_margin"] = safe_div(item["net_profit_parent"], item["revenue"])
        normalized.append(item)
    return normalized


def normalize_balance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for row in rows:
        item = {"period": row.get("_period")}
        for key, candidates in BALANCE_FIELDS.items():
            item[key] = pick_value(row, candidates)
        item["asset_liability_ratio"] = safe_div(item["total_liabilities"], item["total_assets"])
        normalized.append(item)
    return normalized


def normalize_cashflow(
    rows: list[dict[str, Any]],
    income_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    income_by_period = {row.get("period"): row for row in income_rows}
    normalized = []
    for row in rows:
        item = {"period": row.get("_period")}
        for key, candidates in CASHFLOW_FIELDS.items():
            item[key] = pick_value(row, candidates)
        item["free_cashflow"] = safe_sub(item["operating_cashflow"], item["capex"])
        net_profit = (income_by_period.get(item["period"]) or {}).get("net_profit_parent")
        item["operating_cashflow_to_net_profit"] = safe_div(item["operating_cashflow"], net_profit)
        normalized.append(item)
    return normalized


def normalize_indicators(
    rows: list[dict[str, Any]],
    income_rows: list[dict[str, Any]],
    balance_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    income_by_period = {row.get("period"): row for row in income_rows}
    balance_by_period = {row.get("period"): row for row in balance_rows}
    normalized = []

    for row in rows:
        period = row.get("_period")
        item = {"period": period}
        for key, candidates in INDICATOR_FIELDS.items():
            item[key] = pick_value(row, candidates, percent=key in PERCENT_INDICATORS)

        income = income_by_period.get(period, {})
        balance = balance_by_period.get(period, {})
        item["gross_margin"] = coalesce(item.get("gross_margin"), income.get("gross_margin"))
        item["net_margin"] = coalesce(item.get("net_margin"), income.get("net_margin"))
        item["asset_liability_ratio"] = coalesce(item.get("asset_liability_ratio"), balance.get("asset_liability_ratio"))

        revenue = income.get("revenue")
        item["rd_expense_ratio"] = safe_div(income.get("rd_expense"), revenue)
        item["selling_expense_ratio"] = safe_div(income.get("selling_expense"), revenue)
        item["admin_expense_ratio"] = safe_div(income.get("admin_expense"), revenue)
        item["financial_expense_ratio"] = safe_div(income.get("financial_expense"), revenue)
        normalized.append(item)

    if normalized:
        return normalized

    periods = sorted({row.get("period") for row in [*income_rows, *balance_rows] if row.get("period")}, reverse=True)
    for period in periods:
        income = income_by_period.get(period, {})
        balance = balance_by_period.get(period, {})
        revenue = income.get("revenue")
        normalized.append(
            {
                "period": period,
                "roe": None,
                "roa": None,
                "gross_margin": income.get("gross_margin"),
                "net_margin": income.get("net_margin"),
                "asset_liability_ratio": balance.get("asset_liability_ratio"),
                "current_ratio": None,
                "quick_ratio": None,
                "inventory_turnover": None,
                "ar_turnover": None,
                "rd_expense_ratio": safe_div(income.get("rd_expense"), revenue),
                "selling_expense_ratio": safe_div(income.get("selling_expense"), revenue),
                "admin_expense_ratio": safe_div(income.get("admin_expense"), revenue),
                "financial_expense_ratio": safe_div(income.get("financial_expense"), revenue),
            }
        )
    return normalized


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def safe_sub(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        return json_safe(value.item())
    return value
