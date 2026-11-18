"""Records the candidate's own application decisions and outcomes in the
append-only application log as `coach_v1` events. Nothing is inferred. A
decision's snapshot is copied from a saved OPERATIONAL Career Coach run; no
scoring, lens, strategy, card or engine code runs here.

    python scripts/coach_log.py decision --run RUN_DIR --job-id ID
        --decision SAVED|APPLIED|SKIPPED|DEFERRED --date YYYY-MM-DD
        [--effort LOW|MEDIUM|HIGH|UNKNOWN] [--notes TEXT] [--log FILE]
    python scripts/coach_log.py outcome --run RUN_DIR --job-id ID
        --outcome APPLIED|INTERVIEW|FINAL_ROUND|OFFER|REJECTED|WITHDRAWN|CLOSED --date YYYY-MM-DD
        [--notes TEXT] [--log FILE]
    python scripts/coach_log.py void --event-id N --job-id ID --date YYYY-MM-DD --reason TEXT [--log FILE]
    python scripts/coach_log.py history [--job-id ID] [--log FILE]

Dates must be real calendar days and not after today's local date. `void`
appends a `coach_void_v1` event marking event N (its id, as listed by
`history`) as entered in error; the original line is never changed, and the
job id must match the event's. Nothing is appended when any check fails.
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.career_coach.application_log import (  # noqa: E402
    DEFAULT_LOG,
    CoachDecisionEvent,
    CoachOutcomeEvent,
    LegacyEvent,
    append_event,
    decision_event,
    event_date,
    load_events,
    outcome_event,
    reconstruct,
    void_event,
    voids,
)


def _arg(argv: list[str], name: str, required: bool = True) -> str | None:
    if name in argv:
        return argv[argv.index(name) + 1]
    if required:
        raise SystemExit(f"missing {name}")
    return None


def _describe(e) -> str:
    if isinstance(e, LegacyEvent):
        return f"{e.raw['schema_version']} {e.event} {e.value}"
    if isinstance(e, CoachDecisionEvent):
        return f"{e.schema_version} decision {e.decision}"
    if isinstance(e, CoachOutcomeEvent):
        return f"{e.schema_version} outcome {e.outcome}"
    return f"{e.schema_version} void of event {e.event_id}: {e.reason}"


def history(log: Path, job_id: str | None) -> None:
    events = load_events(log)
    voided = voids(events)
    for jid, h in sorted(reconstruct(events).items()):
        if job_id not in (None, jid):
            continue
        print(f"{jid}: decision {h.current_decision} · outcome {h.current_outcome} · "
              f"decisions {h.decisions} · outcomes {h.outcomes}")
        for seq, e in enumerate(events):
            if e.job_id == jid:
                mark = f"  [VOIDED by event {voided[seq][0]}]" if seq in voided else ""
                print(f"  event {seq}  {event_date(e)}  {_describe(e)}{mark}")


def main():
    kind, argv = sys.argv[1], sys.argv[2:]
    log = Path(_arg(argv, "--log", required=False) or DEFAULT_LOG)
    if kind == "history":
        history(log, _arg(argv, "--job-id", required=False))
        return
    today = date.today()                                  # the only clock read: future dates are refused
    job_id, when = _arg(argv, "--job-id"), _arg(argv, "--date")
    try:
        if kind == "void":
            raw_id = _arg(argv, "--event-id")
            if not raw_id.isdigit():
                raise ValueError(f"--event-id must be an event id as listed by history, got {raw_id!r}")
            event = void_event(int(raw_id), job_id, when, _arg(argv, "--reason"), log, today=today)
        elif kind in ("decision", "outcome"):
            run_dir, notes = Path(_arg(argv, "--run")), _arg(argv, "--notes", required=False) or ""
            if kind == "decision":
                event = decision_event(run_dir, job_id, _arg(argv, "--decision"), when,
                                       _arg(argv, "--effort", required=False) or "UNKNOWN", notes, log, today=today)
            else:
                event = outcome_event(run_dir, job_id, _arg(argv, "--outcome"), when, notes, log, today=today)
        else:
            raise SystemExit("first argument must be 'decision', 'outcome', 'void' or 'history'")
    except ValueError as e:
        raise SystemExit(f"not recorded: {e}")
    event_id = len(load_events(log))
    append_event(event, log)
    print(f"{event.schema_version} {event.event} recorded for {job_id} as event {event_id} -> {log}")


if __name__ == "__main__":
    main()
