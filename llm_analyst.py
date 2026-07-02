"""Score ticker-matched headlines with a free LLM.

Tries Google Gemini first (dedicated free quota, reliable), then falls back
to OpenRouter's shared :free models. Writes scores into articles.llm_sent so
scoreboard.py can grade the LLM against VADER on real next-day outcomes.
Skips silently when no key is set; never breaks the pipeline on API failure.

Secrets: GEMINI_API_KEY (aistudio.google.com) and/or OPENROUTER_API_KEY.
Budget: one request per run (~17/day) — inside both free tiers.
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
BATCH = 40
GEMINI_MODELS = ("gemini-2.5-flash", "gemini-2.0-flash")

PROMPT = (
    "You are an equity analyst for Indian stock markets (NSE). For each numbered "
    "headline, score the likely impact on the NAMED stock(s) over the next few "
    "trading days: -1.0 (strongly negative) to +1.0 (strongly positive). Use 0 for "
    "noise, index wrap-ups, or news already reflected in the price. Think about "
    "competition (a rival's win can be NEGATIVE for the named stock), regulation, "
    "and real business impact — not just word tone.\n"
    "Reply with ONLY a JSON array like [{\"id\": 1, \"score\": -0.5}].\n\n")


def parse_scores(text):
    match = re.search(r"\[.*\]", text, re.DOTALL)
    return {int(x["id"]): float(x["score"]) for x in json.loads(match.group(0))}


def score_with_gemini(requests, key, prompt):
    for model in GEMINI_MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": key},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0}},
                timeout=120)
            if r.status_code != 200:
                print(f"  gemini {model}: http {r.status_code}")
                continue
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return parse_scores(text), f"google/{model}"
        except Exception as e:
            print(f"  gemini {model}: {type(e).__name__}")
    return None, None


def openrouter_free_models(requests):
    """Live :free catalog, less-contended families first (big Llama is congested)."""
    def rank(mid):
        for j, kw in enumerate(("deepseek", "qwen", "gemini", "mistral",
                                "llama-3.3", "llama")):
            if kw in mid:
                return j
        return 9
    r = requests.get("https://openrouter.ai/api/v1/models", timeout=30)
    r.raise_for_status()
    ids = [m["id"] for m in r.json()["data"] if m["id"].endswith(":free")]
    return sorted(ids, key=rank)[:6]


def score_with_openrouter(requests, key, prompt):
    try:
        candidates = openrouter_free_models(requests)
    except Exception:
        return None, None
    for m in candidates:
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": m, "temperature": 0,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=120)
            if resp.status_code != 200:
                continue
            data = resp.json()
            return parse_scores(data["choices"][0]["message"]["content"]), \
                data.get("model", m)
        except Exception:
            continue
    return None, None


def main():
    gem_key = os.environ.get("GEMINI_API_KEY")
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not (gem_key or or_key):
        print("LLM analyst skipped — no GEMINI_API_KEY or OPENROUTER_API_KEY set.")
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

    prompt = PROMPT + "\n".join(f"{i + 1}. [{t}] {title}"
                                for i, (_, title, t, _) in enumerate(rows))
    import requests
    scores = served = None
    if gem_key:
        scores, served = score_with_gemini(requests, gem_key, prompt)
    if scores is None and or_key:
        scores, served = score_with_openrouter(requests, or_key, prompt)
    if scores is None:
        con.close()
        print("LLM analyst: no provider available this run.")
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
