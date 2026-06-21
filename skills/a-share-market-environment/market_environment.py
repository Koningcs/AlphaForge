from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import argparse
import sys

SKILL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SKILL_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "skills"))

from a_share_common.market_data import INDEX_CODES, fetch_index_hist, trend_score_from_snapshot, trend_snapshot
from a_share_common.utils import (
    first_number,
    first_text,
    format_decimal,
    format_percent,
    normalize_date,
    now_iso,
    output_dir,
    quality_report,
    render_kv_table,
    render_table,
    safe_call,
    score_state,
    to_number,
    write_json,
    write_text,
)


def get_market_environment(
    as_of_date: str | None = None,
    lookback_days: int = 500,
    benchmarks: list[str] | None = None,
    include_macro: bool = True,
    include_liquidity: bool = True,
    include_policy_news: bool = True,
    policy_news_days: int = 30,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    import akshare as ak

    warnings: list[str] = []
    as_of = as_of_date or datetime.now().strftime("%Y-%m-%d")
    benchmarks = benchmarks or ["沪深300", "中证500", "创业板指"]

    index_rows = []
    hist_map = {}
    for name in benchmarks:
        hist = fetch_index_hist(ak, name, lookback_days, warnings)
        hist_map[name] = hist
        snap = trend_snapshot(hist)
        snap.update({"name": name, "code": INDEX_CODES.get(name, name), "score": trend_score_from_snapshot(snap)})
        index_rows.append(snap)

    risk_appetite = build_risk_appetite(index_rows)
    liquidity = build_liquidity(ak, hist_map, warnings) if include_liquidity else empty_section("liquidity disabled")
    valuation = build_valuation(ak, warnings)
    macro = build_macro(ak, warnings) if include_macro else empty_section("macro disabled")
    policy_news = build_policy_news(ak, policy_news_days, warnings) if include_policy_news else empty_policy_news()
    environment_score = score_environment(index_rows, risk_appetite, liquidity, valuation, macro, policy_news)
    signals = build_signals(environment_score, risk_appetite, liquidity, valuation, macro, policy_news)

    data = {
        "metadata": {
            "as_of_date": as_of,
            "generated_at": now_iso(),
            "lookback_days": lookback_days,
            "data_source": "akshare",
            "warnings": warnings,
        },
        "index_trends": index_rows,
        "risk_appetite": risk_appetite,
        "liquidity": liquidity,
        "valuation": valuation,
        "macro": macro,
        "policy_news": policy_news,
        "environment_score": environment_score,
        "signals": signals,
        "data_quality": {},
    }
    data["data_quality"] = quality_report(
        {
            "index_trends": index_rows,
            "environment_score": environment_score,
            "policy_news": {
                "policy_tone": policy_news.get("policy_tone"),
                "liquidity_tone": policy_news.get("liquidity_tone"),
                "capital_market_tone": policy_news.get("capital_market_tone"),
            },
        },
        warnings,
    )

    root = Path(output_root) if output_root else output_dir(PROJECT_ROOT, "market", as_of)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "market_environment.json"
    md_path = root / "market_environment.md"
    quality_path = root / "data_quality.md"
    write_json(json_path, data)
    write_text(md_path, render_market_environment_markdown(data))
    write_text(quality_path, render_quality_markdown(data["data_quality"]))
    return {"json_path": str(json_path), "markdown_path": str(md_path), "data_quality_path": str(quality_path), "data": data}


def empty_section(reason: str) -> dict[str, Any]:
    return {"score": None, "comments": [reason]}


def build_risk_appetite(index_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {row["name"]: row for row in index_rows}
    hs300 = by_name.get("沪深300", {})
    cyb = by_name.get("创业板指", {})
    zz500 = by_name.get("中证500", {})
    growth_vs_large = subtract(cyb.get("return_60d"), hs300.get("return_60d"))
    small_mid_vs_large = subtract(zz500.get("return_60d"), hs300.get("return_60d"))
    score = 50.0
    for item in [growth_vs_large, small_mid_vs_large]:
        if item is not None:
            score += max(-20, min(20, item * 100))
    comments = []
    if growth_vs_large is not None:
        comments.append(f"创业板指相对沪深300 60日强弱为 {format_percent(growth_vs_large)}")
    if small_mid_vs_large is not None:
        comments.append(f"中证500相对沪深300 60日强弱为 {format_percent(small_mid_vs_large)}")
    return {
        "growth_vs_large_cap": growth_vs_large,
        "small_mid_vs_large_cap": small_mid_vs_large,
        "score": round(max(0, min(100, score)), 2),
        "state": score_state(score, ("risk_on", "positive", "neutral", "cautious", "risk_off")),
        "comments": comments,
    }


def build_liquidity(ak: Any, hist_map: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    market_flow = safe_call(warnings, "市场资金流", getattr(ak, "stock_market_fund_flow", None))
    latest_flow = None
    if market_flow is not None and not market_flow.empty:
        last = market_flow.tail(1).iloc[0].to_dict()
        latest_flow = first_number(*last.values())

    hs300 = hist_map.get("沪深300")
    turnover_change = None
    latest_turnover = None
    if hs300 is not None and not hs300.empty and len(hs300) >= 120:
        latest_turnover = float(hs300["amount"].tail(20).mean())
        base = float(hs300["amount"].tail(120).mean())
        turnover_change = None if base == 0 else latest_turnover / base - 1

    score = 50.0
    if turnover_change is not None:
        score += max(-20, min(20, turnover_change * 50))
    comments = []
    if turnover_change is not None:
        comments.append(f"沪深300近20日成交额相对120日变化 {format_percent(turnover_change)}")
    if latest_flow is not None:
        comments.append(f"市场资金流最新可识别数值 {format_decimal(latest_flow)}")
    return {
        "margin_balance_latest": None,
        "margin_balance_change_20d": None,
        "market_turnover_latest": latest_turnover,
        "market_turnover_change_20d": turnover_change,
        "market_fund_flow_latest": latest_flow,
        "score": round(max(0, min(100, score)), 2),
        "state": score_state(score, ("ample", "positive", "neutral", "tight", "dry")),
        "comments": comments,
    }


def build_valuation(ak: Any, warnings: list[str]) -> dict[str, Any]:
    rows = []
    for name in ["沪深300", "中证500", "创业板指"]:
        df = safe_call(warnings, f"{name} PE", getattr(ak, "stock_index_pe_lg", None), symbol=name)
        pe_latest = pe_percentile = None
        if df is not None and not df.empty:
            numeric_cols = [col for col in df.columns if "PE" in str(col).upper() or "pe" in str(col)]
            col = numeric_cols[0] if numeric_cols else df.select_dtypes("number").columns[-1] if len(df.select_dtypes("number").columns) else None
            if col is not None:
                series = df[col].apply(to_number).dropna()
                if not series.empty:
                    pe_latest = float(series.iloc[-1])
                    pe_percentile = float((series <= pe_latest).mean())
        rows.append({"name": name, "pe_latest": pe_latest, "pe_percentile": pe_percentile})
    percentiles = [row["pe_percentile"] for row in rows if row["pe_percentile"] is not None]
    avg = sum(percentiles) / len(percentiles) if percentiles else None
    state = "unknown"
    if avg is not None:
        state = "cheap" if avg < 0.35 else "expensive" if avg > 0.75 else "fair"
    score = None if avg is None else round(max(0, min(100, 100 - avg * 100)), 2)
    return {"index_percentiles": rows, "market_pb_percentile": None, "valuation_state": state, "score": score, "comments": [f"指数估值平均分位 {format_percent(avg)}"] if avg is not None else []}


def build_macro(ak: Any, warnings: list[str]) -> dict[str, Any]:
    pmi = latest_numeric(safe_call(warnings, "中国PMI", getattr(ak, "macro_china_pmi", None)))
    cpi = latest_numeric(safe_call(warnings, "中国CPI", getattr(ak, "macro_china_cpi", None)))
    lpr = latest_numeric(safe_call(warnings, "中国LPR", getattr(ak, "macro_china_lpr", None)))
    score = 50.0
    comments = []
    if pmi is not None:
        score += 10 if pmi >= 50 else -10
        comments.append(f"PMI 最新可识别值 {format_decimal(pmi)}")
    if cpi is not None:
        comments.append(f"CPI 最新可识别值 {format_decimal(cpi)}")
    if lpr is not None:
        comments.append(f"LPR 最新可识别值 {format_decimal(lpr)}")
    return {"pmi_latest": pmi, "cpi_latest": cpi, "ppi_latest": None, "lpr_latest": lpr, "macro_state": score_state(score, ("expansion", "stable_plus", "stable", "soft", "pressure")), "score": round(score, 2), "comments": comments}


def build_policy_news(ak: Any, days: int, warnings: list[str]) -> dict[str, Any]:
    items = []
    seen = set()
    for i in range(min(days, 10)):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        df = safe_call(warnings, f"央视新闻-{day}", getattr(ak, "news_cctv", None), date=day)
        if df is None or df.empty:
            continue
        for row in df.head(20).to_dict(orient="records"):
            title = first_text(row.get("title"), row.get("标题"), row.get("content"))
            if not title or title in seen:
                continue
            seen.add(title)
            category, tone, importance, keywords = classify_policy_title(title)
            if category == "other":
                continue
            items.append({
                "date": normalize_date(row.get("date") or row.get("时间") or day),
                "source": "央视新闻",
                "source_tier": "state_media",
                "title": title,
                "url": first_text(row.get("url"), row.get("链接")),
                "category": category,
                "policy_tone": tone,
                "importance": importance,
                "keywords": keywords,
            })
    tones = [item["policy_tone"] for item in items]
    policy_tone = aggregate_tone(tones)
    liquidity_tone = "easing" if any("liquidity_easing" == item["category"] or "credit_support" == item["category"] for item in items) else "neutral" if items else "unknown"
    capital_tone = "supportive" if any(item["category"] == "capital_market_support" for item in items) else "neutral" if items else "unknown"
    score = 50
    for tone in tones:
        score += {"supportive": 8, "neutral": 0, "tightening": -8, "risk_warning": -10, "unknown": 0}.get(tone, 0)
    score = round(max(0, min(100, score)), 2)
    keywords = sorted({kw for item in items for kw in item["keywords"]})
    return {
        "lookback_days": days,
        "latest_items": items[:20],
        "policy_tone": policy_tone,
        "liquidity_tone": liquidity_tone,
        "capital_market_tone": capital_tone,
        "keywords": keywords,
        "score": score,
        "comments": [f"识别到 {len(items)} 条权威政策/宏观相关信息"] if items else ["未识别到可用权威政策信息"],
    }


def empty_policy_news() -> dict[str, Any]:
    return {"lookback_days": 0, "latest_items": [], "policy_tone": "unknown", "liquidity_tone": "unknown", "capital_market_tone": "unknown", "keywords": [], "score": None, "comments": ["policy news disabled"]}


def classify_policy_title(title: str) -> tuple[str, str, str, list[str]]:
    rules = [
        ("资本市场", "capital_market_support", "supportive"),
        ("证券", "capital_market_support", "supportive"),
        ("稳增长", "growth_support", "supportive"),
        ("扩大内需", "growth_support", "supportive"),
        ("降准", "liquidity_easing", "supportive"),
        ("降息", "liquidity_easing", "supportive"),
        ("流动性", "liquidity_easing", "supportive"),
        ("信贷", "credit_support", "supportive"),
        ("房地产", "property_policy", "neutral"),
        ("科技", "tech_industry_policy", "supportive"),
        ("人工智能", "tech_industry_policy", "supportive"),
        ("消费", "consumption_policy", "supportive"),
        ("监管", "regulatory_tightening", "tightening"),
        ("风险", "risk_warning", "risk_warning"),
        ("关税", "external_shock", "risk_warning"),
    ]
    keywords = [word for word, _, _ in rules if word in title]
    if not keywords:
        return "other", "unknown", "low", []
    first = next((rule for rule in rules if rule[0] in title), None)
    category, tone = first[1], first[2]
    importance = "high" if tone in {"supportive", "risk_warning", "tightening"} else "medium"
    return category, tone, importance, keywords


def aggregate_tone(tones: list[str]) -> str:
    if not tones:
        return "unknown"
    if tones.count("risk_warning") + tones.count("tightening") > tones.count("supportive"):
        return "risk_warning" if tones.count("risk_warning") >= tones.count("tightening") else "tightening"
    if tones.count("supportive"):
        return "supportive"
    return "neutral"


def latest_numeric(df: Any) -> float | None:
    if df is None or df.empty:
        return None
    nums = []
    for value in df.tail(1).iloc[0].to_dict().values():
        number = to_number(value)
        if number is not None:
            nums.append(number)
    return nums[-1] if nums else None


def score_environment(index_rows: list[dict[str, Any]], risk: dict[str, Any], liquidity: dict[str, Any], valuation: dict[str, Any], macro: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    trend = sum(row.get("score", 50) for row in index_rows) / len(index_rows) if index_rows else None
    parts = {
        "trend": trend,
        "risk_appetite": risk.get("score"),
        "liquidity": liquidity.get("score"),
        "valuation": valuation.get("score"),
        "macro": macro.get("score"),
        "policy_news": policy.get("score"),
    }
    weights = {"trend": 0.30, "risk_appetite": 0.20, "liquidity": 0.20, "valuation": 0.10, "macro": 0.10, "policy_news": 0.10}
    available = {key: value for key, value in parts.items() if value is not None}
    total_weight = sum(weights[key] for key in available)
    total = None if not available else sum(available[key] * weights[key] for key in available) / total_weight
    return {**{key: None if value is None else round(value, 2) for key, value in parts.items()}, "total": None if total is None else round(total, 2), "state": score_state(total, ("favorable", "neutral_plus", "neutral", "defensive", "hostile"))}


def build_signals(score: dict[str, Any], risk: dict[str, Any], liquidity: dict[str, Any], valuation: dict[str, Any], macro: dict[str, Any], policy: dict[str, Any]) -> dict[str, list[str]]:
    positives: list[str] = []
    risks: list[str] = []
    watch: list[str] = []
    if score.get("state") in {"favorable", "neutral_plus"}:
        positives.append("市场环境总分偏积极")
    elif score.get("state") in {"defensive", "hostile"}:
        risks.append("市场环境偏防御")
    if risk.get("state") == "risk_on":
        positives.append("风险偏好较强")
    if liquidity.get("state") in {"tight", "dry"}:
        watch.append("流动性偏弱")
    if valuation.get("valuation_state") == "expensive":
        watch.append("市场估值分位偏高")
    if macro.get("macro_state") in {"soft", "pressure"}:
        watch.append("宏观数据存在压力")
    if policy.get("policy_tone") == "supportive":
        positives.append("权威政策信息语气偏支持")
    if policy.get("policy_tone") in {"tightening", "risk_warning"}:
        risks.append("权威政策信息出现收紧或风险提示")
    return {"positives": positives, "risks": risks, "watch_items": watch}


def subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def render_market_environment_markdown(data: dict[str, Any]) -> str:
    score = data["environment_score"]
    signals = data["signals"]
    lines = [
        "# A 股市场环境分析",
        "",
        f"- 生成时间: {data['metadata']['generated_at']}",
        f"- 截止日期: {data['metadata']['as_of_date']}",
        f"- 总分: {format_decimal(score.get('total'))}",
        f"- 状态: {score.get('state')}",
        "",
        "## 1. 总览",
        "",
        render_signal_section(signals),
        "",
        "## 2. 指数趋势",
        "",
        render_table(data["index_trends"], [("name", "指数"), ("latest_close", "收盘"), ("return_20d", "20日"), ("return_60d", "60日"), ("return_120d", "120日"), ("ma_alignment", "均线"), ("trend_stage", "阶段"), ("score", "分数")], {"return_20d", "return_60d", "return_120d"}),
        "",
        "## 3. 风险偏好",
        "",
        "\n".join(f"- {item}" for item in data["risk_appetite"].get("comments", [])) or "- 无",
        "",
        "## 4. 流动性",
        "",
        "\n".join(f"- {item}" for item in data["liquidity"].get("comments", [])) or "- 无",
        "",
        "## 5. 估值状态",
        "",
        f"- 状态: {data['valuation'].get('valuation_state')}",
        *[f"- {item}" for item in data["valuation"].get("comments", [])],
        "",
        "## 6. 宏观压力",
        "",
        *[f"- {item}" for item in data["macro"].get("comments", [])],
        "",
        "## 7. 政策与权威信息",
        "",
        render_kv_table(data["policy_news"], [("policy_tone", "政策语气"), ("liquidity_tone", "流动性语气"), ("capital_market_tone", "资本市场语气"), ("score", "分数")]),
        "",
        render_table(data["policy_news"].get("latest_items", []), [("date", "日期"), ("source", "来源"), ("source_tier", "层级"), ("title", "标题"), ("category", "分类"), ("policy_tone", "语气")]),
        "",
        "## 8. 数据质量",
        "",
        render_quality_markdown(data["data_quality"]),
    ]
    return "\n".join(lines)


def render_signal_section(signals: dict[str, list[str]]) -> str:
    lines = []
    for title, key in [("优势", "positives"), ("风险", "risks"), ("关注项", "watch_items")]:
        lines.extend([f"### {title}", ""])
        values = signals.get(key) or []
        lines.extend([f"- {item}" for item in values] if values else ["- 无"])
        lines.append("")
    return "\n".join(lines).rstrip()


def render_quality_markdown(data_quality: dict[str, list[str]]) -> str:
    lines = ["# 数据质量", ""]
    for title, key in [("缺失字段", "missing_fields"), ("警告", "warnings")]:
        values = data_quality.get(key) or []
        lines.extend([f"## {title}", ""])
        lines.extend([f"- {item}" for item in values] if values else ["- 无"])
        lines.append("")
    return "\n".join(lines).rstrip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze A-share market environment.")
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--lookback-days", type=int, default=500)
    parser.add_argument("--include-macro", default="true")
    parser.add_argument("--include-liquidity", default="true")
    parser.add_argument("--include-policy-news", default="true")
    args = parser.parse_args()
    result = get_market_environment(
        as_of_date=args.as_of_date,
        lookback_days=args.lookback_days,
        include_macro=args.include_macro.lower() != "false",
        include_liquidity=args.include_liquidity.lower() != "false",
        include_policy_news=args.include_policy_news.lower() != "false",
    )
    print(result["json_path"])


if __name__ == "__main__":
    main()
