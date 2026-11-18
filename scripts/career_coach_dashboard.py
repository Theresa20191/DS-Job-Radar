"""Renders the Career Coach dashboard from a run's decisions.json and
run_meta.json (every decision is schema-validated first).

    python scripts/career_coach_dashboard.py RUN_DIR [--archive-root DIR] [--live-checks FILE]

Writes RUN_DIR/dashboard.md, RUN_DIR/opportunities.csv and RUN_DIR/opportunities.html.
The archive root (default data/archive/runs) and live-check file (default
data/live_checks.jsonl) are only read, for display-only context: the daily
delta against the previous comparable operational run, the archive status and
the observed values of the live records the decisions cite.

It holds the pipeline lock (src/career_coach/run_lock.py) and refuses with
exit 5 when another process holds it (exit 1 if the lock cannot be set up). It never renders into the active
operational output folder (data/output/career_coach_operational) or that
folder's dot-siblings: only run_all writes there.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.career_coach import run_lock  # noqa: E402
from src.career_coach.operational import is_active_output, render_dashboard  # noqa: E402
from src.career_coach.run_archive import ARCHIVE_ROOT  # noqa: E402


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def main():
    run_dir = Path(sys.argv[1:][0])
    if is_active_output(run_dir):
        print(f"career_coach_dashboard: REFUSED: {run_dir} is the active operational output folder; only run_all "
              "writes there (re-run run_all instead)", file=sys.stderr)
        sys.exit(2)
    run_lock.run_locked("career_coach_dashboard", _main)


def _main(lock):
    argv = sys.argv[1:]
    run_dir = Path(argv[0])
    dashboard = render_dashboard(run_dir, archive_root=Path(_arg(argv, "--archive-root") or ARCHIVE_ROOT),
                                 live_path=Path(_arg(argv, "--live-checks") or ROOT / "data" / "live_checks.jsonl"))
    print(f"dashboard for {len(dashboard['all'])} decisions -> {run_dir} · {dashboard['counts']}")


if __name__ == "__main__":
    main()
