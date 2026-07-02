"""Run the full pipeline once and save a digest markdown file.

Called by Windows Task Scheduler twice a day; can also be run by hand.
Digests land in digests/ with the latest copy always at digests/LATEST.md.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
DIGESTS = BASE / "digests"

STEPS = [
    ("News & signals", ["monitor.py"]),
    ("Paper portfolio (Rs 5000 virtual)", ["paper_portfolio.py", "status"]),
    ("Signal scoreboard (last 30 days)", ["scoreboard.py"]),
]


def main():
    DIGESTS.mkdir(exist_ok=True)
    now = datetime.now()
    parts = [f"# Stock digest — {now:%Y-%m-%d %H:%M}\n"]
    for heading, args in STEPS:
        r = subprocess.run(
            [sys.executable, str(BASE / args[0]), *args[1:]],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=BASE, timeout=600,
        )
        body = r.stdout.strip()
        if r.returncode != 0:
            body += f"\n[FAILED exit {r.returncode}]\n{r.stderr.strip()[-1000:]}"
        parts.append(f"## {heading}\n\n```\n{body}\n```\n")

    digest = "\n".join(parts)
    out = DIGESTS / f"{now:%Y-%m-%d_%H%M}.md"
    out.write_text(digest, encoding="utf-8")
    (DIGESTS / "LATEST.md").write_text(digest, encoding="utf-8")
    print(f"Digest written to {out}")


if __name__ == "__main__":
    main()
