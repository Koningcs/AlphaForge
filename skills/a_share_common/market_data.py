from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from .utils import days_ago_compact, infer_market, normalize_date, safe_call, to_number


INDEX_CODES = {
    "沪深300": "000300",
    "中证500": "000905",
    "创业板指": "399006",
    "上证指数": "000001",
    "深证成指": "399001",
}


def fetch_stock_hist(ak: Any, stock_code: str, lookback_days: int, warnings: list[str], adjust: str = "qfq") -> pd.DataFrame:
    start_date = days_ago_compact(max(lookback_days * 2, lookback_days + 80))
    end_date = date.today().strftime("%Y%m%d")
    df = safe_call(
        warnings,
        f"个股日线-{stock_code}",
        getattr(ak, "stock_zh_a_hist", None),
        symbol=stock_code,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
    )
    if df is None or df.empty:
        market_symbol = f"{infer_market(stock_code)}{stock_code}"
        df = safe_call(
            warnings,
            f"新浪个股日线-{stock_code}",
            getattr(ak, "stock_zh_a_daily", None),
            symbol=market_symbol,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
    if df is None or df.empty:
        market_symbol = f"{infer_market(stock_code)}{stock_code}"
        df = safe_call(
            warnings,
            f"腾讯个股日线-{stock_code}",
            getattr(ak, "stock_zh_a_hist_tx", None),
            symbol=market_symbol,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
    return standardize_ohlcv(df)


def fetch_index_hist(ak: Any, index_name: str, lookback_days: int, warnings: list[str]) -> pd.DataFrame:
    code = INDEX_CODES.get(index_name, index_name)
    start_date = days_ago_compact(max(lookback_days * 2, lookback_days + 80))
    end_date = date.today().strftime("%Y%m%d")
    df = safe_call(
        warnings,
        f"指数日线-{index_name}",
        getattr(ak, "index_zh_a_hist", None),
        symbol=code,
        period="daily",
        start_date=start_date,
        end_date=end_date,
    )
    if df is None or df.empty:
        em_code = f"sh{code}" if code.startswith("000") else f"sz{code}"
        df = safe_call(
            warnings,
            f"指数日线备用-{index_name}",
            getattr(ak, "stock_zh_index_daily_em", None),
            symbol=em_code,
            start_date=start_date,
            end_date=end_date,
        )
    if df is None or df.empty:
        em_code = f"sh{code}" if code.startswith("000") else f"sz{code}"
        df = safe_call(
            warnings,
            f"新浪指数日线-{index_name}",
            getattr(ak, "stock_zh_index_daily", None),
            symbol=em_code,
        )
    if df is None or df.empty:
        em_code = f"sh{code}" if code.startswith("000") else f"sz{code}"
        df = safe_call(
            warnings,
            f"腾讯指数日线-{index_name}",
            getattr(ak, "stock_zh_index_daily_tx", None),
            symbol=em_code,
            start_date=start_date,
            end_date=end_date,
        )
    return standardize_ohlcv(df)


def fetch_industry_hist(ak: Any, industry_name: str, lookback_days: int, warnings: list[str]) -> pd.DataFrame:
    df = safe_call(
        warnings,
        f"行业日线-{industry_name}",
        getattr(ak, "stock_board_industry_hist_em", None),
        symbol=industry_name,
        start_date=days_ago_compact(max(lookback_days * 2, lookback_days + 80)),
        end_date=date.today().strftime("%Y%m%d"),
        period="日k",
        adjust="",
    )
    if df is None or df.empty:
        df = safe_call(
            warnings,
            f"同花顺行业日线-{industry_name}",
            getattr(ak, "stock_board_industry_index_ths", None),
            symbol=industry_name,
            start_date=days_ago_compact(max(lookback_days * 2, lookback_days + 80)),
            end_date=date.today().strftime("%Y%m%d"),
        )
    return standardize_ohlcv(df)


def standardize_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume", "amount"])
    mapping = {
        "日期": "date",
        "date": "date",
        "开盘": "open",
        "开盘价": "open",
        "open": "open",
        "收盘": "close",
        "收盘价": "close",
        "close": "close",
        "最高": "high",
        "最高价": "high",
        "high": "high",
        "最低": "low",
        "最低价": "low",
        "low": "low",
        "成交量": "volume",
        "成交量(手)": "volume",
        "成交股数": "volume",
        "volume": "volume",
        "成交额": "amount",
        "成交金额": "amount",
        "成交金额(元)": "amount",
        "amount": "amount",
    }
    result = pd.DataFrame()
    for source, target in mapping.items():
        if source in df.columns and target not in result.columns:
            result[target] = df[source]
    if "date" in result.columns:
        result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in ["open", "close", "high", "low", "volume", "amount"]:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce")
        else:
            result[column] = None
    result = result.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    return result


def return_n(df: pd.DataFrame, n: int) -> float | None:
    if df.empty or len(df) <= n:
        return None
    old = to_number(df.iloc[-n - 1]["close"])
    new = to_number(df.iloc[-1]["close"])
    if old in (None, 0) or new is None:
        return None
    return new / old - 1


def moving_average(df: pd.DataFrame, n: int) -> float | None:
    if df.empty or len(df) < n:
        return None
    return float(df["close"].tail(n).mean())


def volatility(df: pd.DataFrame, n: int) -> float | None:
    if df.empty or len(df) < n + 1:
        return None
    returns = df["close"].pct_change().tail(n).dropna()
    if returns.empty:
        return None
    return float(returns.std() * (252 ** 0.5))


def max_drawdown(df: pd.DataFrame, n: int) -> float | None:
    if df.empty:
        return None
    close = df["close"].tail(n)
    if close.empty:
        return None
    drawdown = close / close.cummax() - 1
    return float(drawdown.min())


def atr(df: pd.DataFrame, n: int = 20) -> float | None:
    if df.empty or len(df) < n + 1:
        return None
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    true_range = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return float(true_range.tail(n).mean())


def regression_slope(df: pd.DataFrame, n: int = 60) -> tuple[float | None, float | None]:
    if df.empty or len(df) < n:
        return None, None
    y = df["close"].tail(n).reset_index(drop=True)
    x = pd.Series(range(len(y)), dtype="float64")
    if y.isna().any():
        return None, None
    x_mean = x.mean()
    y_mean = y.mean()
    denom = ((x - x_mean) ** 2).sum()
    if denom == 0:
        return None, None
    slope = float(((x - x_mean) * (y - y_mean)).sum() / denom)
    pred = y_mean + slope * (x - x_mean)
    ss_tot = float(((y - y_mean) ** 2).sum())
    ss_res = float(((y - pred) ** 2).sum())
    r2 = None if ss_tot == 0 else 1 - ss_res / ss_tot
    base = float(y.iloc[0])
    slope_pct = None if base == 0 else slope * n / base
    return slope_pct, r2


def trend_snapshot(df: pd.DataFrame) -> dict[str, Any]:
    latest = df.iloc[-1].to_dict() if not df.empty else {}
    ma20 = moving_average(df, 20)
    ma60 = moving_average(df, 60)
    ma120 = moving_average(df, 120)
    ma250 = moving_average(df, 250)
    close = to_number(latest.get("close"))
    slope60, r2_60 = regression_slope(df, 60)
    alignment = ma_alignment(ma20, ma60, ma120, ma250)
    stage = classify_trend_stage(close, ma20, ma60, ma120, ma250, return_n(df, 60), max_drawdown(df, 120))
    return {
        "latest_date": normalize_date(latest.get("date")),
        "latest_close": close,
        "return_20d": return_n(df, 20),
        "return_60d": return_n(df, 60),
        "return_120d": return_n(df, 120),
        "return_250d": return_n(df, 250),
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "ma250": ma250,
        "ma_alignment": alignment,
        "above_ma20": None if close is None or ma20 is None else close > ma20,
        "above_ma60": None if close is None or ma60 is None else close > ma60,
        "above_ma120": None if close is None or ma120 is None else close > ma120,
        "above_ma250": None if close is None or ma250 is None else close > ma250,
        "slope_60d": slope60,
        "r2_60d": r2_60,
        "max_drawdown_60d": max_drawdown(df, 60),
        "max_drawdown_120d": max_drawdown(df, 120),
        "volatility_20d": volatility(df, 20),
        "volatility_60d": volatility(df, 60),
        "atr20": atr(df, 20),
        "trend_stage": stage,
    }


def ma_alignment(ma20: float | None, ma60: float | None, ma120: float | None, ma250: float | None) -> str:
    values = [ma20, ma60, ma120, ma250]
    if any(item is None for item in values):
        return "unknown"
    if ma20 > ma60 > ma120 > ma250:
        return "bullish"
    if ma20 < ma60 < ma120 < ma250:
        return "bearish"
    return "mixed"


def classify_trend_stage(close: float | None, ma20: float | None, ma60: float | None, ma120: float | None, ma250: float | None, ret60: float | None, dd120: float | None) -> str:
    if close is None or ma60 is None:
        return "unknown"
    if ma20 and ma120 and ma250 and close > ma20 > ma60 > ma120 > ma250 and (ret60 or 0) > 0.15:
        return "markup"
    if ma20 and close > ma20 > ma60 and (ret60 or 0) > 0.05:
        return "breakout"
    if ma120 and close > ma60 and close < ma120:
        return "base_repair"
    if ma120 and close < ma60 < ma120:
        return "downtrend"
    if dd120 is not None and dd120 < -0.25 and close > ma60:
        return "high_volatility"
    return "range"


def trend_score_from_snapshot(snapshot: dict[str, Any]) -> float:
    score = 50.0
    for key, points in [("above_ma20", 8), ("above_ma60", 12), ("above_ma120", 10), ("above_ma250", 10)]:
        value = snapshot.get(key)
        if value is True:
            score += points
        elif value is False:
            score -= points
    if snapshot.get("ma_alignment") == "bullish":
        score += 12
    elif snapshot.get("ma_alignment") == "bearish":
        score -= 12
    ret60 = snapshot.get("return_60d")
    if ret60 is not None:
        score += max(-15, min(15, ret60 * 80))
    dd = snapshot.get("max_drawdown_120d")
    if dd is not None and dd < -0.25:
        score -= 8
    return max(0, min(100, round(score, 2)))


def amount_ratio(df: pd.DataFrame, short: int = 20, long: int = 120) -> float | None:
    if df.empty or len(df) < long:
        return None
    base = df["amount"].tail(long).mean()
    if base == 0 or pd.isna(base):
        return None
    return float(df["amount"].tail(short).mean() / base)


def up_down_amount_ratio(df: pd.DataFrame, n: int = 60) -> float | None:
    if df.empty or len(df) < n + 1:
        return None
    recent = df.tail(n).copy()
    recent["ret"] = recent["close"].pct_change()
    up = recent[recent["ret"] > 0]["amount"].mean()
    down = recent[recent["ret"] < 0]["amount"].mean()
    if pd.isna(up) or pd.isna(down) or down == 0:
        return None
    return float(up / down)
