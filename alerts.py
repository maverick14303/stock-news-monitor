"""Scan articles fetched in the last window and write a structured ALERT.md.

Sections: news on held stocks (with a per-stock verdict), strong signals on
other tracked stocks, and macro events mapped to the holdings they touch.
The cloud workflow turns ALERT.md into a GitHub issue, which GitHub emails
to the repo owner; the first line of the file becomes the email subject.
"""
import csv
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
    "👉 **What to do:** Before buying with your free cash, check: (1) has the stock "
    "already jumped today? Then the news is priced in — skip. (2) Do at least two "
    "different outlets report it? (3) Size small — one position, never all your cash "
    "on one headline. If unsure, skip; cash is also a position.")
MACRO_ADVICE = (
    "👉 **What to do:** Don't buy or sell from a macro headline alone — it moves whole "
    "sectors, slowly. If one of your holdings is tagged above, watch that position more "
    "closely today. If nothing is tagged, this is background noise for your book.")


def dot(sent):
    return "🔴" if sent < 0 else "🟢"


def load_sectors():
    with open(BASE / "tickers.csv", encoding="utf-8") as f:
        return {row["symbol"]: row.get("sector", "") for row in csv.DictReader(f)}


def main():
    portfolio = json.loads((BASE / "portfolio.json").read_text())
    held, cash = portfolio["positions"], portfolio["cash"]
    sectors = load_sectors()
    since = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)).isoformat()
    con = sqlite3.connect(BASE / "news.db")
    rows = con.execute(
        "SELECT source, title, sentiment, tickers FROM articles WHERE fetched_at >= ?",
        (since,)).fetchall()
    con.close()

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

    if not (holding_items or opportunities or macros):
        OUT.unlink(missing_ok=True)
        print("No alerts this window.")
        return

    # group holding news per ticker and give each a net verdict
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
    if macros:
        bits.append(f"{len(macros)} macro item(s)")
    now = datetime.now().strftime("%d %b %H:%M IST")

    lines = [f"# Stocks {now} — " + ", ".join(bits), ""]
    lines.append(f"**Your book:** {len(held)} positions + Rs {cash:,.0f} free cash.")

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
        lines.append(f"\n{OPPORTUNITY_ADVICE}\n")

    if macros:
        lines.append("\n## 🌍 Macro / world events\n")
        for sent, touched, title, source in macros:
            tag = f" → touches your **{', '.join(touched)}**" if touched else " → no direct hit on your holdings"
            lines.append(f"- {dot(sent)} {title} — *{source}*{tag}")
        lines.append(f"\n{MACRO_ADVICE}\n")

    lines.append("\n---\n_Sentiment is mechanical (word-based); it reads headlines, not "
                 "fundamentals. Paper portfolio — verify anything before treating it as "
                 "a real-money process._")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(seen)} alert item(s) written to ALERT.md")


if __name__ == "__main__":
    main()
