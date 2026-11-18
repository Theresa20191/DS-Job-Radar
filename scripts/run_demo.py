"""Offline demonstration of the full DS Job Radar pipeline on SYNTHETIC data.

    python scripts/run_demo.py

1. Copies the synthetic raw files in demo/raw/ into data/raw/<source>_latest.jsonl,
   holding the pipeline lock while it writes. It refuses, changing nothing, if
   data/raw/ already holds different raw files (your own data is never overwritten).
2. Runs the unchanged daily run on them, offline:
       python scripts/run_all.py --skip-scrape --as-of 2026-01-15 --live-checks demo/live_checks.jsonl
   --skip-scrape means no collector is invoked and no network request is made: the
   pipeline, Career Coach, audit, archive and dashboard stages run on the files above.

Every company, posting, email address and live-check record in demo/ is fictional.
The live-check records are synthetic observations; no website was visited.
Outputs: data/output/career_coach_operational/ (decisions, manifest, dashboard) and
data/archive/runs/ (the run archive). The demo's audit verdict is INVESTIGATE, not
PASS, and the audit says why: the synthetic raw files have no scrape report, and the
synthetic postings use example.test URLs, so the audit cannot tie any live check to a
current MyCareersFuture posting ID (it accepts live evidence as current only for a
real posting ID).
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.career_coach import run_lock  # noqa: E402
from src.career_coach.operational import enabled_sources  # noqa: E402

DEMO = ROOT / "demo"
AS_OF = "2026-01-15"


def stage_inputs(lock) -> int:
    """Copies the synthetic raw files into data/raw/ while holding the lock."""
    raw_dir = ROOT / "data" / "raw"
    copies = []
    for source in enabled_sources():
        src, dest = DEMO / "raw" / f"{source}_latest.jsonl", raw_dir / f"{source}_latest.jsonl"
        data = src.read_bytes()
        if dest.exists() and dest.read_bytes() != data:
            print(f"run_demo: {dest.relative_to(ROOT)} already holds other raw data; the demo will not overwrite it. "
                  "Move it away to run the demo.", file=sys.stderr)
            return 2
        copies.append((dest, data))
    for dest, data in copies:
        run_lock.require_lock(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return 0


def main() -> int:
    code = run_lock.run_locked("run_demo", stage_inputs)
    if code != 0:
        return code
    command = [sys.executable, str(ROOT / "scripts" / "run_all.py"), "--skip-scrape", "--as-of", AS_OF,
               "--live-checks", str(DEMO / "live_checks.jsonl")]
    print("run_demo: synthetic raw files in place; running:", " ".join(["python"] + [str(Path(c).relative_to(ROOT))
          if str(c).startswith(str(ROOT)) else c for c in command[1:]]), flush=True)
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
