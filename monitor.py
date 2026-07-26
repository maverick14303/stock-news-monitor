"""Scrape trusted news feeds, match headlines to NSE tickers, score sentiment,
and store everything in news.db. Run as often as you like; duplicates are skipped.

Usage: python monitor.py
"""
import csv
import json
import re
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, timezone
from pathlib import Path

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

BASE = Path(__file__).parent
DB = BASE / "news.db"

# Auto-generated tracker pages, not news — skip entirely
NOISE = re.compile(r"share price live updates", re.IGNORECASE)

# Finance-aware direction words; VADER alone misreads headlines like
# "profit falls 19%, declares dividend" as positive
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


def load_ticker_patterns():
    patterns = []
    with open(BASE / "tickers.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for alias in row["aliases"].split("|"):
                # word-boundary match so "ITC" doesn't fire inside "pitch"
                pat = re.compile(r"\b" + re.escape(alias.strip()) + r"\b", re.IGNORECASE)
                patterns.append((pat, row["symbol"]))
    return patterns


def init_db():
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            link TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            summary TEXT,
            published TEXT,
            fetched_at TEXT,
            sentiment REAL,
            tickers TEXT,
            llm_sent REAL
        )
    """)
    # Older DBs predate llm_sent (added by llm_analyst.py). Add it if missing so
    # a fresh checkout and an upgraded one share one schema — the missing column
    # is exactly what crashed every scrape for weeks.
    cols = {r[1] for r in con.execute("PRAGMA table_info(articles)")}
    if "llm_sent" not in cols:
        con.execute("ALTER TABLE articles ADD COLUMN llm_sent REAL")
    con.commit()
    return con


def parse_published(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc).isoformat()
    return None


def main():
    patterns = load_ticker_patterns()
    analyzer = SentimentIntensityAnalyzer()
    con = init_db()
    feeds = json.loads((BASE / "config.json").read_text())["feeds"]

    new_total, matched_total = 0, 0
    now = datetime.now(timezone.utc).isoformat()

    for feed in feeds:
        parsed = feedparser.parse(feed["url"], agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        if parsed.bozo and not parsed.entries:
            print(f"[WARN] {feed['name']}: could not fetch ({parsed.get('bozo_exception')})")
            continue
        new_here = 0
        for entry in parsed.entries:
            link = entry.get("link")
            title = (entry.get("title") or "").strip()
            if not link or not title or NOISE.search(title):
                continue
            summary = re.sub(r"<[^>]+>", "", entry.get("summary", "")).strip()[:500]
            text = f"{title}. {summary}"
            # global feeds skip ticker matching: "Titan"/"Apollo" in world news
            # are usually not the NSE companies
            tickers = [] if feed.get("global") else sorted(
                {sym for pat, sym in patterns if pat.search(text)})
            sentiment = score_sentiment(text, analyzer)
            try:
                con.execute(
                    "INSERT INTO articles "
                    "(link, source, title, summary, published, fetched_at, "
                    "sentiment, tickers, llm_sent) VALUES (?,?,?,?,?,?,?,?,?)",
                    (link, feed["name"], title, summary, parse_published(entry),
                     now, sentiment, ",".join(tickers), None),
                )
                new_here += 1
                if tickers:
                    matched_total += 1
            except sqlite3.IntegrityError:
                pass  # already seen
        con.commit()
        new_total += new_here
        print(f"[OK] {feed['name']}: {len(parsed.entries)} entries, {new_here} new")

    print(f"\n{new_total} new articles stored, {matched_total} matched to tickers.\n")

    # Digest of new ticker-matched signals from this run
    rows = con.execute(
        "SELECT source, title, sentiment, tickers FROM articles "
        "WHERE fetched_at = ? AND tickers != '' ORDER BY sentiment DESC",
        (now,),
    ).fetchall()
    if rows:
        print("=== New signals ===")
        for source, title, sent, tickers in rows:
            label = "BULLISH" if sent >= 0.25 else "BEARISH" if sent <= -0.25 else "neutral"
            print(f"[{label:7}] {tickers}  ({sent:+.2f})  {title}  — {source}")
    con.close()


if __name__ == "__main__":
    main()
