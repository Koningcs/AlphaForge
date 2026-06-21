from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests


EASTMONEY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Referer": "https://guba.eastmoney.com/",
}

EASTMONEY_MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
    "Referer": "https://mguba.eastmoney.com/",
    "Origin": "https://mguba.eastmoney.com",
    "Accept": "application/json, text/plain, */*",
}

EASTMONEY_WAP_PARAMS = {
    "deviceid": "ugc",
    "version": "200",
    "plat": "wap",
    "product": "guba",
    "ctoken": "",
    "utoken": "",
}


@dataclass
class StockBuildResult:
    stock_code: str
    raw_comments: list[dict[str, Any]]
    agent_tasks: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    labels: list[dict[str, Any]]
    warnings: list[str]


def normalize_stock_code(value: str) -> str:
    code = re.sub(r"\D", "", str(value))
    if len(code) != 6:
        raise ValueError(f"Invalid A-share stock code: {value!r}")
    return code


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_cutoff_time(value: str) -> clock_time:
    parts = value.split(":")
    if len(parts) == 2:
        return clock_time(int(parts[0]), int(parts[1]), 0)
    if len(parts) == 3:
        return clock_time(int(parts[0]), int(parts[1]), int(parts[2]))
    raise ValueError("cutoff time must be HH:MM or HH:MM:SS")


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, clock_time())
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def compact_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ")
    if pd.isna(value):
        return None
    return str(value)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def stable_hash(value: Any, length: int = 16) -> str:
    if value in (None, ""):
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length]


def strip_html(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*p\s*>", "\n", text)
    text = re.sub(r"(?s)<script.*?</script>", " ", text)
    text = re.sub(r"(?s)<style.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()


def combine_text(parts: list[str], max_chars: int = 1800) -> str:
    seen: set[str] = set()
    cleaned: list[str] = []
    for part in parts:
        text = strip_html(part)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return "\n".join(cleaned)[:max_chars].strip()


def extract_js_object(text: str, var_name: str) -> dict[str, Any]:
    needle = f"var {var_name}="
    idx = text.find(needle)
    if idx < 0:
        needle = f"{var_name}="
        idx = text.find(needle)
    if idx < 0:
        raise ValueError(f"Cannot find JavaScript object: {var_name}")
    start = text.find("{", idx)
    if start < 0:
        raise ValueError(f"Cannot find object start for: {var_name}")

    depth = 0
    in_string = False
    escape = False
    for offset, char in enumerate(text[start:], start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : offset + 1])
    raise ValueError(f"Cannot find object end for: {var_name}")


def cache_path_for_url(cache_dir: Path | None, url: str, suffix: str = ".html") -> Path | None:
    if cache_dir is None:
        return None
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return cache_dir / "http" / f"{digest}{suffix}"


def fetch_text(
    session: requests.Session,
    url: str,
    cache_dir: Path | None,
    refresh_cache: bool,
    timeout: int,
    warnings: list[str],
) -> str:
    cached = cache_path_for_url(cache_dir, url)
    if cached and cached.exists() and not refresh_cache:
        return cached.read_text(encoding="utf-8", errors="ignore")
    try:
        response = session.get(url, headers=EASTMONEY_HEADERS, timeout=timeout)
        response.raise_for_status()
        text = response.content.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Fetch failed: {url} ({exc})")
        raise
    if cached:
        ensure_dir(cached.parent)
        cached.write_text(text, encoding="utf-8")
    return text


def fetch_json_get(
    session: requests.Session,
    url: str,
    params: dict[str, Any],
    cache_dir: Path | None,
    refresh_cache: bool,
    timeout: int,
    warnings: list[str],
) -> dict[str, Any]:
    cache_url = url + "?" + "&".join(f"{key}={params[key]}" for key in sorted(params))
    cached = cache_path_for_url(cache_dir, cache_url, suffix=".json")
    if cached and cached.exists() and not refresh_cache:
        return json.loads(cached.read_text(encoding="utf-8"))
    try:
        response = session.get(url, headers=EASTMONEY_MOBILE_HEADERS, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"JSON fetch failed: {url} ({exc})")
        raise
    if cached:
        ensure_dir(cached.parent)
        cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def fetch_json_post(
    session: requests.Session,
    url: str,
    data: dict[str, Any],
    cache_dir: Path | None,
    refresh_cache: bool,
    timeout: int,
    warnings: list[str],
) -> dict[str, Any]:
    cache_url = url + "?" + "&".join(f"{key}={data[key]}" for key in sorted(data))
    cached = cache_path_for_url(cache_dir, cache_url, suffix=".json")
    if cached and cached.exists() and not refresh_cache:
        return json.loads(cached.read_text(encoding="utf-8"))
    try:
        response = session.post(url, headers=EASTMONEY_MOBILE_HEADERS, data=data, timeout=timeout)
        response.raise_for_status()
        result = response.json()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"JSON post failed: {url} ({exc})")
        raise
    if cached:
        ensure_dir(cached.parent)
        cached.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def eastmoney_list_url(stock_code: str, page: int) -> str:
    if page <= 1:
        return f"https://guba.eastmoney.com/list,{stock_code}.html"
    return f"https://guba.eastmoney.com/list,{stock_code}_{page}.html"


def eastmoney_article_url(stock_code: str, post_id: Any) -> str:
    return f"https://guba.eastmoney.com/news,{stock_code},{post_id}.html"


def eastmoney_article_mobile_url(post_id: Any, post_type: Any = 0) -> str:
    return f"https://mguba.eastmoney.com/mguba/article/{post_type or 0}/{post_id}"


def engagement_score(record: dict[str, Any]) -> float:
    click_count = to_float(record.get("click_count")) or 0.0
    comment_count = to_float(record.get("comment_count")) or 0.0
    forward_count = to_float(record.get("forward_count")) or 0.0
    like_count = to_float(record.get("like_count")) or 0.0
    return click_count + comment_count * 5 + forward_count * 3 + like_count * 2


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def normalize_eastmoney_item(stock_code: str, item: dict[str, Any]) -> dict[str, Any] | None:
    published_at = parse_datetime(item.get("post_publish_time") or item.get("post_display_time"))
    if published_at is None:
        return None
    post_id = item.get("post_id")
    title = strip_html(item.get("post_title"))
    body = combine_text(
        [
            item.get("post_content") or "",
            item.get("post_abstract") or "",
            item.get("source_post_title") or "",
            item.get("source_post_content") or "",
            item.get("source_post_abstract") or "",
        ]
    )
    text = combine_text([title, body])
    record = {
        "source": "eastmoney_guba",
        "stock_code": stock_code,
        "post_id": str(post_id) if post_id is not None else "",
        "url": eastmoney_article_url(stock_code, post_id) if post_id is not None else "",
        "mobile_url": eastmoney_article_mobile_url(post_id, item.get("post_type") or 0) if post_id is not None else "",
        "published_at": published_at.isoformat(sep=" "),
        "title": title,
        "body": body,
        "text": text,
        "user_hash": stable_hash(item.get("user_id")),
        "user_nickname": item.get("user_nickname") or "",
        "click_count": item.get("post_click_count"),
        "comment_count": item.get("post_comment_count"),
        "forward_count": item.get("post_forward_count"),
        "like_count": item.get("post_like_count"),
        "bullish_bearish_raw": item.get("bullish_bearish"),
        "is_top": bool(item.get("post_top_status")),
        "post_type": item.get("post_type"),
    }
    record["engagement_score"] = engagement_score(record)
    return record


def enrich_with_detail(
    record: dict[str, Any],
    session: requests.Session,
    cache_dir: Path | None,
    refresh_cache: bool,
    timeout: int,
    warnings: list[str],
) -> dict[str, Any]:
    if not record.get("post_id"):
        return record
    try:
        post_id = record["post_id"]
        post_type = record.get("post_type") or 0
        article_url = f"https://mguba.eastmoney.com/api/getArticle?postid={post_id}"
        article_data = {
            **EASTMONEY_WAP_PARAMS,
            "postid": post_id,
            "type": post_type,
            "cutword": "true",
            "paytext": "true",
            "location": "WAP|Article|wap|TRUE",
            "env": "prod",
            "bizfrom": "ugc",
        }
        response = fetch_json_post(session, article_url, article_data, cache_dir, refresh_cache, timeout, warnings)
        article = response.get("post") or {}
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Detail fetch failed for {record.get('mobile_url') or record.get('url')}: {exc}")
        return record

    body = combine_text(
        [
            article.get("post_content") or "",
            article.get("post_abstract") or "",
            article.get("source_post_title") or "",
            article.get("source_post_content") or "",
            article.get("source_post_abstract") or "",
        ],
        max_chars=3000,
    )
    if body:
        record["body"] = body
        record["text"] = combine_text([record.get("title") or "", body], max_chars=3200)
    for source_key, target_key in [
        ("post_click_count", "click_count"),
        ("post_comment_count", "comment_count"),
        ("post_forward_count", "forward_count"),
        ("post_like_count", "like_count"),
    ]:
        if article.get(source_key) is not None:
            record[target_key] = article[source_key]
    record["engagement_score"] = engagement_score(record)
    return record


def fetch_eastmoney_api_page(
    session: requests.Session,
    stock_code: str,
    page: int,
    page_size: int,
    cache_dir: Path | None,
    refresh_cache: bool,
    timeout: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    params = {
        **EASTMONEY_WAP_PARAMS,
        "code": stock_code,
        "type": "0",
        "p": str(page),
        "ps": str(page_size),
        "sorttype": "0",
    }
    data = fetch_json_get(
        session,
        "https://gbapi.eastmoney.com/webarticlelist/api/Article/Articlelist",
        params,
        cache_dir,
        refresh_cache,
        timeout,
        warnings,
    )
    if data.get("rc") != 1:
        raise ValueError(data.get("me") or f"Eastmoney API rc={data.get('rc')}")
    return data.get("re") or []


def fetch_eastmoney_html_page(
    session: requests.Session,
    stock_code: str,
    page: int,
    cache_dir: Path | None,
    refresh_cache: bool,
    timeout: int,
    warnings: list[str],
) -> list[dict[str, Any]]:
    text = fetch_text(session, eastmoney_list_url(stock_code, page), cache_dir, refresh_cache, timeout, warnings)
    article_list = extract_js_object(text, "article_list")
    return article_list.get("re") or []


def fetch_eastmoney_comments(
    stock_code: str,
    start_dt: datetime,
    end_dt: datetime,
    max_pages: int,
    timeout: int,
    sleep_seconds: float,
    include_details: bool,
    detail_limit: int,
    cache_dir: Path | None,
    refresh_cache: bool,
    warnings: list[str],
) -> list[dict[str, Any]]:
    session = requests.Session()
    seen: set[str] = set()
    comments: list[dict[str, Any]] = []

    for page in range(1, max_pages + 1):
        try:
            items = fetch_eastmoney_api_page(
                session,
                stock_code,
                page,
                page_size=80,
                cache_dir=cache_dir,
                refresh_cache=refresh_cache,
                timeout=timeout,
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"API list page failed for {stock_code} page {page}: {exc}")
            if page == 1:
                try:
                    items = fetch_eastmoney_html_page(
                        session,
                        stock_code,
                        page,
                        cache_dir=cache_dir,
                        refresh_cache=refresh_cache,
                        timeout=timeout,
                        warnings=warnings,
                    )
                except Exception as html_exc:  # noqa: BLE001
                    warnings.append(f"HTML list page failed for {stock_code} page {page}: {html_exc}")
                    break
            else:
                break

        if not items:
            break

        page_datetimes: list[datetime] = []
        for item in items:
            record = normalize_eastmoney_item(stock_code, item)
            if record is None:
                continue
            published_at = parse_datetime(record["published_at"])
            if published_at is None:
                continue
            page_datetimes.append(published_at)
            if not (start_dt <= published_at <= end_dt):
                continue
            key = f"{stock_code}:{record.get('post_id')}"
            if key in seen:
                continue
            seen.add(key)
            comments.append(record)

        if page_datetimes and max(page_datetimes) < start_dt:
            break
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    comments.sort(key=lambda item: item.get("published_at") or "")
    if comments:
        target_ids: set[Any] = {
            item.get("post_id")
            for item in comments
            if not (item.get("text") or "").strip()
        }
        if include_details:
            detail_targets = sorted(comments, key=engagement_score, reverse=True)
            target_ids.update(item.get("post_id") for item in detail_targets)
        if detail_limit > 0:
            target_ids = {item for item in list(target_ids)[:detail_limit]}
        for record in comments:
            if record.get("post_id") in target_ids:
                enrich_with_detail(record, session, cache_dir, refresh_cache, timeout, warnings)
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
    return comments


def infer_market_prefix(stock_code: str) -> str:
    if stock_code.startswith(("6", "9")):
        return "sh"
    return "sz"


def standardize_prices(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume", "amount"])
    mapping = {
        "\u65e5\u671f": "date",
        "date": "date",
        "\u5f00\u76d8": "open",
        "\u5f00\u76d8\u4ef7": "open",
        "open": "open",
        "\u6536\u76d8": "close",
        "\u6536\u76d8\u4ef7": "close",
        "close": "close",
        "\u6700\u9ad8": "high",
        "\u6700\u9ad8\u4ef7": "high",
        "high": "high",
        "\u6700\u4f4e": "low",
        "\u6700\u4f4e\u4ef7": "low",
        "low": "low",
        "\u6210\u4ea4\u91cf": "volume",
        "\u6210\u4ea4\u80a1\u6570": "volume",
        "volume": "volume",
        "\u6210\u4ea4\u989d": "amount",
        "\u6210\u4ea4\u91d1\u989d": "amount",
        "amount": "amount",
    }
    out = pd.DataFrame()
    for source, target in mapping.items():
        if source in df.columns and target not in out.columns:
            out[target] = df[source]
    if "date" not in out.columns:
        return pd.DataFrame(columns=["date", "open", "close", "high", "low", "volume", "amount"])
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.date
    for col in ["open", "close", "high", "low", "volume", "amount"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        else:
            out[col] = pd.NA
    out = out.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    return out


def fetch_stock_prices(
    stock_code: str,
    start_date: date,
    end_date: date,
    adjust: str,
    cache_dir: Path | None,
    refresh_cache: bool,
    warnings: list[str],
) -> pd.DataFrame:
    cache_path = None
    if cache_dir is not None:
        ensure_dir(cache_dir / "prices")
        cache_path = cache_dir / "prices" / f"{stock_code}_{compact_date(start_date)}_{compact_date(end_date)}_{adjust or 'none'}.csv"
        if cache_path.exists() and not refresh_cache:
            return standardize_prices(pd.read_csv(cache_path))

    try:
        import akshare as ak
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("akshare is required for price fetching") from exc

    df = None
    try:
        df = ak.stock_zh_a_hist(
            symbol=stock_code,
            period="daily",
            start_date=compact_date(start_date),
            end_date=compact_date(end_date),
            adjust=adjust,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"AKShare stock_zh_a_hist failed for {stock_code}: {exc}")

    if df is None or df.empty:
        try:
            df = ak.stock_zh_a_daily(
                symbol=f"{infer_market_prefix(stock_code)}{stock_code}",
                start_date=compact_date(start_date),
                end_date=compact_date(end_date),
                adjust=adjust,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"AKShare stock_zh_a_daily failed for {stock_code}: {exc}")

    prices = standardize_prices(df)
    if cache_path is not None and not prices.empty:
        prices.to_csv(cache_path, index=False, encoding="utf-8-sig")
    return prices


def future_return_label(
    prices: pd.DataFrame,
    signal_date: date,
    forward_days: int,
    return_start: str,
) -> dict[str, Any]:
    label = {
        "signal_date": signal_date.isoformat(),
        "return_start": return_start,
        "forward_days": forward_days,
        "entry_date": "",
        "exit_date": "",
        "entry_price": None,
        "exit_price": None,
        "future_return": None,
        "label_status": "missing_price",
    }
    if prices.empty:
        return label
    dates = list(prices["date"])
    if return_start == "signal_close":
        anchor_candidates = [idx for idx, value in enumerate(dates) if value <= signal_date]
        if not anchor_candidates:
            return label
        entry_idx = anchor_candidates[-1]
        exit_idx = entry_idx + forward_days
        if exit_idx >= len(prices):
            label["label_status"] = "insufficient_forward_days"
            return label
        entry_price = to_float(prices.iloc[entry_idx]["close"])
    else:
        entry_candidates = [idx for idx, value in enumerate(dates) if value > signal_date]
        if not entry_candidates:
            label["label_status"] = "insufficient_forward_days"
            return label
        entry_idx = entry_candidates[0]
        exit_idx = entry_idx + forward_days - 1
        if exit_idx >= len(prices):
            label["label_status"] = "insufficient_forward_days"
            return label
        entry_price = to_float(prices.iloc[entry_idx]["open"])
        if entry_price is None:
            entry_price = to_float(prices.iloc[entry_idx]["close"])

    exit_price = to_float(prices.iloc[exit_idx]["close"])
    if entry_price in (None, 0) or exit_price is None:
        return label
    label.update(
        {
            "entry_date": prices.iloc[entry_idx]["date"].isoformat(),
            "exit_date": prices.iloc[exit_idx]["date"].isoformat(),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "future_return": exit_price / entry_price - 1,
            "label_status": "ok",
        }
    )
    return label


def select_comments(comments: list[dict[str, Any]], max_count: int) -> list[dict[str, Any]]:
    if max_count <= 0 or len(comments) <= max_count:
        selected = comments
    else:
        ranked = sorted(
            comments,
            key=lambda item: (engagement_score(item), item.get("published_at") or ""),
            reverse=True,
        )[:max_count]
        selected = sorted(ranked, key=lambda item: item.get("published_at") or "")
    keep = []
    for item in selected:
        keep.append(
            {
                "published_at": item.get("published_at"),
                "title": item.get("title"),
                "body": item.get("body"),
                "text": item.get("text"),
                "url": item.get("url"),
                "user_hash": item.get("user_hash"),
                "click_count": item.get("click_count"),
                "comment_count": item.get("comment_count"),
                "forward_count": item.get("forward_count"),
                "like_count": item.get("like_count"),
                "engagement_score": item.get("engagement_score"),
            }
        )
    return keep


def build_agent_prompt(stock_code: str, signal_date: date, lookback_days: int, comment_count: int) -> str:
    return (
        "Score the market sentiment for this A-share stock window using only the supplied "
        f"Eastmoney Guba comments. Stock: {stock_code}; signal date: {signal_date.isoformat()}; "
        f"lookback window: {lookback_days} natural days; comment count: {comment_count}. "
        "Return numeric fields: sentiment_score (-100 bearish to 100 bullish), bullish_score, "
        "bearish_score, neutral_score, disagreement_score, confidence, and brief notes. "
        "Do not infer from future returns or later price action."
    )


def build_windows_for_stock(
    stock_code: str,
    args: argparse.Namespace,
    cache_dir: Path | None,
) -> StockBuildResult:
    warnings: list[str] = []
    cutoff = parse_cutoff_time(args.cutoff_time)

    requested_signal_dates: list[date] | None = None
    if args.signal_date:
        requested_signal_dates = [parse_date(args.signal_date)]
        range_start = requested_signal_dates[0]
        range_end = requested_signal_dates[0]
    else:
        range_start = parse_date(args.start_date)
        range_end = parse_date(args.end_date)

    price_start = range_start - timedelta(days=max(args.lookback_days + 10, 30))
    price_end = range_end + timedelta(days=max(args.forward_days * 3 + 20, 45))
    prices = fetch_stock_prices(stock_code, price_start, price_end, args.adjust, cache_dir, args.refresh_cache, warnings)
    if prices.empty:
        warnings.append(f"No price data for {stock_code}")

    if requested_signal_dates is not None:
        signal_dates = requested_signal_dates
    else:
        signal_dates = [
            value
            for value in prices["date"].tolist()
            if isinstance(value, date) and range_start <= value <= range_end
        ]

    if not signal_dates:
        warnings.append(f"No signal dates for {stock_code}")
        return StockBuildResult(stock_code, [], [], [], [], warnings)

    comment_start_date = min(signal_dates) - timedelta(days=args.lookback_days - 1)
    comment_end_date = max(signal_dates)
    comment_start_dt = datetime.combine(comment_start_date, clock_time(0, 0, 0))
    comment_end_dt = datetime.combine(comment_end_date, cutoff)

    raw_comments = fetch_eastmoney_comments(
        stock_code=stock_code,
        start_dt=comment_start_dt,
        end_dt=comment_end_dt,
        max_pages=args.max_pages,
        timeout=args.timeout,
        sleep_seconds=args.sleep_seconds,
        include_details=args.include_details,
        detail_limit=args.detail_limit,
        cache_dir=cache_dir,
        refresh_cache=args.refresh_cache,
        warnings=warnings,
    )

    rows: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    agent_tasks: list[dict[str, Any]] = []

    parsed_comments = [
        (item, parse_datetime(item.get("published_at")))
        for item in raw_comments
        if parse_datetime(item.get("published_at")) is not None
    ]

    for signal in signal_dates:
        window_start = datetime.combine(signal - timedelta(days=args.lookback_days - 1), clock_time(0, 0, 0))
        window_end = datetime.combine(signal, cutoff)
        window_comments = [
            item
            for item, published_at in parsed_comments
            if published_at is not None and window_start <= published_at <= window_end
        ]
        selected = select_comments(window_comments, args.max_comments_per_window)
        window_id = f"{stock_code}_{signal.strftime('%Y%m%d')}_{args.lookback_days}d"
        label = future_return_label(prices, signal, args.forward_days, args.return_start)
        label.update({"stock_code": stock_code, "window_id": window_id})
        labels.append(label)

        row = {
            "window_id": window_id,
            "stock_code": stock_code,
            "signal_date": signal.isoformat(),
            "lookback_days": args.lookback_days,
            "forward_days": args.forward_days,
            "comment_window_start": window_start.isoformat(sep=" "),
            "comment_window_end": window_end.isoformat(sep=" "),
            "comment_count": len(window_comments),
            "included_comment_count": len(selected),
            "comments_truncated": len(selected) < len(window_comments),
            "entry_date": label.get("entry_date"),
            "exit_date": label.get("exit_date"),
            "future_return": label.get("future_return"),
            "label_status": label.get("label_status"),
        }
        rows.append(row)

        agent_tasks.append(
            {
                "window_id": window_id,
                "stock_code": stock_code,
                "signal_date": signal.isoformat(),
                "source": "eastmoney_guba",
                "lookback_days": args.lookback_days,
                "comment_window_start": window_start.isoformat(sep=" "),
                "comment_window_end": window_end.isoformat(sep=" "),
                "comment_count": len(window_comments),
                "included_comment_count": len(selected),
                "comments_truncated": len(selected) < len(window_comments),
                "scoring_schema": {
                    "sentiment_score": "-100 bearish to 100 bullish",
                    "bullish_score": "0 to 100",
                    "bearish_score": "0 to 100",
                    "neutral_score": "0 to 100",
                    "disagreement_score": "0 to 100",
                    "confidence": "0 to 100",
                    "notes": "brief rationale",
                },
                "prompt": build_agent_prompt(stock_code, signal, args.lookback_days, len(window_comments)),
                "comments": selected,
            }
        )

    return StockBuildResult(stock_code, raw_comments, agent_tasks, rows, labels, warnings)


def read_stock_codes(args: argparse.Namespace) -> list[str]:
    values: list[str] = []
    if args.stock_code:
        values.append(args.stock_code)
    if args.stock_codes:
        values.extend(part.strip() for part in args.stock_codes.split(",") if part.strip())
    if args.stock_file:
        for line in Path(args.stock_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            values.extend(part.strip() for part in re.split(r"[,\s]+", line) if part.strip())
    if not values:
        raise ValueError("Provide --stock-code, --stock-codes, or --stock-file")
    result = []
    seen = set()
    for value in values:
        code = normalize_stock_code(value)
        if code not in seen:
            seen.add(code)
            result.append(code)
    return result


def default_output_dir(args: argparse.Namespace, stock_codes: list[str]) -> Path:
    root = Path(args.output_dir) if args.output_dir else Path("outputs") / "comment_backtest"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    code_label = stock_codes[0] if len(stock_codes) == 1 else f"{len(stock_codes)}stocks"
    date_label = args.signal_date or f"{args.start_date}_{args.end_date}"
    return root / f"{stamp}_{code_label}_{date_label}_{args.lookback_days}d_{args.forward_days}td"


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=json_default) + "\n")


def write_csv(path: Path, records: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        keys: list[str] = []
        seen: set[str] = set()
        for record in records:
            for key in record.keys():
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
        fieldnames = keys
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(record)


def build_command(args: argparse.Namespace) -> dict[str, Any]:
    if not args.signal_date and not (args.start_date and args.end_date):
        raise ValueError("Provide either --signal-date or both --start-date and --end-date")
    stock_codes = read_stock_codes(args)
    output_dir = default_output_dir(args, stock_codes)
    ensure_dir(output_dir)
    cache_dir = Path(args.cache_dir) if args.cache_dir else output_dir / ".cache"
    if args.no_cache:
        cache_dir = None
    elif cache_dir is not None:
        ensure_dir(cache_dir)

    all_comments: list[dict[str, Any]] = []
    all_tasks: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    all_labels: list[dict[str, Any]] = []
    warnings: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(build_windows_for_stock, stock_code, args, cache_dir): stock_code
            for stock_code in stock_codes
        }
        for future in as_completed(futures):
            stock_code = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Worker failed for {stock_code}: {exc}")
                continue
            all_comments.extend(result.raw_comments)
            all_tasks.extend(result.agent_tasks)
            all_rows.extend(result.rows)
            all_labels.extend(result.labels)
            warnings.extend(result.warnings)

    all_comments.sort(key=lambda item: (item.get("stock_code") or "", item.get("published_at") or "", item.get("post_id") or ""))
    all_tasks.sort(key=lambda item: (item.get("stock_code") or "", item.get("signal_date") or ""))
    all_rows.sort(key=lambda item: (item.get("stock_code") or "", item.get("signal_date") or ""))
    all_labels.sort(key=lambda item: (item.get("stock_code") or "", item.get("signal_date") or ""))

    raw_comments_path = output_dir / "raw_comments.jsonl"
    agent_tasks_path = output_dir / "agent_tasks.jsonl"
    labels_path = output_dir / "labels.csv"
    dataset_path = output_dir / "backtest_dataset.csv"
    template_path = output_dir / "sentiment_template.csv"
    manifest_path = output_dir / "manifest.json"

    write_jsonl(raw_comments_path, all_comments)
    write_jsonl(agent_tasks_path, all_tasks)
    write_csv(labels_path, all_labels)
    write_csv(dataset_path, all_rows)
    template_records = [
        {
            "window_id": row["window_id"],
            "stock_code": row["stock_code"],
            "signal_date": row["signal_date"],
            "sentiment_score": "",
            "bullish_score": "",
            "bearish_score": "",
            "neutral_score": "",
            "disagreement_score": "",
            "confidence": "",
            "notes": "",
        }
        for row in all_rows
    ]
    write_csv(template_path, template_records)

    manifest = {
        "created_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "stock_count": len(stock_codes),
        "window_count": len(all_rows),
        "raw_comment_count": len(all_comments),
        "parameters": {
            "stock_codes": stock_codes,
            "signal_date": args.signal_date,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "lookback_days": args.lookback_days,
            "forward_days": args.forward_days,
            "return_start": args.return_start,
            "max_pages": args.max_pages,
            "max_comments_per_window": args.max_comments_per_window,
            "include_details": args.include_details,
        },
        "paths": {
            "raw_comments": str(raw_comments_path),
            "agent_tasks": str(agent_tasks_path),
            "labels": str(labels_path),
            "backtest_dataset": str(dataset_path),
            "sentiment_template": str(template_path),
        },
        "warnings": warnings,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    printable = {"output_dir": str(output_dir), **manifest["paths"], "warnings": warnings[:20]}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return manifest


def load_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".jsonl":
        records = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return pd.DataFrame(records)
    return pd.read_csv(path)


def max_drawdown(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    equity = (1 + returns.fillna(0)).cumprod()
    drawdown = equity / equity.cummax() - 1
    return float(drawdown.min())


def backtest_command(args: argparse.Namespace) -> dict[str, Any]:
    dataset = load_table(Path(args.dataset))
    sentiment = load_table(Path(args.sentiment_file))
    if args.score_column not in sentiment.columns:
        raise ValueError(f"Sentiment file missing score column: {args.score_column}")

    if "window_id" in dataset.columns and "window_id" in sentiment.columns:
        merged = dataset.merge(sentiment, on="window_id", how="inner", suffixes=("", "_sent"))
    else:
        keys = ["stock_code", "signal_date"]
        merged = dataset.merge(sentiment, on=keys, how="inner", suffixes=("", "_sent"))

    merged[args.score_column] = pd.to_numeric(merged[args.score_column], errors="coerce")
    merged[args.return_column] = pd.to_numeric(merged[args.return_column], errors="coerce")
    merged = merged.dropna(subset=[args.score_column, args.return_column]).copy()
    if merged.empty:
        raise ValueError("No valid rows after merging sentiment scores and returns")

    daily_ic_records = []
    for signal_date, group in merged.groupby("signal_date"):
        ic = None
        if len(group) >= 3 and group[args.score_column].nunique() > 1 and group[args.return_column].nunique() > 1:
            score_rank = group[args.score_column].rank(method="average")
            return_rank = group[args.return_column].rank(method="average")
            ic = score_rank.corr(return_rank)
        daily_ic_records.append({"signal_date": signal_date, "rank_ic": ic, "sample_count": len(group)})
    daily_ic = pd.DataFrame(daily_ic_records)

    quantile_frames = []
    for signal_date, group in merged.groupby("signal_date"):
        group = group.copy()
        q = min(args.groups, len(group), group[args.score_column].nunique())
        if q < 2:
            continue
        ranks = group[args.score_column].rank(method="first")
        group["quantile"] = pd.qcut(ranks, q=q, labels=False, duplicates="drop") + 1
        quantile_frames.append(group)
    if quantile_frames:
        quantile_data = pd.concat(quantile_frames, ignore_index=True)
        quantile_returns = (
            quantile_data.groupby(["signal_date", "quantile"], as_index=False)[args.return_column]
            .mean()
            .rename(columns={args.return_column: "mean_return"})
        )
        mean_by_quantile = (
            quantile_data.groupby("quantile")[args.return_column]
            .agg(mean_return="mean", sample_count="count")
            .reset_index()
        )
        long_short_records = []
        for signal_date, group in quantile_returns.groupby("signal_date"):
            top_q = group["quantile"].max()
            bottom_q = group["quantile"].min()
            top_ret = group.loc[group["quantile"] == top_q, "mean_return"].mean()
            bottom_ret = group.loc[group["quantile"] == bottom_q, "mean_return"].mean()
            long_short_records.append(
                {
                    "signal_date": signal_date,
                    "top_quantile": int(top_q),
                    "bottom_quantile": int(bottom_q),
                    "top_return": top_ret,
                    "bottom_return": bottom_ret,
                    "long_short_return": top_ret - bottom_ret,
                }
            )
        long_short = pd.DataFrame(long_short_records)
    else:
        quantile_returns = pd.DataFrame(columns=["signal_date", "quantile", "mean_return"])
        mean_by_quantile = pd.DataFrame(columns=["quantile", "mean_return", "sample_count"])
        long_short = pd.DataFrame(columns=["signal_date", "long_short_return"])

    output_dir = Path(args.output_dir) if args.output_dir else Path(args.dataset).resolve().parent / "backtest_results"
    ensure_dir(output_dir)
    merged_path = output_dir / "merged_backtest.csv"
    daily_ic_path = output_dir / "daily_ic.csv"
    quantile_path = output_dir / "quantile_returns.csv"
    mean_quantile_path = output_dir / "mean_by_quantile.csv"
    long_short_path = output_dir / "long_short.csv"
    summary_path = output_dir / "summary.json"

    merged.to_csv(merged_path, index=False, encoding="utf-8-sig")
    daily_ic.to_csv(daily_ic_path, index=False, encoding="utf-8-sig")
    quantile_returns.to_csv(quantile_path, index=False, encoding="utf-8-sig")
    mean_by_quantile.to_csv(mean_quantile_path, index=False, encoding="utf-8-sig")
    long_short.to_csv(long_short_path, index=False, encoding="utf-8-sig")

    valid_ic = pd.to_numeric(daily_ic["rank_ic"], errors="coerce").dropna()
    ls_returns = pd.to_numeric(long_short.get("long_short_return", pd.Series(dtype=float)), errors="coerce").dropna()
    summary = {
        "row_count": int(len(merged)),
        "stock_count": int(merged["stock_code"].nunique()) if "stock_code" in merged.columns else None,
        "date_count": int(merged["signal_date"].nunique()) if "signal_date" in merged.columns else None,
        "mean_future_return": float(merged[args.return_column].mean()),
        "rank_ic_mean": None if valid_ic.empty else float(valid_ic.mean()),
        "rank_ic_ir": None if len(valid_ic) < 2 or valid_ic.std() == 0 else float(valid_ic.mean() / valid_ic.std()),
        "long_short_mean": None if ls_returns.empty else float(ls_returns.mean()),
        "long_short_win_rate": None if ls_returns.empty else float((ls_returns > 0).mean()),
        "long_short_max_drawdown": max_drawdown(ls_returns),
        "paths": {
            "merged_backtest": str(merged_path),
            "daily_ic": str(daily_ic_path),
            "quantile_returns": str(quantile_path),
            "mean_by_quantile": str(mean_quantile_path),
            "long_short": str(long_short_path),
            "summary": str(summary_path),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def add_build_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("build", help="Build comment windows and forward-return labels")
    parser.add_argument("--stock-code", default=None)
    parser.add_argument("--stock-codes", default=None)
    parser.add_argument("--stock-file", default=None)
    parser.add_argument("--signal-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--lookback-days", type=int, required=True)
    parser.add_argument("--forward-days", type=int, required=True)
    parser.add_argument("--return-start", choices=["next_open", "signal_close"], default="next_open")
    parser.add_argument("--cutoff-time", default="23:59:59")
    parser.add_argument("--adjust", default="qfq")
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument("--max-comments-per-window", type=int, default=80)
    parser.add_argument("--include-details", action="store_true")
    parser.add_argument("--detail-limit", type=int, default=200)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--sleep-seconds", type=float, default=0.2)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--output-dir", default=None)
    parser.set_defaults(func=build_command)


def add_backtest_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("backtest", help="Backtest scored sentiment against forward returns")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--sentiment-file", required=True)
    parser.add_argument("--score-column", default="sentiment_score")
    parser.add_argument("--return-column", default="future_return")
    parser.add_argument("--groups", type=int, default=5)
    parser.add_argument("--output-dir", default=None)
    parser.set_defaults(func=backtest_command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eastmoney Guba comment windows and forward-return backtests.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_build_parser(subparsers)
    add_backtest_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "lookback_days", 1) < 1:
        raise ValueError("--lookback-days must be >= 1")
    if getattr(args, "forward_days", 1) < 1:
        raise ValueError("--forward-days must be >= 1")
    args.func(args)


if __name__ == "__main__":
    main()
