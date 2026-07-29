"""Autonomous paper trader (Account B) — trades the NIFTY-50 universe on its
own analysis with Rs 5000, and learns from measured outcomes.

Learning loops, recomputed every run:
  * source weights — from the graded-signals table scoreboard.py maintains:
    sources whose signals predict badly lose influence on buy decisions
  * sector weights — experiential: every closed trade nudges its sector up
    (win, x1.05) or down (loss, x0.92); repeated mistakes shrink future bets
  * every closed trade is journaled in bot_portfolio.json -> "closed" with
    entry thesis, exit reason and P&L — the bot's mistake ledger

Buy: score = sentiment x source_weight x sector_weight >= 0.55, plus the same
checks the human account gets (2+ outlets, 2+ distinct stories, not already up
2.5% today), max 6 positions, max 20% of equity each, max 2 per sector.
Exits: -7% stop, +15% target, 15-day time stop, or strong negative news.
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from alerts import load_sectors
from newslib import distinct_stories
from paper_portfolio import FEES_PCT, live_price

BASE = Path(__file__).parent
LEDGER = BASE / "bot_portfolio.json"
START_CASH = 5000.0

CANDIDATE_HOURS = 6
MIN_SENT = 0.5
BUY_SCORE = 0.55
STOP_PCT, TARGET_PCT, MAX_HOLD_DAYS = -7.0, 15.0, 15
MAX_POSITIONS, POSITION_FRAC, SECTOR_MAX = 6, 0.20, 2
PRICED_IN_MOVE = 2.5
REBUY_COOLDOWN_DAYS = 3


def load():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"cash": START_CASH, "positions": {}, "trades": [], "closed": [],
            "sector_weights": {}}


def source_weights(con):
    """Trust per source, learned from graded 1-day outcomes (neutral if <5 samples)."""
    try:
        rows = con.execute(
            "SELECT source, AVG(hit), COUNT(*) FROM graded GROUP BY source").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {s: max(0.6, min(1.4, 2 * hr)) for s, hr, n in rows if n >= 5}


def close_position(p, sym, price, reason, sectors, now_iso):
    pos = p["positions"].pop(sym)
    proceeds = price * pos["qty"]
    fee = proceeds * FEES_PCT
    p["cash"] += proceeds - fee
    pnl_pct = (price / pos["avg_cost"] - 1) * 100
    win = pnl_pct > 0
    sec = sectors.get(sym, "")
    w = p["sector_weights"].get(sec, 1.0)
    p["sector_weights"][sec] = round(max(0.5, min(1.3, w * (1.05 if win else 0.92))), 3)
    p["trades"].append({"time": now_iso, "action": "sell", "symbol": sym,
                        "qty": pos["qty"], "price": round(price, 2),
                        "fee": round(fee, 2), "reason": reason})
    p["closed"].append({"time": now_iso, "symbol": sym, "sector": sec,
                        "entry": round(pos["avg_cost"], 2), "exit": round(price, 2),
                        "pnl_pct": round(pnl_pct, 2), "win": win, "reason": reason,
                        "thesis": pos.get("thesis", "")})
    print(f"BOT SOLD {pos['qty']} x {sym} @ Rs {price:.2f} ({pnl_pct:+.2f}%) — {reason}")


def main():
    p = load()
    sectors = load_sectors()
    con = sqlite3.connect(BASE / "news.db")
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    sw = source_weights(con)

    prices = {}
    for sym in list(p["positions"]):
        try:
            prices[sym] = live_price(sym)
        except (Exception, SystemExit):
            pass

    # --- exits first ---
    news_since = (now - timedelta(hours=CANDIDATE_HOURS)).isoformat()
    for sym, pos in list(p["positions"].items()):
        price = prices.get(sym)
        if not price:
            continue
        pct = (price / pos["avg_cost"] - 1) * 100
        held_days = (now - datetime.fromisoformat(pos["opened"])).days
        worst = con.execute(
            "SELECT MIN(COALESCE(t.llm_sent, a.sentiment)) "
            "FROM article_tickers t JOIN articles a ON a.link = t.link "
            "WHERE t.symbol = ? AND t.in_title = 1 "
            "AND COALESCE(a.noise, 0) = 0 AND a.fetched_at >= ?",
            (sym, news_since)).fetchone()[0]
        if pct <= STOP_PCT:
            close_position(p, sym, price, f"stop-loss at {pct:+.1f}%", sectors, now_iso)
        elif pct >= TARGET_PCT:
            close_position(p, sym, price, f"target hit at {pct:+.1f}%", sectors, now_iso)
        elif held_days >= MAX_HOLD_DAYS:
            close_position(p, sym, price, f"time stop after {held_days}d ({pct:+.1f}%)",
                           sectors, now_iso)
        elif worst is not None and worst <= -0.5:
            close_position(p, sym, price, f"negative news ({worst:+.2f}) at {pct:+.1f}%",
                           sectors, now_iso)

    # --- candidate scan ---
    # Per-(headline, company) rows, not article-level sentiment. The old query
    # stamped one score on every company an article named, so "Elara Securities
    # prefers ICICI Bank over HDFC Bank" was a BUY signal for HDFC Bank. Noise
    # rows (tracker pages, listicles, index wraps) and body-only mentions are
    # excluded — both measured as anti-predictive on 2026-07-30.
    arts = con.execute(
        "SELECT a.source, a.title, COALESCE(t.llm_sent, a.sentiment) AS sent, "
        "       t.symbol "
        "FROM article_tickers t JOIN articles a ON a.link = t.link "
        "WHERE t.in_title = 1 AND COALESCE(a.noise, 0) = 0 "
        "  AND COALESCE(t.llm_sent, a.sentiment) >= ? AND a.fetched_at >= ?",
        (MIN_SENT, news_since)).fetchall()
    cand = {}
    cooldown = (now - timedelta(days=REBUY_COOLDOWN_DAYS)).isoformat()
    for source, title, sent, sym in arts:
        if sym in p["positions"]:
            continue
        if any(t["symbol"] == sym and t["time"] >= cooldown for t in p["trades"]):
            continue
        score = sent * sw.get(source, 1.0) * p["sector_weights"].get(
            sectors.get(sym, ""), 1.0)
        if score >= BUY_SCORE and (sym not in cand or score > cand[sym][0]):
            cand[sym] = (score, title, source)

    # --- buys, best score first ---
    day_ago = (now - timedelta(hours=24)).isoformat()
    for sym, (score, title, source) in sorted(cand.items(), key=lambda kv: -kv[1][0]):
        if len(p["positions"]) >= MAX_POSITIONS:
            break
        sec = sectors.get(sym, "")
        if sum(1 for s in p["positions"] if sectors.get(s, "") == sec) >= SECTOR_MAX:
            continue
        # Confirmation must come from real news: counting tracker pages and
        # listicle previews as "independent coverage" is how a wire reprint
        # passed as two outlets agreeing.
        cov = con.execute(
            "SELECT a.source, a.title FROM article_tickers t "
            "JOIN articles a ON a.link = t.link "
            "WHERE t.symbol = ? AND t.in_title = 1 AND COALESCE(a.noise, 0) = 0 "
            "AND a.fetched_at >= ? AND ABS(COALESCE(t.llm_sent, a.sentiment)) >= 0.25",
            (sym, day_ago)).fetchall()
        if len({s for s, _ in cov}) < 2 or distinct_stories([t for _, t in cov]) < 2:
            continue
        try:
            import yfinance as yf
            closes = yf.Ticker(sym).history(period="2d")["Close"].dropna()
            if not len(closes):
                continue
            price = float(closes.iloc[-1])
            move = (price / float(closes.iloc[-2]) - 1) * 100 if len(closes) >= 2 else 0.0
        except Exception:
            continue
        if move >= PRICED_IN_MOVE:
            continue
        eq = p["cash"] + sum(prices.get(s, p["positions"][s]["avg_cost"]) * p["positions"][s]["qty"]
                             for s in p["positions"])
        budget = min(p["cash"], eq * POSITION_FRAC)
        qty = int(budget // (price * (1 + FEES_PCT)))
        if qty < 1:
            continue
        cost = price * qty
        fee = cost * FEES_PCT
        p["cash"] -= cost + fee
        p["positions"][sym] = {"qty": qty, "avg_cost": (cost + fee) / qty,
                               "opened": now_iso, "sources": [source],
                               "thesis": title[:100]}
        p["trades"].append({"time": now_iso, "action": "buy", "symbol": sym,
                            "qty": qty, "price": round(price, 2),
                            "fee": round(fee, 2),
                            "reason": f"score {score:.2f} ({source}): {title[:80]}"})
        prices[sym] = price
        print(f"BOT BOUGHT {qty} x {sym} @ Rs {price:.2f} (score {score:.2f}) — {title[:70]}")

    LEDGER.write_text(json.dumps(p, indent=2))

    # --- status & brain report ---
    total = p["cash"] + sum(prices.get(s, pos["avg_cost"]) * pos["qty"]
                            for s, pos in p["positions"].items())
    print(f"\nBot equity: Rs {total:,.2f} ({100 * (total - START_CASH) / START_CASH:+.2f}% "
          f"since start) — cash Rs {p['cash']:,.2f}, {len(p['positions'])} position(s)")
    for s, pos in p["positions"].items():
        pr = prices.get(s, pos["avg_cost"])
        print(f"  {s:<15} {pos['qty']:>3} @ {pos['avg_cost']:.2f} -> {pr:.2f} "
              f"({100 * (pr / pos['avg_cost'] - 1):+.2f}%)")
    closed = p["closed"]
    if closed:
        wins = sum(1 for c in closed if c["win"])
        print(f"Record: {wins}/{len(closed)} wins, "
              f"avg {sum(c['pnl_pct'] for c in closed) / len(closed):+.2f}% per trade")
    if sw:
        ranked = sorted(sw.items(), key=lambda kv: -kv[1])
        print("Learned source trust — high:",
              ", ".join(f"{s} {w:.2f}" for s, w in ranked[:3]))
        print("                       low:",
              ", ".join(f"{s} {w:.2f}" for s, w in ranked[-3:]))
    if p["sector_weights"]:
        print("Learned sector weights:", json.dumps(p["sector_weights"]))
    con.close()


if __name__ == "__main__":
    main()
