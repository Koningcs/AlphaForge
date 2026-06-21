"""Generate final summary table from all cached daily sentiment data."""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

# Add skill dir to path
sys.path.insert(0, str(Path(__file__).parent))
from guba_sentiment import (
    DEFAULT_SENTIMENT_CACHE_DIR,
    SOURCE_NAME,
    read_cached_daily_sentiment,
)

STOCK_CODE = "300308"
START_DATE = date(2025, 6, 14)
END_DATE = date(2026, 6, 14)
CACHE_DIR = DEFAULT_SENTIMENT_CACHE_DIR

# API coverage limit
API_COVERAGE_START = date(2026, 5, 19)

rows = []
current = START_DATE
while current <= END_DATE:
    day_str = current.isoformat()
    cached = read_cached_daily_sentiment(CACHE_DIR, STOCK_CODE, day_str)

    warning = None
    if current < API_COVERAGE_START:
        warning = "API无法覆盖此日期（东方财富股吧API翻页深度限制，最多覆盖约26天历史数据）"

    if cached:
        row = {
            "stock_code": STOCK_CODE,
            "date": day_str,
            "sentiment_score": cached.get("sentiment_score"),
            "bullish_score": cached.get("bullish_score"),
            "bearish_score": cached.get("bearish_score"),
            "disagreement_score": cached.get("disagreement_score"),
            "attention_score": cached.get("attention_score"),
            "confidence": cached.get("confidence"),
            "post_count": cached.get("post_count"),
            "comment_count": cached.get("comment_count"),
            "warning": warning,
        }
    else:
        row = {
            "stock_code": STOCK_CODE,
            "date": day_str,
            "sentiment_score": None,
            "bullish_score": None,
            "bearish_score": None,
            "disagreement_score": None,
            "attention_score": None,
            "confidence": None,
            "post_count": 0,
            "comment_count": 0,
            "warning": warning or "无缓存数据",
        }

    # Additional warnings for data quality
    if cached and cached.get("post_count", 0) == 0:
        row["warning"] = (row["warning"] or "") + "帖子数为0"
    if cached and cached.get("confidence", 100) < 30:
        row["warning"] = (row["warning"] or "") + "置信度低"

    rows.append(row)
    current += timedelta(days=1)

# Write CSV
output_dir = Path("outputs/guba_sentiment/eastmoney_guba/300308")
output_dir.mkdir(parents=True, exist_ok=True)
csv_path = output_dir / "summary_1year.csv"

import csv
fields = [
    "stock_code", "date", "sentiment_score", "bullish_score", "bearish_score",
    "disagreement_score", "attention_score", "confidence", "post_count",
    "comment_count", "warning",
]
with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

# Print summary
cached_count = sum(1 for r in rows if r["sentiment_score"] is not None)
missing_count = sum(1 for r in rows if r["sentiment_score"] is None)
warning_count = sum(1 for r in rows if r["warning"])

print(f"Total days: {len(rows)}")
print(f"Cached (scored): {cached_count}")
print(f"Missing (no data): {missing_count}")
print(f"Warnings: {warning_count}")
print(f"\nCSV saved to: {csv_path}")

# Print the scored rows as a table
print(f"\n{'date':<12} {'sentiment':>10} {'bullish':>8} {'bearish':>8} {'disagree':>9} {'attention':>9} {'conf':>5} {'posts':>6} {'comments':>8} {'warning'}")
print("-" * 100)
for r in rows:
    if r["sentiment_score"] is not None:
        print(
            f"{r['date']:<12} {r['sentiment_score']:>10.2f} {r['bullish_score']:>8.1f} "
            f"{r['bearish_score']:>8.1f} {r['disagreement_score']:>9.1f} "
            f"{r['attention_score']:>9.1f} {r['confidence']:>5.1f} "
            f"{r['post_count']:>6} {r['comment_count']:>8} {r['warning'] or ''}"
        )
