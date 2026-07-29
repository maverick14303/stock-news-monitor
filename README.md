# Stock News Monitor

Scrapes trusted Indian financial news feeds, matches headlines to NSE tickers,
scores sentiment, and — the important part — **scores itself** against what the
stock actually did the next trading day.

The goal is not prediction. It is measurement: does news sentiment from these
sources carry any next-day signal at all? The scoreboard answers that with your
own collected data, at zero financial risk.

## The unit of analysis is a (headline, company) PAIR

Not an article. An article naming four companies produces four independently
scored rows, because "Elara Securities prefers ICICI Bank over HDFC Bank" is
bullish for one and bearish for the other. Scoring the article once and stamping
that number on every company it mentions was the original design and the single
biggest source of garbage.

## Files

- `monitor.py` — scrape all feeds in `config.json`, dedupe, match tickers
  (recording whether each company was named in the **headline** or only in the
  body blurb), flag noise, score VADER, store in `news.db`.
- `newslib.py` — shared classification: the noise regex, syndication
  clustering, after-hours detection, sentiment blending. One definition, used
  by every script.
- `db.py` — the single schema definition + idempotent migration.
- `migrate.py` — re-scan all history against the current `tickers.csv` and noise
  rules. Runs as pipeline step 1 so the cloud self-heals after any rule change.
- `llm_analyst.py` — score each (headline, company) pair with Gemini: impact
  `llm_sent` **and** `llm_novel` (is this new information, or just a
  description of a move that already happened?).
- `scoreboard.py` — grade past signals against actual 1/3/5-day moves, reported
  as **excess return vs NIFTY** (clean 50% baseline) as well as raw direction,
  split by after-hours/in-session, novel/descriptive, headline/body.
  Also writes the `labels` ML dataset.
- `export_signal.py` — publish `news_signal.json` for the trading bot.
- `autotrader.py` — the bot: an autonomous Rs 5000 paper trader. Scores signals
  as sentiment x learned source trust x learned sector weight, exits on
  -7%/+15%/15-day/negative-news rules, journals every closed trade.
- `build_tickers.py` — regenerate `tickers.csv` from the trading bot's universe.
- `paper_portfolio.py` — retired as an account (2026-07-30); still the shared
  price/fee helper `alerts.py` and `autotrader.py` import.
- `ROADMAP_ML.md` — the plan for turning the dataset into a model. Read before
  proposing any ML work.
- `LESSONS.md` — every real defect found, with the measurements. **Read this
  before changing scoring, grading or trading logic.**

## When the bot is allowed to trade

The pipeline gathers news on all 10 runs. The **bot** only acts inside the NSE
session, because a paper fill is only honest if a real order could have been
filled at that price at that moment:

| run | phase | what the bot does |
|---|---|---|
| 08:15 pre-open | `plan` | reads the overnight news, **queues** intended buys — no fills |
| 09:30–14:30 | `trade` | re-checks the plan against fresh prices/news, then fills; exits run every pass |
| everything else | `closed` | news gathering only, zero transactions |

Buying at 08:15 would fill at *yesterday's close* using news that broke after it
— look-ahead bias. See `LESSONS.md` L5, which includes the audit showing 88% of
the bot's first 41 trades executed while the market was shut.

## Two accounts, not three

The **bot** (self-trading) versus the **NIFTY 50 shadow**. The old
"you + Claude" manual account was retired on 2026-07-30: it needed research time
Ankit didn't have, so its P&L measured nothing.

## Usage

```
python run_pipeline.py       # everything, saved to digests/
python migrate.py            # re-scan history after editing tickers.csv
python build_tickers.py      # regenerate tickers.csv from the bot universe
python scoreboard.py         # grade past signals + refresh the ML dataset
```

## Automation

GitHub Actions runs `run_pipeline.py` on GitHub's servers and commits the
results back; no local device needs to be on. Runs are triggered by a
cron-job.org job calling the workflow_dispatch API (GitHub's native cron lags
5-15 min and occasionally drops runs).

**Schedule (IST, weekdays)** — 10 runs, replacing the old hourly 07:00–23:00:

| Time | Why |
|---|---|
| **08:15** | **Pre-open sweep — the important one.** Overnight Indian news and the US close land here, and nobody in India can trade them until 09:15. The only window where news may genuinely not be priced in. |
| 09:30–14:30 hourly | NSE session |
| 15:45 | Post-close sweep — must land before trading-bot's 16:15 run |
| 20:00 | US open |
| 02:30 | US close |

Weekends: 10:00 and 20:00 only. The US times are chosen to fall after the event
in both US summer and winter time, so daylight saving never needs a cron edit.
Read the latest report at `digests/LATEST.md` in the repo
(github.com/maverick14303/stock-news-monitor), or trigger a manual run from
the Actions tab.

When `alerts.py` finds something notable — news on a held stock (|s| >= 0.4),
a strong signal on any tracked stock (|s| >= 0.7), or a macro shock headline —
the workflow opens a GitHub issue, which GitHub emails to the repo owner.
Tune thresholds at the top of `alerts.py`. Feeds marked `"global": true` in
`config.json` (13 international sources) feed macro alerts but skip ticker
matching to avoid false name collisions.

The cloud repo is the source of truth. Before working locally, `git pull`.

A disabled Windows scheduled task (**StockNewsMonitor**) remains as backup;
re-enable with `Enable-ScheduledTask StockNewsMonitor` if GitHub ever fails.

## Trust & risk features

- **NIFTY shadow**: every status/email compares the portfolio against the same
  Rs 5000 put in the index on the start date. Beating the market, not just
  making money, is the bar.
- **Fees**: paper trades pay ~0.25% per side, folded into cost basis.
- **Session-gated trading**: the bot transacts only inside NSE hours, on a real
  trading day (holiday calendar + a live index-bar check). It plans pre-open and
  fills after the open, so no paper fill is at a price it could not have got.
- **Verdict journal**: every ✅ PASSES the alert engine issues is logged and
  graded on 5-day outcomes by scoreboard.py.
- **Scoreboard rigor**: excess return vs NIFTY at 1/3/5-day horizons, with
  Bonferroni-widened confidence intervals (the table runs ~9 simultaneous tests,
  so plain 95% would produce a false positive ~37% of the time) and one
  pre-registered hypothesis.
- **Weekly report** (Sunday 6 PM IST): week stats plus feed-health check
  flagging sources silent 48h+.
- **Syndication-aware confirmation**: near-identical wire-copy headlines are
  clustered so "N outlets" means independent coverage, not reprints.

## Honest limitations (read this)

1. **News is priced in fast.** By the time a headline reaches an RSS feed, the
   market has usually reacted. Measured 2026-07-30 after a full correctness
   audit: **48.7% excess return vs NIFTY at 1 day (n=712, ±5.2)** — a coin flip.
   After-hours 50.8% vs in-session 45.1%; both intervals contain 50%.
   **There is no measurable edge yet.** An earlier, more encouraging version of
   these numbers was an artifact of a 5½-hour timestamp bug (LESSONS.md L11) —
   which is exactly why the scoreboard reports confidence intervals.
2. **A hit rate is not money.** A signal that is right 55% of the time but only
   by 0.3% per trade still loses against a ~2.5% round-trip cost. Grade on net
   outcome, not direction.
3. **This feeds a real-money bot.** `news_signal.json` is consumed by
   trading-bot's fused momentum+news variants. Changes here affect trading
   decisions there — check `ROADMAP_ML.md §4` before altering the export format.
