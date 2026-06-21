"""Auto-score guba sentiment batches using rule-based heuristics.

This script reads agent_batches/*.json files and produces batch_scores.jsonl
using keyword-based sentiment analysis. It does NOT use any external data,
future prices, or outside market data - only the post body and comments
within each batch.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Bullish keywords and their weights
BULLISH_PATTERNS = [
    (r"涨[停上]", 3.0),
    (r"看多|看涨|牛市", 2.5),
    (r"加仓|买入|抄底|建仓|补仓", 2.0),
    (r"利好|好消息|突破|新高|反弹", 2.0),
    (r"强势|走强|拉升|封板|涨停", 2.5),
    (r"主力[进买]|资金流入|净流入", 2.0),
    (r"机会|潜力|低估|价值|看好", 1.5),
    (r"持有|坚守|不卖|锁仓", 1.5),
    (r"目标价|目标位|上看", 1.5),
    (r"业绩[好优增]|超预期|超预期", 2.0),
    (r"订单|需求[旺强]|供不应求", 2.0),
    (r"技术[面位].*支撑|金叉|底背离", 1.5),
    (r"大[涨买]|翻倍|暴涨", 2.5),
    (r"龙头|领涨|领涨", 1.5),
]

BEARISH_PATTERNS = [
    (r"跌[停下]", 3.0),
    (r"看空|看跌|熊市", 2.5),
    (r"减仓|卖出|清仓|割肉|止损|逃命", 2.0),
    (r"利空|坏消息|破位|新低|暴跌", 2.5),
    (r"弱势|走弱|下杀|跌停|闪崩", 2.5),
    (r"主力[出跑]|资金流出|净流出", 2.0),
    (r"风险|危险|泡沫|高估|套牢", 1.5),
    (r"割韭菜|收割|骗线|诱多", 2.0),
    (r"暴雷|造假|违规|处罚|退市", 3.0),
    (r"业绩[差降减]|不及预期|低于预期", 2.0),
    (r"技术[面位].*压力|死叉|顶背离", 1.5),
    (r"大[跌卖]|腰斩|崩盘", 2.5),
    (r"套[了住牢]|深套|被套", 2.0),
    (r"恐慌|绝望|崩了|完蛋", 2.0),
]

ATTENTION_PATTERNS = [
    (r"重要|关键|紧急|注意|警惕", 1.5),
    (r"重磅|突发|重大|震惊", 2.0),
    (r"公告|通知|消息|传闻", 1.5),
]


def score_text(text: str) -> dict:
    """Score a single text string using keyword patterns."""
    bullish_score = 0.0
    bearish_score = 0.0
    attention_score = 0.0
    bullish_hits = 0
    bearish_hits = 0

    for pattern, weight in BULLISH_PATTERNS:
        matches = re.findall(pattern, text)
        if matches:
            bullish_score += weight * len(matches)
            bullish_hits += len(matches)

    for pattern, weight in BEARISH_PATTERNS:
        matches = re.findall(pattern, text)
        if matches:
            bearish_score += weight * len(matches)
            bearish_hits += len(matches)

    for pattern, weight in ATTENTION_PATTERNS:
        matches = re.findall(pattern, text)
        if matches:
            attention_score += weight * len(matches)

    return {
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "attention_score": attention_score,
        "bullish_hits": bullish_hits,
        "bearish_hits": bearish_hits,
    }


def score_batch(batch: dict) -> dict:
    """Score a single batch and return a score record."""
    batch_id = batch["batch_id"]
    stock_code = batch["stock_code"]
    date = batch["date"]
    posts = batch.get("posts", [])

    all_text_parts = []
    total_bullish = 0.0
    total_bearish = 0.0
    total_attention = 0.0
    total_bullish_hits = 0
    total_bearish_hits = 0
    total_weight = 0.0
    post_count = len(posts)
    comment_count = 0

    key_bullish_posts = []
    key_bearish_posts = []

    for post in posts:
        text = (post.get("title", "") or "") + " " + (post.get("body", "") or "")
        weight = float(post.get("weight", 1.0))
        comments = post.get("comments", [])
        comment_count += len(comments)

        # Score post text
        post_score = score_text(text)
        # Score comment texts
        for comment in comments:
            comment_text = comment.get("text", "") or ""
            comment_score = score_text(comment_text)
            post_score["bullish_score"] += comment_score["bullish_score"] * 0.5
            post_score["bearish_score"] += comment_score["bearish_score"] * 0.5
            post_score["attention_score"] += comment_score["attention_score"] * 0.3
            post_score["bullish_hits"] += comment_score["bullish_hits"]
            post_score["bearish_hits"] += comment_score["bearish_hits"]

        weighted_bullish = post_score["bullish_score"] * weight
        weighted_bearish = post_score["bearish_score"] * weight
        weighted_attention = post_score["attention_score"] * weight

        total_bullish += weighted_bullish
        total_bearish += weighted_bearish
        total_attention += weighted_attention
        total_bullish_hits += post_score["bullish_hits"]
        total_bearish_hits += post_score["bearish_hits"]
        total_weight += weight

        if post_score["bullish_score"] > post_score["bearish_score"] + 3:
            key_bullish_posts.append((post.get("post_id", ""), post_score["bullish_score"]))
        elif post_score["bearish_score"] > post_score["bullish_score"] + 3:
            key_bearish_posts.append((post.get("post_id", ""), post_score["bearish_score"]))

    # Normalize scores
    if total_weight > 0:
        avg_bullish = total_bullish / total_weight
        avg_bearish = total_bearish / total_weight
        avg_attention = total_attention / total_weight
    else:
        avg_bullish = 0
        avg_bearish = 0
        avg_attention = 0

    # Scale to 0-100 range
    max_raw = max(avg_bullish, avg_bearish, 1.0)
    bullish_pct = min(100, round(avg_bullish / max_raw * 60 + (total_bullish_hits / max(post_count, 1)) * 20, 1))
    bearish_pct = min(100, round(avg_bearish / max_raw * 60 + (total_bearish_hits / max(post_count, 1)) * 20, 1))

    # Sentiment score: -100 to 100
    if total_bullish + total_bearish > 0:
        sentiment_raw = (total_bullish - total_bearish) / (total_bullish + total_bearish)
        sentiment_score = round(max(-100, min(100, sentiment_raw * 100)), 2)
    else:
        sentiment_score = 0

    # Neutral score
    neutral_pct = max(0, round(100 - bullish_pct - bearish_pct, 1))

    # Disagreement score: higher when both bullish and bearish are high
    disagreement = round(min(100, (bullish_pct + bearish_pct) / 2), 1)

    # Attention score
    attention_pct = min(100, round(avg_attention * 10 + post_count * 0.5, 1))

    # Confidence: higher when there's clear direction and enough data
    direction_strength = abs(bullish_pct - bearish_pct)
    data_strength = min(post_count / 20, 1.0)
    confidence = round(min(100, direction_strength * 0.6 + data_strength * 30 + 20), 1)

    # Key evidence (top 5 by score)
    key_bullish_posts.sort(key=lambda x: x[1], reverse=True)
    key_bearish_posts.sort(key=lambda x: x[1], reverse=True)
    key_bullish_evidence = [p[0] for p in key_bullish_posts[:5] if p[0]]
    key_bearish_evidence = [p[0] for p in key_bearish_posts[:5] if p[0]]

    # Summary
    if sentiment_score > 20:
        direction = "bullish"
    elif sentiment_score < -20:
        direction = "bearish"
    else:
        direction = "neutral/mixed"
    summary = f"Batch {batch_id}: {direction} (score={sentiment_score}), {post_count} posts, {comment_count} comments. Bull={bullish_pct}, Bear={bearish_pct}, Disagreement={disagreement}."

    return {
        "batch_id": batch_id,
        "stock_code": stock_code,
        "date": date,
        "sentiment_score": sentiment_score,
        "bullish_score": bullish_pct,
        "bearish_score": bearish_pct,
        "neutral_score": neutral_pct,
        "disagreement_score": disagreement,
        "attention_score": attention_pct,
        "confidence": confidence,
        "summary": summary,
        "key_bullish_evidence": key_bullish_evidence,
        "key_bearish_evidence": key_bearish_evidence,
    }


def main():
    if len(sys.argv) < 3:
        print("Usage: python auto_score_batches.py <run_dir> <output_jsonl>")
        sys.exit(1)

    run_dir = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    batch_dir = run_dir / "agent_batches"
    if not batch_dir.exists():
        print(f"No agent_batches directory in {run_dir}")
        sys.exit(1)

    batch_files = sorted(batch_dir.glob("*.json"))
    print(f"Found {len(batch_files)} batch files in {batch_dir}")

    results = []
    for i, batch_file in enumerate(batch_files):
        batch = json.loads(batch_file.read_text(encoding="utf-8"))
        score = score_batch(batch)
        results.append(score)
        if (i + 1) % 50 == 0:
            print(f"  Scored {i + 1}/{len(batch_files)} batches...")

    # Write results
    with output_path.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    print(f"Wrote {len(results)} scores to {output_path}")


if __name__ == "__main__":
    main()
