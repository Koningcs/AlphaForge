from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import sys

SKILL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SKILL_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "skills"))

from a_share_common.market_data import (
    amount_ratio,
    fetch_index_hist,
    fetch_industry_hist,
    fetch_stock_hist,
    trend_score_from_snapshot,
    trend_snapshot,
    up_down_amount_ratio,
)
from a_share_common.utils import (
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


def get_trend(
    stock_code: str,
    benchmark: str = "沪深300",
    industry_name: str | None = None,
    lookback_days: int = 500,
    adjust: str = "qfq",
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    import akshare as ak

    warnings: list[str] = []
    code = normalize_stock_code(stock_code)
    stock_name = fetch_stock_name(ak, code, warnings)
    stock_hist = fetch_stock_hist(ak, code, lookback_days, warnings, adjust)
    benchmark_hist = fetch_index_hist(ak, benchmark, lookback_days, warnings)
    industry_hist = fetch_industry_hist(ak, industry_name, lookback_days, warnings) if industry_name else None

    price_trend = trend_snapshot(stock_hist)
    relative_strength = build_relative_strength(stock_hist, benchmark_hist, industry_hist, benchmark)
    volume_price = build_volume_price(stock_hist, price_trend)
    volatility_risk = build_volatility_risk(price_trend)
    stage = build_stage(price_trend, volume_price, relative_strength)
    trend_score = score_trend(price_trend, relative_strength, volume_price, volatility_risk, stage)
    signals = build_signals(trend_score, price_trend, relative_strength, volume_price, volatility_risk, stage)

    data = {
        "metadata": {
            "stock_code": code,
            "stock_name": stock_name,
            "generated_at": now_iso(),
            "lookback_days": lookback_days,
            "adjust": adjust,
            "data_source": "akshare",
            "warnings": warnings,
        },
        "price_trend": price_trend,
        "relative_strength": relative_strength,
        "volume_price": volume_price,
        "volatility_risk": volatility_risk,
        "stage": stage,
        "trend_score": trend_score,
        "signals": signals,
        "data_quality": {},
    }
    data["data_quality"] = quality_report({"price_trend": price_trend, "trend_score": trend_score}, warnings)

    root = Path(output_root) if output_root else output_dir(PROJECT_ROOT, code)
    json_path = root / "trend.json"
    md_path = root / "trend.md"
    quality_path = root / "data_quality_trend.md"
    write_json(json_path, data)
    write_text(md_path, render_trend_markdown(data))
    write_text(quality_path, render_quality_markdown(data["data_quality"]))
    return {"json_path": str(json_path), "markdown_path": str(md_path), "data_quality_path": str(quality_path), "data": data}


def fetch_stock_name(ak: Any, code: str, warnings: list[str]) -> str | None:
    df = safe_call(warnings, "A股代码名称表", getattr(ak, "stock_info_a_code_name", None))
    if df is not None and not df.empty and {"code", "name"}.issubset(df.columns):
        matched = df[df["code"].astype(str).str.zfill(6) == code]
        if not matched.empty:
            return first_text(matched.iloc[0]["name"])
    return None


def build_relative_strength(stock_hist: Any, benchmark_hist: Any, industry_hist: Any, benchmark: str) -> dict[str, Any]:
    from a_share_common.market_data import return_n

    stock_20 = return_n(stock_hist, 20)
    stock_60 = return_n(stock_hist, 60)
    stock_120 = return_n(stock_hist, 120)
    bench_20 = return_n(benchmark_hist, 20)
    bench_60 = return_n(benchmark_hist, 60)
    bench_120 = return_n(benchmark_hist, 120)
    industry_60 = return_n(industry_hist, 60) if industry_hist is not None else None
    rs20 = diff(stock_20, bench_20)
    rs60 = diff(stock_60, bench_60)
    rs120 = diff(stock_120, bench_120)
    industry_rs = diff(stock_60, industry_60)
    score = 50.0
    for item in [rs20, rs60, rs120]:
        if item is not None:
            score += max(-15, min(15, item * 80))
    state = score_state(score, ("strong", "positive", "neutral", "weak", "lagging"))
    return {"benchmark": benchmark, "rs_20d": rs20, "rs_60d": rs60, "rs_120d": rs120, "industry_rs_60d": industry_rs, "rs_slope": diff(rs20, rs120), "score": round(max(0, min(100, score)), 2), "state": state}


def build_volume_price(stock_hist: Any, price: dict[str, Any]) -> dict[str, Any]:
    amount = amount_ratio(stock_hist, 20, 120)
    up_down = up_down_amount_ratio(stock_hist, 60)
    ret60 = price.get("return_60d")
    breakout = bool(price.get("above_ma60") and price.get("above_ma120") and ret60 is not None and ret60 > 0.1 and (amount or 0) > 1.1)
    high_volume_stalling = bool((amount or 0) > 1.5 and ret60 is not None and ret60 < 0.05)
    distribution = bool((amount or 0) > 1.3 and price.get("return_20d") is not None and price.get("return_20d") < -0.05)
    score = 50
    if amount is not None:
        score += max(-15, min(15, (amount - 1) * 30))
    if up_down is not None:
        score += max(-15, min(15, (up_down - 1) * 20))
    if high_volume_stalling or distribution:
        score -= 15
    if breakout:
        score += 10
    return {
        "amount_ratio_20d_120d": amount,
        "up_day_amount_to_down_day_amount": up_down,
        "breakout_with_volume": breakout,
        "high_volume_stalling": high_volume_stalling,
        "distribution_warning": distribution,
        "score": round(max(0, min(100, score)), 2),
        "comments": [],
    }


def build_volatility_risk(price: dict[str, Any]) -> dict[str, Any]:
    close = price.get("latest_close")
    ma60 = price.get("ma60")
    ma120 = price.get("ma120")
    distance60 = None if close is None or ma60 in (None, 0) else close / ma60 - 1
    distance120 = None if close is None or ma120 in (None, 0) else close / ma120 - 1
    risk_score = 70
    for item in [price.get("volatility_20d"), price.get("volatility_60d")]:
        if item is not None and item > 0.55:
            risk_score -= 10
    if distance60 is not None and distance60 > 0.35:
        risk_score -= 15
    if price.get("max_drawdown_120d") is not None and price.get("max_drawdown_120d") < -0.3:
        risk_score -= 10
    return {
        "volatility_20d": price.get("volatility_20d"),
        "volatility_60d": price.get("volatility_60d"),
        "atr20": price.get("atr20"),
        "max_drawdown_60d": price.get("max_drawdown_60d"),
        "max_drawdown_120d": price.get("max_drawdown_120d"),
        "distance_to_ma60": distance60,
        "distance_to_ma120": distance120,
        "score": round(max(0, min(100, risk_score)), 2),
        "risk_state": score_state(risk_score, ("low", "contained", "normal", "elevated", "high")),
    }


def build_stage(price: dict[str, Any], volume: dict[str, Any], rs: dict[str, Any]) -> dict[str, Any]:
    name = price.get("trend_stage", "unknown")
    evidence = [f"均线排列: {price.get('ma_alignment')}", f"60日相对强弱: {format_decimal(rs.get('rs_60d'))}", f"量能比: {format_decimal(volume.get('amount_ratio_20d_120d'))}"]
    confidence = 0.65
    if name in {"markup", "breakout", "downtrend"}:
        confidence = 0.78
    return {"name": name, "confidence": confidence, "evidence": evidence}


def score_trend(price: dict[str, Any], rs: dict[str, Any], volume: dict[str, Any], risk: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
    price_score = trend_score_from_snapshot(price)
    stage_score = {"markup": 90, "breakout": 78, "base_repair": 62, "range": 50, "high_volatility": 45, "downtrend": 20}.get(stage.get("name"), 50)
    total = price_score * 0.30 + (rs.get("score") or 50) * 0.25 + (volume.get("score") or 50) * 0.20 + (risk.get("score") or 50) * 0.15 + stage_score * 0.10
    return {"price_direction": price_score, "relative_strength": rs.get("score"), "volume_price": volume.get("score"), "volatility_risk": risk.get("score"), "stage": stage_score, "total": round(total, 2), "state": score_state(total, ("strong_uptrend", "positive", "neutral", "weak", "downtrend"))}


def build_signals(score: dict[str, Any], price: dict[str, Any], rs: dict[str, Any], volume: dict[str, Any], risk: dict[str, Any], stage: dict[str, Any]) -> dict[str, list[str]]:
    positives: list[str] = []
    risks: list[str] = []
    watch: list[str] = []
    if score.get("state") in {"strong_uptrend", "positive"}:
        positives.append("趋势评分偏强")
    if price.get("ma_alignment") == "bullish":
        positives.append("均线多头排列")
    if rs.get("state") in {"strong", "positive"}:
        positives.append("相对基准表现较强")
    if volume.get("breakout_with_volume"):
        positives.append("突破伴随量能放大")
    if risk.get("risk_state") in {"elevated", "high"}:
        watch.append("波动或均线偏离风险较高")
    if volume.get("distribution_warning"):
        risks.append("出现放量下跌/派发警示")
    if stage.get("name") == "downtrend":
        risks.append("趋势阶段为下降趋势")
    return {"positives": positives, "risks": risks, "watch_items": watch}


def diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def render_trend_markdown(data: dict[str, Any]) -> str:
    md = data["metadata"]
    lines = [
        f"# {md.get('stock_code')} {md.get('stock_name') or ''} 趋势分析",
        "",
        f"- 生成时间: {md.get('generated_at')}",
        f"- 趋势总分: {format_decimal(data['trend_score'].get('total'))}",
        f"- 状态: {data['trend_score'].get('state')}",
        "",
        "## 1. 总览",
        "",
        render_signals(data["signals"]),
        "",
        "## 2. 价格方向",
        "",
        render_kv_table(data["price_trend"], [("latest_close", "最新收盘"), ("return_20d", "20日收益"), ("return_60d", "60日收益"), ("return_120d", "120日收益"), ("ma_alignment", "均线排列"), ("trend_stage", "阶段")], {"return_20d", "return_60d", "return_120d"}),
        "",
        "## 3. 相对强弱",
        "",
        render_kv_table(data["relative_strength"], [("benchmark", "基准"), ("rs_20d", "20日相对强弱"), ("rs_60d", "60日相对强弱"), ("rs_120d", "120日相对强弱"), ("state", "状态")], {"rs_20d", "rs_60d", "rs_120d"}),
        "",
        "## 4. 量价质量",
        "",
        render_kv_table(data["volume_price"], [("amount_ratio_20d_120d", "20/120日成交额比"), ("up_day_amount_to_down_day_amount", "上涨/下跌日成交额比"), ("breakout_with_volume", "放量突破"), ("distribution_warning", "派发警示")]),
        "",
        "## 5. 波动与风险",
        "",
        render_kv_table(data["volatility_risk"], [("volatility_20d", "20日波动率"), ("max_drawdown_120d", "120日最大回撤"), ("distance_to_ma60", "距MA60"), ("risk_state", "风险状态")], {"volatility_20d", "max_drawdown_120d", "distance_to_ma60"}),
        "",
        "## 6. 趋势阶段",
        "",
        f"- 阶段: {data['stage'].get('name')}",
        *[f"- {item}" for item in data["stage"].get("evidence", [])],
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
    parser = argparse.ArgumentParser(description="Analyze A-share stock trend.")
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--benchmark", default="沪深300")
    parser.add_argument("--industry-name", default=None)
    parser.add_argument("--lookback-days", type=int, default=500)
    parser.add_argument("--adjust", default="qfq")
    args = parser.parse_args()
    result = get_trend(args.stock_code, args.benchmark, args.industry_name, args.lookback_days, args.adjust)
    print(result["json_path"])


if __name__ == "__main__":
    main()
