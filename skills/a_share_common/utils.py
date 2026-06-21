from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
import contextlib
import io
import json
import math
import re

import pandas as pd


def project_root_from_skill(file_path: str | Path) -> Path:
    return Path(file_path).resolve().parents[2]


def output_dir(project_root: Path, *parts: str) -> Path:
    path = project_root / "outputs"
    for part in parts:
        path /= sanitize_path_part(str(part))
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_path_part(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip() or "unknown"


def today_compact() -> str:
    return date.today().strftime("%Y%m%d")


def days_ago_compact(days: int) -> str:
    return (date.today() - timedelta(days=days)).strftime("%Y%m%d")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_stock_code(stock_code: str) -> str:
    code = str(stock_code).strip()
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(f"stock_code must be a 6-digit A-share code, got: {stock_code!r}")
    return code


def infer_market(stock_code: str) -> str:
    code = normalize_stock_code(stock_code)
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh"
    if code.startswith(("430", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "920")):
        return "bj"
    return "sz"


def eastmoney_symbol(stock_code: str) -> str:
    return f"{infer_market(stock_code).upper()}{normalize_stock_code(stock_code)}"


def safe_call(
    warnings: list[str],
    label: str,
    func: Callable[..., Any] | None,
    *args: Any,
    **kwargs: Any,
) -> Any:
    if func is None:
        warnings.append(f"{label} 获取失败: 当前 AKShare 版本未提供该接口")
        return pd.DataFrame()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return func(*args, **kwargs)
    except Exception as exc:
        warnings.append(f"{label} 获取失败: {type(exc).__name__}: {exc}")
        return pd.DataFrame()


def is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def to_number(value: Any, *, percent: bool = False) -> float | None:
    if is_null(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        text = str(value).strip()
        if text in {"", "-", "--", "None", "nan", "NaN", "不适用"}:
            return None
        has_percent = "%" in text or "％" in text
        text = text.replace(",", "").replace("，", "").replace("%", "").replace("％", "")
        try:
            number = float(text)
        except ValueError:
            return None
        percent = percent or has_percent
    if math.isnan(number) or math.isinf(number):
        return None
    return number / 100 if percent else number


def normalize_date(value: Any) -> str | None:
    if is_null(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10] if text else None


def first_text(*values: Any) -> str | None:
    for value in values:
        if is_null(value):
            continue
        text = str(value).strip()
        if text and text not in {"-", "--", "None", "nan"}:
            return text
    return None


def first_number(*values: Any, percent: bool = False) -> float | None:
    for value in values:
        number = to_number(value, percent=percent)
        if number is not None:
            return number
    return None


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return normalize_date(value)
    if hasattr(value, "item"):
        return json_safe(value.item())
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(json_safe(data), ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def format_percent(value: float | None) -> str:
    return "" if value is None else f"{value * 100:.2f}%"


def format_decimal(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"


def format_number(value: Any) -> str:
    number = to_number(value)
    if number is None:
        return "" if value is None else str(value)
    if abs(number) >= 10000:
        return f"{number:,.2f}"
    return f"{number:.4f}".rstrip("0").rstrip(".")


def escape_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def render_kv_table(row: dict[str, Any], columns: list[tuple[str, str]], ratio_fields: set[str] | None = None) -> str:
    ratio_fields = ratio_fields or set()
    lines = ["| 字段 | 值 |", "| --- | --- |"]
    for key, label in columns:
        value = row.get(key)
        text = format_percent(value) if key in ratio_fields else format_number(value)
        lines.append(f"| {escape_cell(label)} | {escape_cell(text)} |")
    return "\n".join(lines)


def render_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], ratio_fields: set[str] | None = None) -> str:
    if not rows:
        return "_无数据_"
    ratio_fields = ratio_fields or set()
    lines = [
        "| " + " | ".join(escape_cell(label) for _, label in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        values = []
        for key, _ in columns:
            value = row.get(key)
            values.append(format_percent(value) if key in ratio_fields else format_number(value))
        lines.append("| " + " | ".join(escape_cell(value) for value in values) + " |")
    return "\n".join(lines)


def pct_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return new / old - 1


def safe_div(left: float | None, right: float | None) -> float | None:
    if left is None or right in (None, 0):
        return None
    return left / right


def clamp(value: float | None, low: float = 0, high: float = 100) -> float | None:
    if value is None:
        return None
    return max(low, min(high, value))


def score_state(score: float | None, labels: tuple[str, str, str, str, str] = ("strong", "positive", "neutral", "weak", "negative")) -> str:
    if score is None:
        return "unknown"
    if score >= 80:
        return labels[0]
    if score >= 60:
        return labels[1]
    if score >= 40:
        return labels[2]
    if score >= 20:
        return labels[3]
    return labels[4]


def quality_report(data: dict[str, Any], warnings: list[str]) -> dict[str, list[str]]:
    missing: list[str] = []

    def visit(prefix: str, value: Any) -> None:
        if value is None or value == "":
            missing.append(prefix)
        elif isinstance(value, dict):
            for key, item in value.items():
                visit(f"{prefix}.{key}" if prefix else str(key), item)

    visit("", data)
    return {"missing_fields": missing[:200], "warnings": list(dict.fromkeys(warnings))}
