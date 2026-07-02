"""Paper-trading ledger with real-world constraints: whole shares, live NSE
prices, fixed starting capital of Rs 5000. No real money involved.

Usage:
    python paper_portfolio.py status
    python paper_portfolio.py buy TATASTEEL.NS 1
    python paper_portfolio.py sell TATASTEEL.NS 1
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LEDGER = Path(__file__).parent / "portfolio.json"
STARTING_CASH = 5000.0


def load():
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"cash": STARTING_CASH, "positions": {}, "trades": []}


def save(p):
    LEDGER.write_text(json.dumps(p, indent=2))


def live_price(symbol):
    hist = yf.Ticker(symbol).history(period="1d")
    if hist.empty:
        sys.exit(f"No price data for {symbol}")
    return float(hist["Close"].iloc[-1])


def record(p, action, symbol, qty, price):
    p["trades"].append({
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action, "symbol": symbol, "qty": qty, "price": round(price, 2),
    })


def buy(p, symbol, qty):
    price = live_price(symbol)
    cost = price * qty
    if cost > p["cash"]:
        sys.exit(f"Not enough cash: need Rs {cost:.2f}, have Rs {p['cash']:.2f}")
    pos = p["positions"].get(symbol, {"qty": 0, "avg_cost": 0.0})
    pos["avg_cost"] = (pos["avg_cost"] * pos["qty"] + cost) / (pos["qty"] + qty)
    pos["qty"] += qty
    p["positions"][symbol] = pos
    p["cash"] -= cost
    record(p, "buy", symbol, qty, price)
    print(f"BOUGHT {qty} x {symbol} @ Rs {price:.2f} (cost Rs {cost:.2f}, cash left Rs {p['cash']:.2f})")


def sell(p, symbol, qty):
    pos = p["positions"].get(symbol)
    if not pos or pos["qty"] < qty:
        sys.exit(f"You don't hold {qty} shares of {symbol}")
    price = live_price(symbol)
    p["cash"] += price * qty
    pos["qty"] -= qty
    if pos["qty"] == 0:
        del p["positions"][symbol]
    record(p, "sell", symbol, qty, price)
    print(f"SOLD {qty} x {symbol} @ Rs {price:.2f} (cash now Rs {p['cash']:.2f})")


def status(p):
    total = p["cash"]
    print(f"{'symbol':<15}{'qty':>4}{'avg cost':>10}{'now':>10}{'value':>10}{'P&L':>9}")
    for symbol, pos in p["positions"].items():
        price = live_price(symbol)
        value = price * pos["qty"]
        pnl = value - pos["avg_cost"] * pos["qty"]
        total += value
        print(f"{symbol:<15}{pos['qty']:>4}{pos['avg_cost']:>10.2f}{price:>10.2f}{value:>10.2f}{pnl:>+9.2f}")
    print(f"\nCash: Rs {p['cash']:.2f}")
    print(f"Total value: Rs {total:.2f}  ({total - STARTING_CASH:+.2f} vs Rs {STARTING_CASH:.0f} start, "
          f"{100 * (total - STARTING_CASH) / STARTING_CASH:+.2f}%)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("status", "buy", "sell"):
        sys.exit(__doc__)
    p = load()
    if sys.argv[1] == "status":
        status(p)
    else:
        symbol, qty = sys.argv[2].upper(), int(sys.argv[3])
        (buy if sys.argv[1] == "buy" else sell)(p, symbol, qty)
        save(p)


if __name__ == "__main__":
    main()
