# Stock digest — 2026-11-12 09:30

## Rescan history (tickers, noise, after-hours)

```
Rescanning 141008 articles against 259 alias patterns...
  ...2000
  ...4000
  ...6000
  ...8000
  ...10000
  ...12000
  ...14000
  ...16000
  ...18000
  ...20000
  ...22000
  ...24000
  ...26000
  ...28000
  ...30000
  ...32000
  ...34000
  ...36000
  ...38000
  ...40000
  ...42000
  ...44000
  ...46000
  ...48000
  ...50000
  ...52000
  ...54000
  ...56000
  ...58000
  ...60000
  ...62000
  ...64000
  ...66000
  ...68000
  ...70000
  ...72000
  ...74000
  ...76000
  ...78000
  ...80000
  ...82000
  ...84000
  ...86000
  ...88000
  ...90000
  ...92000
  ...94000
  ...96000
  ...98000
  ...100000
  ...102000
  ...104000
  ...106000
  ...108000
  ...110000
  ...112000
  ...114000
  ...116000
  ...118000
  ...120000
  ...122000
  ...124000
  ...126000
  ...128000
  ...130000
  ...132000
  ...134000
  ...136000
  ...138000
  ...140000

Done. 141008 articles rescanned.
  ticker-matched articles: 15170
  flagged as noise:        8084
  (article, company) pairs: 13024  — 0 awaiting LLM scoring
```

## News & signals

```
[OK] Economic Times Markets: 50 entries, 12 new
[OK] Economic Times Stocks: 50 entries, 0 new
[OK] Economic Times Economy: 50 entries, 0 new
[OK] Economic Times Industry: 50 entries, 0 new
[OK] LiveMint Markets: 35 entries, 1 new
[OK] LiveMint Companies: 35 entries, 3 new
[OK] LiveMint Money: 35 entries, 1 new
[OK] LiveMint Economy: 35 entries, 0 new
[OK] BusinessLine Markets: 60 entries, 0 new
[OK] BusinessLine Stock Markets: 60 entries, 0 new
[OK] BusinessLine Companies: 60 entries, 3 new
[OK] BusinessLine Economy: 60 entries, 0 new
[OK] Business Standard Markets: 35 entries, 3 new
[OK] Business Standard Companies: 35 entries, 4 new
[OK] Business Standard Finance: 35 entries, 0 new
[OK] Business Standard Economy: 35 entries, 0 new
[OK] NDTV Profit Latest: 20 entries, 9 new
[OK] TOI Business: 20 entries, 1 new
[OK] The Hindu Business: 60 entries, 0 new
[OK] The Hindu Economy: 60 entries, 0 new
[OK] News18 Business: 200 entries, 5 new
[OK] CNBC TV18 Markets: 200 entries, 4 new
[OK] Investing.com India Stocks: 10 entries, 10 new
[OK] ET International: 50 entries, 4 new
[OK] BBC Business: 56 entries, 0 new
[OK] BBC World: 29 entries, 1 new
[OK] Guardian Business: 39 entries, 0 new
[OK] Al Jazeera: 25 entries, 2 new
[OK] CNBC Top News: 30 entries, 3 new
[OK] CNBC World Markets: 30 entries, 0 new
[OK] MarketWatch Top: 10 entries, 0 new
[OK] NYT Business: 48 entries, 0 new
[WARN] Yahoo Finance: could not fetch (<unknown>:53:4: mismatched tag)
[OK] OilPrice: 15 entries, 0 new
[OK] DW Business: 20 entries, 0 new
[OK] SCMP Business: 50 entries, 1 new

67 new articles stored, 12 matched to tickers (11 flagged as tracker/listicle/index noise).

=== New signals ===
[BULLISH] BEL.NS  (+0.91) [off-hours] BEL To Gain From Defence Order Pipeline? UBS Sees Attractive Risk-Reward, Raises Target Price  — NDTV Profit Latest
[BULLISH] TATAPOWER.NS  (+0.87) [off-hours] Tata Power shares in focus after Q2 results. Why brokerages are bullish despite target price cuts  — Economic Times Markets
[BULLISH] ITC.NS  (+0.84)  ITC shares in focus after it raises Gold Flake cigarette prices for the second time this year  — CNBC TV18 Markets
[BULLISH] ADANIGREEN.NS,COFORGE.NS,LICI.NS  (+0.73) [off-hours] MSCI November 2026 Rejig: LIC, Adani Green, Coforge, Meesho Added As India's Weight Rises; PI Industries Dropped  — News18 Business
[BULLISH] TATAPOWER.NS  (+0.40) [off-hours] Tata Power eyes more state pacts to boost Mundra tariffs  — BusinessLine Companies
```

## LLM analyst (per-ticker)

```
LLM analyst: queued 7 pair(s) — 7 newest-first, 0 gradeable backlog (28d..1d, headline mentions).
  gemini gemini-2.5-flash: http 503
  gemini gemini-2.0-flash: http 404
LLM analyst: scored 7 (headline, company) pair(s) with baidu/ernie-5.5-flash-preview:free.
  novel & strong — ADANIGREEN.NS +0.60: MSCI November 2026 Rejig: LIC, Adani Green, Coforge, Meesho Added
  novel & strong — COFORGE.NS +0.60: MSCI November 2026 Rejig: LIC, Adani Green, Coforge, Meesho Added
```

## Signal scoreboard (last 30 days)

```
price fetch FAILED for ^NSEI: YFRateLimitError
[FAILED] benchmark ^NSEI unavailable — labels NOT written this run and no hit rates published. Excess-vs-NIFTY cannot be computed, and raw direction is not a substitute for it.
[FAILED exit 1]

```

## Bot account (self-trading)

```
No pre-open plan for today — no entries. (Entries are queued by the 08:15 run; exits still run.)

Bot equity: Rs 4,658.90 (-6.82% since start) — cash Rs 3,769.18, 1 position(s)
  MARICO.NS         1 @ 889.72 -> 889.72 (+0.00%)
Record: 11/32 wins, avg -1.09% per trade
Learned source trust — high: Business Standard Economy 1.36, LiveMint Companies 1.13, ET Markets Wrap 1.08
                       low: Economic Times Economy 0.80, Business Standard Finance 0.67, LiveMint Money 0.65
Learned sector weights: {"metals": 0.902, "consumer": 0.858, "energy": 0.606, "it": 0.92, "fmcg": 0.888, "financials": 0.903, "pharma": 0.846, "infra": 1.014}
```

## Alert scan

```
Alert written: 2 macro item(s)
```

## Sentiment export

```
Exported 78 tickers to news_signal.json (window 7d, 549 scored pairs).
  pipeline_ok=False data_through=2026-11-12T04:00:56+00:00 llm-scored 549/549 pairs (100%)
  failed steps: Signal scoreboard (last 30 days) (scoreboard.py exit 1)
  40 of 78 tickers are single-outlet or single-day (not corroborated — must not veto or sell).
  most negative: PRESTIGE.NS -0.70, CUMMINSIND.NS -0.60, BHARATFORG.NS -0.50
  most positive: SUNPHARMA.NS +0.90, TITAN.NS +0.90, DRREDDY.NS +0.90
```
