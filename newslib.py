"""Shared classification helpers used across the whole pipeline.

One place so monitor.py, llm_analyst.py, scoreboard.py, export_signal.py and
alerts.py all agree on: what counts as noise, what counts as ONE story, and when
a headline arrived relative to the NSE session.

Design note — two different filters, deliberately kept separate:
  * NOISE (here, regex): structurally not company news — auto-generated tracker
    pages, listicle/calendar previews, index wrap-ups. No company-specific claim
    exists to score, so these never reach the LLM and never get graded.
  * NOVELTY (llm_analyst): judgment on whether real news is NEW information or a
    description of a move that already happened ("Coal India shares fall 3%").
    That needs reading comprehension, not a regex, so the LLM decides it.
Keeping the regex conservative matters: over-filtering silently destroys signal.
"""
import difflib
import re
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# NSE continuous session. Used for the after-hours flag, not for trading logic.
SESSION_OPEN = (9, 15)
SESSION_CLOSE = (15, 30)

# Articles naming more than this many companies are listicles/previews: one
# sentiment cannot honestly be attributed to each name.
MAX_TICKERS_BEFORE_LISTICLE = 3

_NOISE_SOURCES = [
    # --- auto-generated per-stock tracker pages (the single biggest polluter) ---
    r"share price (highlights|live updates?|history|target)",
    r"stock price history",
    r"share price (today|live)\b",
    # --- listicle / calendar previews: many names, no claim about any of them ---
    r"\bstocks? to watch\b",
    r"\bstock picks?\b",
    r"\btop stocks to (buy|sell)\b",
    r"\bbuy,? sell,? (or )?hold\b",
    r"\bstock recommendations?\b",
    r"\bmarket trading guide\b",
    r"results live( updates)?\b",
    r"\bamong (companies|more than|\d+|stocks)\b",
    r"\bfull list here\b",
    r"\bto declare .{0,30}(results|earnings)\b",
    r"\bex-date\b",
    # --- index / market wrap-ups: describe the market, not a company ---
    r"\bmarket (wrap|close|live|highlights|outlook)\b",
    r"\b(closing|opening) bell\b",
    r"\btop (gainers|losers)\b",
    r"\bmcap of\b",
    r"\bsensex\b",
    r"\bearnings central\b",
    r"\b(stock|equity) markets?\b[^.]{0,30}\b(decline|rise|fall|gain|end|close|open|slip)",
    r"\bnifty (\d+|it|bank|pack|pharma|auto|metal)\b",
    r"\bnifty\b[^.]{0,40}\b(jumps?|slips?|falls?|rises?|gains?|ends?|closes?|premium)\b",
    # --- not company news at all ---
    r"\bbank holidays?\b",
    r"\brbi calendar\b",
]
NOISE_TITLE = re.compile("|".join(_NOISE_SOURCES), re.IGNORECASE)

# Finance-aware direction words; VADER alone misreads headlines like
# "profit falls 19%, declares dividend" as positive.
UP_WORDS = re.compile(
    r"\b(rises?|rally|rallies|jumps?|gains?|surges?|soars?|climbs?|beats?|"
    r"advances?|rebounds?|upgrades?|record high|buy call)\b", re.IGNORECASE)
DOWN_WORDS = re.compile(
    r"\b(falls?|drops?|slips?|plunges?|declines?|tumbles?|sinks?|crashes?|"
    r"slides?|bleeds?|misses?|downgrades?|weak|losses?|loss)\b", re.IGNORECASE)


def score_sentiment(text, analyzer):
    """VADER blended with finance direction words when any are present."""
    vader = analyzer.polarity_scores(text)["compound"]
    ups, downs = len(UP_WORDS.findall(text)), len(DOWN_WORDS.findall(text))
    if ups + downs == 0:
        return vader
    fin = (ups - downs) / (ups + downs)
    return round(0.4 * vader + 0.6 * fin, 4)


def classify_noise(title, n_title_tickers=0):
    """1 if this headline carries no scoreable company-specific claim.

    Matched on the TITLE only — a passing mention of "Sensex" inside a company
    story must not disqualify it. Likewise the listicle rule counts companies
    named in the HEADLINE, not ones the body blurb happens to mention: an article
    headlined "Jindal Steel Q1 profit falls 44%" is real news even if its summary
    name-drops four peers.
    """
    if NOISE_TITLE.search(title or ""):
        return 1
    if n_title_tickers > MAX_TICKERS_BEFORE_LISTICLE:
        return 1
    return 0


def load_ticker_patterns(path):
    """[(alias_regex, symbol)] from a tickers.csv with symbol,aliases,sector."""
    import csv
    patterns = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for alias in row["aliases"].split("|"):
                alias = alias.strip()
                if not alias:
                    continue
                # word-boundary match so "ITC" doesn't fire inside "pitch"
                patterns.append(
                    (re.compile(r"\b" + re.escape(alias) + r"\b", re.IGNORECASE),
                     row["symbol"]))
    return patterns


def match_tickers(title, summary, patterns):
    """Return {symbol: in_title} for every company this article names.

    `in_title` separates a headline claim ("HDFC Bank fines CEO") from a passing
    mention in the body blurb. They are worth very different amounts and the old
    code conflated them, which is how single-company stories ended up tagged with
    four peers and flagged as listicles.
    """
    title = title or ""
    body = f"{title}. {summary or ''}"
    found = {}
    for pat, sym in patterns:
        if sym in found and found[sym]:
            continue
        if pat.search(title):
            found[sym] = 1
        elif pat.search(body):
            found.setdefault(sym, 0)
    return found


def _normalize(title):
    return re.sub(r"\W+", " ", (title or "").lower()).strip()


def cluster_titles(titles, threshold=0.85):
    """Group near-identical headlines so syndicated wire copy counts once.

    Returns a list of cluster indexes parallel to `titles`. One PTI/Reuters story
    reprinted by eight outlets is ONE story, not eight-source confirmation — the
    distinction the exporter's `sources` count depends on.
    """
    reps, out = [], []
    for t in (_normalize(x) for x in titles):
        for i, c in enumerate(reps):
            if difflib.SequenceMatcher(None, t, c).ratio() > threshold:
                out.append(i)
                break
        else:
            reps.append(t)
            out.append(len(reps) - 1)
    return out


def distinct_stories(titles, threshold=0.85):
    """How many genuinely independent stories are in this list of headlines."""
    return len(set(cluster_titles(titles, threshold))) if titles else 0


def parse_ts(ts):
    """Parse an ISO timestamp to an aware UTC datetime, or None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def is_after_hours(ts):
    """1 if the headline landed while the NSE was shut, else 0 (None if unknown).

    This is the hypothesis the whole project rests on: news breaking after the
    15:30 close or before the 09:15 open cannot be acted on by anyone in India
    until the next session, so it is the one window where a headline may not be
    priced in yet. Everything else is usually already in the tape.
    """
    dt = parse_ts(ts)
    if dt is None:
        return None
    ist = dt.astimezone(IST)
    if ist.weekday() >= 5:          # Saturday/Sunday
        return 1
    minutes = ist.hour * 60 + ist.minute
    open_m = SESSION_OPEN[0] * 60 + SESSION_OPEN[1]
    close_m = SESSION_CLOSE[0] * 60 + SESSION_CLOSE[1]
    return 0 if open_m <= minutes <= close_m else 1
