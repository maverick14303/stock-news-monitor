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
6. **Wrong frame of reference** — the right number in the wrong timezone,
   calendar or trading session. Nothing crashes; the effect just disappears.

When something new breaks, check these six first.

**The single most useful habit:** look for a number that is *impossible* rather
than merely disappointing. "0% of articles discovered within 2 hours, with hourly
polling" found a 5½-hour timezone bug that had corrupted every measurement in the
project since day one (L11). Disappointing numbers get rationalised; impossible
ones have mechanisms.

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

## L11 — Every timestamp was 5½ hours early, for the entire life of the project

**Found:** 2026-07-30 (audit). **Severity: critical — it invalidated the project's one positive result.**

`monitor.py` built publication times with:

```python
datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
```

`time.mktime()` interprets a `struct_time` as **local** time. feedparser always
returns `published_parsed` in **UTC**. The workflow sets `TZ: Asia/Kolkata`, so
every article was stamped **exactly 5h30m early** — silently, from day one, on
all 10 163 rows.

**How it surfaced:** not by reading the code. By noticing an impossible number —
**0% of articles were discovered within 2 hours of publication**, despite hourly
polling. The "6.4h median discovery lag" was not lag, it was the offset. After
repair: median 1.24h, 44% within the hour.

**What it cost:** `after_hours` is derived from that timestamp, so a 14:00 IST
in-session story was recorded as 08:30 IST and labelled *pre-open*. Correcting
the shift moved **696 of 1 134 pairs (61%) into a different bucket**. The
previously reported "after-hours 53.8% vs in-session 41.2%" — the only
encouraging result this project had produced — was mostly an artifact. Re-measured
on correct timestamps: **50.8% vs 45.1%**, both inside their confidence intervals.

**Fix:** `calendar.timegm()`, which is timezone-independent and therefore cannot
regress if someone changes `TZ`. Historical rows repaired by a one-time +5:30
shift, guarded by a `meta` flag (applying it twice would push everything 11 hours
into the future).

**Rule:** when a measurement is impossible rather than merely surprising, stop and
find the mechanism — do not explain it away. And never use `time.mktime()` on a
UTC struct; `calendar.timegm()` is the only correct pairing.

---

## L12 — The trading day is not the calendar day

**Found:** 2026-07-30. **Severity: critical — silently deleted the effect being measured.**

`scoreboard.py` did `datetime.fromisoformat(ts).date()` on a UTC timestamp.
Anything published 18:30–24:00 UTC is 00:00–05:30 IST *the next day*, so **28.5%
of pairs were graded against the wrong trading day** — and that window is exactly
the US session, the news we care most about.

Worse, even the IST date is wrong for overnight news. A story at 02:00 IST
Thursday must be baselined against **Wednesday's** close, because Thursday has
not opened. Using Thursday's close as the baseline measures Thu→Fri and discards
the opening gap — precisely the move an overnight signal is supposed to capture.
The bug systematically erased the effect it was meant to detect.

**Fix:** `newslib.signal_trading_day()` — convert to IST, roll back to the
previous session if the news arrived before the open, then roll back over
weekends and holidays.

**Rule:** for market data, "what day is this" is a domain question, not a
`datetime` question. Write it down as a named function with the reasoning in the
docstring.

---

## L13 — The session gate knew about weekends but not holidays

**Found:** 2026-07-30. **Severity: critical — L5 returning through the back door.**

`market_phase()` checked `weekday() >= 5`. In 2026 the NSE is shut on Republic
Day (Mon), Holi (Tue), Gandhi Jayanti (Fri), Christmas (Fri) and eleven other
weekdays. On each, the freshly-written "only trade when the market is open" gate
returned `"trade"` and would have filled at a stale close — the exact bug it had
just been built to prevent.

**Fix:** `NSE_HOLIDAYS` list + `is_trading_day()`, **plus** a second, self-maintaining
guard: before filling, confirm the NIFTY index actually has a bar for today. A
hand-maintained calendar will go stale; the data check catches an unlisted
closure without knowing about it, and only delays entries by one run if Yahoo is
merely slow.

**Rule:** a fix that depends on a hand-maintained list is half a fix. Pair it
with something derived from the data, and make the failure mode "do nothing".

---

## L14 — Aliases were legal names; headlines use short names

**Found:** 2026-07-30. **Severity: high — 20% of the universe was silently unreachable.**

`build_tickers.py` generated aliases from yfinance `longName`, stripping only
`Limited|Ltd|Corp` anchored at the end. So:

| ticker | alias generated | headlines say | mentions | matched |
|---|---|---|---|---|
| TATAPOWER | "The Tata Power Company" | "Tata Power" | 33 | **0** |
| LICI | "Life Insurance Corporation" | "LIC" | 224 | **0** |
| HINDALCO | "Hindalco Industries" | "Hindalco" | — | **0** |
| INDIGO | "InterGlobe Aviation" | "IndiGo" | 36 | 4 |

**29 of 148 tickers had never matched a single article in 10 000.**

**Fix:** `short_forms()` peels a leading "The" and trailing generic words
(Company/Industries/Technologies/India/…) — *after* suffix-stripping, since the
tail regex is end-anchored and "Havells India Limited" otherwise never reduces.
Every generated short form is still filtered through `BLOCKLIST`, so "Titan
Company" → "Titan" is correctly rejected (verified: of 13 "Titan" headlines the
real ones were *"financial titan J.P. Morgan"* and *"A Titan Story"*). Dead
tickers: 29 → 20, matched articles +19%.

**Rule:** generate identifiers the way the *source* writes them, not the way a
registry does. And validate coverage by asking "which entries never fire?" — a
config file full of plausible-looking rows tells you nothing.

---

## L15 — Deduping by URL stole source attribution

**Found:** 2026-07-30. **Severity: medium — it corrupted a learned parameter.**

Articles are keyed by link, so the first feed to carry a story owned
`articles.source` forever. 19 of The Hindu Economy's 60 current items were
credited to The Hindu Business purely because it appears earlier in
`config.json`. Learned source trust — which the bot multiplies into every buy
score — was therefore partly an artifact of file ordering.

**Fix:** `article_sources(link, source)` records every outlet that carried a
link, written on every sighting including duplicates. Trust is graded through
that join, on excess return rather than raw direction.

**Caveat kept honest:** historical attribution cannot be recovered — we never
recorded which other feeds carried old links. The seed backfill therefore has one
source per link, and multi-outlet rows only accumulate from 2026-07-30 forward.

**Rule:** deduplication must not destroy the dimension you later want to measure.
Dedupe the *content*, keep every *observation*.

---

## L16 — Missing LLM answers were written as real scores

**Found:** 2026-07-30. **Severity: medium — silent, permanent, and unauditable.**

```python
score, novel = scores.get(i + 1, (0.0, 0))   # the default is the bug
```

If the model returned 23 items for a 25-item batch — truncation, a dropped id,
one malformed entry — the two missing pairs were written as **score 0.0, novel
0**, indistinguishable from a genuine "this is noise" verdict. And because
`llm_sent` became non-NULL, they were never retried.

**Fix:** a missing id leaves the row NULL so it returns to the queue, and the run
reports how many answers were missing.

**Rule:** never let "no answer" and "answered zero" collapse into the same value.
If a default is doing real work, it is a decision and it needs to be visible.

---

## L17 — Feeds that are dead but return HTTP 200

**Found:** 2026-07-30. **Severity: medium — invisible data loss.**

Six configured feeds returned 200 OK with a full set of entries and looked
perfectly healthy. They were frozen archives:

```
Moneycontrol (all 5 endpoints)   newest item: 2024-04-23   — 2+ years stale
WSJ Markets / WSJ World          newest item: 2025-01-27   — 18 months stale
```

Moneycontrol is a major Indian financial source and *Buzzing Stocks* was one of
the highest-signal feeds in the list. The existing feed-health check reported
"silent 664h" but could not distinguish *dead* from *quiet*, and nobody reads it.

**Fix:** all six removed. Health checks should compare the newest *item date* in
the feed against now, not the last time we stored something.

**Rule:** liveness is not availability. Check the freshness of the content, not
the status code.

---

## L18 — Nine simultaneous significance tests

**Found:** 2026-07-30. **Severity: medium — would have manufactured a false edge.**

The scoreboard prints ~9 `EDGE?` tests at once (3 horizons × 3 splits). At 95%
confidence each, **P(at least one false positive) ≈ 37%**. Hunting a small edge
across many slices, one *will* eventually clear the bar and be believed.

**Fix:** confidence intervals are Bonferroni-widened by the test count, and a
single **pre-registered** hypothesis (after-hours, 1 day) is named in the output.
Everything else is labelled exploratory.

**Rule:** decide which test matters *before* looking. If you slice until
something is significant, you have measured your own persistence.

---

## L19 — The scorer and the grader never met

**Found:** 2026-07-30. **Severity: high — the ML dataset's LLM arm was n=0.**

`llm_analyst.py` scored strictly newest-first. `scoreboard.py` grades strictly
older than 24 hours. The two windows barely intersect, so:

```
article_tickers with llm_sent:     299        labels rows: 1069
labels.llm_sent NOT NULL:            0   <-- 100% of the dataset
labels.llm_novel NOT NULL:           0
```

Both halves worked exactly as documented. The defect was in the *seam*, which no
docstring owns. Meanwhile ROADMAP_ML §0 quoted "Gemini 66% vs VADER 61% on
identical articles in this repo's own scoreboard" — a number that could not be
reproduced from the data on disk, because the LLM arm had no rows at all. P1
logistic regression was untrainable: its two headline features were absent.

**Fix:** the per-run budget is split 60/40 — 60% newest-first (today's trading
signal, unchanged) and 40% oldest-first inside the scoreboard's 30-day regrade
window (`published` between 28 and 1 days ago, headline mentions). Verified on a
copy of `news.db`: `labels.llm_sent NOT NULL` went 0 → 444 in one run.

**The part that cannot be fixed:** `labels` is only rewritten for rows inside the
30-day window, so **311 rows had already aged out and are permanently VADER-only**.
That is missingness *correlated with age* — the worst kind for a walk-forward
split, because it lines up with the split axis. Any P1 evaluation must either
exclude those rows or carry an explicit `llm_missing` flag; it must not treat
them as a random sample.

**Rule:** when two components each have a correct policy, check what their
policies do to *each other*. Nothing tests a seam.

---

## L20 — A feature that grew after the fact

**Found:** 2026-07-30. **Severity: medium — future leakage into a P1 feature.**

`labels.n_sources` was built from every non-noise (article, symbol) pair in the
database with **no date bound**, then stamped onto each label row — and every
in-window row is rewritten on every run. So a row dated 5 July carried a coverage
count that kept growing with news published on the 20th. Values reached 72 for a
four-week corpus, which is what gave it away: it was never per-story
independence, it was per-symbol *lifetime* coverage, i.e. a company-size proxy
that also leaks the future.

ROADMAP_ML §3 lists this as a P1 feature and mandates walk-forward evaluation
precisely to avoid look-ahead. The feature was quietly defeating it.

**Fix:** a new column `n_sources_win` counts independent stories in
`[day-1, day+1]`. `n_sources` is **frozen, not redefined** — rewriting it in
place would have given in-window rows the new meaning and out-of-window rows the
old one, in the same column, with no marker. `meta['labels_schema']` records the
cut. The date bound also bounds the O(n²) title clustering, which had none.

**Rule:** a derived column that is recomputed every run is not a fact about the
past, it is a fact about *now*. If it is a training feature, bound it to the
information available at the row's own timestamp.

---

## L21 — DISCONTINUITY: the alias cut of 2026-07-31

**Found:** 2026-07-31. **Severity: high — 78% of one symbol's rows were a
different company.** This entry is the marker for a **data-meaning change**;
`meta['alias_rev']` carries the machine-readable version.

L14 claimed *"every generated short form is still filtered through BLOCKLIST"*
and cited "Titan" being correctly rejected. True, and beside the point: the
blocklist is a hand-typed 40-word set, and tail-word peeling produces plenty of
ordinary English and plenty of *sibling company names* that nobody thought to
type. Measured over 12 132 articles:

```
alias         title hits   of which correct   what the rest actually were
"Adani"           95              27         ADANIPORTS, ADANIGREEN, ADANIENSOL,
                                             ADANIPOWER, "Adani Group" — other LISTINGS
"Coal"            70              53         sector/macro ("India's coal production up 5.35%")
"Reliance"        24              ~12        Reliance Power, Reliance Infra (separate
                                             listings) + the English noun: \b fires after
                                             the hyphen in "self-reliance"
"Persistent"       6               6         correct today; "persistent inflation" is one
                                             headline away, and there is no upside
```

The Adani case is the instructive one. This was not *noise* — every false hit was
real, well-formed company news that the pipeline scored, exported and graded. It
was simply attributed to the wrong listing. ADANIENT was simultaneously the
most-matched alias in the corpus and the least accurate one, and nothing in the
system could notice, because "a headline about a company" and "a headline about
*this* company" look identical to every check downstream of the alias.

The mirror-image defect (NM-17): SBIN.NS, a top-5 NSE constituent, had matched
**zero** headlines ever, while 54 titles said "SBI". A bare `SBI` alias was
impossible because `SBI Card(s)` → SBICARD.NS and `SBI Life` → SBILIFE.NS are
different listings, so it had been avoided and then never resolved. A regex alias
with two negative lookaheads, `re:\bSBI\b(?!\s+Cards?\b)(?!\s+Life\b)`, resolves
it; `newslib.RE_ALIAS` documents the mechanism and why it must not use a `|`
(that is the alias separator in tickers.csv).

**Measured effect of the cut, on a copy of `news.db` (12 132 articles):**

```
symbol           in_title rows   note
ADANIENT.NS       72 ->  16      56 lost; 54 of them named a sibling listing or "Adani Group"
SBIN.NS            0 ->  45      45 gained; 0 mismatches on manual regex re-check
SBICARD.NS         6 ->   6      collision guard holds
SBILIFE.NS         1 ->   1      collision guard holds
COALINDIA.NS      39 ->  28      11 lost, 0 of them about Coal India
RELIANCE.NS       18 ->   4      14 lost, of which 8 WERE genuinely RIL — see cost below
+1 each on ASIANPAINT, EICHERMOT, ADANIPORTS, HCLTECH, HEROMOTOCO, INDUSINDBK,
INFY, TCS, WIPRO — not new matches. Removing a false ADANIENT hit dropped those
articles' n_title_tickers from 4 to 3, under MAX_TICKERS_BEFORE_LISTICLE, so they
are no longer flagged as listicles and their real pairs now exist.
```

**The cost, stated plainly:** blocking bare `Reliance` also loses 8 real RIL
headlines ("Reliance AGM 2026: …", "Reliance Retail Q1 Results: …", "Reliance
ramps up diesel exports"). RELIANCE.NS keeps `Reliance Industries` and `RIL`,
which do catch the earnings coverage (RIL Q1 Results etc.) but not the AGM/Retail
stories. That was accepted rather than fixed with a third guarded regex, because
the false half is irreducible: `Reliance Power` and `Reliance Infra` are separate
listings and "self-reliance"/"reliance on" are ordinary English. A guarded
`Reliance` alias is a FIX-LATER candidate with ~8 rows of measured upside; do it
only with the same lookahead discipline as SBI, and record a new `alias_rev`.

**Also fixed in the same commit (NM-14):** `config.json` listed one Economic
Times URL twice, as *Economic Times Markets* and *ET Markets Wrap*. Every run
fetched it twice and `monitor.py` wrote an `article_sources` row under both names,
so `source_weights()` saw one newsroom as two outlets and the digest claimed 37
active sources. The duplicate is deleted; the 50 existing `ET Markets Wrap`
`article_sources` rows are **left alone** (never delete data, only stop making
more of it).

**What a future analysis MUST do about this:**

`migrate.py` rescans **all** history on every run, so this edit retroactively
rewrote `article_tickers` for the whole corpus — but `scoreboard.py` only rewrites
`labels` inside its 30-day window. Rows that had already aged out keep the OLD
attribution. So:

- `labels` rows dated before **2026-07-01** (30 days before the cut) may carry
  ADANIENT rows that are really ADANIPORTS/ADANIGREEN/"Adani Group", and carry no
  SBIN rows at all.
- A walk-forward split **must not straddle 2026-07-31** without an explicit flag.
  This is the second cut in the dataset, alongside L20's `n_sources` freeze.
- All four alias edits landed in **one commit** on purpose, so there is exactly
  one cut date instead of four undocumented ones.

**Rule:** an alias is a claim about identity, and the only way to check it is to
read the headlines it matched. Count them: a match rate is not an accuracy rate.
When you change one, change them all at once and stamp the date.

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
