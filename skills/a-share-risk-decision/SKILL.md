---
name: a-share-risk-decision
description: Synthesize A-share market environment, industry trend, fundamentals, stock trend, and event expectation outputs into a risk and decision framework with evidence, risks, invalidation conditions, tracking plan, JSON output, and Markdown output. Use when Codex needs a final stock analysis synthesis without issuing investment advice.
---

# A 股风险与决策框架

## Quick Start

```bash
python run_decision.py --stock-code 300308
```

Outputs:

```text
outputs/{stock_code}/
  decision.json
  decision.md
  data_quality_decision.md
```

## Notes

This skill produces a research classification and risk framework. It must not output buy/sell instructions or personalized investment advice.
