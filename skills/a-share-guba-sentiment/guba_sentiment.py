from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests


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

SOURCE_NAME = "eastmoney_guba"
COLLECTOR_VERSION = "eastmoney_guba_collect_v1"
SCORING_PROMPT_VERSION = "guba_sentiment_agent_v1"
SENTIMENT_CACHE_SCHEMA_VERSION = 1
DEFAULT_OUTPUT_DIR = Path("outputs") / "guba_sentiment"
DEFAULT_SENTIMENT_CACHE_DIR = DEFAULT_OUTPUT_DIR
LEGACY_SENTIMENT_CACHE_DIR = Path("outputs") / "guba_sentiment_cache"

DAILY_CSV_FIELDS = [
    "stock_code",
    "date",
    "sentiment_score",
    "bullish_score",
    "bearish_score",
    "neutral_score",
    "disagreement_score",
    "attention_score",
    "confidence",
    "batch_count",
    "post_count",
    "comment_count",
    "cache_hit",
    "cache_file",
    "summary",
    "key_bullish_evidence",
    "key_bearish_evidence",
]


@dataclass
class CollectResult:
    stock_code: str
    posts: list[dict[str, Any]]
    warnings: list[str]


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
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


def normalize_stock_code(value: str) -> str:
    code = re.sub(r"\D", "", str(value))
    if len(code) != 6:
        raise ValueError(f"Invalid A-share stock code: {value!r}")
    return code


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def iter_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def stable_hash(value: Any, length: int = 16) -> str:
    if value in (None, ""):
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length]


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ")
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return str(value)


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


def sentiment_cache_version_label() -> str:
    return f"{COLLECTOR_VERSION}__{SCORING_PROMPT_VERSION}"


def sentiment_cache_key(stock_code: str, day: str) -> str:
    return "|".join(
        [
            SOURCE_NAME,
            stock_code,
            day,
            f"collector={COLLECTOR_VERSION}",
            f"scoring_prompt={SCORING_PROMPT_VERSION}",
        ]
    )


def sentiment_cache_path(cache_dir: Path, stock_code: str, day: str) -> Path:
    return cache_dir / SOURCE_NAME / stock_code / day / "sentiment" / f"{sentiment_cache_version_label()}.json"


def legacy_sentiment_cache_path(stock_code: str, day: str) -> Path:
    return LEGACY_SENTIMENT_CACHE_DIR / SOURCE_NAME / sentiment_cache_version_label() / stock_code / f"{day}.json"


def load_sentiment_cache_record(path: Path, stock_code: str, day: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("cache_schema_version") != SENTIMENT_CACHE_SCHEMA_VERSION:
        return None
    if record.get("cache_key") != sentiment_cache_key(stock_code, day):
        return None
    return record


def read_cached_daily_sentiment(cache_dir: Path, stock_code: str, day: str) -> dict[str, Any] | None:
    path = sentiment_cache_path(cache_dir, stock_code, day)
    record = load_sentiment_cache_record(path, stock_code, day)
    if record is None:
        legacy_path = legacy_sentiment_cache_path(stock_code, day)
        record = load_sentiment_cache_record(legacy_path, stock_code, day)
        if record is None:
            return None
        path = legacy_path
    row = dict(record.get("daily_sentiment") or {})
    if not row:
        return None
    row["cache_hit"] = True
    row["cache_key"] = record.get("cache_key")
    row["cache_file"] = str(path)
    row["cached_at"] = record.get("cached_at")
    row["source_data_hash"] = record.get("source_data_hash")
    row.setdefault("source", SOURCE_NAME)
    row.setdefault("collector_version", COLLECTOR_VERSION)
    row.setdefault("scoring_prompt_version", SCORING_PROMPT_VERSION)
    return row


def write_cached_daily_sentiment(
    cache_dir: Path,
    row: dict[str, Any],
    run_dir: Path,
    source_data_hash: str,
    scorer_id: str,
) -> Path:
    day = str(row["date"])
    stock_code = normalize_stock_code(str(row["stock_code"]))
    path = sentiment_cache_path(cache_dir, stock_code, day)
    ensure_dir(path.parent)
    clean_row = dict(row)
    clean_row.pop("cache_file", None)
    clean_row["cache_hit"] = False
    clean_row["source"] = SOURCE_NAME
    clean_row["collector_version"] = COLLECTOR_VERSION
    clean_row["scoring_prompt_version"] = SCORING_PROMPT_VERSION
    record = {
        "cache_schema_version": SENTIMENT_CACHE_SCHEMA_VERSION,
        "cache_key": sentiment_cache_key(stock_code, day),
        "source": SOURCE_NAME,
        "collector_version": COLLECTOR_VERSION,
        "scoring_prompt_version": SCORING_PROMPT_VERSION,
        "scorer_id": scorer_id,
        "stock_code": stock_code,
        "date": day,
        "cached_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "run_dir": str(run_dir),
        "source_data_hash": source_data_hash,
        "daily_sentiment": clean_row,
    }
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    tmp.replace(path)
    return path


def daily_input_hash(run_dir: Path, day: str) -> str:
    path = run_dir / "daily_inputs" / f"{day}.json"
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def combine_text(parts: list[Any], max_chars: int = 20000) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        text = strip_html(part)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return "\n".join(out)[:max_chars].strip()


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def engagement_weight(post: dict[str, Any]) -> float:
    metrics = post.get("metrics") or {}
    replies = to_float(metrics.get("reply_count")) or 0
    likes = to_float(metrics.get("like_count")) or 0
    clicks = to_float(metrics.get("click_count")) or 0
    forwards = to_float(metrics.get("forward_count")) or 0
    comments = len(post.get("comments") or [])
    raw = 1 + replies * 2 + comments * 2 + likes + forwards * 2 + min(clicks, 50000) / 2000
    return min(50.0, max(1.0, math.log1p(raw) * 5))


def cache_path_for_request(cache_dir: Path | None, key: str) -> Path | None:
    if cache_dir is None:
        return None
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return cache_dir / "http" / f"{digest}.json"


def fetch_json(
    session: requests.Session,
    method: str,
    url: str,
    params: dict[str, Any],
    cache_dir: Path | None,
    refresh_cache: bool,
    timeout: int,
    warnings: list[str],
) -> dict[str, Any]:
    key = method.upper() + " " + url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    cached = cache_path_for_request(cache_dir, key)
    if cached and cached.exists() and not refresh_cache:
        return json.loads(cached.read_text(encoding="utf-8"))
    try:
        if method.lower() == "post":
            response = session.post(url, headers=EASTMONEY_MOBILE_HEADERS, data=params, timeout=timeout)
        else:
            response = session.get(url, headers=EASTMONEY_MOBILE_HEADERS, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Fetch failed: {url} ({exc})")
        raise
    if cached:
        ensure_dir(cached.parent)
        cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def guba_url(stock_code: str, post_id: Any) -> str:
    return f"https://guba.eastmoney.com/news,{stock_code},{post_id}.html"


def mobile_url(post_id: Any, post_type: Any = 0) -> str:
    return f"https://mguba.eastmoney.com/mguba/article/{post_type or 0}/{post_id}"


def fetch_post_list_page(
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
    data = fetch_json(
        session,
        "get",
        "https://gbapi.eastmoney.com/webarticlelist/api/Article/Articlelist",
        params,
        cache_dir,
        refresh_cache,
        timeout,
        warnings,
    )
    if data.get("rc") != 1:
        raise RuntimeError(data.get("me") or f"Eastmoney list rc={data.get('rc')}")
    return data.get("re") or []


def fetch_post_detail(
    session: requests.Session,
    post_id: Any,
    post_type: Any,
    cache_dir: Path | None,
    refresh_cache: bool,
    timeout: int,
    warnings: list[str],
) -> dict[str, Any]:
    type_candidates = []
    for value in [post_type, 0]:
        if value not in type_candidates:
            type_candidates.append(value)
    last_message = ""
    for candidate in type_candidates:
        params = {
            **EASTMONEY_WAP_PARAMS,
            "postid": str(post_id),
            "type": str(candidate or 0),
            "cutword": "true",
            "paytext": "true",
            "location": "WAP|Article|wap|TRUE",
            "env": "prod",
            "bizfrom": "ugc",
        }
        data = fetch_json(
            session,
            "post",
            f"https://mguba.eastmoney.com/api/getArticle?postid={post_id}",
            params,
            cache_dir,
            refresh_cache,
            timeout,
            warnings,
        )
        if data.get("rc") == 1 and data.get("post"):
            return data["post"]
        last_message = data.get("me") or f"Article detail missing for {post_id}"
    raise RuntimeError(last_message)


def normalize_reply(reply: dict[str, Any], parent_id: Any = None) -> dict[str, Any]:
    user = reply.get("reply_user") or {}
    published = reply.get("reply_publish_time") or reply.get("reply_time")
    return {
        "comment_id": str(reply.get("reply_id") or ""),
        "parent_id": str(parent_id) if parent_id not in (None, "") else None,
        "published_at": published or "",
        "author_hash": stable_hash(user.get("user_id") or reply.get("user_id")),
        "author_nickname": user.get("user_nickname") or "",
        "text": strip_html(reply.get("reply_text")),
        "like_count": reply.get("reply_like_count"),
        "is_author": bool(reply.get("reply_is_author")),
    }


def fetch_post_replies(
    session: requests.Session,
    post_id: Any,
    post_type: Any,
    max_pages: int,
    page_size: int,
    cache_dir: Path | None,
    refresh_cache: bool,
    timeout: int,
    sleep_seconds: float,
    warnings: list[str],
) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    seen: set[str] = set()
    total: int | None = None
    for page in range(1, max_pages + 1):
        params = {
            **EASTMONEY_WAP_PARAMS,
            "postid": str(post_id),
            "type": str(post_type or 0),
            "ps": str(page_size),
            "p": str(page),
            "needrich": "true",
            "sort": "1",
            "sorttype": "1",
        }
        data = fetch_json(
            session,
            "get",
            "https://gbapi.eastmoney.com/reply/api/Reply/ArticleNewReplyList",
            params,
            cache_dir,
            refresh_cache,
            timeout,
            warnings,
        )
        if data.get("rc") != 1:
            warnings.append(f"Reply list rc={data.get('rc')} post_id={post_id}: {data.get('me')}")
            break
        replies = data.get("re") or []
        total = data.get("reply_total_count") or data.get("count") or total
        if not replies:
            break
        for reply in replies:
            normalized = normalize_reply(reply)
            cid = normalized["comment_id"]
            if cid and cid not in seen and normalized["text"]:
                seen.add(cid)
                comments.append(normalized)
            for child in reply.get("child_replys") or []:
                child_normalized = normalize_reply(child, parent_id=cid)
                child_id = child_normalized["comment_id"]
                if child_id and child_id not in seen and child_normalized["text"]:
                    seen.add(child_id)
                    comments.append(child_normalized)
        if total is not None and len([item for item in comments if item.get("parent_id") is None]) >= int(total):
            break
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    comments.sort(key=lambda item: item.get("published_at") or "")
    return comments


def normalize_post(stock_code: str, item: dict[str, Any], detail: dict[str, Any], comments: list[dict[str, Any]]) -> dict[str, Any]:
    post_id = str(detail.get("post_id") or item.get("post_id") or "")
    post_type = detail.get("post_type", item.get("post_type", 0))
    published_dt = parse_datetime(detail.get("post_publish_time") or item.get("post_publish_time") or item.get("post_display_time"))
    published_at = "" if published_dt is None else published_dt.isoformat(sep=" ")
    guba = detail.get("post_guba") or {}
    user = detail.get("post_user") or {}
    title = strip_html(detail.get("post_title") or item.get("post_title"))
    body = combine_text(
        [
            detail.get("post_content") or "",
            detail.get("post_abstract") or "",
            detail.get("source_post_title") or "",
            detail.get("source_post_content") or "",
            detail.get("source_post_abstract") or "",
        ],
        max_chars=30000,
    )
    text = combine_text([title, body], max_chars=32000)
    metrics = {
        "click_count": detail.get("post_click_count", item.get("post_click_count")),
        "reply_count": detail.get("post_comment_count", item.get("post_comment_count")),
        "like_count": detail.get("post_like_count", item.get("post_like_count")),
        "forward_count": detail.get("post_forward_count", item.get("post_forward_count")),
    }
    post = {
        "stock_code": stock_code,
        "stock_name": guba.get("stockbar_name") or item.get("stockbar_name") or "",
        "source": "eastmoney_guba",
        "post_id": post_id,
        "post_type": post_type,
        "url": guba_url(stock_code, post_id),
        "mobile_url": mobile_url(post_id, post_type),
        "published_at": published_at,
        "date": "" if published_dt is None else published_dt.date().isoformat(),
        "author_hash": stable_hash(user.get("user_id") or item.get("user_id")),
        "author_nickname": user.get("user_nickname") or item.get("user_nickname") or "",
        "title": title,
        "body": body,
        "text": text,
        "metrics": metrics,
        "comments": comments,
    }
    post["weight"] = engagement_weight(post)
    return post


def collect_posts_for_stock(
    stock_code: str,
    start_dt: datetime,
    end_dt: datetime,
    max_pages: int,
    max_reply_pages: int,
    reply_page_size: int,
    timeout: int,
    sleep_seconds: float,
    cache_dir: Path | None,
    refresh_cache: bool,
) -> CollectResult:
    session = requests.Session()
    warnings: list[str] = []
    posts: list[dict[str, Any]] = []
    seen_posts: set[str] = set()
    oldest_seen: datetime | None = None
    newest_seen: datetime | None = None
    reached_before_start = False
    for page in range(1, max_pages + 1):
        try:
            items = fetch_post_list_page(session, stock_code, page, 80, cache_dir, refresh_cache, timeout, warnings)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Post list failed stock={stock_code} page={page}: {exc}")
            break
        if not items:
            break
        page_times: list[datetime] = []
        for item in items:
            published_dt = parse_datetime(item.get("post_publish_time") or item.get("post_display_time"))
            if published_dt is None:
                continue
            page_times.append(published_dt)
            oldest_seen = published_dt if oldest_seen is None else min(oldest_seen, published_dt)
            newest_seen = published_dt if newest_seen is None else max(newest_seen, published_dt)
            if published_dt < start_dt or published_dt > end_dt:
                continue
            post_id = str(item.get("post_id") or "")
            if not post_id or post_id in seen_posts:
                continue
            seen_posts.add(post_id)
            post_type = item.get("post_type", 0)
            try:
                detail = fetch_post_detail(session, post_id, post_type, cache_dir, refresh_cache, timeout, warnings)
                comments = fetch_post_replies(
                    session,
                    post_id,
                    detail.get("post_type", post_type),
                    max_reply_pages,
                    reply_page_size,
                    cache_dir,
                    refresh_cache,
                    timeout,
                    sleep_seconds,
                    warnings,
                )
                posts.append(normalize_post(stock_code, item, detail, comments))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Post detail/replies failed post_id={post_id}: {exc}")
            if sleep_seconds > 0:
                time.sleep(sleep_seconds)
        if page_times and max(page_times) < start_dt:
            reached_before_start = True
            break
    if not reached_before_start:
        if oldest_seen is None:
            warnings.append(f"Coverage unknown stock={stock_code}: no list timestamps were returned before max_pages={max_pages}.")
        elif oldest_seen > start_dt:
            warnings.append(
                "Coverage incomplete "
                f"stock={stock_code}: oldest_seen={oldest_seen.isoformat(sep=' ')} "
                f"is newer than start={start_dt.isoformat(sep=' ')}; increase --max-pages "
                "or split the request."
            )
    posts.sort(key=lambda item: (item.get("published_at") or "", item.get("post_id") or ""))
    return CollectResult(stock_code=stock_code, posts=posts, warnings=warnings)


def parse_date_args(args: argparse.Namespace) -> tuple[date, date]:
    if args.date:
        day = parse_date(args.date)
        return day, day
    if not (args.start_date and args.end_date):
        raise ValueError("Provide --date or both --start-date and --end-date")
    start = parse_date(args.start_date)
    end = parse_date(args.end_date)
    if end < start:
        raise ValueError("--end-date must be >= --start-date")
    return start, end


def default_run_dir(args: argparse.Namespace, start: date, end: date) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    stock_root = root / SOURCE_NAME / args.stock_code
    if start == end:
        return stock_root / start.isoformat() / "runs" / stamp
    date_label = f"{start.isoformat()}_{end.isoformat()}"
    return stock_root / "_range_runs" / date_label / stamp


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=json_default) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_daily_sentiment_outputs(run_dir: Path, daily_rows: list[dict[str, Any]]) -> dict[str, Any]:
    out_jsonl = run_dir / "daily_sentiment.jsonl"
    out_csv = run_dir / "daily_sentiment.csv"
    write_jsonl(out_jsonl, daily_rows)
    with out_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=DAILY_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in daily_rows:
            csv_row = dict(row)
            csv_row["key_bullish_evidence"] = ",".join(row.get("key_bullish_evidence") or [])
            csv_row["key_bearish_evidence"] = ",".join(row.get("key_bearish_evidence") or [])
            writer.writerow(csv_row)
    summary = {
        "daily_count": len(daily_rows),
        "paths": {
            "daily_sentiment_jsonl": str(out_jsonl),
            "daily_sentiment_csv": str(out_csv),
            "sentiment_summary": str(run_dir / "sentiment_summary.json"),
        },
        "daily_sentiment": daily_rows,
    }
    (run_dir / "sentiment_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    return summary


def batch_prompt(stock_code: str, day: str, batch_id: str) -> str:
    return (
        f"Analyze Eastmoney Guba sentiment for stock {stock_code} on {day}, batch {batch_id}. "
        "Use only the provided post body and comments. Do not use future returns, later news, "
        "outside market data, or any information not present in this batch. "
        "Return one JSON object with sentiment_score (-100 to 100), "
        "bullish_score, bearish_score, neutral_score, disagreement_score, attention_score, "
        "confidence, summary, key_bullish_evidence, key_bearish_evidence. Evidence should use post_id values."
    )


def slim_post_for_batch(post: dict[str, Any]) -> dict[str, Any]:
    return {
        "post_id": post.get("post_id"),
        "published_at": post.get("published_at"),
        "url": post.get("url"),
        "author_hash": post.get("author_hash"),
        "title": post.get("title"),
        "body": post.get("body"),
        "metrics": post.get("metrics"),
        "weight": post.get("weight"),
        "comments": post.get("comments") or [],
    }


def make_batches(posts: list[dict[str, Any]], run_dir: Path, max_chars: int, max_posts: int) -> list[dict[str, Any]]:
    batch_dir = run_dir / "agent_batches"
    daily_dir = run_dir / "daily_inputs"
    ensure_dir(batch_dir)
    ensure_dir(daily_dir)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for post in posts:
        if post.get("date"):
            by_day[post["date"]].append(post)
    batch_index: list[dict[str, Any]] = []
    score_templates: list[dict[str, Any]] = []
    for day in sorted(by_day):
        day_posts = by_day[day]
        stock_code = str(day_posts[0].get("stock_code") or "")
        daily_payload = {
            "stock_code": stock_code,
            "date": day,
            "post_count": len(day_posts),
            "comment_count": sum(len(post.get("comments") or []) for post in day_posts),
            "posts": [slim_post_for_batch(post) for post in day_posts],
        }
        (daily_dir / f"{day}.json").write_text(json.dumps(daily_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        current: list[dict[str, Any]] = []
        batch_no = 1
        for post in day_posts:
            candidate = current + [post]
            payload = {
                "stock_code": stock_code,
                "date": day,
                "posts": [slim_post_for_batch(item) for item in candidate],
            }
            size = len(json.dumps(payload, ensure_ascii=False))
            if current and (size > max_chars or (max_posts > 0 and len(candidate) > max_posts)):
                batch_no = flush_batch(batch_dir, batch_index, score_templates, stock_code, day, batch_no, current)
                current = [post]
            else:
                current = candidate
        if current:
            flush_batch(batch_dir, batch_index, score_templates, stock_code, day, batch_no, current)
    write_jsonl(run_dir / "batch_score_template.jsonl", score_templates)
    with (run_dir / "batch_index.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fields = ["batch_id", "stock_code", "date", "path", "post_count", "comment_count", "weight"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in batch_index:
            writer.writerow(row)
    return batch_index


def flush_batch(
    batch_dir: Path,
    batch_index: list[dict[str, Any]],
    score_templates: list[dict[str, Any]],
    stock_code: str,
    day: str,
    batch_no: int,
    posts: list[dict[str, Any]],
) -> int:
    batch_id = f"{stock_code}_{day.replace('-', '')}_{batch_no:03d}"
    payload = {
        "batch_id": batch_id,
        "stock_code": stock_code,
        "date": day,
        "post_count": len(posts),
        "comment_count": sum(len(post.get("comments") or []) for post in posts),
        "weight": sum(float(post.get("weight") or 1) for post in posts),
        "scoring_schema": {
            "sentiment_score": "-100 bearish to 100 bullish",
            "bullish_score": "0 to 100",
            "bearish_score": "0 to 100",
            "neutral_score": "0 to 100",
            "disagreement_score": "0 to 100",
            "attention_score": "0 to 100",
            "confidence": "0 to 100",
        },
        "prompt": batch_prompt(stock_code, day, batch_id),
        "posts": [slim_post_for_batch(post) for post in posts],
    }
    path = batch_dir / f"{batch_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    batch_index.append(
        {
            "batch_id": batch_id,
            "stock_code": stock_code,
            "date": day,
            "path": str(path),
            "post_count": payload["post_count"],
            "comment_count": payload["comment_count"],
            "weight": payload["weight"],
        }
    )
    score_templates.append(
        {
            "batch_id": batch_id,
            "stock_code": stock_code,
            "date": day,
            "sentiment_score": None,
            "bullish_score": None,
            "bearish_score": None,
            "neutral_score": None,
            "disagreement_score": None,
            "attention_score": None,
            "confidence": None,
            "summary": "",
            "key_bullish_evidence": [],
            "key_bearish_evidence": [],
        }
    )
    return batch_no + 1


def collect_command(args: argparse.Namespace) -> dict[str, Any]:
    args.stock_code = normalize_stock_code(args.stock_code)
    start, end = parse_date_args(args)
    run_dir = default_run_dir(args, start, end)
    ensure_dir(run_dir)
    requested_days = [item.isoformat() for item in iter_days(start, end)]
    sentiment_cache_dir = None if args.no_sentiment_cache else Path(args.sentiment_cache_dir)
    cached_rows: dict[str, dict[str, Any]] = {}
    if sentiment_cache_dir is not None and not args.refresh_sentiment_cache:
        for day in requested_days:
            cached = read_cached_daily_sentiment(sentiment_cache_dir, args.stock_code, day)
            if cached:
                cached_rows[day] = cached
    missing_days = [day for day in requested_days if day not in cached_rows]
    cached_rows_path = run_dir / "cached_daily_sentiment.jsonl"
    write_jsonl(cached_rows_path, [cached_rows[day] for day in sorted(cached_rows)])

    if not missing_days:
        posts_path = run_dir / "posts.jsonl"
        write_jsonl(posts_path, [])
        batch_index = make_batches([], run_dir, args.max_batch_chars, args.max_posts_per_batch)
        daily_rows = [cached_rows[day] for day in sorted(cached_rows)]
        summary = write_daily_sentiment_outputs(run_dir, daily_rows)
        manifest = {
            "created_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "stock_code": args.stock_code,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "post_count": 0,
            "comment_count": 0,
            "batch_count": len(batch_index),
            "daily_counts": {},
            "cache": {
                "enabled": sentiment_cache_dir is not None,
                "cache_dir": "" if sentiment_cache_dir is None else str(sentiment_cache_dir),
                "cache_version": sentiment_cache_version_label(),
                "hit_dates": sorted(cached_rows),
                "miss_dates": [],
                "refresh": bool(args.refresh_sentiment_cache),
            },
            "paths": {
                "posts": str(posts_path),
                "daily_inputs": str(run_dir / "daily_inputs"),
                "agent_batches": str(run_dir / "agent_batches"),
                "batch_index": str(run_dir / "batch_index.csv"),
                "batch_score_template": str(run_dir / "batch_score_template.jsonl"),
                "cached_daily_sentiment": str(cached_rows_path),
                **summary["paths"],
            },
            "warnings": [],
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
        printable = {
            "run_dir": str(run_dir),
            **manifest["paths"],
            "cache_hit": True,
            "cache_hit_dates": sorted(cached_rows),
            "cache_miss_dates": [],
            "post_count": 0,
            "comment_count": 0,
            "batch_count": len(batch_index),
            "warnings": [],
        }
        print(json.dumps(printable, ensure_ascii=False, indent=2, default=json_default))
        return manifest

    cache_dir = None if args.no_cache else run_dir / ".cache"
    if cache_dir:
        ensure_dir(cache_dir)
    collect_start = parse_date(min(missing_days))
    collect_end = parse_date(max(missing_days))
    missing_day_set = set(missing_days)
    start_dt = datetime.combine(collect_start, clock_time(0, 0, 0))
    end_dt = datetime.combine(collect_end, clock_time(23, 59, 59))
    result = collect_posts_for_stock(
        stock_code=args.stock_code,
        start_dt=start_dt,
        end_dt=end_dt,
        max_pages=args.max_pages,
        max_reply_pages=args.max_reply_pages,
        reply_page_size=args.reply_page_size,
        timeout=args.timeout,
        sleep_seconds=args.sleep_seconds,
        cache_dir=cache_dir,
        refresh_cache=args.refresh_cache,
    )
    result.posts = [post for post in result.posts if post.get("date") in missing_day_set]
    posts_path = run_dir / "posts.jsonl"
    write_jsonl(posts_path, result.posts)
    batch_index = make_batches(result.posts, run_dir, args.max_batch_chars, args.max_posts_per_batch)
    daily_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"post_count": 0, "comment_count": 0})
    for post in result.posts:
        day = post.get("date") or ""
        daily_counts[day]["post_count"] += 1
        daily_counts[day]["comment_count"] += len(post.get("comments") or [])
    manifest = {
        "created_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "stock_code": args.stock_code,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "post_count": len(result.posts),
        "comment_count": sum(len(post.get("comments") or []) for post in result.posts),
        "batch_count": len(batch_index),
        "daily_counts": daily_counts,
        "cache": {
            "enabled": sentiment_cache_dir is not None,
            "cache_dir": "" if sentiment_cache_dir is None else str(sentiment_cache_dir),
            "cache_version": sentiment_cache_version_label(),
            "hit_dates": sorted(cached_rows),
            "miss_dates": missing_days,
            "refresh": bool(args.refresh_sentiment_cache),
        },
        "paths": {
            "posts": str(posts_path),
            "daily_inputs": str(run_dir / "daily_inputs"),
            "agent_batches": str(run_dir / "agent_batches"),
            "batch_index": str(run_dir / "batch_index.csv"),
            "batch_score_template": str(run_dir / "batch_score_template.jsonl"),
            "cached_daily_sentiment": str(cached_rows_path),
        },
        "warnings": result.warnings,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    printable = {
        "run_dir": str(run_dir),
        **manifest["paths"],
        "cache_hit": False,
        "cache_hit_dates": sorted(cached_rows),
        "cache_miss_dates": missing_days,
        "post_count": manifest["post_count"],
        "comment_count": manifest["comment_count"],
        "batch_count": manifest["batch_count"],
        "warnings": result.warnings[:20],
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2, default=json_default))
    return manifest


def read_batch_index(run_dir: Path) -> dict[str, dict[str, Any]]:
    path = run_dir / "batch_index.csv"
    with path.open("r", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    return {row["batch_id"]: row for row in rows}


def numeric(value: Any, default: float | None = None) -> float | None:
    result = to_float(value)
    return default if result is None else result


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def aggregate_command(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir)
    scores = load_jsonl(Path(args.scores))
    index = read_batch_index(run_dir)
    cached_rows_path = run_dir / "cached_daily_sentiment.jsonl"
    cached_rows = load_jsonl(cached_rows_path) if cached_rows_path.exists() else []
    by_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for score in scores:
        batch_id = score.get("batch_id")
        meta = index.get(batch_id)
        if not meta:
            raise ValueError(f"Score references unknown batch_id: {batch_id}")
        row = {**score, "_meta": meta}
        by_day[(meta["stock_code"], meta["date"])].append(row)
    daily_rows: list[dict[str, Any]] = []
    score_fields = ["sentiment_score", "bullish_score", "bearish_score", "neutral_score", "disagreement_score", "attention_score", "confidence"]
    for (stock_code, day), rows in sorted(by_day.items()):
        weights = [numeric(row.get("_meta", {}).get("weight"), 1.0) or 1.0 for row in rows]
        total_weight = sum(weights) or 1.0
        aggregated: dict[str, Any] = {
            "stock_code": stock_code,
            "date": day,
            "batch_count": len(rows),
            "post_count": sum(int(float(row["_meta"].get("post_count") or 0)) for row in rows),
            "comment_count": sum(int(float(row["_meta"].get("comment_count") or 0)) for row in rows),
            "weight": total_weight,
        }
        aggregated["cache_hit"] = False
        for field in score_fields:
            vals = [numeric(row.get(field)) for row in rows]
            weighted = [
                (value, weight)
                for value, weight in zip(vals, weights, strict=False)
                if value is not None
            ]
            if not weighted:
                aggregated[field] = None
                continue
            score_value = sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted)
            if field == "sentiment_score":
                aggregated[field] = round(clamp(score_value, -100, 100), 2)
            else:
                aggregated[field] = round(clamp(score_value, 0, 100), 2)
        aggregated["summary"] = " | ".join(str(row.get("summary") or "").strip() for row in rows if row.get("summary"))[:1200]
        aggregated["key_bullish_evidence"] = merge_evidence(rows, "key_bullish_evidence")
        aggregated["key_bearish_evidence"] = merge_evidence(rows, "key_bearish_evidence")
        daily_rows.append(aggregated)
    if not args.no_sentiment_cache:
        cache_dir = Path(args.sentiment_cache_dir)
        for row in daily_rows:
            cache_file = write_cached_daily_sentiment(
                cache_dir=cache_dir,
                row=row,
                run_dir=run_dir,
                source_data_hash=daily_input_hash(run_dir, str(row["date"])),
                scorer_id=args.scorer_id,
            )
            row["cache_file"] = str(cache_file)
    combined: dict[tuple[str, str], dict[str, Any]] = {}
    for row in cached_rows:
        combined[(str(row.get("stock_code")), str(row.get("date")))] = row
    for row in daily_rows:
        combined[(str(row.get("stock_code")), str(row.get("date")))] = row
    output_rows = [combined[key] for key in sorted(combined)]
    summary = write_daily_sentiment_outputs(run_dir, output_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=json_default))
    return summary


def merge_evidence(rows: list[dict[str, Any]], key: str, limit: int = 12) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows:
        values = row.get(key) or []
        if isinstance(values, str):
            values = [item.strip() for item in values.split(",") if item.strip()]
        for value in values:
            text = str(value)
            if text and text not in seen:
                seen.add(text)
                out.append(text)
            if len(out) >= limit:
                return out
    return out


def show_batches_command(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir)
    index = read_batch_index(run_dir)
    rows = list(index.values())
    print(json.dumps({"run_dir": str(run_dir), "batch_count": len(rows), "batches": rows}, ensure_ascii=False, indent=2))


def cache_status_command(args: argparse.Namespace) -> dict[str, Any]:
    stock_code = normalize_stock_code(args.stock_code)
    start, end = parse_date_args(args)
    cache_dir = Path(args.sentiment_cache_dir)
    rows: list[dict[str, Any]] = []
    for day_obj in iter_days(start, end):
        day = day_obj.isoformat()
        cached = read_cached_daily_sentiment(cache_dir, stock_code, day)
        rows.append(
            {
                "stock_code": stock_code,
                "date": day,
                "cached": cached is not None,
                "sentiment_score": None if cached is None else cached.get("sentiment_score"),
                "cache_file": "" if cached is None else cached.get("cache_file", ""),
            }
        )
    status = {
        "stock_code": stock_code,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "cache_dir": str(cache_dir),
        "cache_version": sentiment_cache_version_label(),
        "cached_count": sum(1 for row in rows if row["cached"]),
        "missing_count": sum(1 for row in rows if not row["cached"]),
        "dates": rows,
    }
    print(json.dumps(status, ensure_ascii=False, indent=2, default=json_default))
    return status


def add_collect_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("collect", help="Collect full posts, bodies, replies, and model batches")
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--max-pages", type=int, default=2500)
    parser.add_argument("--max-reply-pages", type=int, default=20)
    parser.add_argument("--reply-page-size", type=int, default=50)
    parser.add_argument("--max-batch-chars", type=int, default=60000)
    parser.add_argument("--max-posts-per-batch", type=int, default=25)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--sleep-seconds", type=float, default=0.1)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--sentiment-cache-dir", default=str(DEFAULT_SENTIMENT_CACHE_DIR))
    parser.add_argument("--no-sentiment-cache", action="store_true", help="Do not read daily sentiment cache before collecting")
    parser.add_argument("--refresh-sentiment-cache", action="store_true", help="Ignore existing daily sentiment cache and regenerate batches")
    parser.set_defaults(func=collect_command)


def add_aggregate_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("aggregate", help="Aggregate batch sentiment scores into daily scores")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--scores", required=True, help="batch_scores.jsonl")
    parser.add_argument("--sentiment-cache-dir", default=str(DEFAULT_SENTIMENT_CACHE_DIR))
    parser.add_argument("--no-sentiment-cache", action="store_true", help="Do not write aggregate daily sentiment to cache")
    parser.add_argument("--scorer-id", default="codex-agent")
    parser.set_defaults(func=aggregate_command)


def add_show_batches_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("show-batches", help="Show generated batch metadata")
    parser.add_argument("--run-dir", required=True)
    parser.set_defaults(func=show_batches_command)


def add_cache_status_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("cache-status", help="Show daily sentiment cache hits and misses")
    parser.add_argument("--stock-code", required=True)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--sentiment-cache-dir", default=str(DEFAULT_SENTIMENT_CACHE_DIR))
    parser.set_defaults(func=cache_status_command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect Eastmoney Guba posts/replies and save daily sentiment outputs.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_collect_parser(subparsers)
    add_aggregate_parser(subparsers)
    add_show_batches_parser(subparsers)
    add_cache_status_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
