"""Regenerate tickers.csv from the trading bot's universe.

Why this exists: the monitor watched 56 NIFTY-50 names while the trading bot
picks momentum leaders from 147 (NATIONALUM, ADANIPOWER, BHEL, NMDC...). Measured
2026-07-30: ZERO of the ₹1k news bot's holdings were in the news universe, so the
news overlay was a no-op on the exact stocks it was supposed to inform.

Company names come from yfinance (longName), so the alias list doesn't depend on
anyone typing 147 company names from memory. Curated aliases in CURATED win —
they encode brand names and abbreviations no API returns ("HUL", "L&T",
"Royal Enfield").

Aliases too generic to match safely on their own are listed in BLOCKLIST: "Titan",
"Apollo", "Century", "Sun", "Trent", "Page" are ordinary English words and would
fire on unrelated news. Those tickers keep only their unambiguous long forms.

Run this, eyeball the diff, then `python migrate.py` to re-scan history against
the new aliases.

Usage: python build_tickers.py [--out tickers.csv]
"""
import csv
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent
BOT_UNIVERSE = BASE.parent / "trading-bot" / "src" / "universe.py"

# Single words that are ordinary English or shared across many companies. An
# alias equal to one of these is dropped; longer aliases containing them are fine
# ("Titan Company" stays, "Titan" goes).
BLOCKLIST = {
    "titan", "apollo", "century", "sun", "trent", "page", "power", "grid",
    "india", "indian", "national", "bharat", "united", "new", "max", "one",
    "gail", "canara", "union", "central", "federal", "city", "prestige",
    "oil", "steel", "motors", "bank", "finance", "cement", "pharma", "tech",
    "life", "auto", "petroleum", "chemicals", "industries", "enterprises",
}

# Brand/abbreviation aliases no data source returns. Merged with generated ones.
CURATED = {
    "RELIANCE.NS": ["Reliance Industries", "RIL"],
    "TCS.NS": ["Tata Consultancy", "TCS"],
    "HDFCBANK.NS": ["HDFC Bank"],
    "ICICIBANK.NS": ["ICICI Bank"],
    "INFY.NS": ["Infosys"],
    "BHARTIARTL.NS": ["Bharti Airtel", "Airtel"],
    "SBIN.NS": ["State Bank of India", "SBI Bank"],
    "HINDUNILVR.NS": ["Hindustan Unilever", "HUL"],
    "LT.NS": ["Larsen & Toubro", "Larsen and Toubro", "L&T"],
    "KOTAKBANK.NS": ["Kotak Mahindra Bank", "Kotak Bank"],
    "MARUTI.NS": ["Maruti Suzuki", "Maruti"],
    "SUNPHARMA.NS": ["Sun Pharma", "Sun Pharmaceutical"],
    "TITAN.NS": ["Titan Company"],
    "ULTRACEMCO.NS": ["UltraTech Cement", "UltraTech"],
    "BAJFINANCE.NS": ["Bajaj Finance"],
    "ONGC.NS": ["ONGC", "Oil and Natural Gas Corporation"],
    "TMPV.NS": ["Tata Motors PV", "Tata Motors"],
    "TMCV.NS": ["Tata Motors CV"],
    "M&M.NS": ["Mahindra & Mahindra", "Mahindra and Mahindra", "M&M"],
    "HCLTECH.NS": ["HCL Technologies", "HCLTech", "HCL Tech"],
    "EICHERMOT.NS": ["Eicher Motors", "Royal Enfield"],
    "APOLLOHOSP.NS": ["Apollo Hospitals"],
    "DIVISLAB.NS": ["Divi's Lab", "Divis Lab", "Divi's Laboratories"],
    "HEROMOTOCO.NS": ["Hero MotoCorp", "Hero Moto"],
    "DRREDDY.NS": ["Dr Reddy", "Dr. Reddy", "Dr Reddy's"],
    "NESTLEIND.NS": ["Nestle India", "Nestle"],
    "ETERNAL.NS": ["Eternal", "Zomato", "Blinkit"],
    "NATIONALUM.NS": ["National Aluminium", "NALCO"],
    "ADANIPOWER.NS": ["Adani Power"],
    "ADANIENT.NS": ["Adani Enterprises"],
    "ADANIPORTS.NS": ["Adani Ports", "Adani Ports SEZ"],
    "ADANIGREEN.NS": ["Adani Green"],
    "BHEL.NS": ["Bharat Heavy Electricals", "BHEL"],
    "NMDC.NS": ["NMDC"],
    "SAIL.NS": ["Steel Authority of India", "SAIL"],
    "HINDZINC.NS": ["Hindustan Zinc"],
    "VEDL.NS": ["Vedanta"],
    "FEDERALBNK.NS": ["Federal Bank"],
    "MOTHERSON.NS": ["Samvardhana Motherson", "Motherson Sumi"],
    "NYKAA.NS": ["Nykaa", "FSN E-Commerce"],
    "AUROPHARMA.NS": ["Aurobindo Pharma"],
    "HINDPETRO.NS": ["Hindustan Petroleum", "HPCL"],
    "BPCL.NS": ["Bharat Petroleum", "BPCL"],
    "IOC.NS": ["Indian Oil", "IOCL"],
    "POWERGRID.NS": ["Power Grid Corporation", "PowerGrid"],
    "COALINDIA.NS": ["Coal India"],
    "PAYTM.NS": ["Paytm", "One 97 Communications"],
    # Blocklisted words kept deliberately: in an Indian markets feed these are
    # unambiguous, and global feeds skip ticker matching entirely.
    "TRENT.NS": ["Trent", "Westside", "Zudio"],
    "PAGEIND.NS": ["Page Industries", "Jockey India"],
    "GAIL.NS": ["GAIL", "GAIL India"],
    "CANBK.NS": ["Canara Bank"],
    "UNIONBANK.NS": ["Union Bank of India"],
}

# Sector labels drive autotrader's learned sector weights; unknown -> "other".
SECTOR_MAP = {
    "Technology": "it", "Financial Services": "financials",
    "Basic Materials": "metals", "Energy": "energy", "Utilities": "energy",
    "Consumer Cyclical": "consumer", "Consumer Defensive": "fmcg",
    "Healthcare": "pharma", "Industrials": "infra",
    "Communication Services": "telecom", "Real Estate": "infra",
}

_SUFFIXES = re.compile(
    r"\s+(limited|ltd\.?|corporation|corp\.?|company|co\.?|inc\.?|plc)$",
    re.IGNORECASE)


def bot_universe():
    """Read TICKERS out of the trading bot without importing pandas etc."""
    src = BOT_UNIVERSE.read_text(encoding="utf-8")
    names = re.search(r"_NAMES\s*=\s*\[(.*?)\]", src, re.DOTALL)
    if not names:
        raise SystemExit(f"could not parse _NAMES from {BOT_UNIVERSE}")
    # Strip comments first: universe.py documents the Tata Motors rename with a
    # quoted "TATAMOTORS" inside a comment, which a naive scan reads as a ticker.
    body = re.sub(r"#[^\n]*", "", names.group(1))
    return [n + ".NS" for n in re.findall(r'"([^"]+)"', body)]


def clean_aliases(raw, curated=()):
    """Drop blank, too-short, duplicate and blocklisted aliases.

    The blocklist guards AUTO-GENERATED names only. A curated alias is a
    deliberate human call ("Trent", "SAIL") and is always kept.
    """
    out, seen = [], set()
    for a in curated:
        # Verbatim: a curated alias is already the intended form. Suffix-stripping
        # it turns "Titan Company" into bare "Titan" — the exact generic-word
        # false positive the blocklist exists to prevent.
        a = (a or "").strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    for a in raw:
        a = _SUFFIXES.sub("", (a or "").strip()).strip(" .,")
        key = a.lower()
        if not a or len(a) < 3 or key in BLOCKLIST or key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def main():
    out_path = BASE / (sys.argv[sys.argv.index("--out") + 1]
                       if "--out" in sys.argv else "tickers.csv")
    symbols = bot_universe()
    # keep any curated symbol the bot universe doesn't carry (e.g. PAYTM)
    for extra in CURATED:
        if extra not in symbols:
            symbols.append(extra)
    print(f"Building aliases for {len(symbols)} symbols...")

    import yfinance as yf
    rows, no_data = [], []
    for i, sym in enumerate(symbols, 1):
        long_name = sector = None
        try:
            info = yf.Ticker(sym).get_info()
            long_name = info.get("longName") or info.get("shortName")
            sector = info.get("sector")
        except Exception:
            pass
        aliases = clean_aliases([long_name], curated=CURATED.get(sym, []))
        if not aliases:
            no_data.append(sym)
            continue
        rows.append({"symbol": sym, "aliases": "|".join(aliases),
                     "sector": SECTOR_MAP.get(sector, "other")})
        if i % 25 == 0:
            print(f"  ...{i}/{len(symbols)}")

    rows.sort(key=lambda r: r["symbol"])
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "aliases", "sector"])
        w.writeheader()
        w.writerows(rows)

    print(f"\nWrote {len(rows)} tickers to {out_path.name}.")
    if no_data:
        print(f"  no name resolved (skipped): {', '.join(no_data)}")
    print("Next: eyeball the file, then `python migrate.py` to rescan history.")


if __name__ == "__main__":
    main()
