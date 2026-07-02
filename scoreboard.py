"""Score past news signals against what the stock actually did.

For every article matched to a ticker with a non-neutral sentiment, compare the
stock's close on the news day vs the next trading day. A signal "hits" when the
direction of the move matches the sentiment. Run after signals are 1+ days old.

Usage: python scoreboard.py
"""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

import yfinance as yf

BASE = Path(__file__).parent
DB = BASE / "news.db"
THRESHOLD = 0.25  # |sentiment| below this is neutral -> not a directional signal

_price_cache = {}


def next_day_return(symbol, date):
    """Return % change from close on `date` (or prior close) to next trading close."""
    if symbol not in _price_cache:
        hist = yf.Ticker(symbol).history(period="3mo")
        if hist.empty:
            _price_cache[symbol] = None
        else:
            hist.index = hist.index.tz_localize(None)
            _price_cache[symbol] = hist["Close"]
    closes = _price_cache[symbol]
    if closes is None:
        return None
    after = closes[closes.index > datetime.combine(date, datetime.min.time())]
    before = closes[closes.index <= datetime.combine(date, datetime.max.time())]
    if len(after) < 1 or len(before) < 1:
        return None
    return (after.iloc[0] / before.iloc[-1] - 1) * 100


def main():
    con = sqlite3.connect(DB)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    window_start = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = con.execute(
        "SELECT title, source, sentiment, tickers, COALESCE(published, fetched_at) "
        "FROM articles WHERE tickers != '' AND ABS(sentiment) >= ? "
        "AND COALESCE(published, fetched_at) < ? "
        "AND COALESCE(published, fetched_at) >= ?",
        (THRESHOLD, cutoff, window_start),
    ).fetchall()
    con.close()

    if not rows:
        print("No scoreable signals yet (need ticker-matched, non-neutral articles 1+ days old).")
        print("Keep running monitor.py daily — the scoreboard fills in as history builds.")
        return

    results, by_source = [], {}
    for title, source, sent, tickers, ts in rows:
        date = datetime.fromisoformat(ts).date()
        for symbol in tickers.split(","):
            ret = next_day_return(symbol, date)
            if ret is None:
                continue
            hit = (sent > 0) == (ret > 0)
            results.append((date, symbol, sent, ret, hit, title[:60], source))
            by_source.setdefault(source, []).append(hit)

    if not results:
        print("Signals found but no price data available yet.")
        return

    print(f"{'date':<12}{'symbol':<15}{'sent':>6}{'move%':>8}  hit  headline")
    for date, symbol, sent, ret, hit, title, _ in sorted(results):
        print(f"{date!s:<12}{symbol:<15}{sent:>+6.2f}{ret:>+8.2f}  {'YES' if hit else 'no ':<3}  {title}")

    hits = sum(1 for r in results if r[4])
    print(f"\nOverall: {hits}/{len(results)} signals matched next-day direction "
          f"({100 * hits / len(results):.0f}%). Coin flip = 50%.")
    print("\nBy source:")
    for source, flags in sorted(by_source.items()):
        print(f"  {source:<28} {sum(flags)}/{len(flags)} ({100 * sum(flags) / len(flags):.0f}%)")


if __name__ == "__main__":
    main()
