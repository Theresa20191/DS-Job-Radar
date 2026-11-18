"""LEGACY — DO NOT USE FOR PRODUCTION APPLICATION LOGGING.
Production logging is `scripts/coach_log.py` (`coach_v1` / `coach_void_v1`).
This script runs the legacy scoring pipeline and strategy code and writes
legacy `application_log_v1` events to the shared data/applications.jsonl.

Records the candidate's own application decisions and outcomes in the
append-only application log (Application Strategy V1, D4). Nothing is
inferred: every value comes from these arguments or, for a decision, from the
snapshot of the job's frozen and lens values in the named scoring run.

    python scripts/log_application.py decision --as-of YYYY-MM-DD --job-id ID
        --decision APPLIED|SKIPPED|DEFERRED|SAVED --date YYYY-MM-DD
        [--effort LOW|MEDIUM|HIGH|UNKNOWN] [--notes TEXT] [--log FILE]
    python scripts/log_application.py outcome --job-id ID
        --outcome APPLIED|INTERVIEW|FINAL_ROUND|OFFER|REJECTED|WITHDRAWN|CLOSED --date YYYY-MM-DD
        [--notes TEXT] [--log FILE]
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_profile  # noqa: E402
from src.pipeline.orchestrator import run  # noqa: E402
from src.strategy.application import build_strategies  # noqa: E402
from src.strategy.application_log import DEFAULT_LOG, append_event, decision_event, outcome_event  # noqa: E402


def _arg(argv: list[str], name: str, required: bool = True) -> str | None:
    if name in argv:
        return argv[argv.index(name) + 1]
    if required:
        raise SystemExit(f"missing {name}")
    return None


def main():
    kind, argv = sys.argv[1], sys.argv[2:]
    log = Path(_arg(argv, "--log", required=False) or DEFAULT_LOG)
    job_id, when, notes = _arg(argv, "--job-id"), _arg(argv, "--date"), _arg(argv, "--notes", required=False) or ""
    if kind == "decision":
        scored = run(ROOT, as_of_date=date.fromisoformat(_arg(argv, "--as-of")))["scored_jobs"]
        job = next((j for j in scored if j.job_id == job_id), None)
        if job is None:
            raise SystemExit(f"{job_id} is not in that scoring run")
        strategy = next(s for s in build_strategies(scored, load_profile()) if s.job_id == job_id)
        event = decision_event(job, strategy, _arg(argv, "--decision"), when,
                               _arg(argv, "--effort", required=False) or "UNKNOWN", notes)
    elif kind == "outcome":
        event = outcome_event(job_id, _arg(argv, "--outcome"), when, notes)
    else:
        raise SystemExit("first argument must be 'decision' or 'outcome'")
    append_event(event, log)
    print(f"{event.event} recorded for {job_id} -> {log}")


if __name__ == "__main__":
    main()
