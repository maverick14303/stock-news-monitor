"""The single definition of news.db's schema, with idempotent migration.

Every script opens the DB through `connect()` so a fresh clone and a long-running
checkout can never disagree about columns. Schema drift here is not theoretical:
a missing `llm_sent` column silently crashed every scrape for ~3.5 weeks in July
2026. Adding a column now means editing exactly one place.

Tables
------
articles          one row per fetched article (dedup key = link)
article_tickers   one row per (article, company) pair — where per-ticker scores
                  live. This is the fix for "one sentiment stamped on every
                  company an article mentions".
graded            trailing-30-day grades, rebuilt each run. Schema deliberately
                  UNCHANGED: autotrader.py and export_signal.py learn source
                  trust from it, and its history is the learned weights.
labels            the growing ML dataset (see ROADMAP_ML.md §1). Append-only,
                  richer than `graded`, never rebuilt or pruned.
verdicts          alerts.py's ✅ calls, graded at 5 days by scoreboard.py
"""
import sqlite3

_TABLES = [
    """CREATE TABLE IF NOT EXISTS articles (
        link TEXT PRIMARY KEY,
        source TEXT,
        title TEXT,
        summary TEXT,
        published TEXT,
        fetched_at TEXT,
        sentiment REAL,
        tickers TEXT,
        llm_sent REAL
    )""",
    """CREATE TABLE IF NOT EXISTS article_tickers (
        link TEXT,
        symbol TEXT,
        in_title INTEGER,
        llm_sent REAL,
        llm_novel INTEGER,
        PRIMARY KEY (link, symbol)
    )""",
    """CREATE TABLE IF NOT EXISTS graded (
        day TEXT, symbol TEXT, source TEXT, title TEXT,
        sent REAL, ret REAL, hit INT,
        PRIMARY KEY (day, symbol, source, title)
    )""",
    """CREATE TABLE IF NOT EXISTS labels (
        link TEXT, symbol TEXT, day TEXT, source TEXT, title TEXT,
        vader REAL, llm_sent REAL, llm_novel INTEGER,
        in_title INTEGER, after_hours INTEGER, n_sources INTEGER,
        ret_1d REAL, ret_3d REAL, ret_5d REAL,
        mkt_1d REAL, mkt_3d REAL, mkt_5d REAL,
        PRIMARY KEY (link, symbol)
    )""",
    """CREATE TABLE IF NOT EXISTS verdicts (
        ts TEXT, symbol TEXT, price REAL, title TEXT
    )""",
    # Articles are deduped by URL, so the FIRST feed to carry a story owns the
    # `articles.source` column forever — which made learned source trust partly
    # an artifact of the order feeds appear in config.json. This records every
    # outlet that carried a link, so trust is graded on what a source actually
    # published. See LESSONS.md L13.
    """CREATE TABLE IF NOT EXISTS article_sources (
        link TEXT, source TEXT,
        PRIMARY KEY (link, source)
    )""",
    # One-time data repairs record themselves here so they cannot run twice.
    """CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value TEXT
    )""",
]

# Columns added after the original schema shipped. (table, column, decl)
_ADDED_COLUMNS = [
    ("articles", "llm_sent", "REAL"),
    ("articles", "noise", "INTEGER"),
    ("articles", "after_hours", "INTEGER"),
    ("articles", "n_title_tickers", "INTEGER"),
]

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at)",
    "CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published)",
    "CREATE INDEX IF NOT EXISTS idx_at_symbol ON article_tickers(symbol)",
    "CREATE INDEX IF NOT EXISTS idx_asrc_source ON article_sources(source)",
]


def flag(con, key):
    """Has this one-time repair already run?"""
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_flag(con, key, value="done"):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
    con.commit()


def migrate(con):
    for stmt in _TABLES:
        con.execute(stmt)
    for table, column, decl in _ADDED_COLUMNS:
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    for stmt in _INDEXES:
        con.execute(stmt)
    con.commit()
    return con


def connect(path):
    return migrate(sqlite3.connect(path))
