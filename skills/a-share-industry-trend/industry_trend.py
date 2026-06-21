from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import sys

SKILL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SKILL_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "skills"))

from a_share_common.market_data import fetch_index_hist, fetch_industry_hist, fetch_stock_hist, return_n, trend_score_from_snapshot, trend_snapshot
from a_share_common.utils import (
    first_number,
    first_text,
    format_decimal,
    normalize_stock_code,
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


def get_industry_trend(
    stock_code: str | None = None,
    industry_name: str | None = None,
    lookback_days: int = 500,
    benchmark: str = "沪深300",
    top_n_constituents: int = 15,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    import akshare as ak

    warnings: list[str] = []
    code = normalize_stock_code(stock_code) if stock_code else None
    industry = industry_name or resolve_industry(ak, code, warnings)
    industry_hist = fetch_industry_hist(ak, industry, lookback_days, warnings)
    benchmark_hist = fetch_index_hist(ak, benchmark, lookback_days, warnings)
    trend = trend_snapshot(industry_hist)
    trend["score"] = trend_score_from_snapshot(trend)
    relative_strength = build_relative_strength(industry_hist, benchmark_hist, benchmark)
    constituents = fetch_constituents(ak, industry, warnings)
    summary_snapshot = fetch_industry_summary(ak, industry, warnings)
    breadth = build_breadth(ak, constituents, top_n_constituents, lookback_days, warnings)
    if breadth.get("score") is None:
        breadth = build_breadth_from_summary(summary_snapshot)
    capital_flow = build_capital_flow(ak, industry, warnings, summary_snapshot)
    valuation = build_valuation_hint(ak, industry, warnings)
    industry_score = score_industry(trend, relative_strength, breadth, capital_flow, valuation)
    signals = build_signals(industry_score, trend, relative_strength, breadth, capital_flow, valuation)
    constituent_count = len(constituents) or breadth.get("sample_count") or 0

    data = {
        "metadata": {
            "stock_code": code,
            "industry_name": industry,
            "generated_at": now_iso(),
            "lookback_days": lookback_days,
            "data_source": "akshare",
            "warnings": warnings,
        },
        "industry_profile": {
            "industry_name": industry,
            "industry_source": "explicit_or_profile_keyword",
            "constituent_count": constituent_count,
            "matched_by_stock_code": code,
        },
        "industry_trend": trend,
        "relative_strength": relative_strength,
        "breadth": breadth,
        "capital_flow": capital_flow,
        "valuation": valuation,
        "industry_score": industry_score,
        "signals": signals,
        "data_quality": {},
    }
    data["data_quality"] = quality_report({"industry_profile": data["industry_profile"], "industry_score": industry_score}, warnings)
    root = Path(output_root) if output_root else output_dir(PROJECT_ROOT, "industry", industry)
    json_path = root / "industry_trend.json"
    md_path = root / "industry_trend.md"
    quality_path = root / "data_quality.md"
    write_json(json_path, data)
    write_text(md_path, render_industry_markdown(data))
    write_text(quality_path, render_quality_markdown(data["data_quality"]))
    return {"json_path": str(json_path), "markdown_path": str(md_path), "data_quality_path": str(quality_path), "data": data}


def resolve_industry(ak: Any, stock_code: str | None, warnings: list[str]) -> str:
    if not stock_code:
        return "通信设备"
    df = safe_call(warnings, "巨潮公司概况", getattr(ak, "stock_profile_cninfo", None), symbol=stock_code)
    raw = ""
    if df is not None and not df.empty:
        row = df.iloc[0].to_dict()
        raw = first_text(row.get("所属行业"), row.get("主营业务"), row.get("经营范围")) or ""
    return map_industry_name(raw, stock_code)


def map_industry_name(raw: str, stock_code: str | None = None) -> str:
    text = raw or ""
    rules = [
        ("通信", "通信设备"),
        ("光通信", "通信设备"),
        ("半导体", "半导体"),
        ("计算机", "软件开发"),
        ("软件", "软件开发"),
        ("银行", "银行"),
        ("证券", "证券"),
        ("保险", "保险"),
        ("白酒", "酿酒行业"),
        ("医药", "化学制药"),
        ("新能源", "电池"),
        ("汽车", "汽车整车"),
        ("房地产", "房地产开发"),
    ]
    for key, value in rules:
        if key in text:
            return value
    if stock_code == "300308":
        return "通信设备"
    return "通信设备"


def build_relative_strength(industry_hist: Any, benchmark_hist: Any, benchmark: str) -> dict[str, Any]:
    rs20 = diff(return_n(industry_hist, 20), return_n(benchmark_hist, 20))
    rs60 = diff(return_n(industry_hist, 60), return_n(benchmark_hist, 60))
    rs120 = diff(return_n(industry_hist, 120), return_n(benchmark_hist, 120))
    score = 50
    for item in [rs20, rs60, rs120]:
        if item is not None:
            score += max(-15, min(15, item * 80))
    return {"benchmark": benchmark, "rs_20d": rs20, "rs_60d": rs60, "rs_120d": rs120, "rs_slope": diff(rs20, rs120), "score": round(max(0, min(100, score)), 2), "state": score_state(score, ("leading", "positive", "neutral", "weakening", "lagging"))}


def fetch_constituents(ak: Any, industry: str, warnings: list[str]) -> list[dict[str, Any]]:
    df = safe_call(warnings, f"行业成分-{industry}", getattr(ak, "stock_board_industry_cons_em", None), symbol=industry)
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


def fetch_industry_summary(ak: Any, industry: str, warnings: list[str]) -> dict[str, Any]:
    df = safe_call(warnings, "同花顺行业汇总", getattr(ak, "stock_board_industry_summary_ths", None))
    if df is not None and not df.empty:
        for row in df.to_dict(orient="records"):
            name = first_text(row.get("板块"), row.get("name"))
            if name == industry:
                return row

    info = safe_call(warnings, f"同花顺行业摘要-{industry}", getattr(ak, "stock_board_industry_info_ths", None), symbol=industry)
    if info is None or info.empty or not {"项目", "值"}.issubset(info.columns):
        return {}
    return {str(row["项目"]): row["值"] for row in info.to_dict(orient="records")}


def build_breadth(ak: Any, constituents: list[dict[str, Any]], top_n: int, lookback_days: int, warnings: list[str]) -> dict[str, Any]:
    rows = []
    for row in constituents[:top_n]:
        code = first_text(row.get("代码"), row.get("code"))
        if not code:
            continue
        hist = fetch_stock_hist(ak, str(code).zfill(6), min(lookback_days, 260), warnings, "qfq")
        snap = trend_snapshot(hist)
        rows.append(snap)
    if not rows:
        return {"above_ma20_ratio": None, "above_ma60_ratio": None, "positive_return_20d_ratio": None, "positive_return_60d_ratio": None, "new_high_60d_ratio": None, "median_return_60d": None, "advance_ratio": None, "up_count": None, "down_count": None, "sample_count": None, "diffusion_state": "unknown", "score": None, "source": "constituent_history"}
    above20 = ratio(rows, lambda item: item.get("above_ma20") is True)
    above60 = ratio(rows, lambda item: item.get("above_ma60") is True)
    pos20 = ratio(rows, lambda item: (item.get("return_20d") or -1) > 0)
    pos60 = ratio(rows, lambda item: (item.get("return_60d") or -1) > 0)
    returns60 = sorted([item.get("return_60d") for item in rows if item.get("return_60d") is not None])
    median60 = returns60[len(returns60) // 2] if returns60 else None
    score = ((above20 or 0) + (above60 or 0) + (pos20 or 0) + (pos60 or 0)) * 25
    state = "broad_strength" if score >= 70 else "diffusing" if score >= 55 else "mixed" if score >= 40 else "narrow_or_weak"
    return {"above_ma20_ratio": above20, "above_ma60_ratio": above60, "positive_return_20d_ratio": pos20, "positive_return_60d_ratio": pos60, "new_high_60d_ratio": None, "median_return_60d": median60, "advance_ratio": None, "up_count": None, "down_count": None, "sample_count": len(rows), "diffusion_state": state, "score": round(score, 2), "source": "constituent_history"}


def build_breadth_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    up_count, down_count = parse_up_down(summary)
    sample_count = None if up_count is None or down_count is None else up_count + down_count
    advance_ratio = None if not sample_count else up_count / sample_count
    sector_change = to_number(first_text(summary.get("涨跌幅"), summary.get("板块涨幅")), percent=True)
    score = None
    if advance_ratio is not None:
        score = advance_ratio * 100
        if sector_change is not None:
            score += max(-10, min(10, sector_change * 200))
        score = round(max(0, min(100, score)), 2)
    state = "unknown"
    if score is not None:
        state = "broad_strength" if score >= 70 else "diffusing" if score >= 55 else "mixed" if score >= 40 else "narrow_or_weak"
    return {
        "above_ma20_ratio": None,
        "above_ma60_ratio": None,
        "positive_return_20d_ratio": None,
        "positive_return_60d_ratio": None,
        "new_high_60d_ratio": None,
        "median_return_60d": None,
        "advance_ratio": advance_ratio,
        "up_count": up_count,
        "down_count": down_count,
        "sample_count": sample_count,
        "diffusion_state": state,
        "score": score,
        "source": "ths_summary",
    }


def parse_up_down(summary: dict[str, Any]) -> tuple[int | None, int | None]:
    up = first_number(summary.get("上涨家数"))
    down = first_number(summary.get("下跌家数"))
    if up is not None and down is not None:
        return int(up), int(down)
    text = first_text(summary.get("涨跌家数"))
    if not text or "/" not in text:
        return None, None
    left, right = text.split("/", 1)
    up = first_number(left)
    down = first_number(right)
    if up is None or down is None:
        return None, None
    return int(up), int(down)


def build_capital_flow(ak: Any, industry: str, warnings: list[str], summary: dict[str, Any] | None = None) -> dict[str, Any]:
    df = safe_call(warnings, f"行业资金流-{industry}", getattr(ak, "stock_sector_fund_flow_hist", None), symbol=industry)
    values = []
    if df is not None and not df.empty:
        for row in df.tail(20).to_dict(orient="records"):
            values.append(first_number(*row.values()))
    values = [item for item in values if item is not None]
    flow20 = sum(values[-20:]) if values else None
    flow5 = sum(values[-5:]) if values else None
    flow1 = values[-1] if values else None
    if flow1 is None and summary:
        flow1 = first_number(summary.get("净流入"), summary.get("资金净流入(亿)"))
    score = 50 if flow20 is None else 65 if flow20 > 0 else 35
    if flow20 is None and flow1 is not None:
        score = 60 if flow1 > 0 else 40
    state = "inflow" if (flow20 or flow1 or 0) > 0 else "outflow" if (flow20 or flow1 or 0) < 0 else "unknown"
    return {"main_net_inflow_1d": flow1, "main_net_inflow_5d": flow5, "main_net_inflow_20d": flow20, "northbound_rank": None, "flow_state": state, "score": score, "comments": []}


def build_valuation_hint(ak: Any, industry: str, warnings: list[str]) -> dict[str, Any]:
    df = safe_call(warnings, "行业市盈率", getattr(ak, "stock_industry_pe_ratio_cninfo", None), symbol="证监会行业分类")
    pe_latest = None
    if df is not None and not df.empty:
        for row in df.tail(20).to_dict(orient="records"):
            if industry[:2] in "".join(str(v) for v in row.values()):
                pe_latest = first_number(*row.values())
                break
    return {"pe_latest": pe_latest, "pe_percentile": None, "valuation_state": "unknown", "score": 50 if pe_latest is None else 60 if pe_latest < 30 else 40, "comments": []}


def score_industry(trend: dict[str, Any], rs: dict[str, Any], breadth: dict[str, Any], flow: dict[str, Any], valuation: dict[str, Any]) -> dict[str, Any]:
    total = (trend.get("score") or 50) * 0.30 + (rs.get("score") or 50) * 0.25 + (breadth.get("score") or 50) * 0.20 + (flow.get("score") or 50) * 0.15 + (valuation.get("score") or 50) * 0.10
    return {"trend": trend.get("score"), "relative_strength": rs.get("score"), "breadth": breadth.get("score"), "capital_flow": flow.get("score"), "valuation": valuation.get("score"), "total": round(total, 2), "state": score_state(total, ("leading", "improving", "neutral", "weakening", "lagging"))}


def build_signals(score: dict[str, Any], trend: dict[str, Any], rs: dict[str, Any], breadth: dict[str, Any], flow: dict[str, Any], valuation: dict[str, Any]) -> dict[str, list[str]]:
    positives: list[str] = []
    risks: list[str] = []
    watch: list[str] = []
    if score.get("state") in {"leading", "improving"}:
        positives.append("行业综合状态偏强")
    if trend.get("trend_stage") in {"markup", "breakout"}:
        positives.append("行业指数趋势向上")
    if rs.get("state") in {"leading", "positive"}:
        positives.append("行业相对基准走强")
    if breadth.get("diffusion_state") in {"broad_strength", "diffusing"}:
        positives.append("行业内扩散度较好")
    if flow.get("flow_state") == "outflow":
        watch.append("行业资金流偏流出")
    if score.get("state") in {"weakening", "lagging"}:
        risks.append("行业趋势偏弱")
    return {"positives": positives, "risks": risks, "watch_items": watch}


def ratio(rows: list[dict[str, Any]], predicate: Any) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if predicate(row)) / len(rows)


def diff(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def render_industry_markdown(data: dict[str, Any]) -> str:
    md = data["metadata"]
    lines = [
        f"# {md.get('industry_name')} 行业趋势分析",
        "",
        f"- 生成时间: {md.get('generated_at')}",
        f"- 综合分: {format_decimal(data['industry_score'].get('total'))}",
        f"- 状态: {data['industry_score'].get('state')}",
        "",
        "## 1. 总览",
        "",
        render_signals(data["signals"]),
        "",
        "## 2. 行业趋势",
        "",
        render_kv_table(data["industry_trend"], [("latest_close", "最新点位"), ("return_20d", "20日收益"), ("return_60d", "60日收益"), ("return_120d", "120日收益"), ("ma_alignment", "均线"), ("trend_stage", "阶段")], {"return_20d", "return_60d", "return_120d"}),
        "",
        "## 3. 相对强弱",
        "",
        render_kv_table(data["relative_strength"], [("benchmark", "基准"), ("rs_20d", "20日"), ("rs_60d", "60日"), ("rs_120d", "120日"), ("state", "状态")], {"rs_20d", "rs_60d", "rs_120d"}),
        "",
        "## 4. 行业内扩散",
        "",
        render_kv_table(data["breadth"], [("above_ma20_ratio", "站上MA20比例"), ("above_ma60_ratio", "站上MA60比例"), ("positive_return_60d_ratio", "60日上涨比例"), ("median_return_60d", "60日收益中位数"), ("advance_ratio", "当日上涨家数占比"), ("up_count", "上涨家数"), ("down_count", "下跌家数"), ("source", "来源"), ("diffusion_state", "扩散状态")], {"above_ma20_ratio", "above_ma60_ratio", "positive_return_60d_ratio", "median_return_60d", "advance_ratio"}),
        "",
        "## 5. 资金流",
        "",
        render_kv_table(data["capital_flow"], [("main_net_inflow_1d", "1日净流"), ("main_net_inflow_5d", "5日净流"), ("main_net_inflow_20d", "20日净流"), ("flow_state", "状态")]),
        "",
        "## 6. 行业估值",
        "",
        render_kv_table(data["valuation"], [("pe_latest", "PE"), ("valuation_state", "估值状态")]),
    ]
    return "\n".join(lines)


def render_signals(signals: dict[str, list[str]]) -> str:
    lines = []
    for title, key in [("优势", "positives"), ("风险", "risks"), ("关注项", "watch_items")]:
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
    parser = argparse.ArgumentParser(description="Analyze A-share industry trend.")
    parser.add_argument("--stock-code", default=None)
    parser.add_argument("--industry-name", default=None)
    parser.add_argument("--lookback-days", type=int, default=500)
    parser.add_argument("--benchmark", default="沪深300")
    args = parser.parse_args()
    result = get_industry_trend(args.stock_code, args.industry_name, args.lookback_days, args.benchmark)
    print(result["json_path"])


if __name__ == "__main__":
    main()
