from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import argparse
import sys

SKILL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SKILL_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "skills"))

from a_share_common.utils import (
    first_number,
    first_text,
    normalize_date,
    normalize_stock_code,
    now_iso,
    output_dir,
    quality_report,
    render_kv_table,
    render_table,
    safe_call,
    score_state,
    write_json,
    write_text,
)


def get_event_expectation(
    stock_code: str,
    lookback_days: int = 180,
    include_news: bool = True,
    include_notices: bool = True,
    include_forecast: bool = True,
    include_shareholder: bool = True,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    import akshare as ak

    warnings: list[str] = []
    code = normalize_stock_code(stock_code)
    stock_name = fetch_stock_name(ak, code, warnings)
    timeline: list[dict[str, Any]] = []
    if include_news:
        timeline.extend(fetch_news(ak, code, lookback_days, warnings))
    if include_notices:
        timeline.extend(fetch_notices(ak, code, stock_name, lookback_days, warnings))
    forecast = fetch_forecast(ak, code, warnings) if include_forecast else {"comments": ["forecast disabled"]}
    shareholder = fetch_shareholder_events(ak, code, warnings) if include_shareholder else {}
    timeline = sorted(dedupe_timeline(timeline), key=lambda item: item.get("date") or "", reverse=True)
    catalysts = build_catalysts(timeline)
    event_score = score_events(timeline, forecast, shareholder)
    signals = build_signals(timeline, catalysts, event_score)

    data = {
        "metadata": {
            "stock_code": code,
            "stock_name": stock_name,
            "generated_at": now_iso(),
            "lookback_days": lookback_days,
            "data_source": "akshare",
            "warnings": warnings,
        },
        "timeline": timeline,
        "expectation": forecast,
        "catalysts": catalysts,
        "shareholder_events": shareholder,
        "event_score": event_score,
        "signals": signals,
        "data_quality": {},
    }
    data["data_quality"] = quality_report({"timeline": timeline[:3], "event_score": event_score}, warnings)
    root = Path(output_root) if output_root else output_dir(PROJECT_ROOT, code)
    json_path = root / "event_expectation.json"
    md_path = root / "event_expectation.md"
    quality_path = root / "data_quality_event_expectation.md"
    write_json(json_path, data)
    write_text(md_path, render_event_markdown(data))
    write_text(quality_path, render_quality_markdown(data["data_quality"]))
    return {"json_path": str(json_path), "markdown_path": str(md_path), "data_quality_path": str(quality_path), "data": data}


def fetch_stock_name(ak: Any, code: str, warnings: list[str]) -> str | None:
    df = safe_call(warnings, "A股代码名称表", getattr(ak, "stock_info_a_code_name", None))
    if df is not None and not df.empty and {"code", "name"}.issubset(df.columns):
        matched = df[df["code"].astype(str).str.zfill(6) == code]
        if not matched.empty:
            return first_text(matched.iloc[0]["name"])
    return None


def fetch_news(ak: Any, code: str, lookback_days: int, warnings: list[str]) -> list[dict[str, Any]]:
    df = safe_call(warnings, "个股新闻", getattr(ak, "stock_news_em", None), symbol=code)
    cutoff = datetime.now() - timedelta(days=lookback_days)
    rows = []
    if df is None or df.empty:
        return rows
    for row in df.to_dict(orient="records"):
        title = first_text(row.get("title"), row.get("新闻标题"), row.get("标题"))
        date_text = normalize_date(row.get("public_time") or row.get("发布时间") or row.get("date"))
        if not title:
            continue
        if date_text:
            try:
                if datetime.fromisoformat(date_text[:10]) < cutoff:
                    continue
            except ValueError:
                pass
        event_type, sentiment, importance, keywords = classify_event(title)
        rows.append({"date": date_text, "source": "东方财富新闻", "type": event_type, "title": title, "summary": first_text(row.get("content")), "url": first_text(row.get("url")), "sentiment": sentiment, "importance": importance, "keywords": keywords})
    return rows


def fetch_notices(ak: Any, code: str, stock_name: str | None, lookback_days: int, warnings: list[str]) -> list[dict[str, Any]]:
    begin = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    rows = []
    for symbol in ["全部", "重大事项", "财务报告"]:
        df = safe_call(warnings, f"个股公告-{symbol}", getattr(ak, "stock_individual_notice_report", None), security=code, symbol=symbol, begin_date=begin, end_date=end)
        if df is None or df.empty:
            continue
        for row in df.head(50).to_dict(orient="records"):
            title = first_text(row.get("公告标题"), row.get("title"), row.get("公告名称"), row.get("名称"))
            if not title:
                continue
            event_type, sentiment, importance, keywords = classify_event(title)
            rows.append({"date": normalize_date(row.get("公告日期") or row.get("date") or row.get("公告时间")), "source": "公司公告", "type": event_type, "title": title, "summary": "", "url": first_text(row.get("公告链接"), row.get("url")), "sentiment": sentiment, "importance": importance, "keywords": keywords})
    return rows


def fetch_forecast(ak: Any, code: str, warnings: list[str]) -> dict[str, Any]:
    df = safe_call(warnings, "盈利预测", getattr(ak, "stock_profit_forecast_em", None), symbol=code)
    comments = []
    latest = {}
    if df is not None and not df.empty:
        latest = df.tail(1).iloc[0].to_dict()
        comments.append("获取到盈利预测数据")
    profit_change = first_number(*(latest.values() if latest else []))
    return {"forecast_revenue_change": None, "forecast_profit_change": profit_change, "analyst_rating_change": "", "earnings_surprise_hint": "", "comments": comments}


def fetch_shareholder_events(ak: Any, code: str, warnings: list[str]) -> dict[str, Any]:
    gdhs = safe_call(warnings, "股东户数", getattr(ak, "stock_zh_a_gdhs_detail_em", None), symbol=code)
    shareholder_change = None
    if gdhs is not None and not gdhs.empty and len(gdhs) >= 2:
        nums = []
        for _, row in gdhs.tail(2).iterrows():
            nums.append(first_number(*row.to_dict().values()))
        if len(nums) == 2 and nums[0] not in (None, 0) and nums[1] is not None:
            shareholder_change = nums[1] / nums[0] - 1
    hsgt = safe_call(warnings, "北向个股持仓", getattr(ak, "stock_hsgt_individual_em", None), symbol=code)
    northbound_change = None
    if hsgt is not None and not hsgt.empty and len(hsgt) >= 2:
        nums = [first_number(*row.values()) for row in hsgt.tail(2).to_dict(orient="records")]
        if len(nums) == 2 and nums[0] not in (None, 0) and nums[1] is not None:
            northbound_change = nums[1] / nums[0] - 1
    pledge = safe_call(warnings, "个股质押", getattr(ak, "stock_gpzy_individual_pledge_ratio_detail_em", None), symbol=code)
    pledge_ratio = None
    if pledge is not None and not pledge.empty:
        pledge_ratio = first_number(*pledge.tail(1).iloc[0].to_dict().values(), percent=True)
    return {"shareholder_count_change": shareholder_change, "northbound_holding_change": northbound_change, "pledge_ratio": pledge_ratio, "repurchase_events": [], "dividend_events": []}


def classify_event(title: str) -> tuple[str, str, str, list[str]]:
    rules = [
        ("业绩预告", "guidance", "positive"),
        ("业绩快报", "earnings", "positive"),
        ("年报", "earnings", "neutral"),
        ("季报", "earnings", "neutral"),
        ("合同", "order_contract", "positive"),
        ("订单", "order_contract", "positive"),
        ("回购", "capital_action", "positive"),
        ("分红", "capital_action", "positive"),
        ("减持", "shareholder", "negative"),
        ("质押", "shareholder", "negative"),
        ("风险", "risk_warning", "negative"),
        ("诉讼", "risk_warning", "negative"),
        ("监管", "risk_warning", "negative"),
        ("人工智能", "policy_industry", "positive"),
        ("算力", "policy_industry", "positive"),
        ("光模块", "capacity_product", "positive"),
    ]
    hits = [item for item in rules if item[0] in title]
    if not hits:
        return "other", "neutral", "low", []
    word, event_type, sentiment = hits[0]
    importance = "high" if event_type in {"earnings", "guidance", "order_contract", "risk_warning"} else "medium"
    return event_type, sentiment, importance, [item[0] for item in hits]


def dedupe_timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for row in rows:
        key = (row.get("date"), row.get("title"))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def build_catalysts(timeline: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    positive = [item for item in timeline if item.get("sentiment") == "positive"][:10]
    negative = [item for item in timeline if item.get("sentiment") == "negative"][:10]
    neutral = [item for item in timeline if item.get("sentiment") == "neutral"][:10]
    return {"positive": positive, "negative": negative, "neutral": neutral}


def score_events(timeline: list[dict[str, Any]], forecast: dict[str, Any], shareholder: dict[str, Any]) -> dict[str, Any]:
    score = 50
    high_positive = sum(1 for item in timeline if item.get("importance") == "high" and item.get("sentiment") == "positive")
    high_negative = sum(1 for item in timeline if item.get("importance") == "high" and item.get("sentiment") == "negative")
    score += min(25, high_positive * 8)
    score -= min(30, high_negative * 10)
    if forecast.get("comments"):
        score += 5
    if shareholder.get("pledge_ratio") and shareholder.get("pledge_ratio") > 0.3:
        score -= 10
    total = max(0, min(100, score))
    return {"earnings": None, "forecast": 55 if forecast.get("comments") else None, "capital_action": None, "shareholder": None, "news": None, "total": total, "state": score_state(total, ("positive_revision", "catalyst_active", "neutral", "risk_event", "risk_event"))}


def build_signals(timeline: list[dict[str, Any]], catalysts: dict[str, Any], event_score: dict[str, Any]) -> dict[str, list[str]]:
    positives = [f"{item.get('date')}: {item.get('title')}" for item in catalysts.get("positive", [])[:5]]
    risks = [f"{item.get('date')}: {item.get('title')}" for item in catalysts.get("negative", [])[:5]]
    watch = []
    if not timeline:
        watch.append("近期事件数据不足")
    if event_score.get("state") == "neutral":
        watch.append("事件预期暂未形成明确方向")
    return {"positives": positives, "risks": risks, "watch_items": watch}


def render_event_markdown(data: dict[str, Any]) -> str:
    md = data["metadata"]
    lines = [
        f"# {md.get('stock_code')} {md.get('stock_name') or ''} 事件与预期分析",
        "",
        f"- 生成时间: {md.get('generated_at')}",
        f"- 事件分: {data['event_score'].get('total')}",
        f"- 状态: {data['event_score'].get('state')}",
        "",
        "## 1. 总览",
        "",
        render_signals(data["signals"]),
        "",
        "## 2. 重要事件时间线",
        "",
        render_table(data["timeline"][:30], [("date", "日期"), ("source", "来源"), ("type", "类型"), ("title", "标题"), ("sentiment", "语气"), ("importance", "重要性")]),
        "",
        "## 3. 盈利预测与预期变化",
        "",
        render_kv_table(data["expectation"], [("forecast_profit_change", "预测变化线索"), ("earnings_surprise_hint", "业绩超预期线索")]),
        "",
        "## 4. 股东、回购、质押与分红",
        "",
        render_kv_table(data["shareholder_events"], [("shareholder_count_change", "股东户数变化"), ("northbound_holding_change", "北向持仓变化"), ("pledge_ratio", "质押比例")], {"shareholder_count_change", "northbound_holding_change", "pledge_ratio"}),
    ]
    return "\n".join(lines)


def render_signals(signals: dict[str, list[str]]) -> str:
    lines = []
    for title, key in [("积极线索", "positives"), ("风险事件", "risks"), ("关注项", "watch_items")]:
        values = signals.get(key) or []
        lines.extend([f"### {title}", ""])
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
    parser = argparse.ArgumentParser(description="Analyze A-share events and expectations.")
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--lookback-days", type=int, default=180)
    args = parser.parse_args()
    result = get_event_expectation(args.stock_code, args.lookback_days)
    print(result["json_path"])


if __name__ == "__main__":
    main()
