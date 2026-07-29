# LESSONS — what was broken, why, and what not to do again

> **Purpose.** A permanent record of every real defect found in this project, in
> a form that survives being moved to another machine, another repo, or another
> model. Read this before changing scoring, grading, or trading logic.
>
> **Also a training set.** Each entry is a worked example of a bug class that
> looks fine in code review and only shows up in the measurements. If a model is
> ever trained on this project (see [ROADMAP_ML.md](ROADMAP_ML.md)), these are
> the negative examples.
>
> **Rule for future entries:** only add something you actually measured or
> reproduced. "Might be a problem" belongs in an issue, not here. Append newest
> last, keep the numbers.

---

## The one-line version

Every serious bug in this project so far belonged to one of five families:

1. **Wrong unit of analysis** — scoring an article when the thing you trade is a
   company.
2. **Contamination that flatters** — junk that *raises* your measured accuracy,
   so cleaning it makes the numbers look worse and feel wrong.
3. **Impossible fills** — acting at prices that were never available.
4. **Silent NaN** — a float that is neither a number nor falsy, sailing past
   every guard until it dies somewhere unrelated.
5. **Monitoring that cannot go red** — a green tick wired to a proxy instead of
   to the thing you actually care about.

When something new breaks, check these five first.

---

## L1 — One sentiment score stamped onto every company in an article

**Found:** 2026-07-30. **Severity: critical — it drove real buy decisions.**

`monitor.py` scored an ARTICLE and wrote that number against every ticker it
matched. So this headline:

> "Elara Securities prefers ICICI Bank, PSU banks **over HDFC Bank** for growth"

scored **+0.73** and was recorded as +0.73 for *both* ICICIBANK **and**
HDFCBANK — and the autotrader bought HDFC Bank on it. About 20% of matched
articles named 2+ companies.

**Why it survived so long:** the code is obviously correct if you think the unit
is an article. Nothing crashes. The scoreboard still produces a plausible number.

**Fix:** `article_tickers` table, one row per (article, company); the LLM scores
each pair independently and is explicitly told that news good for a rival is
NEGATIVE for the named company.

**Rule:** the unit of analysis must match the unit of the decision. You trade a
company, so you score a company.

---

## L2 — The junk was *inflating* the hit rate, not diluting it

**Found:** 2026-07-30. **Severity: high — it made a dead signal look alive.**

Auto-generated pages ("*X* Share Price Highlights: *X* Stock Price History"),
listicle previews ("Stocks to Watch today: …"), and index wraps were **24% of all
matched articles**. The intuition is that noise drags accuracy down. Measured:

| | 1-day hit rate |
|---|---|
| tracker/wrap pages | **69%** |
| real company news | **60%** |

The junk scored *better*, because it is **backward-looking** — a page titled
"Nestle India shows resilience with today's return" is a description of a move
that already happened, and moves have mild next-day continuation. It was
measuring momentum and calling it news.

**Fix:** `newslib.classify_noise` flags them; scoreboard and export exclude them.

**Rule:** if removing bad data makes your metric *worse*, that is evidence the
metric was being propped up by the bad data — not a reason to put it back. Ask
what a row would score if the model knew nothing, and be suspicious of anything
that beats the real signal.

**Corollary — two filters, not one.** Structural non-news (tracker pages) is a
regex job. "Is this new information or a description of an existing move" is a
reading-comprehension job and belongs to the LLM (`llm_novel`). Do not try to do
the second with a regex; do not waste LLM budget on the first.

---

## L3 — Companies named only in the body blurb are anti-predictive

**Found:** 2026-07-30. **Severity: high.**

Tickers were matched against title **+** summary, so any company name-dropped in
an article's blurb got tagged with the headline's sentiment. Split by where the
match occurred:

| match location | n | beat NIFTY | ±95% |
|---|---|---|---|
| body blurb only | 95 | **32.6%** | 9.4 |
| headline | 360 | 51.1% | 4.8 |

Body-only mentions are not merely weak — the entire confidence interval sits
**below** coin-flip. They were reliably wrong.

**Fix:** `match_tickers` records `in_title`; export and autotrader require it.
They are still collected, as ML features and negative examples.

**Rule:** record *where* a match came from, not just that it matched. The
provenance turned out to be worth more than the sentiment score.

---

## L4 — The news universe didn't overlap the stocks being traded

**Found:** 2026-07-30. **Severity: critical — the entire feature was a no-op.**

The monitor watched 56 NIFTY-50 names. The trading bot picks momentum leaders
from 147, which skew to PSU/metal/midcap names — NATIONALUM, ADANIPOWER, BHEL,
NMDC, SAIL. Result:

- universe overlap: **54 / 147**
- names the bots actually held that news could see: **5 / 16**
- names the ₹1,000 news-fused bot held that news could see: **0 / 2**

The "news + momentum" bot had been running for weeks and was *provably* running
pure momentum, because news could not speak about anything it owned.

**Fix:** `build_tickers.py` generates `tickers.csv` from the trading bot's own
universe file. Overlap now 147/147, held-name coverage 16/16.

**Rule:** when two systems are wired together, test the *intersection*, not each
side. Both were individually healthy and the integration was empty.

---

## L5 — Trading while the market was shut

**Found:** 2026-07-30. **Severity: critical — invalidates the entire track record.**

The autotrader ran on every pipeline invocation and had no concept of session
hours. `live_price()` returns the last close when the market is closed, so the
bot happily "traded" at prices it could never have obtained. Audit of its first
41 trades:

- **36 of 41 (88%)** executed while the NSE was shut
- **13 of them on Sunday 2026-07-26**, at Friday's closing prices
- one buy at **02:04 IST**, from the live cloud pipeline

Everything the bot reported before that date — +0.93%, 4/18 wins — is
**structurally invalid, not just noisy.** Do not compare performance across the
2026-07-30 boundary.

**The trap that nearly replaced it:** the obvious fix is "trade at 08:15, right
after the pre-open news sweep." That is *worse*: it buys at yesterday's close
using news that broke after that close — textbook look-ahead bias, and it would
have produced a beautiful, completely untradeable equity curve.

**Fix:** `market_phase()` splits every run into `plan` (pre-open: queue orders,
never fill), `trade` (session: re-validate against fresh prices/news, then fill),
and `closed` (news gathering only). Orders are only valid for their own open.

**Rule:** a paper trade is only honest if a real order could have been filled at
that price at that moment. Ask "could I actually have got this fill?" before
trusting any backtest or paper number.

**Track record reset, 2026-07-30.** The ledger was archived verbatim to
`bot_portfolio_invalid.json` and restarted at a clean Rs 5,000. Measurement
begins at the next session open; every fill from here is inside session hours at
an obtainable price. Note what did and did not survive:
- **sector weights: reset** — they were learned from the invalid closed trades.
- **source trust: kept** — it comes from the `graded` table, which grades news
  against real stock moves and never touched the bot's fills. It was never
  contaminated.

That split is worth remembering: when invalidating results, trace which learned
parameters actually depended on the broken path. Resetting everything would have
thrown away four weeks of legitimate source grading.

---

## L6 — Syndication counted as independent confirmation

**Found:** 2026-07-30. **Severity: medium — it inverted a safety check.**

The exporter counted **distinct outlets** as `sources`, and the trading bot
required `sources >= 2` before acting. But one PTI/Reuters wire story reprinted
by eight papers produced `sources: 8` — read as overwhelming confirmation when it
is a single claim from a single newsroom. The check meant to demand independence
was rewarding virality.

**Fix:** `newslib.cluster_titles` groups near-identical headlines; `sources` now
counts independent story clusters.

**Rule:** a confirmation check must count independent *evidence*, not independent
*publishers*.

---

## L7 — Silent NaN, three times in one codebase

**Found:** 2026-07-30. **Severity: medium — one would have killed the alert step in production.**

yfinance returns placeholder rows with `NaN` closes (mid-session, or for a bar it
later revises away). `NaN` is a `float`, and **`bool(float('nan'))` is `True`** —
so it walks straight through `if price:` guards and dies much later at
`int(cap // price)` with "cannot convert float NaN to integer", far from the
cause. Three separate sites had it: `scoreboard.horizon_returns`,
`alerts.opportunity_checks`, `autotrader`'s buy path, plus
`paper_portfolio.live_price`.

It also silently produced a scoreboard full of `+nan` move percentages that were
being counted as real grades.

**Fix:** `.dropna()` at every point price data enters, plus explicit
length checks.

**Rule:** guard NaN where the data enters, never where it is used — and remember
that truthiness checks do not catch it.

---

## L8 — Derived data committed instead of regenerated

**Found:** 2026-07-30. **Severity: low, but it would have bitten on the next clone.**

`news.db` is committed to the repo, so a schema/derived-column change locally and
a fresh checkout in CI could disagree — the same class of drift that had already
frozen every scrape for ~3.5 weeks in July when a missing `llm_sent` column
crashed the insert.

**Fix:** all derived columns (`tickers`, `noise`, `after_hours`, `in_title`) are
rebuilt by `migrate.py`, which runs as **pipeline step 1** on every run (8.8s,
idempotent). The cloud self-heals after any `tickers.csv` or noise-rule change.
`db.py` is the single schema definition with idempotent migration.

**Rule:** anything derivable should be derived on a schedule, not committed and
trusted. Keep raw data; regenerate everything else.

**Sub-lesson:** a rescan that only ever `INSERT OR IGNORE`s can add matches but
never retire them. When ZOMATO.NS was renamed ETERNAL.NS, the old rows kept
exporting a live signal for a delisted ticker. A rebuild must delete what no
longer matches.

---

## L9 — Measuring against the wrong baseline

**Found:** 2026-07-30. **Severity: medium — it hid how bad things were.**

Hit rate was reported against an "always-bull" baseline (what fraction of moves
were simply up). That is better than comparing to 50%, but it drifts with the
market and is hard to reason about. Reported honestly:

```
1d   61.1% hit   vs   63.5% always-bull baseline    <- BELOW the baseline
```

The system was *worse than buying anything at random*, while a 61% number looked
respectable in isolation.

**Fix:** grade on **excess return vs NIFTY** over the same window. Market drift
cancels out, so the baseline is a clean, unmoving 50%.

**Rule:** pick a baseline that cannot move on its own. Always print `n` and the
confidence interval next to any rate — a 5-point improvement on n=95 is noise.

---

## L10 — A green tick that could not go red

**Found:** 2026-07-30. **Severity: critical — it is why L-class bugs survive for weeks.**

`run_pipeline.py` ran each step with `subprocess.run`, wrote `[FAILED exit N]`
into the digest when one crashed… and then **always exited 0**. So:

- GitHub Actions showed a green tick on every run, including broken ones
- cron-job.org showed 100% success — it only sees whether GitHub *accepted the
  trigger*, never whether the pipeline worked
- the only evidence was a line buried in a digest file nobody reads every hour

This is the mechanism behind the July 2026 outage: a column-count bug crashed the
scrape on **407 of 417 runs over ~3.5 weeks** while every dashboard stayed green.
The bug was not hard to find. Nothing ever said to go looking.

**Fix:** `run_pipeline.py` exits 1 when any step fails (after writing the digest,
so the evidence survives); the workflow commits with `if: always()` and opens a
GitHub issue on failure, which GitHub emails.

**Rule:** **silence must never mean success.** Any unattended job needs a signal
that can actually go red, and that signal must be tied to the thing you care
about — not to a proxy one layer away. Ask of every monitor: "what exactly would
have to break for this to stay quiet?"

---

## Things that were RIGHT and should not be "fixed"

Preserved deliberately; do not undo these in a cleanup pass.

- **Fail-soft news loading** (`trading-bot/src/news_signal.py`): any error yields
  `{}` and the bot runs pure momentum. News is an overlay, never a dependency.
- **Never delete data, only flag it.** `noise=1` rows stay. Today's junk is
  tomorrow's negative training example, and deletion destroys the ability to
  re-measure an old decision.
- **The A/B design** (`news1k` vs `A`, identical but for the news overlay) is the
  correct shape for proving the overlay does anything. It just needed L4 fixed
  before it could produce a signal.
- **Confidence intervals and a stated kill criterion** in ROADMAP_ML.md. The
  ability to conclude "no edge exists here" is the feature that stops this
  becoming a money pit.

---

## Progress log

- **2026-07-30** — L1–L9 recorded. All fixed and deployed in the same session.
  Bot ledger reset to a clean Rs 5,000 (old ledger archived, not deleted);
  performance measurement starts from the next NSE open.
  L5's fix (session-aware trading) resets the bot's track record from this date.
