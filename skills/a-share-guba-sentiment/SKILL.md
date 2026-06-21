---
name: a-share-guba-sentiment
description: Collect and score Eastmoney Guba sentiment for Chinese A-share stocks by day or date range. Use when Codex needs to fetch all posts, post bodies, and post replies for a stock and time period, split the data into model-sized JSON batches, analyze sentiment with the agent model, aggregate daily sentiment scores, and save day-level sentiment outputs for later research or backtesting.
---

# A-share Guba Sentiment

## Purpose

Use this skill for sentiment only. The input is a stock code and a date or date range. The output is saved daily sentiment, with one score per stock per day. Do not compute future returns in this skill.

## Workflow

1. Collect full Guba data:

```bash
python run_guba_sentiment.py collect --stock-code 300308 --date 2026-06-14
```

or:

```bash
python run_guba_sentiment.py collect --stock-code 300308 --start-date 2026-06-10 --end-date 2026-06-14
```

The collect command checks the daily sentiment cache first. Dates already scored for the same stock and cache version are written directly to `daily_sentiment.*`; only missing dates produce new `agent_batches/*.json`.

For historical backtests on high-traffic stocks, keep deep pagination enabled. The default `--max-pages` is intentionally high. Do not lower it unless the target dates are recent or already cached.

Default output is organized by source, stock code, and day:

```text
outputs/guba_sentiment/eastmoney_guba/<stock_code>/<YYYY-MM-DD>/
```

Single-day runs are stored below that day:

```text
outputs/guba_sentiment/eastmoney_guba/300308/2026-05-30/runs/<run_timestamp>/
```

2. Inspect generated batches:

```bash
python run_guba_sentiment.py show-batches --run-dir outputs/guba_sentiment/<run>
```

3. Score each batch as Codex. Read every `agent_batches/*.json` file and create `batch_scores.jsonl`. Use only the batch content, never future returns, later news, outside market data, or price action.

4. Aggregate batch scores into daily sentiment:

```bash
python run_guba_sentiment.py aggregate --run-dir outputs/guba_sentiment/<run> --scores outputs/guba_sentiment/<run>/batch_scores.jsonl
```

The aggregate command writes new daily scores back to the cache by default.

5. Check cache status before a large backtest:

```bash
python run_guba_sentiment.py cache-status --stock-code 300308 --start-date 2026-06-10 --end-date 2026-06-14
```

## Collected JSON

`posts.jsonl` contains one full post per line:

```json
{
  "stock_code": "300308",
  "stock_name": "stock bar name",
  "source": "eastmoney_guba",
  "post_id": "1726086368",
  "url": "https://guba.eastmoney.com/news,300308,1726086368.html",
  "published_at": "2026-06-14 07:13:41",
  "date": "2026-06-14",
  "author_hash": "anonymous",
  "title": "",
  "body": "post body",
  "text": "title plus body",
  "metrics": {
    "click_count": 0,
    "reply_count": 12,
    "like_count": 3,
    "forward_count": 0
  },
  "comments": [
    {
      "comment_id": "9907869936",
      "parent_id": null,
      "published_at": "2026-06-14 11:26:49",
      "author_hash": "anonymous",
      "text": "comment text",
      "like_count": 0
    }
  ]
}
```

## Batch Scoring

Write one JSON object per line to `batch_scores.jsonl`:

```json
{
  "batch_id": "300308_20260614_001",
  "stock_code": "300308",
  "date": "2026-06-14",
  "sentiment_score": 42,
  "bullish_score": 68,
  "bearish_score": 26,
  "neutral_score": 18,
  "disagreement_score": 75,
  "attention_score": 82,
  "confidence": 70,
  "summary": "Overall bullish, but disagreement is high.",
  "key_bullish_evidence": ["1726086368"],
  "key_bearish_evidence": ["1726093069"]
}
```

Use `sentiment_score` from `-100` bearish to `100` bullish. Other score fields use `0` to `100`.

## Outputs

The `collect` command saves:

- `posts.jsonl`: full posts with body and replies.
- `daily_inputs/YYYY-MM-DD.json`: one complete daily input file.
- `agent_batches/*.json`: model-sized daily batches.
- `batch_score_template.jsonl`: fillable scoring template.
- `cached_daily_sentiment.jsonl`: cache-hit daily rows reused in this run.
- `manifest.json`: run parameters, counts, paths, and warnings.

The `aggregate` command saves:

- `daily_sentiment.jsonl`: one sentiment row per stock per day.
- `daily_sentiment.csv`: spreadsheet-friendly version.
- `sentiment_summary.json`: run-level summary.

## Daily Sentiment Cache

The default cache root is `outputs/guba_sentiment`.

Cache files are stored one stock-date per JSON file under the stock/date folder:

```text
outputs/guba_sentiment/eastmoney_guba/<stock_code>/<YYYY-MM-DD>/sentiment/<collector_version>__<scoring_prompt_version>.json
```

Use the cache for backtests so the same stock-date is not scored repeatedly. Use `--refresh-sentiment-cache` on `collect` to ignore existing daily scores and regenerate batches. Use `--no-sentiment-cache` on `collect` or `aggregate` to disable cache reads or writes for special runs.

Older cache files under `outputs/guba_sentiment_cache` may still be read for compatibility, but new scores should be written to the stock/date structure above.

## Eastmoney Pagination

Use this script's WAP list API path. Do not replace it with a PC-page scraper or another Eastmoney endpoint without revalidating depth:

```text
https://gbapi.eastmoney.com/webarticlelist/api/Article/Articlelist
```

For hot stocks such as `300308`, old dates require deep pages. As observed on 2026-06-14:

```text
page 250  ~= 2026-05-29
page 418  ~= 2026-05-01
page 1000 ~= 2026-01-08
page 2000 ~= 2025-03-27
```

If `collect` returns `Coverage incomplete`, increase `--max-pages` or split the request. Never treat a zero-post day as real unless coverage reached dates earlier than the requested start date.

## Guidance

- Always score at the finest daily granularity, even for date ranges.
- Reuse cached daily sentiment when available unless the user explicitly asks to refresh or change the scoring prompt.
- Keep deep pagination for historical runs, especially for high-traffic stocks.
- Check `manifest.json` warnings before scoring. Fix coverage warnings first.
- If a day has many posts, score every generated batch first, then aggregate.
- Keep evidence as post IDs, not long copied text.
- If a batch is mostly unrelated spam, reflect that in lower confidence and notes instead of dropping it silently.
