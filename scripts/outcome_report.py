"""V1 descriptive outcome report (Gate 5): application log -> canonical
outcome state -> analysis dataset -> report. Read-only over the log and the
run archive; nothing is written back to either.

    python scripts/outcome_report.py --as-of YYYY-MM-DD [--log FILE] [--archive-root DIR] [--out DIR]

Writes OUT/outcome_report.md, OUT/analysis_dataset.json and OUT/outcome_state.json
(default OUT: data/output/learning).
"""

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.career_coach.application_log import DEFAULT_LOG, LegacyEvent, load_events  # noqa: E402
from src.career_coach.run_archive import ARCHIVE_ROOT  # noqa: E402
from src.learning.dataset import build_rows  # noqa: E402
from src.learning.outcome_state import build_states, voided_records  # noqa: E402
from src.learning.reports import render  # noqa: E402


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def build(log: Path, archive_root: Path, report_date: date) -> tuple[str, list[dict], dict]:
    events = load_events(log)
    states = build_states(events, report_date)
    rows = build_rows(states, archive_root)
    meta = {"report_date": report_date.isoformat(), "log": str(log), "archive_root": str(archive_root),
            "event_count": len(events),
            "events_by_schema": dict(Counter(e.raw["schema_version"] if isinstance(e, LegacyEvent) else e.schema_version
                                             for e in events)),
            "archive_count": sum(1 for p in archive_root.glob("*/archive.json")) if archive_root.exists() else 0,
            "voided_events": [{"event_id": r.seq, "job_id": r.event.job_id, "schema_version": r.schema_version,
                               "kind": r.kind, "value": r.value, "date": r.date, "void_event_id": void_seq,
                               "void_date": void_date} for r, void_seq, void_date in voided_records(events)]}
    state_dump = {j: {"events": [(e.seq, e.schema_version, e.kind, e.value, e.date) for e in s.events],
                      "voided": [e.seq for e in s.voided],
                      "resolution": s.resolution, "current_status": s.current_status, "anomalies": s.anomalies}
                  for j, s in states.items()}
    return render(rows, meta), rows, state_dump


def main():
    argv = sys.argv[1:]
    if not _arg(argv, "--as-of"):
        raise SystemExit("--as-of YYYY-MM-DD is required (the report never reads the clock)")
    report_date = date.fromisoformat(_arg(argv, "--as-of"))
    log = Path(_arg(argv, "--log") or DEFAULT_LOG)
    archive_root = Path(_arg(argv, "--archive-root") or ARCHIVE_ROOT)
    out = Path(_arg(argv, "--out") or ROOT / "data" / "output" / "learning")
    report, rows, states = build(log, archive_root, report_date)
    out.mkdir(parents=True, exist_ok=True)
    (out / "outcome_report.md").write_text(report)
    (out / "analysis_dataset.json").write_text(json.dumps(rows, indent=1, sort_keys=True, default=str))
    (out / "outcome_state.json").write_text(json.dumps(states, indent=1, sort_keys=True))
    print(f"outcome report for {len(rows)} jobs ({sum(r['out_submitted'] for r in rows)} submitted) -> {out}")


if __name__ == "__main__":
    main()
