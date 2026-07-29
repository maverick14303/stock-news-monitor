"""Run the full pipeline once and save a digest markdown file.

Triggered by cron-job.org against the GitHub Actions workflow_dispatch API;
can also be run by hand. Digests land in digests/ with the latest copy always
at digests/LATEST.md.

Two accounts are tracked: the self-trading bot and the NIFTY 50 shadow. The
manual "you + Claude" paper account was retired 2026-07-30 (paper_portfolio.py
stays as the shared price/fee helper that alerts.py and autotrader.py import).
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
DIGESTS = BASE / "digests"

STEPS = [
    # Idempotent, ~20s over 10k articles. Runs EVERY time on purpose: it is what
    # makes the cloud self-heal after a tickers.csv or noise-rule change, so the
    # derived columns are always reproducible from the raw articles rather than
    # depending on a committed database being in the right state.
    ("Rescan history (tickers, noise, after-hours)", ["migrate.py"]),
    ("News & signals", ["monitor.py"]),
    # Per-(headline, company) scoring. Runs before the scoreboard so this run's
    # fresh news is graded with LLM scores, not the VADER fallback.
    ("LLM analyst (per-ticker)", ["llm_analyst.py"]),
    ("Signal scoreboard (last 30 days)", ["scoreboard.py"]),
    ("Bot account (self-trading)", ["autotrader.py"]),
    ("Alert scan", ["alerts.py"]),
    # export runs LAST so it reflects this run's fresh news + updated grades;
    # news_signal.json is consumed by trading-bot's fused momentum+news bot.
    ("Sentiment export", ["export_signal.py"]),
]


def main():
    DIGESTS.mkdir(exist_ok=True)
    now = datetime.now()
    parts = [f"# Stock digest — {now:%Y-%m-%d %H:%M}\n"]
    failures = []
    for heading, args in STEPS:
        r = subprocess.run(
            [sys.executable, str(BASE / args[0]), *args[1:]],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=BASE, timeout=600,
        )
        body = r.stdout.strip()
        if r.returncode != 0:
            body += f"\n[FAILED exit {r.returncode}]\n{r.stderr.strip()[-1000:]}"
            failures.append(f"{heading} ({args[0]} exit {r.returncode})")
        parts.append(f"## {heading}\n\n```\n{body}\n```\n")

    digest = "\n".join(parts)
    out = DIGESTS / f"{now:%Y-%m-%d_%H%M}.md"
    out.write_text(digest, encoding="utf-8")
    (DIGESTS / "LATEST.md").write_text(digest, encoding="utf-8")
    print(f"Digest written to {out}")

    # Exit non-zero so the runner goes RED. This used to always exit 0, which is
    # exactly how a crashing scrape hid for ~3.5 weeks in July 2026: the failure
    # was written into the digest, nobody reads every digest, and Actions showed
    # a green tick on all 407 broken runs. Silence must never mean success.
    # The digest is written BEFORE this, and the workflow commits with
    # `if: always()`, so a red run still preserves its evidence.
    if failures:
        print(f"\n[PIPELINE FAILED] {len(failures)} of {len(STEPS)} steps:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
