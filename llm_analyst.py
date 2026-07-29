"""Score each (headline, company) PAIR with a free LLM.

The old version scored an ARTICLE and stamped that one number onto every company
the article named — so "Elara prefers ICICI Bank over HDFC Bank" gave HDFC Bank
+0.73. Now every pair is scored on its own, which is the single biggest accuracy
fix available here (the LLM already beats VADER 66% vs 61% on this repo's own
scoreboard, even while handicapped that way).

Two values per pair:
  llm_sent  — impact on THAT company, -1..+1
  llm_novel — 1 if genuinely new information, 0 if it just describes a move that
              already happened. Novelty is the feature most likely to carry edge;
              see ROADMAP_ML.md §5.

Tries Google Gemini first (dedicated free quota), then OpenRouter's :free models.
Skips silently when no key is set; never breaks the pipeline on API failure.

Secrets: GEMINI_API_KEY (aistudio.google.com) and/or OPENROUTER_API_KEY.
"""
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import db

BASE = Path(__file__).parent
DB = BASE / "news.db"

# Smaller batches score more accurately than long lists; more calls per run is
# the trade. Runs dropped from 17/day to 10/day, so there is budget for this.
BATCH = 25
MAX_CALLS_PER_RUN = 6
GEMINI_MODELS = ("gemini-2.5-flash", "gemini-2.0-flash")

PROMPT = (
    "You are an equity analyst for Indian stock markets (NSE). Each numbered item "
    "below gives ONE headline and ONE specific company. Score that headline's "
    "impact on THAT company's stock over the next 1-5 trading days.\n\n"
    "\"score\": -1.0 (strongly negative) to +1.0 (strongly positive).\n"
    "  - Judge the NAMED company only. If the news is good for a rival and bad for "
    "the named company, the score is NEGATIVE.\n"
    "  - If the headline names several companies but makes no claim about this "
    "one, score 0.\n"
    "  - Score the SURPRISE versus expectations, not the tone of the words. "
    "\"Profit falls 19% but beats estimates\" is POSITIVE. \"Profit rises 5%, "
    "below estimates\" is NEGATIVE.\n"
    "  - Magnitude: 0.2 minor, 0.5 meaningful, 0.9 major (M&A, fraud, guidance "
    "shock, regulatory action, large order win).\n"
    "  - Score 0 for routine or administrative items with no earnings or "
    "valuation impact.\n\n"
    "\"novel\": 1 if this is genuinely NEW information the market has not had time "
    "to price in. 0 if it merely DESCRIBES a price move that already happened "
    "(\"X shares rally 4%\", \"X hits 52-week high\"), recycles known facts, is a "
    "preview/expectation piece, or is analyst commentary on old news. Be strict — "
    "most financial headlines are NOT novel.\n\n"
    "Reply with ONLY a JSON array, one object per item, no prose:\n"
    "[{\"id\": 1, \"score\": -0.5, \"novel\": 1}]\n\n")


def parse_scores(text):
    match = re.search(r"\[.*\]", text, re.DOTALL)
    out = {}
    for x in json.loads(match.group(0)):
        out[int(x["id"])] = (float(x.get("score", 0.0)), int(x.get("novel", 0)))
    return out


def company_names():
    """symbol -> display name (first alias), for unambiguous prompts."""
    names = {}
    with open(BASE / "tickers.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            names[row["symbol"]] = row["aliases"].split("|")[0].strip()
    return names


def score_with_gemini(requests, key, prompt):
    for model in GEMINI_MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": key},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0}},
                timeout=120)
            if r.status_code == 429:
                print(f"  gemini {model}: rate limited")
                continue
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


def score_batch(requests, gem_key, or_key, rows, names):
    """rows: [(link, symbol, title, summary)] -> {index: (score, novel)}"""
    lines = []
    for i, (_, symbol, title, summary) in enumerate(rows):
        blurb = (summary or "")[:150]
        lines.append(f"{i + 1}. [{names.get(symbol, symbol)}] \"{title}\""
                     + (f" — {blurb}" if blurb else ""))
    prompt = PROMPT + "\n".join(lines)
    scores = served = None
    if gem_key:
        scores, served = score_with_gemini(requests, gem_key, prompt)
    if scores is None and or_key:
        scores, served = score_with_openrouter(requests, or_key, prompt)
    return scores, served


def main():
    gem_key = os.environ.get("GEMINI_API_KEY")
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not (gem_key or or_key):
        print("LLM analyst skipped — no GEMINI_API_KEY or OPENROUTER_API_KEY set.")
        return

    con = db.connect(DB)
    # Newest first: fresh news is what the trading bot consumes today. Headline
    # mentions (in_title) before passing body mentions.
    pending = con.execute(
        "SELECT t.link, t.symbol, a.title, a.summary FROM article_tickers t "
        "JOIN articles a ON a.link = t.link "
        "WHERE t.llm_sent IS NULL AND COALESCE(a.noise, 0) = 0 "
        "ORDER BY t.in_title DESC, a.fetched_at DESC "
        "LIMIT ?", (BATCH * MAX_CALLS_PER_RUN,)).fetchall()
    if not pending:
        con.close()
        print("LLM analyst: nothing new to score.")
        return

    import requests
    names = company_names()
    scored_n, served_last, disagreements = 0, None, []

    for c in range(0, len(pending), BATCH):
        rows = pending[c:c + BATCH]
        scores, served = score_batch(requests, gem_key, or_key, rows, names)
        if scores is None:
            print("LLM analyst: no provider available, stopping this run.")
            break
        served_last = served
        for i, (link, symbol, title, _) in enumerate(rows):
            score, novel = scores.get(i + 1, (0.0, 0))
            score = max(-1.0, min(1.0, score))
            con.execute(
                "UPDATE article_tickers SET llm_sent = ?, llm_novel = ? "
                "WHERE link = ? AND symbol = ?", (score, novel, link, symbol))
            scored_n += 1
            if novel and abs(score) >= 0.6:
                disagreements.append((symbol, score, title))
        con.commit()
        if c + BATCH < len(pending):
            time.sleep(2)  # be polite to the free tier

    # Legacy article-level column: mean of the article's per-ticker scores. Kept
    # so nothing that still reads articles.llm_sent breaks; per-ticker is truth.
    con.execute(
        "UPDATE articles SET llm_sent = ("
        "  SELECT AVG(llm_sent) FROM article_tickers t "
        "  WHERE t.link = articles.link AND t.llm_sent IS NOT NULL) "
        "WHERE link IN (SELECT link FROM article_tickers WHERE llm_sent IS NOT NULL)")
    con.commit()
    con.close()

    print(f"LLM analyst: scored {scored_n} (headline, company) pair(s) with {served_last}.")
    for symbol, score, title in disagreements[:5]:
        print(f"  novel & strong — {symbol} {score:+.2f}: {title[:65]}")


if __name__ == "__main__":
    main()
