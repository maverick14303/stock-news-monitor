"""Scan recent articles and portfolio state; write a structured ALERT.md.

Sections: live P&L vs the NIFTY shadow, exit checks on holdings (twice a day),
news on held stocks with verdicts, pre-checked buy ideas (✅ ones are logged to
the verdicts table so scoreboard.py can grade them later), macro events mapped
to exposed holdings, and a Sunday-evening weekly report with feed health.
The cloud workflow turns ALERT.md into a GitHub issue (emailed by GitHub);
the first line becomes the subject.
"""
import csv
import difflib
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent
OUT = BASE / "ALERT.md"

WINDOW_HOURS = 1.2  # slightly over the hourly run cadence so nothing slips between runs
HELD_THRESHOLD = 0.4
OPPORTUNITY_THRESHOLD = 0.7
MAX_OPPORTUNITIES = 6
MAX_MACRO = 4
PRICED_IN_MOVE = 2.5    # % move that means the news is likely already in the price
MIN_OUTLETS = 2
MAX_CHECKED = 8         # bound on price lookups per email
STOP_LOSS_PCT = -7.0
TAKE_PROFIT_PCT = 15.0
STALE_DAYS = 30
EXIT_CHECK_HOURS = (9, 16)      # IST run hours that evaluate exit rules
WEEKLY_DAY, WEEKLY_HOUR = 6, 18  # Sunday 6 PM IST
FEED_SILENT_HOURS = 48

MACRO_EVENT = re.compile(
    r"\b(crash(es|ed)?|plunge[sd]?|tumble[sd]?|sink(s|ing)?|black monday|"
    r"rate (hike|cut)|repo rate|circuit|emergency|war|strike[sd]?|sanction|"
    r"tariff|default|recession|inflation (surge|spike|shock))\b", re.IGNORECASE)
MACRO_SCOPE = re.compile(
    r"\b(sensex|nifty|dalal street|rbi|fed|federal reserve|wall street|"
    r"crude|oil|opec|rupee|dollar|gold|china|us market)\b", re.IGNORECASE)

# Index wrap-ups mention many tickers but aren't stock-specific news;
# they repeat every session and would spam ticker alerts
MARKET_WRAP = re.compile(
    r"\b(sensex|nifty|stock market|market live|top gainers|top losers|"
    r"opening bell|closing bell|market highlights)\b", re.IGNORECASE)

# macro keyword -> sectors it usually moves; used to tag which holdings
# a macro headline touches
EXPOSURE = [
    (re.compile(r"\b(crude|oil|opec|fuel)\b", re.IGNORECASE), {"energy"}),
    (re.compile(r"\b(rate hike|rate cut|repo rate|rbi|fed|federal reserve|inflation)\b",
                re.IGNORECASE), {"banks", "financials", "auto"}),
    (re.compile(r"\b(china|tariff|sanction|trade war)\b", re.IGNORECASE),
     {"metals", "auto", "it"}),
    (re.compile(r"\b(rupee|dollar)\b", re.IGNORECASE), {"it", "pharma"}),
    (re.compile(r"\b(war|geopolitic\w*)\b", re.IGNORECASE), {"energy", "metals"}),
]

REVIEW_ADVICE = (
    "👉 **What to do:** Read the full story first. Sell only if the problem is "
    "fundamental — profit collapse, fraud, regulator action, lost business. If it's "
    "a broad market dip or one analyst's opinion, holding is usually right. You never "
    "have to act in minutes; a real problem will still be true tomorrow.")
SUPPORT_ADVICE = (
    "👉 **What to do:** Nothing — this is supportive news on a stock you already own. "
    "Avoid buying more just because it's up today; that gain is already in the price.")
MIXED_ADVICE = (
    "👉 **What to do:** Signals conflict — read both sides before doing anything. "
    "When in doubt with conflicting news, doing nothing is a valid decision.")
OPPORTUNITY_ADVICE = (
    "👉 **What to do:** The checks above are automated: *today's move* tests if the "
    "news is already priced in, *outlets/stories* tests independent confirmation, and "
    "the share count respects the sizing rule (max half your free cash on one idea). "
    "A ✅ is a shortlist, not an order — still read the story before buying.")
MACRO_ADVICE = (
    "👉 **What to do:** Don't buy or sell from a macro headline alone — it moves whole "
    "sectors, slowly. If one of your holdings is tagged above, watch that position more "
    "closely today. If nothing is tagged, this is background noise for your book.")
EXIT_ADVICE = (
    "👉 **What to do:** An exit flag is a review, not an order. Stop-loss: did the "
    "thesis break, or did the market just dip? If the thesis broke, sell. Take-profit: "
    "consider selling half to lock the gain. Stale: if you can't say why you still own "
    "it, you don't own it — it owns you.")


def dot(x):
    return "🔴" if x < 0 else "🟢"


def load_sectors():
    with open(BASE / "tickers.csv", encoding="utf-8") as f:
        return {row["symbol"]: row.get("sector", "") for row in csv.DictReader(f)}


def get_prices(symbols):
    from paper_portfolio import live_price
    prices = {}
    for s in symbols:
        try:
            prices[s] = live_price(s)
        except (Exception, SystemExit):
            prices[s] = None
    return prices


def book_lines(held, cash, bench, prices):
    from paper_portfolio import STARTING_CASH
    out = ["", "| stock | qty | avg cost | now | P&L |", "|---|---:|---:|---:|---|"]
    total = cash
    for t, pos in held.items():
        price = prices.get(t) or pos["avg_cost"]
        cost = pos["avg_cost"] * pos["qty"]
        pnl = price * pos["qty"] - cost
        total += price * pos["qty"]
        out.append(f"| {t} | {pos['qty']} | {pos['avg_cost']:.2f} | {price:.2f} "
                   f"| {dot(pnl)} {pnl:+.2f} ({100 * pnl / cost:+.2f}%) |")
    out.append(f"\n**Total: Rs {total:,.2f} ({total - STARTING_CASH:+.2f}, "
               f"{100 * (total - STARTING_CASH) / STARTING_CASH:+.2f}% since start) "
               f"— incl. Rs {cash:,.2f} cash**")
    level = bench and prices.get(bench["symbol"])
    shadow = bench["start_capital"] * level / bench["start_level"] if level else None
    return out, total, shadow


def bot_recent_trades():
    ledger = BASE / "bot_portfolio.json"
    if not ledger.exists():
        return [], None
    b = json.loads(ledger.read_text())
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)).isoformat()
    return [t for t in b["trades"] if t["time"] >= cutoff], b


def bot_lines(bot, recent, a_total, shadow):
    if bot is None:
        return []
    prices = get_prices(set(bot["positions"]))
    b_total = bot["cash"] + sum((prices.get(s) or pos["avg_cost"]) * pos["qty"]
                                for s, pos in bot["positions"].items())
    out = ["", "## 🤖 Account B — the bot (trades itself)", "",
           "| account | value | since start |", "|---|---:|---:|"]
    out.append(f"| A — you + Claude | Rs {a_total:,.2f} | {100 * (a_total - 5000) / 5000:+.2f}% |")
    out.append(f"| B — bot | Rs {b_total:,.2f} | {100 * (b_total - 5000) / 5000:+.2f}% |")
    if shadow:
        out.append(f"| NIFTY 50 index | Rs {shadow:,.2f} | {100 * (shadow - 5000) / 5000:+.2f}% |")
    if recent:
        out.append("\n**Bot trades this hour:**")
        for t in recent:
            out.append(f"- {t['action'].upper()} {t['qty']} x {t['symbol']} @ "
                       f"Rs {t['price']:.2f} — {t.get('reason', '')}")
    if bot["positions"]:
        holds = ", ".join(f"{s} x{pos['qty']}" for s, pos in bot["positions"].items())
        out.append(f"\nBot holds: {holds} + Rs {bot['cash']:,.2f} cash")
    closed = bot.get("closed", [])
    if closed:
        wins = sum(1 for c in closed if c["win"])
        out.append(f"Bot record: {wins}/{len(closed)} wins, "
                   f"avg {sum(c['pnl_pct'] for c in closed) / len(closed):+.2f}% per closed trade")
    return out


def exit_lines(held, trades, prices, now):
    if now.hour not in EXIT_CHECK_HOURS:
        return []
    first_buy = {}
    for tr in trades:
        if tr.get("action") == "buy":
            first_buy.setdefault(tr["symbol"], tr["time"])
    flags = []
    for t, pos in held.items():
        price = prices.get(t)
        if not price:
            continue
        pct = (price / pos["avg_cost"] - 1) * 100
        age = 0
        if t in first_buy:
            age = (now - datetime.fromisoformat(first_buy[t]).replace(tzinfo=None)).days
        if pct <= STOP_LOSS_PCT:
            flags.append(f"- 🔴 **{t}** is {pct:+.1f}% vs your cost — **STOP-LOSS REVIEW**")
        elif pct >= TAKE_PROFIT_PCT:
            flags.append(f"- 🟢 **{t}** is {pct:+.1f}% vs your cost — **TAKE-PROFIT REVIEW**")
        elif age >= STALE_DAYS:
            flags.append(f"- ⚪ **{t}** held {age} days with {pct:+.1f}% — **STALE, re-justify or exit**")
    if flags:
        return ["", "## 🚪 Exit checks on your holdings", ""] + flags + ["", EXIT_ADVICE]
    return []


def distinct_stories(titles):
    """Cluster near-identical headlines so syndicated wire copy counts once."""
    clusters = []
    for t in (re.sub(r"\W+", " ", x.lower()).strip() for x in titles):
        for c in clusters:
            if difflib.SequenceMatcher(None, t, c).ratio() > 0.85:
                break
        else:
            clusters.append(t)
    return len(clusters)


def opportunity_checks(opps, cash, con, now):
    """Pre-buy checklist per ticker; log ✅ PASSES to the verdicts table."""
    import yfinance as yf
    day_ago = (now - timedelta(hours=24)).isoformat()
    cap = cash / 2
    lines = ["", "**🤖 Pre-buy checks, done for you:**", ""]
    checked = []
    for sent, tks, title, source in opps:
        for t in tks:
            if t in checked or len(checked) >= MAX_CHECKED:
                continue
            checked.append(t)
            if sent < 0:
                lines.append(f"- `{t}`: ℹ️ bad-news signal on a stock you don't own — "
                             "nothing to do (no short selling).")
                continue
            cov = con.execute(
                "SELECT source, title FROM articles "
                "WHERE (',' || tickers || ',') LIKE ? AND fetched_at >= ? "
                "AND ABS(sentiment) >= 0.25",
                (f"%,{t},%", day_ago)).fetchall()
            outlets = len({s for s, _ in cov})
            stories = distinct_stories([ttl for _, ttl in cov])
            try:
                closes = yf.Ticker(t).history(period="2d")["Close"]
                price = float(closes.iloc[-1])
                move = (price / float(closes.iloc[-2]) - 1) * 100 if len(closes) >= 2 else None
            except Exception:
                price, move = None, None

            facts = [f"today {move:+.1f}%" if move is not None else "today's move n/a",
                     f"{outlets} outlet(s), {stories} distinct stor{'y' if stories == 1 else 'ies'}"]
            shares = int(cap // price) if price else 0
            if move is not None and move >= PRICED_IN_MOVE:
                verdict = f"❌ SKIP — already up {move:+.1f}% today, news likely priced in"
            elif outlets < MIN_OUTLETS or stories < 2:
                verdict = "⚠️ WAIT — not independently confirmed yet; look again if more report it"
            elif price is None:
                verdict = "⚠️ WAIT — no price data to verify"
            elif shares == 0:
                verdict = f"❌ SKIP — 1 share (Rs {price:,.2f}) exceeds your Rs {cap:,.0f} sizing cap"
            else:
                verdict = f"✅ PASSES — you could buy up to {shares} share(s) @ ~Rs {price:,.2f}"
                dup = con.execute(
                    "SELECT 1 FROM verdicts WHERE symbol = ? AND ts >= ?",
                    (t, (now - timedelta(days=3)).isoformat())).fetchone()
                if not dup:
                    con.execute("INSERT INTO verdicts VALUES (?,?,?,?)",
                                (now.isoformat(), t, price, title))
                    con.commit()
            lines.append(f"- `{t}`: {'; '.join(facts)} → {verdict}")
    return lines


def weekly_lines(con, now):
    if not (now.weekday() == WEEKLY_DAY and now.hour == WEEKLY_HOUR):
        return []
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    n_art, n_sig = con.execute(
        "SELECT COUNT(*), SUM(tickers != '') FROM articles WHERE fetched_at >= ?",
        (week_ago,)).fetchone()
    lines = ["", "## 📅 Weekly report", "",
             f"- Articles collected this week: **{n_art}** ({n_sig or 0} matched to stocks)",
             "- Scoreboard detail (hit rates vs baseline, verdict outcomes): see "
             "`digests/LATEST.md` in the repo"]
    feeds = json.loads((BASE / "config.json").read_text())["feeds"]
    silent_cutoff = (datetime.now(timezone.utc) - timedelta(hours=FEED_SILENT_HOURS)).isoformat()
    silent = []
    for f in feeds:
        last = con.execute("SELECT MAX(fetched_at) FROM articles WHERE source = ?",
                           (f["name"],)).fetchone()[0]
        if not last or last < silent_cutoff:
            silent.append(f["name"])
    lines.append(f"- Feed health: {len(feeds) - len(silent)}/{len(feeds)} sources active"
                 + (f" — ⚠️ silent 48h+: {', '.join(silent)}" if silent else ""))
    return lines


def main():
    portfolio = json.loads((BASE / "portfolio.json").read_text())
    held, cash = portfolio["positions"], portfolio["cash"]
    bench = portfolio.get("benchmark")
    trades = portfolio.get("trades", [])
    sectors = load_sectors()
    now_local = datetime.now()

    con = sqlite3.connect(BASE / "news.db")
    con.execute("CREATE TABLE IF NOT EXISTS verdicts "
                "(ts TEXT, symbol TEXT, price REAL, title TEXT)")
    since = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)).isoformat()
    rows = con.execute(
        "SELECT source, title, sentiment, tickers FROM articles WHERE fetched_at >= ?",
        (since,)).fetchall()

    holding_items, opportunities, macros = [], [], []
    seen = set()
    for source, title, sent, tickers in rows:
        if title in seen:
            continue
        tset = set(tickers.split(",")) if tickers else set()
        is_wrap = bool(MARKET_WRAP.search(title))
        if tset & held.keys() and abs(sent) >= HELD_THRESHOLD and not is_wrap:
            holding_items.append((sent, sorted(tset & held.keys()), title, source))
        elif tset and abs(sent) >= OPPORTUNITY_THRESHOLD and not is_wrap:
            opportunities.append((sent, sorted(tset), title, source))
        elif MACRO_EVENT.search(title) and MACRO_SCOPE.search(title):
            hit_sectors = set().union(*(secs for pat, secs in EXPOSURE if pat.search(title)))
            touched = sorted(t for t in held if sectors.get(t) in hit_sectors)
            macros.append((sent, touched, title, source))
        else:
            continue
        seen.add(title)

    opportunities.sort(key=lambda i: -abs(i[0]))
    opportunities = opportunities[:MAX_OPPORTUNITIES]
    macros = macros[:MAX_MACRO]

    price_symbols = set(held)
    if bench:
        price_symbols.add(bench["symbol"])
    prices = get_prices(price_symbols) if (holding_items or opportunities or macros
                                           or now_local.hour in EXIT_CHECK_HOURS
                                           or (now_local.weekday() == WEEKLY_DAY
                                               and now_local.hour == WEEKLY_HOUR)) else {}
    exit_sec = exit_lines(held, trades, prices, now_local)
    weekly_sec = weekly_lines(con, now_local)
    bot_trades, bot = bot_recent_trades()

    if not (holding_items or opportunities or macros or exit_sec or weekly_sec
            or bot_trades):
        OUT.unlink(missing_ok=True)
        con.close()
        print("No alerts this window.")
        return

    by_ticker = {}
    for sent, tks, title, source in holding_items:
        for t in tks:
            by_ticker.setdefault(t, []).append((sent, title, source))
    n_review = sum(1 for items in by_ticker.values()
                   if sum(s for s, _, _ in items) <= -0.2)

    bits = []
    if n_review:
        bits.append(f"{n_review} holding(s) to REVIEW")
    if len(by_ticker) - n_review:
        bits.append(f"{len(by_ticker) - n_review} holding(s) with good news")
    if opportunities:
        bits.append(f"{len(opportunities)} buy idea(s)")
    if exit_sec:
        bits.append("exit checks")
    if bot_trades:
        bits.append(f"bot made {len(bot_trades)} trade(s)")
    if macros:
        bits.append(f"{len(macros)} macro item(s)")
    if weekly_sec:
        bits.append("weekly report")
    if not bits:
        bits = ["portfolio check"]

    lines = [f"# Stocks {now_local:%d %b %H:%M} IST — " + ", ".join(bits), ""]
    blines, a_total, shadow = book_lines(held, cash, bench, prices)
    lines.extend(blines)
    lines.extend(bot_lines(bot, bot_trades, a_total, shadow))
    lines.extend(exit_sec)

    if by_ticker:
        lines.append("\n## 🧳 News on stocks you OWN\n")
        for t in sorted(by_ticker, key=lambda t: sum(s for s, _, _ in by_ticker[t])):
            pos = held[t]
            net = sum(s for s, _, _ in by_ticker[t])
            lines.append(f"### {t} — you hold {pos['qty']} @ avg Rs {pos['avg_cost']:.2f}")
            for sent, title, source in by_ticker[t]:
                lines.append(f"- {dot(sent)} ({sent:+.2f}) {title} — *{source}*")
            advice = (REVIEW_ADVICE if net <= -0.2 else
                      SUPPORT_ADVICE if net >= 0.2 else MIXED_ADVICE)
            lines.append(f"\n{advice}\n")

    if opportunities:
        lines.append("\n## 💡 Strong signals on stocks you DON'T own\n")
        for sent, tks, title, source in opportunities:
            lines.append(f"- {dot(sent)} ({sent:+.2f}) `{','.join(tks)}` {title} — *{source}*")
        lines.extend(opportunity_checks(opportunities, cash, con,
                                        datetime.now(timezone.utc)))
        lines.append(f"\n{OPPORTUNITY_ADVICE}\n")

    if macros:
        lines.append("\n## 🌍 Macro / world events\n")
        for sent, touched, title, source in macros:
            tag = (f" → touches your **{', '.join(touched)}**" if touched
                   else " → no direct hit on your holdings")
            lines.append(f"- {dot(sent)} {title} — *{source}*{tag}")
        lines.append(f"\n{MACRO_ADVICE}\n")

    lines.extend(weekly_sec)
    lines.append("\n---\n_Sentiment is mechanical (word-based); it reads headlines, not "
                 "fundamentals. Paper portfolio — verify anything before treating it as "
                 "a real-money process._")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    con.close()
    print(f"Alert written: {', '.join(bits)}")


if __name__ == "__main__":
    main()
