---
name: a-share-fundamental
description: Fetch, normalize, and summarize A-share company fundamental data with AKShare, including company profile, recent annual income statements, balance sheets, cash flow statements, core financial indicators, valuation snapshots, JSON output, Markdown output, rule-based analysis summaries, and basic data quality checks. Use when Codex needs to generate fundamental data and lightweight analysis files for Chinese A-share stock codes such as 300308 or 600519.
---

# A 股基本面

## Overview

Use this skill to collect lightweight A-share fundamental data from AKShare and produce four artifacts:

- `fundamental.json`
- `fundamental.md`
- `analysis.md`
- `data_quality.md`

The implementation keeps missing values as `null` in JSON and writes API failures or incomplete data into `warnings`.

## Quick Start

From this skill directory:

```bash
python run_fundamental.py --stock-code 300308 --years 5
```

Outputs are written to the project root by default:

```text
outputs/
  300308/
    fundamental.json
    fundamental.md
    analysis.md
    data_quality.md
```

Use `--output-dir` when the user wants artifacts in another directory.

## Python API

```python
from fundamental import get_fundamental

result = get_fundamental(stock_code="300308", years=5)
```

The function returns:

```python
{
    "json_path": "outputs/300308/fundamental.json",
    "markdown_path": "outputs/300308/fundamental.md",
    "analysis_path": "outputs/300308/analysis.md",
    "data_quality_path": "outputs/300308/data_quality.md",
    "data": {}
}
```

## Workflow

1. Normalize the stock code to a six-digit A-share code.
2. Fetch company profile data from CNInfo, and use Eastmoney, Xueqiu, or the A-share code-name table only as fallback sources.
3. Fetch annual profit statement, balance sheet, and cash flow statement data through AKShare Sina financial reports.
4. Fetch annual core financial indicators through AKShare Sina financial indicators.
5. Fetch a valuation snapshot from AKShare Eastmoney valuation interfaces when available.
6. Normalize the required schema, compute derived fields such as gross margin, net margin, asset-liability ratio, free cash flow, growth rates, cash conversion, asset quality ratios, solvency ratios, and expense ratios.
7. Build a rule-based `analysis_summary` covering growth, profitability, cash flow quality, asset quality, solvency, and valuation.
8. Run basic data quality checks and write JSON and Markdown outputs.

## Analysis Scope

The analysis layer is intentionally lightweight and rule-based:

- Growth: revenue CAGR, parent net profit CAGR, deducted net profit CAGR, and latest YoY changes.
- Profitability: gross margin, net margin, ROE, ROA, and margin or ROE changes.
- Cash flow quality: operating cash flow, free cash flow, operating cash flow to net profit, and multi-period negative cash flow.
- Asset quality: receivables growth, inventory growth, receivables/revenue, inventory/revenue, and goodwill/assets.
- Solvency: asset-liability ratio, cash coverage of short-term debt, interest-bearing debt, net cash, and debt/equity.
- Valuation: market cap, PE(TTM), PB, PS(TTM), PEG, PCF, EV/EBITDA, and industry valuation ranking when available.

Keep the generated analysis phrased as financial data interpretation, not investment advice.

## Data Quality Checks

Check for:

- Missing required fields.
- Fewer annual records than requested by `years`.
- Abnormal gross margin.
- Abnormal asset-liability ratio.
- Consecutive negative operating cash flow.
- Low operating cash flow to net profit.
- Significant accounts receivable growth.
- Significant inventory growth.

## Implementation Notes

- Keep dependencies in a project-local virtual environment when installing or validating this skill.
- Install runtime dependencies with `pip install -r requirements.txt`.
- Treat AKShare interfaces as network-dependent. If an interface fails, keep partial output and record the exception in `metadata.warnings` and `data_quality.warnings`.
- Preserve `null` for missing fields instead of inventing values.
