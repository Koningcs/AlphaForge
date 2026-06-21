from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import sys

SKILL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SKILL_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "skills"))

from a_share_common.utils import format_decimal, load_json, normalize_stock_code, now_iso, output_dir, quality_report, render_kv_table, render_table, write_json, write_text


def get_risk_decision(
    stock_code: str,
    market_environment_path: str | None = None,
    industry_trend_path: str | None = None,
    fundamental_path: str | None = None,
    trend_path: str | None = None,
    event_expectation_path: str | None = None,
    risk_profile: str = "balanced",
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    code = normalize_stock_code(stock_code)
    paths = resolve_paths(code, market_environment_path, industry_trend_path, fundamental_path, trend_path, event_expectation_path)
    modules = {name: load_optional(path, warnings, name) for name, path in paths.items()}
    module_states = map_module_states(modules)
    decision = build_decision(module_states, risk_profile)
    evidence = build_evidence(modules, module_states)
    risk_register = build_risk_register(modules, module_states)
    invalidation = build_invalidation_conditions(modules, module_states)
    tracking_plan = build_tracking_plan(modules, module_states)
    position_framework = build_position_framework(decision, risk_profile)
    missing_modules = [name for name, value in modules.items() if not value]
    stock_name = ((modules.get("fundamental") or {}).get("metadata") or {}).get("stock_name")

    data = {
        "metadata": {
            "stock_code": code,
            "stock_name": stock_name,
            "generated_at": now_iso(),
            "risk_profile": risk_profile,
            "data_source": "module_outputs",
            "warnings": warnings,
        },
        "module_states": module_states,
        "decision": decision,
        "evidence": evidence,
        "risk_register": risk_register,
        "invalidation_conditions": invalidation,
        "tracking_plan": tracking_plan,
        "position_framework": position_framework,
        "data_quality": {},
    }
    data["data_quality"] = quality_report({"decision": decision}, warnings)
    data["data_quality"]["missing_modules"] = missing_modules

    root = Path(output_root) if output_root else output_dir(PROJECT_ROOT, code)
    json_path = root / "decision.json"
    md_path = root / "decision.md"
    quality_path = root / "data_quality_decision.md"
    write_json(json_path, data)
    write_text(md_path, render_decision_markdown(data))
    write_text(quality_path, render_quality_markdown(data["data_quality"]))
    return {"json_path": str(json_path), "markdown_path": str(md_path), "data_quality_path": str(quality_path), "data": data}


def resolve_paths(code: str, market: str | None, industry: str | None, fundamental: str | None, trend: str | None, event: str | None) -> dict[str, Path]:
    outputs = PROJECT_ROOT / "outputs"
    market_candidates = sorted((outputs / "market").glob("*/market_environment.json")) if (outputs / "market").exists() else []
    industry_candidates = sorted((outputs / "industry").glob("*/industry_trend.json")) if (outputs / "industry").exists() else []
    return {
        "market_environment": Path(market) if market else (market_candidates[-1] if market_candidates else outputs / "market" / "missing" / "market_environment.json"),
        "industry_trend": Path(industry) if industry else (industry_candidates[-1] if industry_candidates else outputs / "industry" / "missing" / "industry_trend.json"),
        "fundamental": Path(fundamental) if fundamental else outputs / code / "fundamental.json",
        "trend": Path(trend) if trend else outputs / code / "trend.json",
        "event_expectation": Path(event) if event else outputs / code / "event_expectation.json",
    }


def load_optional(path: Path, warnings: list[str], name: str) -> dict[str, Any] | None:
    if not path.exists():
        warnings.append(f"{name} 缺失: {path}")
        return None
    try:
        return load_json(path)
    except Exception as exc:
        warnings.append(f"{name} 读取失败: {type(exc).__name__}: {exc}")
        return None


def map_module_states(modules: dict[str, dict[str, Any] | None]) -> dict[str, dict[str, Any]]:
    return {
        "market_environment": state_from_score((modules.get("market_environment") or {}).get("environment_score", {}).get("total"), (70, 45), (modules.get("market_environment") or {}).get("environment_score", {}).get("state")),
        "industry_trend": state_from_score((modules.get("industry_trend") or {}).get("industry_score", {}).get("total"), (70, 45), (modules.get("industry_trend") or {}).get("industry_score", {}).get("state")),
        "fundamental": state_from_fundamental(modules.get("fundamental")),
        "trend": state_from_score((modules.get("trend") or {}).get("trend_score", {}).get("total"), (70, 45), (modules.get("trend") or {}).get("trend_score", {}).get("state")),
        "event_expectation": state_from_score((modules.get("event_expectation") or {}).get("event_score", {}).get("total"), (65, 40), (modules.get("event_expectation") or {}).get("event_score", {}).get("state")),
    }


def state_from_score(score: float | None, thresholds: tuple[int, int], summary: str | None) -> dict[str, Any]:
    if score is None:
        return {"state": "unknown", "score": None, "summary": summary or ""}
    positive, negative = thresholds
    state = "positive" if score >= positive else "negative" if score < negative else "neutral"
    return {"state": state, "score": score, "summary": summary or ""}


def state_from_fundamental(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {"state": "unknown", "score": None, "summary": ""}
    analysis = data.get("analysis_summary", {})
    overall = analysis.get("overall", {})
    positives = len(overall.get("strengths", []))
    risks = len(overall.get("risks", []))
    watch = len(overall.get("watch_items", []))
    score = max(0, min(100, 50 + positives * 6 - risks * 10 - watch * 3))
    state = "positive" if score >= 70 else "negative" if score < 45 else "neutral"
    return {"state": state, "score": score, "summary": "；".join((overall.get("strengths") or [])[:3])}


def build_decision(module_states: dict[str, dict[str, Any]], risk_profile: str) -> dict[str, Any]:
    weights = {"market_environment": 0.15, "industry_trend": 0.20, "fundamental": 0.30, "trend": 0.25, "event_expectation": 0.10}
    if risk_profile == "conservative":
        weights.update({"market_environment": 0.20, "fundamental": 0.35, "trend": 0.20, "event_expectation": 0.05})
    elif risk_profile == "aggressive":
        weights.update({"market_environment": 0.10, "fundamental": 0.25, "trend": 0.35, "event_expectation": 0.15})
    available = {k: v for k, v in module_states.items() if v.get("score") is not None}
    total_weight = sum(weights[k] for k in available)
    score = None if not available else sum(available[k]["score"] * weights[k] for k in available) / total_weight
    states = [v["state"] for v in module_states.values()]
    if states.count("unknown") >= 3:
        classification = "insufficient_data"
    elif score is None:
        classification = "insufficient_data"
    elif score >= 80 and states.count("negative") == 0:
        classification = "core_candidate"
    elif score >= 65:
        classification = "watch_candidate"
    elif score >= 50:
        classification = "neutral_watch"
    elif score >= 35:
        classification = "high_risk_watch"
    else:
        classification = "avoid"
    if module_states["fundamental"]["state"] == "negative" and module_states["trend"]["state"] == "negative":
        classification = "avoid"
    confidence = None if score is None else min(0.9, 0.45 + 0.08 * (5 - states.count("unknown")))
    return {"classification": classification, "confidence": confidence, "decision_score": None if score is None else round(score, 2), "summary": decision_summary(classification), "not_investment_advice": True}


def decision_summary(classification: str) -> str:
    return {
        "core_candidate": "多维度共振，进入核心研究候选。",
        "watch_candidate": "具备研究价值，但仍需等待部分维度确认。",
        "neutral_watch": "维度分歧或优势不足，适合观察。",
        "high_risk_watch": "风险较高，仅适合作为观察样本。",
        "avoid": "多项维度不支持，暂不纳入优先研究。",
        "insufficient_data": "数据不足，无法形成稳健判断。",
    }.get(classification, "")


def build_evidence(modules: dict[str, Any], states: dict[str, Any]) -> dict[str, list[str]]:
    supporting: list[str] = []
    opposing: list[str] = []
    uncertainties: list[str] = []
    for name, state in states.items():
        text = f"{name}: {state.get('state')} ({format_decimal(state.get('score'))}) {state.get('summary')}"
        if state.get("state") == "positive":
            supporting.append(text)
        elif state.get("state") == "negative":
            opposing.append(text)
        elif state.get("state") == "unknown":
            uncertainties.append(text)
    return {"supporting": supporting, "opposing": opposing, "uncertainties": uncertainties}


def build_risk_register(modules: dict[str, Any], states: dict[str, Any]) -> list[dict[str, Any]]:
    risks = []
    if states["market_environment"]["state"] == "negative":
        risks.append(risk("market_risk", "high", "市场环境偏弱", "market_environment.state", "环境转入 defensive/hostile"))
    if states["industry_trend"]["state"] == "negative":
        risks.append(risk("industry_risk", "medium", "行业趋势偏弱", "industry_score.state", "行业跌破中期趋势"))
    fundamental = modules.get("fundamental") or {}
    valuation_watch = ((fundamental.get("analysis_summary") or {}).get("overall") or {}).get("watch_items", [])
    if any("PE" in item or "估值" in item or "PB" in item for item in valuation_watch):
        risks.append(risk("valuation_risk", "medium", "估值相关关注项较多", "fundamental.analysis_summary", "估值继续扩张但盈利预期未上修"))
    if states["trend"]["state"] == "negative":
        risks.append(risk("trend_risk", "high", "个股趋势偏弱", "trend_score.state", "跌破MA60且相对强弱转负"))
    if states["event_expectation"]["state"] == "negative":
        risks.append(risk("event_risk", "medium", "事件预期偏负", "event_score.state", "出现高重要性负面事件"))
    return risks


def risk(kind: str, level: str, desc: str, monitor: str, trigger: str) -> dict[str, str]:
    return {"type": kind, "level": level, "description": desc, "monitor": monitor, "trigger": trigger}


def build_invalidation_conditions(modules: dict[str, Any], states: dict[str, Any]) -> list[str]:
    return [
        "基本面：营收或归母净利润增速显著低于当前趋势",
        "趋势：跌破 MA60 且相对强弱转负",
        "行业：行业趋势转弱且扩散度下降",
        "估值：估值继续扩张但盈利预测未同步上修",
        "事件：关键催化被证伪或出现重大负面公告",
        "市场：市场环境转入 defensive/hostile",
    ]


def build_tracking_plan(modules: dict[str, Any], states: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"item": "市场环境评分", "frequency": "weekly", "trigger": "总分跌破40或升破70"},
        {"item": "行业趋势与扩散度", "frequency": "weekly", "trigger": "行业相对强弱连续转负"},
        {"item": "个股趋势", "frequency": "daily", "trigger": "跌破MA60或放量下跌"},
        {"item": "基本面", "frequency": "quarterly", "trigger": "财报、业绩预告、盈利预测更新"},
        {"item": "事件预期", "frequency": "weekly", "trigger": "重大公告、订单、政策或风险事件"},
    ]


def build_position_framework(decision: dict[str, Any], risk_profile: str) -> dict[str, Any]:
    classification = decision.get("classification")
    level = "none"
    if classification == "core_candidate":
        level = "medium" if risk_profile != "aggressive" else "high"
    elif classification == "watch_candidate":
        level = "low"
    return {"risk_budget_level": level, "reason": "这是研究状态对应的风险预算提示，不是交易建议。", "constraints": ["必须结合个人风险承受能力", "必须设置失效条件", "不得忽视流动性和估值风险"]}


def render_decision_markdown(data: dict[str, Any]) -> str:
    md = data["metadata"]
    dec = data["decision"]
    lines = [
        f"# {md.get('stock_code')} {md.get('stock_name') or ''} 风险与决策框架",
        "",
        f"- 生成时间: {md.get('generated_at')}",
        f"- 分类: {dec.get('classification')}",
        f"- 决策分: {format_decimal(dec.get('decision_score'))}",
        f"- 说明: {dec.get('summary')}",
        f"- 不构成投资建议: {dec.get('not_investment_advice')}",
        "",
        "## 1. 五维状态矩阵",
        "",
        render_table([{"module": k, **v} for k, v in data["module_states"].items()], [("module", "模块"), ("state", "状态"), ("score", "分数"), ("summary", "摘要")]),
        "",
        "## 2. 支持理由",
        "",
        "\n".join(f"- {item}" for item in data["evidence"]["supporting"]) or "- 无",
        "",
        "## 3. 反对理由与不确定性",
        "",
        "\n".join(f"- {item}" for item in data["evidence"]["opposing"] + data["evidence"]["uncertainties"]) or "- 无",
        "",
        "## 4. 风险清单",
        "",
        render_table(data["risk_register"], [("type", "类型"), ("level", "等级"), ("description", "描述"), ("trigger", "触发条件")]),
        "",
        "## 5. 失效条件",
        "",
        "\n".join(f"- {item}" for item in data["invalidation_conditions"]),
        "",
        "## 6. 跟踪计划",
        "",
        render_table(data["tracking_plan"], [("item", "项目"), ("frequency", "频率"), ("trigger", "触发")]),
    ]
    return "\n".join(lines)


def render_quality_markdown(data_quality: dict[str, list[str]]) -> str:
    lines = ["# 数据质量", ""]
    for title, key in [("缺失模块", "missing_modules"), ("缺失字段", "missing_fields"), ("警告", "warnings")]:
        values = data_quality.get(key) or []
        lines.extend([f"## {title}", ""])
        lines.extend([f"- {item}" for item in values] if values else ["- 无"])
        lines.append("")
    return "\n".join(lines).rstrip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthesize A-share risk decision framework.")
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--risk-profile", default="balanced")
    args = parser.parse_args()
    result = get_risk_decision(args.stock_code, risk_profile=args.risk_profile)
    print(result["json_path"])


if __name__ == "__main__":
    main()
