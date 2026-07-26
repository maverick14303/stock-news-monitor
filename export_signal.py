"""Export a per-ticker news-sentiment snapshot for other projects to consume.

Writes news_signal.json: for each NSE ticker with recent coverage, an aggregate
sentiment in [-1, 1] weighted by the SAME learned source-trust the autotrader
uses (a source whose past signals graded badly counts less). This is the clean
hand-off point — the trading-bot's fused momentum+news bot reads this file
(locally, or from the raw GitHub URL on CI) instead of touching news.db.

Prefers the LLM score (llm_sent) when present, else VADER (sentiment).

Usage: python export_signal.py
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent
DB = BASE / "news.db"
OUT = BASE / "news_signal.json"

WINDOW_DAYS = 7        # how far back news still counts toward "current" sentiment
MIN_ABS = 0.15         # ignore near-neutral noise when aggregating
HALF_LIFE_DAYS = 3.0   # older articles decay: weight halves every 3 days


def source_weights(con):
    """Trust per source from graded 1-day outcomes (0.6–1.4, neutral if <5)."""
    try:
        rows = con.execute(
            "SELECT source, AVG(hit), COUNT(*) FROM graded GROUP BY source").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {s: max(0.6, min(1.4, 2 * hr)) for s, hr, n in rows if n >= 5}


def main():
    con = sqlite3.connect(DB)
    sw = source_weights(con)
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=WINDOW_DAYS)).isoformat()

    rows = con.execute(
        "SELECT tickers, source, sentiment, llm_sent, "
        "COALESCE(published, fetched_at) FROM articles "
        "WHERE tickers != '' AND COALESCE(published, fetched_at) >= ?",
        (since,)).fetchall()

    agg = {}  # ticker -> [weighted_sent_sum, weight_sum, n, {sources}]
    for tickers, source, vader, llm, ts in rows:
        sent = llm if llm is not None else vader
        if sent is None or abs(sent) < MIN_ABS:
            continue
        try:
            age_days = (now - datetime.fromisoformat(ts)).total_seconds() / 86400
        except (ValueError, TypeError):
            age_days = 0.0
        recency = 0.5 ** (max(age_days, 0) / HALF_LIFE_DAYS)
        w = sw.get(source, 1.0) * recency
        for t in tickers.split(","):
            a = agg.setdefault(t, [0.0, 0.0, 0, set()])
            a[0] += sent * w
            a[1] += w
            a[2] += 1
            a[3].add(source)

    signal = {}
    for t, (ws, wsum, n, sources) in agg.items():
        if wsum <= 0:
            continue
        signal[t] = {
            "score": round(ws / wsum, 4),      # trust+recency-weighted mean sentiment
            "n": n,                             # article count
            "sources": len(sources),            # distinct outlets (confidence)
        }

    out = {
        "generated_at": now.isoformat(),
        "window_days": WINDOW_DAYS,
        "half_life_days": HALF_LIFE_DAYS,
        "note": "Per-ticker news sentiment, weighted by learned source trust + recency. "
                "score in [-1,1]; consumers should require sources>=2 before acting.",
        "tickers": dict(sorted(signal.items())),
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    con.close()

    strong = sorted(signal.items(), key=lambda kv: kv[1]["score"])
    print(f"Exported {len(signal)} tickers to {OUT.name} "
          f"(window {WINDOW_DAYS}d, {len(rows)} articles).")
    if strong:
        neg = [f"{t} {v['score']:+.2f}" for t, v in strong[:3] if v["score"] < 0]
        pos = [f"{t} {v['score']:+.2f}" for t, v in strong[::-1][:3] if v["score"] > 0]
        if neg:
            print("  most negative:", ", ".join(neg))
        if pos:
            print("  most positive:", ", ".join(pos))


if __name__ == "__main__":
    main()
