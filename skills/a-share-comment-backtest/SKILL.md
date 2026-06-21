---
name: a-share-comment-backtest
description: Build A-share Eastmoney Guba comment-window datasets and forward-return labels for sentiment research and backtests. Use when Codex needs to fetch stock forum posts, create N-day comment windows, align them with M-trading-day future returns, prepare LLM sentiment scoring tasks, or run batch sentiment factor backtests for Chinese A-share stock codes.
---

# A-share Comment Backtest

## Quick Start

Use this skill to prepare data, not to hard-code sentiment rules. The script fetches Eastmoney Guba posts, builds one comment window per stock-date, computes forward returns, and writes agent-facing JSONL tasks. The agent should score sentiment from `agent_tasks.jsonl`, then run `backtest` with the generated scores.

Single stock and date:

```bash
python run_comment_backtest.py build --stock-code 300308 --signal-date 2026-06-10 --lookback-days 3 --forward-days 10
```

Batch backtest:

```bash
python run_comment_backtest.py build --stock-file stocks.txt --start-date 2026-01-01 --end-date 2026-03-31 --lookback-days 3 --forward-days 10 --workers 8
```

Backtest scored sentiment:

```bash
python run_comment_backtest.py backtest --dataset outputs/comment_backtest/<run>/backtest_dataset.csv --sentiment-file outputs/comment_backtest/<run>/sentiment_scores.csv
```

## Inputs

- `--stock-code`: one 6-digit A-share code.
- `--stock-codes`: comma-separated stock codes.
- `--stock-file`: text file with one stock code per line.
- `--signal-date`: one signal date, usually the date whose prior comments are used.
- `--start-date` and `--end-date`: build one sample per trading day in the range.
- `--lookback-days`: N natural days of comments ending on the signal date.
- `--forward-days`: M trading sessions of future return.
- `--workers`: parallel stock workers for batch runs.
- `--max-comments-per-window`: maximum comments included in each agent task; use `0` to include all. `raw_comments.jsonl` always keeps the full fetched window.

Default return label is `next_open`: enter at the next trading day's open after `signal_date`, exit at the M-th trading session's close. Use `--return-start signal_close` for close-to-close labels.

## Outputs

The `build` command writes:

- `raw_comments.jsonl`: deduplicated Eastmoney Guba post records.
- `agent_tasks.jsonl`: comment windows for LLM sentiment scoring; no future return fields are included.
- `sentiment_template.csv`: fillable scoring template keyed by `window_id`.
- `labels.csv`: forward-return labels.
- `backtest_dataset.csv`: one row per stock-date with comment counts and labels.
- `manifest.json`: run parameters, warnings, and file paths.

The agent should create a sentiment file with at least:

```text
window_id,sentiment_score
```

Optional columns:

```text
bullish_score,bearish_score,neutral_score,disagreement_score,confidence,notes
```

Use a numeric `sentiment_score` where higher means more bullish. Keep future returns hidden while scoring to avoid leakage.

## Workflow

1. Build data with `build`.
2. Read `agent_tasks.jsonl` and score each window using only comments and metadata.
3. Save scores to CSV or JSONL.
4. Run `backtest` to merge scores with labels.
5. Inspect `summary.json`, `daily_ic.csv`, `quantile_returns.csv`, and `long_short.csv`.

## Notes

- First version supports Eastmoney Guba only; Xueqiu is intentionally out of scope.
- Comment windows use natural calendar days; future returns use trading days.
- Guba list pages are used by default for scalable backtests. Detail-page body fetching is available with `--include-details`, but it is slower.
- Use `--max-pages` to control how far back each stock crawler scans.
