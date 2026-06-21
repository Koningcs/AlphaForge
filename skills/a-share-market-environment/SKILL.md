---
name: a-share-market-environment
description: Analyze the A-share market environment with AKShare, including index trend, risk appetite, liquidity, valuation, macro indicators, authoritative policy/news tone, JSON output, Markdown output, and data quality checks. Use when Codex needs a top-down market environment module for Chinese A-share stock analysis.
---

# A 股市场环境

## Quick Start

```bash
python run_market_environment.py --as-of-date 2026-06-13
```

Outputs are written to:

```text
outputs/market/{as_of_date}/
  market_environment.json
  market_environment.md
  data_quality.md
```

## Workflow

1. Fetch benchmark index daily bars.
2. Compute market trend, relative risk appetite, liquidity hints, valuation hints, macro hints, and policy/news tone.
3. Build a weighted environment score.
4. Write JSON, Markdown, and data quality outputs.

## Notes

- Treat policy/news as authoritative policy information, not generic sentiment.
- Default scoring includes only official, state media, and securities media sources.
- Missing external data should produce warnings and partial output instead of failing the whole run.
