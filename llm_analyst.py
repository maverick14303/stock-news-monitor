"""Score ticker-matched headlines with a free LLM via OpenRouter.

Writes scores into articles.llm_sent so scoreboard.py can grade the LLM
against VADER on real next-day outcomes. Skips silently when no
OPENROUTER_API_KEY is set; never breaks the pipeline on API failure.

Free-tier budget: one batched request per run (up to 40 headlines),
~17 requests/day — inside OpenRouter's free limits.
"""
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).parent
# free models in fallback order — OpenRouter routes to the first with capacity
MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "google/gemini-2.0-flash-exp:free",
    "qwen/qwen-2.5-72b-instruct:free",
]
BATCH = 40

PROMPT = (
    "You are an equity analyst for Indian stock markets (NSE). For each numbered "
    "headline, score the likely impact on the NAMED stock(s) over the next few "
    "trading days: -1.0 (strongly negative) to +1.0 (strongly positive). Use 0 for "
    "noise, index wrap-ups, or news already reflected in the price. Think about "
    "competition (a rival's win can be NEGATIVE for the named stock), regulation, "
    "and real business impact — not just word tone.\n"
    "Reply with ONLY a JSON array like [{\"id\": 1, \"score\": -0.5}].\n\n")


def main():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("LLM analyst skipped — no OPENROUTER_API_KEY set.")
        return

    con = sqlite3.connect(BASE / "news.db")
    try:
        con.execute("ALTER TABLE articles ADD COLUMN llm_sent REAL")
    except sqlite3.OperationalError:
        pass  # column exists
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    rows = con.execute(
        "SELECT link, title, tickers, sentiment FROM articles "
        "WHERE tickers != '' AND llm_sent IS NULL AND fetched_at >= ? LIMIT ?",
        (since, BATCH)).fetchall()
    if not rows:
        con.close()
        print("LLM analyst: nothing new to score.")
        return

    numbered = "\n".join(f"{i + 1}. [{t}] {title}"
                         for i, (_, title, t, _) in enumerate(rows))
    try:
        import time

        import requests
        for attempt in (1, 2):
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"models": MODELS, "temperature": 0,
                      "messages": [{"role": "user", "content": PROMPT + numbered}]},
                timeout=120)
            if resp.status_code == 429 and attempt == 1:
                time.sleep(15)
                continue
            break
        resp.raise_for_status()
        data = resp.json()
        served = data.get("model", "?")
        text = data["choices"][0]["message"]["content"]
        match = re.search(r"\[.*\]", text, re.DOTALL)
        scores = {int(x["id"]): float(x["score"]) for x in json.loads(match.group(0))}
    except Exception as e:
        con.close()
        print(f"LLM analyst: API/parse failure, skipping this run ({type(e).__name__}: {e})")
        return

    disagreements = []
    for i, (link, title, tickers, vader) in enumerate(rows):
        s = max(-1.0, min(1.0, scores.get(i + 1, 0.0)))
        con.execute("UPDATE articles SET llm_sent = ? WHERE link = ?", (s, link))
        if abs(s - vader) >= 0.8:
            disagreements.append((vader, s, title))
    con.commit()
    con.close()
    print(f"LLM analyst: scored {len(rows)} headline(s) with {served}.")
    for vader, s, title in disagreements[:5]:
        print(f"  disagreement — VADER {vader:+.2f} vs LLM {s:+.2f}: {title[:70]}")


if __name__ == "__main__":
    main()
