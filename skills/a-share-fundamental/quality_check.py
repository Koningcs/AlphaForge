from __future__ import annotations

from typing import Any


REQUIRED_FIELDS = {
    "company_profile": [
        "stock_code",
        "stock_name",
        "company_name",
        "exchange",
        "industry",
        "main_business",
        "listing_date",
    ],
    "income_statement": [
        "period",
        "revenue",
        "operating_cost",
        "gross_profit",
        "gross_margin",
        "selling_expense",
        "admin_expense",
        "rd_expense",
        "financial_expense",
        "operating_profit",
        "net_profit_parent",
        "deducted_net_profit",
        "net_margin",
    ],
    "balance_sheet": [
        "period",
        "total_assets",
        "cash",
        "accounts_receivable",
        "inventory",
        "fixed_assets",
        "construction_in_progress",
        "goodwill",
        "short_term_debt",
        "long_term_debt",
        "total_liabilities",
        "asset_liability_ratio",
        "shareholder_equity",
    ],
    "cashflow_statement": [
        "period",
        "operating_cashflow",
        "investing_cashflow",
        "financing_cashflow",
        "capex",
        "free_cashflow",
        "operating_cashflow_to_net_profit",
    ],
    "financial_indicators": [
        "period",
        "roe",
        "roa",
        "gross_margin",
        "net_margin",
        "asset_liability_ratio",
        "current_ratio",
        "quick_ratio",
        "inventory_turnover",
        "ar_turnover",
        "rd_expense_ratio",
        "selling_expense_ratio",
        "admin_expense_ratio",
        "financial_expense_ratio",
    ],
}


def check_data_quality(data: dict[str, Any], years: int) -> dict[str, list[str]]:
    missing_fields: list[str] = []
    abnormal_values: list[str] = []
    warnings = list(data.get("metadata", {}).get("warnings", []))

    profile = data.get("company_profile", {})
    for field in REQUIRED_FIELDS["company_profile"]:
        if is_missing(profile.get(field)):
            missing_fields.append(f"company_profile.{field}")

    for section in ("income_statement", "balance_sheet", "cashflow_statement", "financial_indicators"):
        rows = data.get(section, [])
        if len(rows) < years:
            warnings.append(f"{section} 仅获取到 {len(rows)} 年数据，少于请求的 {years} 年")
        for row in rows:
            period = row.get("period") or "unknown"
            for field in REQUIRED_FIELDS[section]:
                if is_missing(row.get(field)):
                    missing_fields.append(f"{section}[{period}].{field}")

    check_gross_margin(data.get("income_statement", []), abnormal_values)
    check_asset_liability_ratio(data.get("balance_sheet", []), abnormal_values)
    check_operating_cashflow(data.get("cashflow_statement", []), abnormal_values)
    check_growth(data.get("balance_sheet", []), "accounts_receivable", "应收账款", abnormal_values)
    check_growth(data.get("balance_sheet", []), "inventory", "存货", abnormal_values)

    return {
        "missing_fields": missing_fields,
        "abnormal_values": abnormal_values,
        "warnings": unique_keep_order(warnings),
    }


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def check_gross_margin(rows: list[dict[str, Any]], abnormal_values: list[str]) -> None:
    for row in rows:
        value = row.get("gross_margin")
        if value is None:
            continue
        if value < -0.2 or value > 1:
            abnormal_values.append(f"{row.get('period')} 毛利率异常: {value}")


def check_asset_liability_ratio(rows: list[dict[str, Any]], abnormal_values: list[str]) -> None:
    for row in rows:
        value = row.get("asset_liability_ratio")
        if value is None:
            continue
        if value < 0 or value > 1:
            abnormal_values.append(f"{row.get('period')} 资产负债率异常: {value}")
        elif value >= 0.8:
            abnormal_values.append(f"{row.get('period')} 资产负债率偏高: {value}")


def check_operating_cashflow(rows: list[dict[str, Any]], abnormal_values: list[str]) -> None:
    negative_periods = [
        str(row.get("period"))
        for row in rows
        if row.get("operating_cashflow") is not None and row.get("operating_cashflow") < 0
    ]
    if len(negative_periods) >= 2:
        abnormal_values.append(f"经营现金流连续或多期为负: {', '.join(negative_periods)}")

    for row in rows:
        ratio = row.get("operating_cashflow_to_net_profit")
        if ratio is not None and ratio < 0.5:
            abnormal_values.append(f"{row.get('period')} 经营现金流/净利润明显偏低: {ratio}")


def check_growth(
    rows: list[dict[str, Any]],
    field: str,
    label: str,
    abnormal_values: list[str],
) -> None:
    usable = [row for row in rows if row.get(field) is not None]
    if len(usable) < 2:
        return
    latest = usable[0]
    oldest = usable[-1]
    latest_value = latest.get(field)
    oldest_value = oldest.get(field)
    if oldest_value is None or oldest_value <= 0 or latest_value is None:
        return
    if latest_value > oldest_value * 1.5:
        abnormal_values.append(
            f"{label}明显增长: {oldest.get('period')}={oldest_value}, {latest.get('period')}={latest_value}"
        )


def unique_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
