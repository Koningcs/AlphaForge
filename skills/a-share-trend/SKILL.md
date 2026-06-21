---
name: a-share-trend
description: Analyze A-share stock trend with AKShare, including price direction, relative strength, volume-price quality, volatility risk, trend stage, trend score, JSON output, Markdown output, and data quality checks. Use when Codex needs technical trend analysis for Chinese A-share stock codes such as 300308 or 600519.
---

# A 股个股趋势

## Quick Start

```bash
python run_trend.py --stock-code 300308
```

Outputs:

```text
outputs/{stock_code}/
  trend.json
  trend.md
  data_quality.md
```

## Workflow

1. Fetch front-adjusted daily bars for the stock and benchmark index.
2. Compute 20/60/120/250 day returns, moving averages, relative strength, volume-price quality, volatility, drawdown, ATR, and stage.
3. Build a transparent 0-100 trend score.
4. Write JSON, Markdown, and data quality outputs.
