"""Grade past news signals against what stocks actually did.

Grades one row per (article, company) pair — never a whole article — and only
real news (tracker pages, listicles and index wraps are excluded via the noise
flag; they used to be ~24% of all signals and, being backward-looking
descriptions of moves that had already happened, they INFLATED the hit rate).

Two hit rates are reported, and the second one is the honest one:
  * RAW direction   — did the stock go up after positive news? Compared against
                      the always-bull baseline, because the market drifts up.
  * EXCESS vs NIFTY — did the stock beat the index? Market drift cancels out, so
                      the baseline is a clean 50%. This is the number that has to
                      clear its confidence interval before any edge is real.

Splits the result by the things most likely to carry edge (ROADMAP_ML.md §5):
after-hours vs in-session, novel vs descriptive, headline vs body mention.

Also writes the `labels` table — the growing ML dataset. See ROADMAP_ML.md §1.

Usage: python scoreboard.py
"""
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import db
from newslib import cluster_titles, signal_trading_day

BASE = Path(__file__).parent
DB = BASE / "news.db"
THRESHOLD = 0.25    # |sentiment| below this is neutral -> not a directional signal
HORIZONS = (1, 3, 5)
WINDOW_DAYS = 30
SHOW_ROWS = 15
BENCHMARK = "^NSEI"   # NIFTY 50, for excess-return grading

# The scoreboard prints several EDGE? tests at once (horizons x splits). At 95%
# each, running 9 of them gives a ~37% chance that one clears by luck alone —
# and we are hunting a small edge, so a false positive would be believed.
# Confidence intervals are Bonferroni-widened by this count.
N_TESTS = 9
PRIMARY_HYPOTHESIS = ("after-hours", 1)   # the ONE pre-registered test (§5 roadmap)

_price_cache = {}


def closes_for(symbol):
    if symbol not in _price_cache:
        try:
            hist = yf.Ticker(symbol).history(period="6mo")
        except Exception:
            hist = None
        if hist is None or hist.empty:
            _price_cache[symbol] = None
        else:
            hist.index = hist.index.tz_localize(None)
            # yfinance emits NaN closes for partial/placeholder bars (a run before
            # the session ends returns one for today). Any NaN that survives into
            # a return calculation silently poisons the whole scoreboard.
            _price_cache[symbol] = hist["Close"].dropna()
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


def ci95(p, n, tests=N_TESTS):
    """Bonferroni-corrected confidence half-width, in percentage points.

    z is raised from 1.96 to the two-sided quantile for alpha/tests, so a table
    of nine simultaneous tests cannot manufacture an EDGE? by chance.
    """
    if not n:
        return 0.0
    z = _norm_ppf(1 - (0.05 / max(tests, 1)) / 2)
    return z * math.sqrt(p * (1 - p) / n) * 100


def _norm_ppf(q):
    """Inverse normal CDF (Acklam's approximation) — avoids a scipy dependency."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low = 0.02425
    if q < p_low:
        x = math.sqrt(-2 * math.log(q))
        return (((((c[0] * x + c[1]) * x + c[2]) * x + c[3]) * x + c[4]) * x + c[5]) / \
               ((((d[0] * x + d[1]) * x + d[2]) * x + d[3]) * x + 1)
    if q > 1 - p_low:
        x = math.sqrt(-2 * math.log(1 - q))
        return -(((((c[0] * x + c[1]) * x + c[2]) * x + c[3]) * x + c[4]) * x + c[5]) / \
               ((((d[0] * x + d[1]) * x + d[2]) * x + d[3]) * x + 1)
    x = q - 0.5
    r = x * x
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * x / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def rate_line(label, sample):
    """sample: [(hit, excess_hit)] -> one formatted row, or None if empty."""
    n = len(sample)
    if not n:
        return None
    raw = 100 * sum(1 for h, _ in sample if h) / n
    exc = 100 * sum(1 for _, e in sample if e) / n
    ci = ci95(exc / 100, n)
    return (f"{label:<22}{n:>6}{raw:>8.1f}{exc:>9.1f}{ci:>7.1f}"
            f"{'EDGE?' if exc - ci > 50 else 'no edge':>10}")


def main():
    con = db.connect(DB)
    now = datetime.now(timezone.utc)

    rows = con.execute(
        "SELECT t.link, t.symbol, a.title, a.source, a.sentiment, t.llm_sent, "
        "       t.llm_novel, t.in_title, a.after_hours, "
        "       COALESCE(a.published, a.fetched_at) "
        "FROM article_tickers t JOIN articles a ON a.link = t.link "
        "WHERE COALESCE(a.noise, 0) = 0 "
        "  AND COALESCE(a.published, a.fetched_at) < ? "
        "  AND COALESCE(a.published, a.fetched_at) >= ?",
        ((now - timedelta(days=1)).isoformat(),
         (now - timedelta(days=WINDOW_DAYS)).isoformat())).fetchall()

    samples = {h: [] for h in HORIZONS}
    by_hours, by_novel, by_where = {}, {}, {}
    detail, by_source, label_rows, graded_rows = [], {}, [], []

    # How many independent outlets carried each ticker's story (ML feature).
    src_titles = {}
    for symbol, title in con.execute(
            "SELECT t.symbol, a.title FROM article_tickers t "
            "JOIN articles a ON a.link = t.link "
            "WHERE COALESCE(a.noise, 0) = 0").fetchall():
        src_titles.setdefault(symbol, []).append(title)
    n_sources = {s: len(set(cluster_titles(t))) for s, t in src_titles.items()}

    for (link, symbol, title, source, vader, llm, novel, in_title,
         after_hours, ts) in rows:
        sent = llm if llm is not None else vader
        # Trading day, NOT the calendar date: news at 02:00 IST belongs to the
        # PREVIOUS session's close, otherwise the overnight gap — the exact move
        # an after-hours signal is meant to capture — is measured away.
        date = signal_trading_day(ts)
        if date is None:
            continue
        rets = horizon_returns(symbol, date)
        mkt = horizon_returns(BENCHMARK, date)
        if not rets:
            continue
        # The ML dataset keeps EVERY pair, including neutral ones. A classifier
        # trained only on directional signals can never learn to recognise a
        # weak one, because it never sees "looks like news, isn't tradeable".
        label_rows.append((
            link, symbol, str(date), source, title, vader, llm, novel,
            in_title, after_hours, n_sources.get(symbol),
            rets.get(1), rets.get(3), rets.get(5),
            mkt.get(1), mkt.get(3), mkt.get(5)))
        if sent is None or abs(sent) < THRESHOLD:
            continue
        up = sent > 0
        for h, ret in rets.items():
            excess = ret - mkt.get(h, 0.0)
            pair = ((up == (ret > 0)), (up == (excess > 0)))
            samples[h].append(pair)
            if h == 1:
                by_hours.setdefault("after-hours" if after_hours else "in-session",
                                    []).append(pair)
                if novel is not None:
                    by_novel.setdefault("novel" if novel else "descriptive",
                                        []).append(pair)
                by_where.setdefault("headline" if in_title else "body mention",
                                    []).append(pair)
        if 1 in rets:
            hit = up == (rets[1] > 0)
            detail.append((date, symbol, sent, rets[1], hit, title[:55], source))
            by_source.setdefault(source, []).append(hit)
            graded_rows.append((str(date), symbol, source, title, sent,
                                rets[1], int(hit)))

    # `graded` powers learned source trust in autotrader.py / export_signal.py
    for r in graded_rows:
        con.execute("INSERT OR REPLACE INTO graded VALUES (?,?,?,?,?,?,?)", r)
    # `labels` is the append-only ML dataset (ROADMAP_ML.md)
    for r in label_rows:
        con.execute("INSERT OR REPLACE INTO labels VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    con.commit()

    if not detail:
        print("No scoreable signals yet — keep collecting.")
    else:
        print(f"=== Recent signals (last {SHOW_ROWS} of {len(detail)}, 1-day horizon) ===")
        print(f"{'date':<12}{'symbol':<15}{'sent':>6}{'move%':>8}  hit  headline")
        for date, symbol, sent, ret, hit, title, _ in sorted(detail)[-SHOW_ROWS:]:
            print(f"{date!s:<12}{symbol:<15}{sent:>+6.2f}{ret:>+8.2f}  "
                  f"{'YES' if hit else 'no ':<3}  {title}")

        print("\n=== Hit rate by horizon ===")
        print(f"{'horizon':<22}{'n':>6}{'raw%':>8}{'excess%':>9}{'±95%':>7}{'verdict':>10}")
        for h in HORIZONS:
            line = rate_line(f"{h}d", samples[h])
            if line:
                print(line)
        print("  raw%   = direction correct (inflated by market drift)")
        print("  excess% = beat NIFTY over the same window — baseline is a clean 50%")
        print(f"  ±95% is Bonferroni-widened for {N_TESTS} simultaneous tests: at plain")
        print("  95% there is a ~37% chance one row clears 50% by luck alone.")
        print(f"  PRE-REGISTERED hypothesis = '{PRIMARY_HYPOTHESIS[0]}' at "
              f"{PRIMARY_HYPOTHESIS[1]}d. Treat every other row as exploratory.")

        for name, groups in (("after-hours vs in-session", by_hours),
                             ("novel vs descriptive", by_novel),
                             ("headline vs body mention", by_where)):
            if len(groups) < 1:
                continue
            print(f"\n=== 1-day split: {name} ===")
            print(f"{'group':<22}{'n':>6}{'raw%':>8}{'excess%':>9}{'±95%':>7}{'verdict':>10}")
            for label, sample in sorted(groups.items()):
                line = rate_line(label, sample)
                if line:
                    print(line)

        print("\n=== By source (1-day, raw direction) ===")
        for source, flags in sorted(by_source.items(), key=lambda kv: -len(kv[1]))[:10]:
            print(f"  {source:<30} {sum(flags)}/{len(flags)} "
                  f"({100 * sum(flags) / len(flags):.0f}%)")

    # head-to-head: VADER vs the per-ticker LLM score, same pairs, 1-day
    stats = {"VADER": [0, 0], "LLM": [0, 0]}
    for (_, symbol, _, _, vader, llm, _, _, _, ts) in rows:
        if llm is None:
            continue
        date = datetime.fromisoformat(ts).date()
        rets = horizon_returns(symbol, date)
        if 1 not in rets:
            continue
        up = rets[1] > 0
        if vader is not None and abs(vader) >= THRESHOLD:
            stats["VADER"][0] += (vader > 0) == up
            stats["VADER"][1] += 1
        if abs(llm) >= THRESHOLD:
            stats["LLM"][0] += (llm > 0) == up
            stats["LLM"][1] += 1
    if stats["LLM"][1]:
        print("\n=== VADER vs LLM analyst (same pairs, 1-day) ===")
        for name, (hits, n) in stats.items():
            if n:
                print(f"  {name:<6} {hits}/{n} ({100 * hits / n:.0f}%)")

    n_labels = con.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
    print(f"\nML dataset: {n_labels} labelled (article, company) rows "
          f"— see ROADMAP_ML.md for the phase gates.")

    # grade the ✅ PASSES verdicts from alerts.py at 5 trading days
    verdicts = con.execute(
        "SELECT ts, symbol, price, title FROM verdicts WHERE ts < ?",
        ((now - timedelta(days=1)).isoformat(),)).fetchall()
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
