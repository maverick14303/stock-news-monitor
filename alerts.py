"""Scan articles fetched in the last window for events worth an email.

Three triggers:
  HOLDING     — news matched to a ticker you hold, |sentiment| >= 0.4
  OPPORTUNITY — strong signal (|sentiment| >= 0.7) on any tracked NSE stock
  MACRO       — shock words + market scope in any headline, incl. global feeds

Writes ALERT.md when anything fires; the cloud workflow turns that file into
a GitHub issue, which GitHub emails to the repo owner. Prints and exits
quietly when nothing qualifies.
"""
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent
OUT = BASE / "ALERT.md"

WINDOW_HOURS = 2.5
HELD_THRESHOLD = 0.4
OPPORTUNITY_THRESHOLD = 0.7
MAX_ITEMS = 12

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


def main():
    held = set(json.loads((BASE / "portfolio.json").read_text())["positions"])
    since = (datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)).isoformat()
    con = sqlite3.connect(BASE / "news.db")
    rows = con.execute(
        "SELECT source, title, sentiment, tickers FROM articles WHERE fetched_at >= ?",
        (since,)).fetchall()
    con.close()

    alerts, seen_titles = [], set()
    for source, title, sent, tickers in rows:
        if title in seen_titles:
            continue
        tset = set(tickers.split(",")) if tickers else set()
        is_wrap = bool(MARKET_WRAP.search(title))
        kind = None
        if tset & held and abs(sent) >= HELD_THRESHOLD and not is_wrap:
            kind = "HOLDING"
        elif tset and abs(sent) >= OPPORTUNITY_THRESHOLD and not is_wrap:
            kind = "OPPORTUNITY"
        elif MACRO_EVENT.search(title) and MACRO_SCOPE.search(title):
            kind = "MACRO"
        if kind:
            seen_titles.add(title)
            alerts.append((kind, sent, ",".join(tset), title, source))

    # HOLDING first, then by signal strength
    alerts.sort(key=lambda a: (a[0] != "HOLDING", -abs(a[1])))
    alerts = alerts[:MAX_ITEMS]

    if not alerts:
        OUT.unlink(missing_ok=True)
        print("No alerts this window.")
        return

    lines = [f"{len(alerts)} item(s) in the last {WINDOW_HOURS:g}h that may affect your holdings.\n"]
    for kind, sent, tickers, title, source in alerts:
        tag = f" `{tickers}`" if tickers else ""
        lines.append(f"- **[{kind}]**{tag} ({sent:+.2f}) {title} — *{source}*")
    lines.append("\n_Paper portfolio only. Verify before acting; news may already be priced in._")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(alerts)} alert(s) written to ALERT.md")


if __name__ == "__main__":
    main()
