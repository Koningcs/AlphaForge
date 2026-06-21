---
name: a-share-event-expectation
description: Analyze A-share stock events and expectations with AKShare, including recent news, notices, profit forecasts, shareholder and capital events, event timeline, catalysts, expectation tone, JSON output, Markdown output, and data quality checks. Use when Codex needs event and expectation analysis for Chinese A-share stock codes.
---

# A 股事件与预期

## Quick Start

```bash
python run_event_expectation.py --stock-code 300308
```

Outputs:

```text
outputs/{stock_code}/
  event_expectation.json
  event_expectation.md
  data_quality_event_expectation.md
```

## Notes

The module classifies event titles and available summaries into rule-based categories. It does not invent missing details or treat ordinary news as confirmed policy.
