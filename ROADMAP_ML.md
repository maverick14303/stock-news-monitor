# ML Roadmap — from news scraper to a model with a measurable edge

> **Living document.** Update the "Progress log" at the bottom every time work
> lands on this repo or on trading-bot's news integration. Written 2026-07-30.
>
> **Owner:** Ankit. **Goal:** a signal that beats the always-bull baseline by
> enough to survive ~2.5% round-trip costs, feeding the trading bot's real money.

---

## 0. The decision this document records

Ankit asked (2026-07-30) whether to fine-tune a local LLM (Ollama) specialised for
stock news. Answer: **not yet, and probably never as a fine-tune.** Reasons, so
nobody re-litigates this without new facts:

1. **Sample size.** Fine-tuning needs thousands of examples minimum. Against a
   target this noisy (next-day direction, ~61% base rate) you need tens of
   thousands to distinguish a real 5pp improvement from luck. At n=773 the 95%
   confidence interval is ±3.4pp — the improvement would be invisible inside the
   error bars. You would be fitting noise.
2. **Model scale.** Locally runnable models (llama3.2:1b-q4 on Ankit's machine)
   reason poorly about finance. Gemini 2.5 Flash already scores **66% vs VADER's
   61%** on identical articles in this repo's own scoreboard. A 1B fine-tune is a
   downgrade, not an upgrade.
3. **Wrong task shape.** Fine-tuning wins when a model must learn a private
   vocabulary or output format. "Is this headline good or bad for company X" is
   general reading comprehension, which frontier models already do well. Domain
   knowledge belongs in the prompt, not the weights.

**What replaces it:** a big hosted LLM does the *reading*; a small, sample-efficient
classifier does the *predicting*, trained on the labelled dataset this repo
accumulates. That classifier is portable, interpretable, trains in seconds, cannot
hallucinate, and is what quant desks actually use for this shape of problem.

---

## 1. The real asset: the labelled dataset

Not the model. The model is replaceable in an afternoon; the dataset is not.

Every scored (article, ticker) pair, joined to what the stock actually did, is a
labelled example nobody else has. Target schema (built incrementally — see §4):

| field | source | notes |
|---|---|---|
| `link`, `symbol` | monitor.py | primary key of the pair |
| `title`, `summary`, `source`, `published` | RSS | raw text kept for re-scoring later |
| `vader` | monitor.py | cheap baseline score |
| `llm_sent` | llm_analyst.py | **per-ticker** LLM impact score, −1..+1 |
| `llm_novel` | llm_analyst.py | 1 = genuinely new info, 0 = descriptive/recycled |
| `noise` | newslib | 1 = tracker page / index wrap / listicle |
| `after_hours` | newslib | 1 = published outside NSE 09:15–15:30 IST |
| `n_tickers` | monitor.py | how many companies the article named |
| `cluster_id` | newslib | syndication group — wire copy reprinted N times |
| `source_trust` | scoreboard | learned hit-rate weight of the outlet |
| `ret_1d`, `ret_3d`, `ret_5d` | scoreboard | **the label** |
| `mkt_ret_1d…` | scoreboard | NIFTY move same window — for excess return |

**Non-negotiable rule: never delete rows.** Bad data gets *flagged* (`noise=1`),
never removed. Today's junk is tomorrow's negative training example, and deletion
destroys the ability to re-measure a past decision.

**Storage.** news.db is ~3 MB and committed hourly. At current rates the labelled
table grows ~40–120 rows/day. This is fine for years. If the repo gets heavy,
making it public (Ankit already agreed, 2026-07-30) removes the private-repo
storage pressure — but check first that no API key ever entered the DB or digests.

---

## 2. Phase gates — do not skip ahead

Each phase is gated on **data volume**, not on enthusiasm. The gate exists because
a model trained below it cannot be distinguished from luck.

| Phase | Gate | What to build |
|---|---|---|
| **P0 — Clean measurement** | now | Per-ticker scoring, noise flags, honest scoreboard. No model. |
| **P1 — Feature baseline** | ~2 000 clean pairs (≈2–3 months) | Logistic regression. Establish whether ANY feature set beats baseline. |
| **P2 — Nonlinear** | ~10 000 pairs (≈9–12 months) | Gradient boosting (LightGBM/XGBoost). Only if P1 showed life. |
| **P3 — Production signal** | P2 beats baseline out-of-sample | Replace the hand-tuned export weights with model output. |
| **P4 — Revisit local LLM** | ≥10 000 pairs AND P2 plateaued | Only then, and only for cost/volume reasons — never for accuracy. |

**Kill criterion (be honest about this).** If P1 and P2 both fail to beat the
always-bull baseline out-of-sample with the confidence interval clearing it, the
correct conclusion is *news sentiment from public RSS has no tradeable edge at this
latency* — a legitimate, valuable finding. Ship the trading bot on momentum alone
and stop spending on this. Write that result up rather than tuning until something
looks good; that's how you fool yourself.

---

## 3. Model progression (P1 → P2)

### P1 — Logistic regression
Predict `P(excess return over next 1 day > 0)`. Why this first: ~10 parameters,
works at n≈2 000, coefficients are directly readable ("after-hours news is worth
+0.3 in log-odds"), and it becomes the honest benchmark every later model must beat.

Starting feature set (all available from §1):
- `llm_sent`, `llm_novel`, `vader`, `llm_sent × llm_novel`
- `after_hours`, `hours_since_publication`
- `n_distinct_clusters` (real independent coverage, NOT raw article count)
- `source_trust`, `n_tickers`, `sector` (one-hot)
- `stock_ret_5d_prior` (was the move already underway — the priced-in control)

### P2 — Gradient boosting
LightGBM, depth ≤4, heavy regularisation, early stopping on a walk-forward split.
Captures interactions logistic regression misses (e.g. after-hours × high-novelty ×
trusted-source). Only worth it once n ≥ 10 000; below that it will overfit and
*look* brilliant in-sample.

### Evaluation protocol (this is what keeps it honest)
- **Walk-forward only.** Train on months 1..k, test on month k+1. Never random
  k-fold — random splits leak future information through time-correlated market
  regimes and will show a fake edge.
- **Baseline-relative always.** Report against the always-bull rate for the same
  window, never against 50%. This repo already does this; keep it.
- **Report the confidence interval, every time.** An improvement that doesn't clear
  its own error bars is not an improvement.
- **One holdout month never touched** until a go-live decision is made.
- **Costs applied.** A signal that wins 55% of the time but only by 0.3% per trade
  loses money against a 2.5% round trip. Grade on *net* outcome, not hit rate.

---

## 4. How this connects to the trading bot

Today: `export_signal.py` → `news_signal.json` → trading-bot `src/news_signal.py` →
`fuse_ranks()` blends `0.7 × momentum + 0.3 × news`, with a hard veto at ≤ −0.5.

The 0.3 weight is currently **a judgment call, not a measurement**. The whole point
of P1–P3 is to replace it with a number derived from data. When P3 lands:
- `news_signal.json` gains a `p_up` field (model probability) alongside `score`.
- The trading bot's fusion weight becomes a function of measured model skill.
- **Interface stays identical** so the bot never breaks — new field, old field kept.

Structural constraint discovered 2026-07-30: the news universe (56 names) barely
overlapped the bot's momentum picks (NATIONALUM, ADANIPOWER, BHEL…), so the news
overlay was a no-op on the ₹1k bot's actual holdings. Universe alignment (plan
step 3) is a prerequisite for ANY of this mattering to the trading bot.

---

## 5. Where the edge is most likely to be (hypothesis to test first)

Everything measured so far says intraday RSS news is already priced in. The one
window with a mechanical reason to hold edge: **news breaking after the Indian
close (15:30 IST) and before the next open (09:15 IST)** — overnight announcements,
US session moves, global macro. No Indian participant can act until the open.

`after_hours` exists specifically to test this. If any edge survives cleaning, this
is where it will show up. Test it as a *split of the scoreboard* before building any
model — if after-hours news doesn't beat baseline, no classifier will save it.

---

## 6. Progress log

- **2026-07-30 — Document created.** Decision recorded: no LLM fine-tune; build the
  dataset, then logistic regression → gradient boosting. Phase gates set.
  Current state: 9 961 articles, 955 ticker-matched, 773 graded signals.
  Measured: 1d hit 61.1% vs 63.5% baseline (below baseline, and inflated by ~16%
  tracker-page contamination). Starting P0.

- **2026-07-30 — P0 shipped.** Seven changes, all measured rather than assumed:
  1. **Per-(headline, company) scoring.** New `article_tickers` table; each pair
     scored on its own. Killed the bug where one article-level score was stamped
     on every company named ("prefers ICICI over HDFC Bank" was a BUY for HDFC).
  2. **Noise flagging** (`newslib.classify_noise`): tracker pages, listicle
     previews, index wraps. 451 of 10 086 articles flagged; ~24% of previously
     *matched* articles were junk. Flagged, never deleted — they are negative
     training examples. Deliberately kept separate from the LLM's `novel` flag:
     regex for structural non-news, LLM judgment for "already priced in".
  3. **Universe aligned with the trading bot**: 56 → 148 names, generated by
     `build_tickers.py` from `trading-bot/src/universe.py`. Overlap went 54/147
     → **147/147**, and coverage of names the bots actually hold 5/16 → **16/16**.
     The news overlay was previously a literal no-op on the ₹1k bot's holdings.
  4. **Excess-return grading** vs NIFTY, so the baseline is a clean 50% instead
     of a drifting always-bull rate.
  5. **Syndication clustering** in the exporter — `sources` now counts
     independent stories, so one wire reprinted 8× no longer reads as 8-source
     confirmation.
  6. **Headline vs body split, and it mattered more than anything else measured:
     body-only mentions beat NIFTY 32.6% of the time (n=95, ±9.4) vs 51.1% for
     headline mentions — the entire CI below coin-flip.** Body-only mentions are
     now excluded from the export and from autotrader's candidate scan.
  7. Manual "you + Claude" account retired; alerts re-pointed at the bot's book.

  **Post-clean measurement (VADER only — LLM backlog of 1 131 pairs still
  scoring): 1d excess 50.4% (n=696, ±3.7). After-hours 53.8% vs in-session
  41.2%.** The after-hours gap is the thesis in §5 showing its first real
  signal; it has NOT cleared its confidence interval and must not be traded on
  yet. Dataset: 781 labelled rows → P1 gate is 2 000.

  Also fixed three latent NaN crashes (yfinance emits placeholder rows with NaN
  closes mid-session; NaN is truthy, so it sailed past `if price` guards and
  died at `int()`). One of them would have taken down the alert step in
  production.

  **Next:** let the LLM backlog clear, then re-read the after-hours split with
  `llm_sent`/`llm_novel` populated. That is the first honest test of whether
  there is an edge here at all.

- **2026-07-30 (later) — session-aware trading + LESSONS.md.** Audited the bot's
  execution times: **36 of its first 41 trades (88%) had run while the NSE was
  shut**, at closing prices it could never have obtained, including 13 on Sunday
  2026-07-26 and one at 02:04 IST from the live cloud pipeline. Every performance
  figure before this date is structurally invalid, not merely noisy — **do not
  train on, or compare against, pre-2026-07-30 bot P&L.**
  Replaced with an explicit `market_phase()`: `plan` pre-open (queue orders, no
  fills), `trade` in-session (re-validate against fresh prices and news, then
  fill at an obtainable price), `closed` otherwise (news only). Entry decisions
  are now once a day off the full overnight news window; exits still run every
  session pass so a stop-loss is not delayed.
  Note the rejected alternative: filling at 08:15 right after the pre-open sweep
  would buy at *yesterday's close* using news that broke after it — look-ahead
  bias that would have produced a beautiful, untradeable equity curve.
  Created **`LESSONS.md`** — all nine defects with their measurements, organised
  by bug family (wrong unit of analysis / contamination that flatters / impossible
  fills / silent NaN). It is both the portability record and the negative-example
  set for any future model (§1).
