"""Grade past news signals against what stocks actually did.

For every ticker-matched, non-neutral article (last 30 days, 1+ day old),
compare sentiment direction vs the stock's move over 1, 3 and 5 trading days.
Hit rates are shown against the "always-bull" baseline (fraction of moves that
were simply up) — beating 50% means nothing if the market drifts up anyway.
Also grades the ✅ PASSES verdicts logged by alerts.py at a 5-day horizon.

Usage: python scoreboard.py
"""
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent
DB = BASE / "news.db"
THRESHOLD = 0.25    # |sentiment| below this is neutral -> not a directional signal
HORIZONS = (1, 3, 5)
WINDOW_DAYS = 30
SHOW_ROWS = 15

_price_cache = {}


def closes_for(symbol):
    if symbol not in _price_cache:
        hist = yf.Ticker(symbol).history(period="6mo")
        if hist.empty:
            _price_cache[symbol] = None
        else:
            hist.index = hist.index.tz_localize(None)
            _price_cache[symbol] = hist["Close"]
    return _price_cache[symbol]


def horizon_returns(symbol, date):
    """% returns from the close on/before `date` to 1/3/5 trading closes after."""
    closes = closes_for(symbol)
    if closes is None:
        return {}
    day_end = datetime.combine(date, datetime.max.time())
    before = closes[closes.index <= day_end]
    after = closes[closes.index > day_end]
    if len(before) < 1:
        return {}
    base = float(before.iloc[-1])
    return {h: (float(after.iloc[h - 1]) / base - 1) * 100
            for h in HORIZONS if len(after) >= h}


def ci95(p, n):
    return 1.96 * math.sqrt(p * (1 - p) / n) * 100 if n else 0.0


def main():
    con = sqlite3.connect(DB)
    now = datetime.now(timezone.utc)
    rows = con.execute(
        "SELECT title, source, sentiment, tickers, COALESCE(published, fetched_at) "
        "FROM articles WHERE tickers != '' AND ABS(sentiment) >= ? "
        "AND COALESCE(published, fetched_at) < ? "
        "AND COALESCE(published, fetched_at) >= ?",
        (THRESHOLD, (now - timedelta(days=1)).isoformat(),
         (now - timedelta(days=WINDOW_DAYS)).isoformat())).fetchall()

    samples = {h: [] for h in HORIZONS}   # (hit, ret)
    detail, by_source = [], {}
    for title, source, sent, tickers, ts in rows:
        date = datetime.fromisoformat(ts).date()
        for symbol in tickers.split(","):
            rets = horizon_returns(symbol, date)
            for h, ret in rets.items():
                samples[h].append(((sent > 0) == (ret > 0), ret))
            if 1 in rets:
                hit = (sent > 0) == (rets[1] > 0)
                detail.append((date, symbol, sent, rets[1], hit, title[:55], source))
                by_source.setdefault(source, []).append(hit)

    # persist 1-day grades so autotrader.py can learn source reliability
    con.execute("CREATE TABLE IF NOT EXISTS graded (day TEXT, symbol TEXT, "
                "source TEXT, title TEXT, sent REAL, ret REAL, hit INT, "
                "PRIMARY KEY (day, symbol, source, title))")
    for date, symbol, sent, ret, hit, title, source in detail:
        con.execute("INSERT OR REPLACE INTO graded VALUES (?,?,?,?,?,?,?)",
                    (str(date), symbol, source, title, sent, ret, int(hit)))
    con.commit()

    if not detail:
        print("No scoreable signals yet — keep collecting.")
    else:
        print(f"=== Recent signals (last {SHOW_ROWS} of {len(detail)}, 1-day horizon) ===")
        print(f"{'date':<12}{'symbol':<15}{'sent':>6}{'move%':>8}  hit  headline")
        for date, symbol, sent, ret, hit, title, _ in sorted(detail)[-SHOW_ROWS:]:
            print(f"{date!s:<12}{symbol:<15}{sent:>+6.2f}{ret:>+8.2f}  "
                  f"{'YES' if hit else 'no ':<3}  {title}")

        print("\n=== Hit rate by horizon (vs always-bull baseline) ===")
        print(f"{'horizon':<10}{'n':>6}{'hit%':>8}{'±95%':>7}{'baseline':>10}{'verdict':>10}")
        for h in HORIZONS:
            s = samples[h]
            if not s:
                continue
            n = len(s)
            hitp = 100 * sum(1 for hit, _ in s if hit) / n
            base = 100 * sum(1 for _, r in s if r > 0) / n
            edge = "EDGE?" if hitp - ci95(hitp / 100, n) > base else "no edge"
            print(f"{h}d{'':<8}{n:>6}{hitp:>8.1f}{ci95(hitp / 100, n):>7.1f}{base:>10.1f}{edge:>10}")
        print("(EDGE? only when the whole confidence range clears the baseline)")

        print("\n=== By source (1-day) ===")
        for source, flags in sorted(by_source.items(), key=lambda kv: -len(kv[1]))[:10]:
            print(f"  {source:<30} {sum(flags)}/{len(flags)} ({100 * sum(flags) / len(flags):.0f}%)")

    # grade the ✅ PASSES verdicts from alerts.py at 5 trading days
    try:
        verdicts = con.execute(
            "SELECT ts, symbol, price, title FROM verdicts WHERE ts < ?",
            ((now - timedelta(days=1)).isoformat(),)).fetchall()
    except sqlite3.OperationalError:
        verdicts = []
    if verdicts:
        print("\n=== ✅ verdict journal (5-day outcomes) ===")
        outcomes = []
        for ts, symbol, price, title in verdicts:
            closes = closes_for(symbol)
            if closes is None or not price:
                continue
            day_end = datetime.combine(datetime.fromisoformat(ts).date(),
                                       datetime.max.time())
            after = closes[closes.index > day_end]
            if len(after) < 1:
                continue
            ret = (float(after.iloc[min(5, len(after)) - 1]) / price - 1) * 100
            outcomes.append(ret)
            print(f"  {ts[:10]}  {symbol:<15}{ret:>+7.2f}%  {title[:50]}")
        if outcomes:
            wins = sum(1 for r in outcomes if r > 0)
            print(f"  → {wins}/{len(outcomes)} positive, avg {sum(outcomes) / len(outcomes):+.2f}%")
    con.close()


if __name__ == "__main__":
    main()
