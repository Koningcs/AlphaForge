from __future__ import annotations

from typing import Any


def build_analysis_summary(data: dict[str, Any]) -> dict[str, Any]:
    income = data.get("income_statement", [])
    balance = data.get("balance_sheet", [])
    cashflow = data.get("cashflow_statement", [])
    indicators = data.get("financial_indicators", [])
    valuation = data.get("valuation", {})

    growth = analyze_growth(income)
    profitability = analyze_profitability(income, indicators)
    cashflow_quality = analyze_cashflow_quality(cashflow)
    asset_quality = analyze_asset_quality(income, balance)
    solvency = analyze_solvency(balance)
    valuation_summary = analyze_valuation(valuation)

    strengths: list[str] = []
    risks: list[str] = []
    watch_items: list[str] = []

    collect_signal(strengths, risks, watch_items, growth)
    collect_signal(strengths, risks, watch_items, profitability)
    collect_signal(strengths, risks, watch_items, cashflow_quality)
    collect_signal(strengths, risks, watch_items, asset_quality)
    collect_signal(strengths, risks, watch_items, solvency)
    collect_signal(strengths, risks, watch_items, valuation_summary)

    return {
        "method": "rule_based_snapshot",
        "note": "规则化摘要仅用于梳理财务数据，不构成投资建议。",
        "overall": {
            "strengths": strengths[:8],
            "risks": risks[:8],
            "watch_items": watch_items[:8],
        },
        "growth": growth,
        "profitability": profitability,
        "cashflow_quality": cashflow_quality,
        "asset_quality": asset_quality,
        "solvency": solvency,
        "valuation": valuation_summary,
    }


def analyze_growth(income: list[dict[str, Any]]) -> dict[str, Any]:
    latest = first(income)
    previous = second(income)
    oldest = last(income)
    periods = period_span(income)

    revenue_cagr = cagr(value(oldest, "revenue"), value(latest, "revenue"), periods)
    net_profit_cagr = cagr(value(oldest, "net_profit_parent"), value(latest, "net_profit_parent"), periods)
    deducted_net_profit_cagr = cagr(value(oldest, "deducted_net_profit"), value(latest, "deducted_net_profit"), periods)
    latest_revenue_yoy = growth_rate(value(previous, "revenue"), value(latest, "revenue"))
    latest_net_profit_yoy = growth_rate(value(previous, "net_profit_parent"), value(latest, "net_profit_parent"))

    comments: list[str] = []
    positives: list[str] = []
    risks: list[str] = []
    watch: list[str] = []

    if revenue_cagr is not None:
        comments.append(f"营收 {periods} 年复合增速为 {format_percent(revenue_cagr)}")
        if revenue_cagr >= 0.15:
            positives.append("营收保持较快复合增长")
        elif revenue_cagr < 0:
            risks.append("营收复合增速为负")

    if net_profit_cagr is not None:
        comments.append(f"归母净利润 {periods} 年复合增速为 {format_percent(net_profit_cagr)}")
        if net_profit_cagr >= 0.15:
            positives.append("归母净利润保持较快复合增长")
        elif net_profit_cagr < 0:
            risks.append("归母净利润复合增速为负")

    if latest_revenue_yoy is not None:
        comments.append(f"最近一年营收同比为 {format_percent(latest_revenue_yoy)}")
        if latest_revenue_yoy < 0:
            watch.append("最近一年营收同比下滑")

    if latest_net_profit_yoy is not None:
        comments.append(f"最近一年归母净利润同比为 {format_percent(latest_net_profit_yoy)}")
        if latest_net_profit_yoy < 0:
            watch.append("最近一年归母净利润同比下滑")

    return {
        "latest_period": value(latest, "period"),
        "periods": periods,
        "revenue_cagr": revenue_cagr,
        "net_profit_parent_cagr": net_profit_cagr,
        "deducted_net_profit_cagr": deducted_net_profit_cagr,
        "latest_revenue_yoy": latest_revenue_yoy,
        "latest_net_profit_parent_yoy": latest_net_profit_yoy,
        "comments": comments,
        "positives": positives,
        "risks": risks,
        "watch_items": watch,
    }


def analyze_profitability(
    income: list[dict[str, Any]],
    indicators: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_income = first(income)
    oldest_income = last(income)
    latest_indicator = first(indicators)
    oldest_indicator = last(indicators)

    latest_gross_margin = coalesce(value(latest_income, "gross_margin"), value(latest_indicator, "gross_margin"))
    latest_net_margin = coalesce(value(latest_income, "net_margin"), value(latest_indicator, "net_margin"))
    latest_roe = value(latest_indicator, "roe")
    latest_roa = value(latest_indicator, "roa")
    gross_margin_change = change(value(oldest_income, "gross_margin"), latest_gross_margin)
    roe_change = change(value(oldest_indicator, "roe"), latest_roe)

    comments: list[str] = []
    positives: list[str] = []
    risks: list[str] = []
    watch: list[str] = []

    if latest_gross_margin is not None:
        comments.append(f"最近一期毛利率为 {format_percent(latest_gross_margin)}")
        if latest_gross_margin >= 0.35:
            positives.append("毛利率处于较高水平")
        elif latest_gross_margin < 0.15:
            watch.append("毛利率偏低")

    if latest_net_margin is not None:
        comments.append(f"最近一期净利率为 {format_percent(latest_net_margin)}")
        if latest_net_margin >= 0.15:
            positives.append("净利率较强")
        elif latest_net_margin < 0.03:
            risks.append("净利率较低")

    if latest_roe is not None:
        comments.append(f"最近一期 ROE 为 {format_percent(latest_roe)}")
        if latest_roe >= 0.15:
            positives.append("ROE 表现较强")
        elif latest_roe < 0.05:
            watch.append("ROE 偏低")

    if gross_margin_change is not None and gross_margin_change < -0.05:
        watch.append("毛利率较早期明显下滑")
    if roe_change is not None and roe_change < -0.05:
        watch.append("ROE 较早期明显下滑")

    return {
        "latest_period": value(latest_income, "period") or value(latest_indicator, "period"),
        "latest_gross_margin": latest_gross_margin,
        "latest_net_margin": latest_net_margin,
        "latest_roe": latest_roe,
        "latest_roa": latest_roa,
        "gross_margin_change": gross_margin_change,
        "roe_change": roe_change,
        "comments": comments,
        "positives": positives,
        "risks": risks,
        "watch_items": watch,
    }


def analyze_cashflow_quality(cashflow: list[dict[str, Any]]) -> dict[str, Any]:
    latest = first(cashflow)
    latest_ocf = value(latest, "operating_cashflow")
    latest_fcf = value(latest, "free_cashflow")
    latest_ocf_to_np = value(latest, "operating_cashflow_to_net_profit")
    positive_fcf_years = count_if(cashflow, "free_cashflow", lambda item: item > 0)
    negative_ocf_years = count_if(cashflow, "operating_cashflow", lambda item: item < 0)

    comments: list[str] = []
    positives: list[str] = []
    risks: list[str] = []
    watch: list[str] = []

    if latest_ocf is not None:
        comments.append(f"最近一期经营现金流为 {format_number(latest_ocf)}")
        if latest_ocf > 0:
            positives.append("经营现金流为正")
        else:
            risks.append("经营现金流为负")

    if latest_fcf is not None:
        comments.append(f"最近一期自由现金流为 {format_number(latest_fcf)}")
        if latest_fcf > 0:
            positives.append("自由现金流为正")
        else:
            watch.append("自由现金流为负")

    if latest_ocf_to_np is not None:
        comments.append(f"经营现金流/归母净利润为 {format_decimal(latest_ocf_to_np)}")
        if latest_ocf_to_np >= 1:
            positives.append("利润现金含量较好")
        elif latest_ocf_to_np < 0.5:
            risks.append("利润现金含量偏弱")

    if negative_ocf_years >= 2:
        risks.append("多期经营现金流为负")

    return {
        "latest_period": value(latest, "period"),
        "latest_operating_cashflow": latest_ocf,
        "latest_free_cashflow": latest_fcf,
        "latest_operating_cashflow_to_net_profit": latest_ocf_to_np,
        "positive_free_cashflow_years": positive_fcf_years,
        "negative_operating_cashflow_years": negative_ocf_years,
        "comments": comments,
        "positives": positives,
        "risks": risks,
        "watch_items": watch,
    }


def analyze_asset_quality(
    income: list[dict[str, Any]],
    balance: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_income = first(income)
    oldest_income = last(income)
    latest_balance = first(balance)
    oldest_balance = last(balance)

    revenue_growth = growth_rate(value(oldest_income, "revenue"), value(latest_income, "revenue"))
    ar_growth = growth_rate(value(oldest_balance, "accounts_receivable"), value(latest_balance, "accounts_receivable"))
    inventory_growth = growth_rate(value(oldest_balance, "inventory"), value(latest_balance, "inventory"))
    ar_to_revenue = safe_div(value(latest_balance, "accounts_receivable"), value(latest_income, "revenue"))
    inventory_to_revenue = safe_div(value(latest_balance, "inventory"), value(latest_income, "revenue"))
    goodwill_to_assets = safe_div(value(latest_balance, "goodwill"), value(latest_balance, "total_assets"))

    comments: list[str] = []
    positives: list[str] = []
    risks: list[str] = []
    watch: list[str] = []

    if ar_growth is not None:
        comments.append(f"应收账款较期初变化 {format_percent(ar_growth)}")
    if inventory_growth is not None:
        comments.append(f"存货较期初变化 {format_percent(inventory_growth)}")
    if ar_to_revenue is not None:
        comments.append(f"应收账款/营收为 {format_percent(ar_to_revenue)}")
    if inventory_to_revenue is not None:
        comments.append(f"存货/营收为 {format_percent(inventory_to_revenue)}")
    if goodwill_to_assets is not None:
        comments.append(f"商誉/总资产为 {format_percent(goodwill_to_assets)}")

    if revenue_growth is not None and ar_growth is not None and ar_growth > revenue_growth + 0.2:
        watch.append("应收账款增长明显快于营收")
    if revenue_growth is not None and inventory_growth is not None and inventory_growth > revenue_growth + 0.2:
        watch.append("存货增长明显快于营收")
    if goodwill_to_assets is not None and goodwill_to_assets >= 0.15:
        watch.append("商誉占总资产比例较高")
    if ar_to_revenue is not None and ar_to_revenue >= 0.3:
        risks.append("应收账款占营收比例较高")
    if inventory_to_revenue is not None and inventory_to_revenue >= 0.4:
        watch.append("存货占营收比例较高")
    if not risks and not watch:
        positives.append("资产质量未触发明显风险规则")

    return {
        "latest_period": value(latest_balance, "period"),
        "revenue_growth": revenue_growth,
        "accounts_receivable_growth": ar_growth,
        "inventory_growth": inventory_growth,
        "accounts_receivable_to_revenue": ar_to_revenue,
        "inventory_to_revenue": inventory_to_revenue,
        "goodwill_to_assets": goodwill_to_assets,
        "comments": comments,
        "positives": positives,
        "risks": risks,
        "watch_items": watch,
    }


def analyze_solvency(balance: list[dict[str, Any]]) -> dict[str, Any]:
    latest = first(balance)
    cash = value(latest, "cash")
    short_debt = value(latest, "short_term_debt")
    long_debt = value(latest, "long_term_debt")
    total_debt = sum_present(short_debt, long_debt)
    net_cash = safe_sub(cash, total_debt)
    cash_to_short_debt = safe_div(cash, short_debt)
    debt_to_equity = safe_div(total_debt, value(latest, "shareholder_equity"))
    asset_liability_ratio = value(latest, "asset_liability_ratio")

    comments: list[str] = []
    positives: list[str] = []
    risks: list[str] = []
    watch: list[str] = []

    if asset_liability_ratio is not None:
        comments.append(f"资产负债率为 {format_percent(asset_liability_ratio)}")
        if asset_liability_ratio <= 0.45:
            positives.append("资产负债率较低")
        elif asset_liability_ratio >= 0.7:
            risks.append("资产负债率偏高")

    if net_cash is not None:
        comments.append(f"货币资金扣除短长债后为 {format_number(net_cash)}")
        if net_cash > 0:
            positives.append("账面净现金为正")
        else:
            watch.append("账面净现金为负")

    if cash_to_short_debt is not None:
        comments.append(f"货币资金/短期借款为 {format_decimal(cash_to_short_debt)}")
        if cash_to_short_debt < 1:
            risks.append("货币资金覆盖短债不足")

    return {
        "latest_period": value(latest, "period"),
        "asset_liability_ratio": asset_liability_ratio,
        "cash_to_short_term_debt": cash_to_short_debt,
        "interest_bearing_debt": total_debt,
        "net_cash": net_cash,
        "debt_to_equity": debt_to_equity,
        "comments": comments,
        "positives": positives,
        "risks": risks,
        "watch_items": watch,
    }


def analyze_valuation(valuation: dict[str, Any]) -> dict[str, Any]:
    pe_ttm = value(valuation, "pe_ttm")
    pb = value(valuation, "pb")
    ps_ttm = value(valuation, "ps_ttm")
    peg = value(valuation, "peg")
    ev_ebitda = value(valuation, "ev_ebitda")

    comments: list[str] = []
    positives: list[str] = []
    risks: list[str] = []
    watch: list[str] = []

    if pe_ttm is not None:
        comments.append(f"PE(TTM) 为 {format_decimal(pe_ttm)}")
        if pe_ttm >= 60:
            watch.append("PE(TTM) 较高，需关注业绩兑现")
        elif pe_ttm > 0 and pe_ttm <= 20:
            positives.append("PE(TTM) 处于较低区间")
    if pb is not None:
        comments.append(f"PB 为 {format_decimal(pb)}")
        if pb >= 8:
            watch.append("PB 较高")
    if ps_ttm is not None:
        comments.append(f"PS(TTM) 为 {format_decimal(ps_ttm)}")
        if ps_ttm >= 10:
            watch.append("PS(TTM) 较高")
    if peg is not None:
        comments.append(f"PEG 为 {format_decimal(peg)}")
        if peg > 0 and peg <= 1:
            positives.append("PEG 低于或接近 1")
    if ev_ebitda is not None:
        comments.append(f"EV/EBITDA 为 {format_decimal(ev_ebitda)}")
    if not comments:
        watch.append("估值数据缺失")

    return {
        "date": valuation.get("date"),
        "market_cap": valuation.get("market_cap"),
        "pe_ttm": pe_ttm,
        "pb": pb,
        "ps_ttm": ps_ttm,
        "peg": peg,
        "ev_ebitda": ev_ebitda,
        "industry_rank": valuation.get("industry_rank"),
        "comments": comments,
        "positives": positives,
        "risks": risks,
        "watch_items": watch,
    }


def collect_signal(
    strengths: list[str],
    risks: list[str],
    watch_items: list[str],
    section: dict[str, Any],
) -> None:
    strengths.extend(section.get("positives", []))
    risks.extend(section.get("risks", []))
    watch_items.extend(section.get("watch_items", []))


def first(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return rows[0] if rows else {}


def second(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return rows[1] if len(rows) > 1 else {}


def last(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return rows[-1] if rows else {}


def value(row: dict[str, Any], key: str) -> Any:
    if not row:
        return None
    return row.get(key)


def period_span(rows: list[dict[str, Any]]) -> int:
    return max(len(rows) - 1, 1)


def cagr(start: float | None, end: float | None, periods: int) -> float | None:
    if start is None or end is None or periods <= 0 or start <= 0 or end < 0:
        return None
    return (end / start) ** (1 / periods) - 1


def growth_rate(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start == 0:
        return None
    return end / start - 1


def change(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return end - start


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def safe_sub(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def sum_present(*values: float | None) -> float | None:
    present = [item for item in values if item is not None]
    if not present:
        return None
    return sum(present)


def count_if(rows: list[dict[str, Any]], key: str, predicate: Any) -> int:
    count = 0
    for row in rows:
        item = value(row, key)
        if item is not None and predicate(item):
            count += 1
    return count


def coalesce(*values: Any) -> Any:
    for item in values:
        if item is not None:
            return item
    return None


def format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def format_decimal(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def format_number(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}"
