from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
import contextlib
import io
import math
import re

import pandas as pd


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


def exchange_name(stock_code: str) -> str:
    market = infer_market(stock_code)
    if market == "sh":
        return "上海证券交易所"
    if market == "bj":
        return "北京证券交易所"
    return "深圳证券交易所"


def sina_symbol(stock_code: str) -> str:
    return f"{infer_market(stock_code)}{normalize_stock_code(stock_code)}"


def xueqiu_symbol(stock_code: str) -> str:
    return f"{infer_market(stock_code).upper()}{normalize_stock_code(stock_code)}"


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
        warnings.append(f"{label} 获取失败: AKShare 当前版本未提供该接口")
        return pd.DataFrame()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return func(*args, **kwargs)
    except Exception as exc:  # AKShare/network interfaces fail in many ways.
        warnings.append(f"{label} 获取失败: {type(exc).__name__}: {exc}")
        return pd.DataFrame()


def item_value_map(df: pd.DataFrame | None) -> dict[str, Any]:
    if df is None or df.empty:
        return {}
    if {"item", "value"}.issubset(set(df.columns)):
        return {
            str(row["item"]).strip(): row["value"]
            for _, row in df.iterrows()
            if str(row.get("item", "")).strip()
        }
    return {}


def first_row_map(df: pd.DataFrame | None) -> dict[str, Any]:
    if df is None or df.empty:
        return {}
    return dict(df.iloc[0].to_dict())


def normalize_date(value: Any) -> str | None:
    if value is None or is_null(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    if re.fullmatch(r"\d{4}", digits):
        return f"{digits}-12-31"
    return text[:10]


def period_from_row(row: dict[str, Any], date_columns: tuple[str, ...] = ("报告日", "日期", "REPORT_DATE")) -> str | None:
    for column in date_columns:
        if column in row:
            period = normalize_date(row[column])
            if period:
                return period
    return None


def latest_annual_records(
    df: pd.DataFrame | None,
    years: int,
    date_columns: tuple[str, ...] = ("报告日", "日期", "REPORT_DATE"),
) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []

    rows: list[dict[str, Any]] = []
    for raw in df.to_dict(orient="records"):
        period = period_from_row(raw, date_columns)
        if not period:
            continue
        if not period.endswith("12-31"):
            continue
        row = dict(raw)
        row["_period"] = period
        rows.append(row)

    if not rows:
        for raw in df.to_dict(orient="records"):
            period = period_from_row(raw, date_columns)
            if not period:
                continue
            row = dict(raw)
            row["_period"] = period
            rows.append(row)

    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(str(row["_period"]), row)

    return [
        unique[period]
        for period in sorted(unique.keys(), reverse=True)[: max(1, int(years))]
    ]


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
        text = text.replace(",", "").replace("，", "")
        has_percent = "%" in text or "％" in text
        text = text.replace("%", "").replace("％", "")
        try:
            number = float(text)
        except ValueError:
            return None
        percent = percent or has_percent
    if math.isnan(number) or math.isinf(number):
        return None
    if percent:
        return number / 100
    return number


def normalize_key(value: str) -> str:
    return (
        str(value)
        .replace("（", "(")
        .replace("）", ")")
        .replace(" ", "")
        .replace("_", "")
        .lower()
    )


def pick_value(row: dict[str, Any], candidates: list[str], *, percent: bool = False) -> float | None:
    for candidate in candidates:
        if candidate in row:
            return to_number(row[candidate], percent=percent or "(%)" in candidate)

    normalized = {normalize_key(key): key for key in row.keys()}
    for candidate in candidates:
        key = normalized.get(normalize_key(candidate))
        if key is not None:
            return to_number(row[key], percent=percent or "(%)" in key)

    for candidate in candidates:
        candidate_key = normalize_key(candidate)
        for raw_key in row.keys():
            key = normalize_key(raw_key)
            if candidate_key and (candidate_key in key or key in candidate_key):
                return to_number(row[raw_key], percent=percent or "(%)" in str(raw_key))
    return None


def fetch_company_profile(stock_code: str, warnings: list[str]) -> dict[str, Any]:
    import akshare as ak

    code = normalize_stock_code(stock_code)
    cninfo_df = safe_call(
        warnings,
        "巨潮公司概况",
        getattr(ak, "stock_profile_cninfo", None),
        symbol=code,
    )
    cninfo = first_row_map(cninfo_df)

    eastmoney: dict[str, Any] = {}
    if not all([cninfo.get("A股简称"), cninfo.get("所属行业"), cninfo.get("上市日期")]):
        eastmoney_df = safe_call(
            warnings,
            "东方财富个股信息",
            getattr(ak, "stock_individual_info_em", None),
            symbol=code,
        )
        eastmoney = item_value_map(eastmoney_df)

    xueqiu: dict[str, Any] = {}
    if not all([cninfo.get("公司名称"), cninfo.get("主营业务")]):
        xueqiu_df = safe_call(
            warnings,
            "雪球公司概况",
            getattr(ak, "stock_individual_basic_info_xq", None),
            symbol=xueqiu_symbol(code),
        )
        xueqiu = item_value_map(xueqiu_df)

    stock_name = first_text(
        cninfo.get("A股简称"),
        eastmoney.get("股票简称"),
        xueqiu.get("org_short_name_cn"),
        xueqiu.get("org_short_name_en"),
    )
    if not stock_name:
        code_name_df = safe_call(
            warnings,
            "A股代码名称表",
            getattr(ak, "stock_info_a_code_name", None),
        )
        stock_name = stock_name_from_code_table(code_name_df, code)

    return {
        "stock_code": code,
        "stock_name": stock_name,
        "company_name": first_text(cninfo.get("公司名称"), xueqiu.get("org_name_cn"), xueqiu.get("公司名称")),
        "exchange": first_text(cninfo.get("所属市场"), exchange_name(code)),
        "industry": first_text(eastmoney.get("行业"), cninfo.get("所属行业"), xueqiu.get("行业")),
        "main_business": first_text(cninfo.get("主营业务"), xueqiu.get("main_operation_business"), xueqiu.get("operating_scope")),
        "listing_date": normalize_date(eastmoney.get("上市时间") or cninfo.get("上市日期") or xueqiu.get("上市时间")),
    }


def stock_name_from_code_table(df: pd.DataFrame | None, stock_code: str) -> str | None:
    if df is None or df.empty or not {"code", "name"}.issubset(set(df.columns)):
        return None
    code = normalize_stock_code(stock_code)
    matched = df[df["code"].astype(str).str.zfill(6) == code]
    if matched.empty:
        return None
    return first_text(matched.iloc[0].get("name"))


def first_text(*values: Any) -> str | None:
    for value in values:
        if is_null(value):
            continue
        text = str(value).strip()
        if text and text not in {"-", "--", "None", "nan"}:
            return text
    return None


def fetch_financial_report(stock_code: str, report_name: str, years: int, warnings: list[str]) -> list[dict[str, Any]]:
    import akshare as ak

    df = safe_call(
        warnings,
        f"新浪财务报表-{report_name}",
        getattr(ak, "stock_financial_report_sina", None),
        stock=sina_symbol(stock_code),
        symbol=report_name,
    )
    return latest_annual_records(df, years, ("报告日", "日期"))


def fetch_financial_indicators(stock_code: str, years: int, warnings: list[str]) -> list[dict[str, Any]]:
    import akshare as ak

    start_year = str(datetime.now().year - max(1, int(years)) - 2)
    df = safe_call(
        warnings,
        "新浪财务指标",
        getattr(ak, "stock_financial_analysis_indicator", None),
        symbol=normalize_stock_code(stock_code),
        start_year=start_year,
    )
    return latest_annual_records(df, years, ("日期", "报告日"))


def fetch_valuation(stock_code: str, warnings: list[str]) -> dict[str, Any]:
    import akshare as ak

    code = normalize_stock_code(stock_code)
    value_df = safe_call(
        warnings,
        "东方财富估值分析",
        getattr(ak, "stock_value_em", None),
        symbol=code,
    )
    latest_value = first_row_map(value_df.tail(1)) if value_df is not None and not value_df.empty else {}

    comparison_df = safe_call(
        warnings,
        "东方财富同行估值比较",
        getattr(ak, "stock_zh_valuation_comparison_em", None),
        symbol=eastmoney_symbol(code),
    )
    comparison = row_by_code(comparison_df, code)

    pe_ttm = first_number(latest_value.get("PE(TTM)"), comparison.get("市盈率-TTM"))
    pb = first_number(latest_value.get("市净率"), comparison.get("市净率-MRQ"), comparison.get("市净率-24A"))
    ps_ttm = first_number(latest_value.get("市销率"), comparison.get("市销率-TTM"))
    peg = first_number(latest_value.get("PEG值"), comparison.get("PEG"))

    return {
        "date": normalize_date(latest_value.get("数据日期")),
        "close_price": to_number(latest_value.get("当日收盘价")),
        "market_cap": to_number(latest_value.get("总市值")),
        "float_market_cap": to_number(latest_value.get("流通市值")),
        "total_shares": to_number(latest_value.get("总股本")),
        "float_shares": to_number(latest_value.get("流通股本")),
        "pe_ttm": pe_ttm,
        "pe_static": to_number(latest_value.get("PE(静)")),
        "pb": pb,
        "peg": peg,
        "pcf": to_number(latest_value.get("市现率")),
        "ps_ttm": ps_ttm,
        "ev_ebitda": to_number(comparison.get("EV/EBITDA-24A")),
        "industry_rank": first_text(comparison.get("排名")),
        "source": "akshare.stock_value_em, akshare.stock_zh_valuation_comparison_em",
    }


def row_by_code(df: pd.DataFrame | None, stock_code: str) -> dict[str, Any]:
    if df is None or df.empty or "代码" not in df.columns:
        return {}
    code = normalize_stock_code(stock_code)
    matched = df[df["代码"].astype(str).str.zfill(6) == code]
    if matched.empty:
        return {}
    return dict(matched.iloc[0].to_dict())


def first_number(*values: Any) -> float | None:
    for item in values:
        number = to_number(item)
        if number is not None:
            return number
    return None
