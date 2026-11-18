"""Canonical outcome state (Gate 5 V1, layer C) — derived, recomputable,
never written back into the event log.

One state per job_id (D6), folded from the raw event stream in file order.
Nothing is inferred: a submission exists only when an APPLIED outcome was
recorded (D2), a job stays OPEN until a terminal outcome is recorded, and no
timeout ever closes it (D4). Anomaly flags describe the stream; they never
change it (D10).

Contract — `resolution` and `current_status` are separate fields:
  resolution      "Has any terminal application outcome ever been recorded?"
                  NO_SUBMISSION  no APPLIED outcome was ever recorded — even if a
                                 terminal event such as CLOSED was;
                  TERMINAL       an APPLIED outcome and any of OFFER, REJECTED,
                                 WITHDRAWN, CLOSED were ever recorded (D3);
                  OPEN           an APPLIED outcome, and no terminal outcome ever.
  current_status  the latest recorded outcome in file order, whatever it is.
So APPLIED -> REJECTED -> INTERVIEW is resolution TERMINAL, current_status
INTERVIEW, flagged OUT_OF_ORDER; the events themselves are never rewritten,
discarded or normalised.

Voids: an event named by a `coach_void_v1` event was entered in error. It
stays in the raw log and is listed in `voided`, but it takes no part in the
canonical state (submission, milestones, resolution, decisions, dates,
anomalies). The void itself is never a decision or an outcome. A job whose
every event was voided has no canonical state.
"""

from dataclasses import dataclass, field
from datetime import date

from src.career_coach.application_log import (
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    CoachDecisionEvent,
    Event,
    LegacyEvent,
    VoidEvent,
    voids,
)

TERMINAL = ("OFFER", "REJECTED", "WITHDRAWN", "CLOSED")             # D3
MILESTONES = ("APPLIED", "INTERVIEW", "FINAL_ROUND", "OFFER")
FUNNEL_RANK = {stage: n for n, stage in enumerate(MILESTONES, start=1)}
EMPLOYER_STAGES = ("INTERVIEW", "FINAL_ROUND", "OFFER", "REJECTED")  # outcomes that presuppose a submission
ANOMALIES = ("OUT_OF_ORDER", "DUPLICATE_STAGE", "OUTCOME_WITHOUT_SUBMISSION")
SEQUENCE_ANOMALIES = ("OUT_OF_ORDER", "OUTCOME_WITHOUT_SUBMISSION")  # break metrics that depend on ordering


@dataclass(frozen=True)
class EventRecord:
    seq: int                   # position in the log file (0-based)
    schema_version: str
    kind: str                  # decision | outcome
    value: str
    date: str
    event: Event


@dataclass
class JobOutcomeState:
    job_id: str
    events: list[EventRecord] = field(default_factory=list)
    schema_versions: list[str] = field(default_factory=list)
    submitted: bool = False                         # an APPLIED outcome was recorded (D2)
    decided_applied_without_submission: bool = False
    first_dates: dict[str, str] = field(default_factory=dict)     # stage -> first recorded date
    reached: dict[str, bool] = field(default_factory=dict)        # milestone -> ever recorded (any order)
    current_status: str = "NOT_STARTED"             # latest recorded outcome, file order
    resolution: str = "NO_SUBMISSION"               # TERMINAL | OPEN | NO_SUBMISSION
    terminal_outcome: str | None = None             # latest terminal outcome recorded
    last_event_date: str | None = None
    days_since_last_event: int | None = None        # OPEN only, against the explicit report date
    anomalies: list[str] = field(default_factory=list)
    voided: list[EventRecord] = field(default_factory=list)       # raw events voided as entered in error

    @property
    def decisions(self) -> list[EventRecord]:
        return [e for e in self.events if e.kind == "decision"]

    @property
    def outcomes(self) -> list[EventRecord]:
        return [e for e in self.events if e.kind == "outcome"]


def to_record(seq: int, e: Event) -> EventRecord:
    if isinstance(e, LegacyEvent):
        return EventRecord(seq, LEGACY_SCHEMA_VERSION, e.event, e.value, e.date, e)
    if isinstance(e, CoachDecisionEvent):
        return EventRecord(seq, SCHEMA_VERSION, "decision", e.decision, e.decision_date, e)
    return EventRecord(seq, SCHEMA_VERSION, "outcome", e.outcome, e.outcome_date, e)


def anomalies(outcomes: list[EventRecord]) -> list[str]:
    values = [o.value for o in outcomes]
    found = set()
    if len(values) != len(set(values)):
        found.add("DUPLICATE_STAGE")
    if "APPLIED" not in values and any(v in EMPLOYER_STAGES for v in values):
        found.add("OUTCOME_WITHOUT_SUBMISSION")
    for prev, cur in zip(outcomes, outcomes[1:]):
        if cur.date < prev.date:
            found.add("OUT_OF_ORDER")                                   # dates run backwards in file order
        if cur.value in FUNNEL_RANK and prev.value in FUNNEL_RANK and FUNNEL_RANK[cur.value] < FUNNEL_RANK[prev.value]:
            found.add("OUT_OF_ORDER")                                   # funnel stage goes backwards
    for n, o in enumerate(outcomes):
        if o.value in TERMINAL and any(later.value != o.value for later in outcomes[n + 1:]):
            found.add("OUT_OF_ORDER")                                   # a different event after a terminal one
    return [a for a in ANOMALIES if a in found]


def voided_records(events: list[Event]) -> list[tuple[EventRecord, int, str]]:
    """Every voided event, as (its record, the void's event id, the void date)."""
    return [(to_record(target, events[target]), void_seq, void.void_date)
            for target, (void_seq, void) in sorted(voids(events).items())]


def build_states(events: list[Event], report_date: date) -> dict[str, JobOutcomeState]:
    states: dict[str, JobOutcomeState] = {}
    voided = voids(events)
    for seq, e in enumerate(events):
        if isinstance(e, VoidEvent) or seq in voided:
            continue
        states.setdefault(e.job_id, JobOutcomeState(job_id=e.job_id)).events.append(to_record(seq, e))
    for record, _, _ in voided_records(events):
        if record.event.job_id in states:
            states[record.event.job_id].voided.append(record)
    for s in states.values():
        outs = s.outcomes
        s.schema_versions = sorted({e.schema_version for e in s.events})
        for o in outs:
            s.first_dates.setdefault(o.value, o.date)
        s.reached = {m: m in s.first_dates for m in MILESTONES}
        s.submitted = s.reached["APPLIED"]
        s.decided_applied_without_submission = not s.submitted and any(d.value == "APPLIED" for d in s.decisions)
        s.current_status = outs[-1].value if outs else "NOT_STARTED"
        terminals = [o.value for o in outs if o.value in TERMINAL]
        s.terminal_outcome = terminals[-1] if terminals else None
        s.resolution = "NO_SUBMISSION" if not s.submitted else ("TERMINAL" if terminals else "OPEN")
        s.last_event_date = max(e.date for e in s.events)
        if s.resolution == "OPEN":
            s.days_since_last_event = (report_date - date.fromisoformat(s.last_event_date)).days
        s.anomalies = anomalies(outs)
    return dict(sorted(states.items()))
