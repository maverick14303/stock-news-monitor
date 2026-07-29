"""Export a per-ticker news-sentiment snapshot for other projects to consume.

Writes news_signal.json: for each NSE ticker with recent coverage, an aggregate
sentiment in [-1, 1]. This is the clean hand-off point — the trading-bot's fused
momentum+news bot reads this file (locally, or from the raw GitHub URL on CI)
instead of touching news.db.

What each article's weight is built from:
  source trust   learned from graded 1-day outcomes (a source that grades badly
                 counts less)
  recency        half-life decay, newest news dominates
  novelty        the LLM's judgment that this is NEW information, not a
                 description of a move that already happened. Descriptive items
                 are kept but heavily discounted.
  headline vs body  a company named in the headline is the subject; one named in
                 the body blurb is usually a passing mention

`sources` counts INDEPENDENT STORIES, not outlets. One PTI wire reprinted by
eight papers used to read as eight-source confirmation, which is exactly
backwards — consumers gate on this field.

Usage: python export_signal.py
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import db
from newslib import cluster_titles

BASE = Path(__file__).parent
DB = BASE / "news.db"
OUT = BASE / "news_signal.json"

WINDOW_DAYS = 7        # how far back news still counts toward "current" sentiment
MIN_ABS = 0.15         # ignore near-neutral noise when aggregating
HALF_LIFE_DAYS = 3.0   # older articles decay: weight halves every 3 days
NOVEL_DISCOUNT = 0.3   # weight for news the LLM judged already-priced-in
# Companies named ONLY in an article's body blurb are excluded, not discounted.
# Measured 2026-07-30 over 30 days: body-only mentions beat NIFTY 32.6% of the
# time (n=95, ±9.4) versus 51.1% for headline mentions — the whole confidence
# interval sits below coin-flip, i.e. they are actively anti-predictive. They
# keep flowing into news.db as an ML feature; they just don't reach the bot.
REQUIRE_HEADLINE = True


def source_weights(con):
    """Trust per source from graded 1-day outcomes (0.6–1.4, neutral if <5)."""
    try:
        rows = con.execute(
            "SELECT source, AVG(hit), COUNT(*) FROM graded GROUP BY source").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {s: max(0.6, min(1.4, 2 * hr)) for s, hr, n in rows if n >= 5}


def main():
    con = db.connect(DB)
    sw = source_weights(con)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=WINDOW_DAYS)).isoformat()

    rows = con.execute(
        "SELECT t.symbol, a.source, a.title, a.sentiment, t.llm_sent, "
        "       t.llm_novel, t.in_title, COALESCE(a.published, a.fetched_at) "
        "FROM article_tickers t JOIN articles a ON a.link = t.link "
        "WHERE COALESCE(a.noise, 0) = 0 "
        "  AND COALESCE(a.published, a.fetched_at) >= ?",
        (since,)).fetchall()

    agg = {}   # symbol -> [weighted_sum, weight_sum, n, [titles], novel_n]
    for symbol, source, title, vader, llm, novel, in_title, ts in rows:
        if REQUIRE_HEADLINE and not in_title:
            continue
        sent = llm if llm is not None else vader
        if sent is None or abs(sent) < MIN_ABS:
            continue
        try:
            age_days = (now - datetime.fromisoformat(ts)).total_seconds() / 86400
        except (ValueError, TypeError):
            age_days = 0.0
        w = sw.get(source, 1.0) * 0.5 ** (max(age_days, 0) / HALF_LIFE_DAYS)
        # novel is NULL for pairs the LLM has not reached yet — treat as unknown
        # (no discount) rather than assuming stale.
        if novel == 0:
            w *= NOVEL_DISCOUNT
        a = agg.setdefault(symbol, [0.0, 0.0, 0, [], 0])
        a[0] += sent * w
        a[1] += w
        a[2] += 1
        a[3].append(title)
        a[4] += 1 if novel else 0

    signal = {}
    for symbol, (ws, wsum, n, titles, novel_n) in agg.items():
        if wsum <= 0:
            continue
        signal[symbol] = {
            "score": round(ws / wsum, 4),          # trust+recency+novelty weighted
            "n": n,                                 # article count
            "sources": len(set(cluster_titles(titles))),  # INDEPENDENT stories
            "novel": novel_n,                       # how many were new information
        }

    out = {
        "generated_at": now.isoformat(),
        "window_days": WINDOW_DAYS,
        "half_life_days": HALF_LIFE_DAYS,
        "note": "Per-ticker news sentiment. Scores are per-(article,company) LLM "
                "judgments weighted by learned source trust, recency, novelty and "
                "headline-vs-body position. 'sources' counts INDEPENDENT stories "
                "(syndicated reprints collapse to one); consumers should require "
                "sources>=2 before acting.",
        "tickers": dict(sorted(signal.items())),
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    con.close()

    strong = sorted(signal.items(), key=lambda kv: kv[1]["score"])
    print(f"Exported {len(signal)} tickers to {OUT.name} "
          f"(window {WINDOW_DAYS}d, {len(rows)} scored pairs).")
    if strong:
        neg = [f"{t} {v['score']:+.2f}" for t, v in strong[:3] if v["score"] < 0]
        pos = [f"{t} {v['score']:+.2f}" for t, v in strong[::-1][:3] if v["score"] > 0]
        if neg:
            print("  most negative:", ", ".join(neg))
        if pos:
            print("  most positive:", ", ".join(pos))


if __name__ == "__main__":
    main()
