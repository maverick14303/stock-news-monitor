# Stock News Monitor

Scrapes trusted Indian financial news feeds, matches headlines to NSE tickers,
scores sentiment, and — the important part — **scores itself** against what the
stock actually did the next trading day.

The goal is not prediction. It is measurement: does news sentiment from these
sources carry any next-day signal at all? The scoreboard answers that with your
own collected data, at zero financial risk.

## Files

- `monitor.py` — scrape all feeds in `config.json`, dedupe, match tickers,
  score sentiment (VADER), store in `news.db`, print new signals. Run daily
  (or several times a day — duplicates are skipped).
- `scoreboard.py` — for every past non-neutral signal, compare sentiment
  direction vs the stock's actual next-day move (via Yahoo Finance). Prints
  hit-rate overall and per source.
- `config.json` — the trusted feed list. Add/remove sources here.
- `tickers.csv` — symbol → name aliases used for headline matching.
- `news.db` — SQLite archive of everything collected (created on first run).

## Usage

```
python monitor.py            # collect news + print today's signals
python scoreboard.py         # grade past signals (needs 1+ day of history)
python paper_portfolio.py status   # Rs 500 virtual portfolio vs live prices
python run_pipeline.py       # all three, saved to digests/
```

## Automation

GitHub Actions runs `run_pipeline.py` on GitHub's servers and commits the
results back; no local device needs to be on. Runs are triggered every hour
from 7 AM to 11 PM IST by a cron-job.org job calling the workflow_dispatch
API (GitHub's native cron lags 5-15 min and occasionally drops runs).
Read the latest report at `digests/LATEST.md` in the repo
(github.com/maverick14303/stock-news-monitor), or trigger a manual run from
the Actions tab.

When `alerts.py` finds something notable — news on a held stock (|s| >= 0.4),
a strong signal on any tracked stock (|s| >= 0.7), or a macro shock headline —
the workflow opens a GitHub issue, which GitHub emails to the repo owner.
Tune thresholds at the top of `alerts.py`. Feeds marked `"global": true` in
`config.json` (15 international sources) feed macro alerts but skip ticker
matching to avoid false name collisions.

The cloud repo is the source of truth. Before working locally, `git pull`.
After local trades (`paper_portfolio.py buy/sell`), commit and push so the
cloud prices the right positions.

A disabled Windows scheduled task (**StockNewsMonitor**) remains as backup;
re-enable with `Enable-ScheduledTask StockNewsMonitor` if GitHub ever fails.

## Honest limitations (read this)

1. **News is priced in fast.** By the time a headline reaches an RSS feed,
   the market has usually reacted. Expect the hit-rate to hover near 50%.
   That result is the lesson, not a failure of the tool.
2. **VADER sentiment is crude for finance.** "Profit falls 19%, declares
   dividend" can score positive because of the word "dividend". Upgrading
   sentiment to an LLM pass is the first real improvement to make.
3. **This is a research/learning tool.** It produces no trade advice and
   should never be wired to real money.
