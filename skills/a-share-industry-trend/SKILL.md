---
name: a-share-industry-trend
description: Analyze A-share industry trend with AKShare, including industry mapping, industry index trend, relative strength, breadth, capital flow, valuation hints, JSON output, Markdown output, and data quality checks. Use when Codex needs industry-level confirmation for Chinese A-share stock analysis.
---

# A 股行业趋势

## Quick Start

```bash
python run_industry_trend.py --stock-code 300308
```

Outputs:

```text
outputs/industry/{industry_name}/
  industry_trend.json
  industry_trend.md
  data_quality.md
```

## Workflow

1. Resolve industry by explicit industry name or stock profile.
2. Fetch industry board history and benchmark history.
3. Compute trend, relative strength, breadth, capital flow, valuation hints, and score.
4. Write JSON and Markdown outputs.
