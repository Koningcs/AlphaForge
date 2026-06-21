# AlphaForge

A 股投资研究工具集，基于 AKShare 数据源，提供从市场环境到个股决策的全链路分析框架。

## Skill 列表

| Skill | 说明 | 入口 |
|-------|------|------|
| **a-share-market-environment** | 市场环境分析：指数趋势、风险偏好、流动性、估值、宏观指标、政策/新闻基调 | `python run_market_environment.py --as-of-date 2026-06-13` |
| **a-share-industry-trend** | 行业趋势分析：行业映射、行业指数趋势、相对强度、宽度、资金流向、估值 | `python run_industry_trend.py --stock-code 300308` |
| **a-share-fundamental** | 基本面分析：公司概况、三大报表、核心财务指标、估值快照、规则化分析摘要 | `python run_fundamental.py --stock-code 300308 --years 5` |
| **a-share-trend** | 个股趋势分析：价格方向、相对强度、量价质量、波动风险、趋势阶段、趋势评分 | `python run_trend.py --stock-code 300308` |
| **a-share-event-expectation** | 事件与预期分析：近期新闻、公告、业绩预告、股东/资本事件、催化剂、预期基调 | `python run_event_expectation.py --stock-code 300308` |
| **a-share-guba-sentiment** | 东方财富股吧情绪：帖子采集、分批打分、日级情绪汇总、缓存管理 | `python run_guba_sentiment.py collect --stock-code 300308 --date 2026-06-14` |
| **a-share-risk-decision** | 风险与决策框架：综合市场、行业、基本面、趋势、事件输出，生成研究分类与风险框架 | `python run_decision.py --stock-code 300308` |
| **a-share-comment-backtest** | 评论回测：构建评论窗口数据集、前瞻收益标签、LLM 情绪打分、情绪因子回测 | `python run_comment_backtest.py build --stock-code 300308 --signal-date 2026-06-10` |

## 分析链路

```
市场环境 ──┐
行业趋势 ──┤
基本面 ────┤──→ 风险与决策框架
个股趋势 ──┤
事件预期 ──┘

股吧情绪 ──→ 评论回测（独立回测链路）
```

## 依赖

- Python 3.11+
- akshare >= 1.16.83
- pandas >= 1.5
- requests >= 2.28（股吧情绪、评论回测）

## 项目结构

```
AlphaForge/
├── skills/
│   ├── a_share_common/          # 公共工具（数据获取、格式化、评分）
│   ├── a-share-market-environment/
│   ├── a-share-industry-trend/
│   ├── a-share-fundamental/
│   ├── a-share-trend/
│   ├── a-share-event-expectation/
│   ├── a-share-guba-sentiment/
│   ├── a-share-risk-decision/
│   └── a-share-comment-backtest/
├── outputs/                     # 运行产出（已 gitignore）
├── agents.md                    # Agent 规则
└── readme.md
```

## 声明

本项目仅供投资研究参考，不构成任何投资建议。所有分析输出均为财务数据解读，不提供买卖指令或个性化投资建议。
