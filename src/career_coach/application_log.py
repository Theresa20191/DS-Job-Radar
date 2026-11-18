"""Career Coach application log — `coach_v1` (Gate 4).

A factual, append-only record of what the candidate explicitly did. Nothing
is inferred: a decision never creates an outcome, a job disappearing never
creates CLOSED/REJECTED, and no dashboard placement, frozen value or run
output ever produces an event. Out-of-order events are recorded as entered.

Storage is the existing `data/applications.jsonl` (gitignored), shared with
the legacy `application_log_v1` events. Every line is dispatched by its
explicit `schema_version` value; legacy lines are validated against their
own contract and kept verbatim, never coerced into `coach_v1`.

A decision snapshot is copied from a saved OPERATIONAL run's decisions.json
(no scoring, lens, strategy, card or engine call at logging time). Dates are
explicit YYYY-MM-DD arguments naming a real calendar day; the wall clock is
never read here — the CLI passes today's date in to refuse future dates.

Mistakes are never edited out. A `coach_void_v1` event marks ONE earlier
decision or outcome, named by its event id (its 0-based position in the log,
which append-only storage never changes), as entered in error. The voided
line stays in the file unchanged; derived state skips it. A void cannot be
voided, and an event can be voided once.
"""

import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from src.models.career_coach import CareerCoachDecision, JobRef, ResumeTrack, RunInfo

SCHEMA_VERSION = "coach_v1"
VOID_SCHEMA_VERSION = "coach_void_v1"
LEGACY_SCHEMA_VERSION = "application_log_v1"
DECISIONS = ("SAVED", "APPLIED", "SKIPPED", "DEFERRED")
OUTCOMES = ("APPLIED", "INTERVIEW", "FINAL_ROUND", "OFFER", "REJECTED", "WITHDRAWN", "CLOSED")
DECLARED_EFFORTS = ("LOW", "MEDIUM", "HIGH", "UNKNOWN")      # candidate-declared; never estimated
NOT_STARTED = "NOT_STARTED"
DEFAULT_LOG = Path(__file__).resolve().parent.parent.parent / "data" / "applications.jsonl"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_date_format(value: str) -> str:
    if not _DATE.match(value or ""):
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}")
    return value


def _check_date(value: str) -> str:
    """YYYY-MM-DD naming a real calendar day (2026-02-29 and 2026-02-30 are refused)."""
    _check_date_format(value)
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"date must be a real calendar day, got {value!r}") from None
    return value


def check_not_future(value: str, today: date | None) -> None:
    """Refuses an event dated after `today`. The caller supplies today's date
    (the CLI reads the clock; this module never does); None skips the check."""
    if today is not None and date.fromisoformat(value) > today:
        raise ValueError(f"date {value} is after today ({today.isoformat()}); events cannot be future-dated")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------ coach_v1 events

class CoachSnapshot(_Strict):
    """The Career Coach values for this job in the referenced OPERATIONAL run,
    exactly as stored there (V1.2 section R). Layer values are None for a
    hard-filter-rejected job (IN-4)."""
    run_dir: str
    run: RunInfo
    job: JobRef
    evaluation_scope: str
    viability: str
    blocking_rule: str | None
    career_value: str | None
    deciding_track: str | None
    declared_tier: str | None
    readiness: str | None
    readiness_rule: str | None
    candidate_measure: str | None
    seniority: str | None
    practical_constraint: str | None
    priority: str
    justified_investment: str
    required_investment: str | None
    investment_fit: str
    resume_track: ResumeTrack | Literal["NONE"] | None
    resume_effort: str | None
    research_effort: str | None
    next_action: str
    primary_reason_rule: str
    live_record_ids: list[str] = Field(default_factory=list)


class CoachDecisionEvent(_Strict):
    event: Literal["decision"] = "decision"
    schema_version: Literal["coach_v1"] = "coach_v1"
    job_id: str
    decision_date: str
    decision: str
    declared_effort: str = "UNKNOWN"
    notes: str = ""
    snapshot: CoachSnapshot

    @field_validator("decision_date")
    @classmethod
    def _date(cls, v):
        return _check_date(v)

    @model_validator(mode="after")
    def _values(self):
        if self.decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        if self.declared_effort not in DECLARED_EFFORTS:
            raise ValueError(f"declared_effort must be one of {DECLARED_EFFORTS}")
        if self.snapshot.job.job_id != self.job_id:
            raise ValueError("snapshot belongs to a different job")
        return self


class CoachOutcomeEvent(_Strict):
    event: Literal["outcome"] = "outcome"
    schema_version: Literal["coach_v1"] = "coach_v1"
    job_id: str
    outcome_date: str
    outcome: str
    notes: str = ""

    @field_validator("outcome_date")
    @classmethod
    def _date(cls, v):
        return _check_date(v)

    @model_validator(mode="after")
    def _values(self):
        if self.outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")
        return self


class LegacyEvent(_Strict):
    """An `application_log_v1` line, checked against that format's own
    contract and kept verbatim (read-only; never converted to coach_v1)."""
    raw: dict

    @property
    def event(self) -> str:
        return self.raw["event"]

    @property
    def job_id(self) -> str:
        return self.raw["job_id"]

    @property
    def value(self) -> str:
        return self.raw["decision" if self.event == "decision" else "outcome"]

    @property
    def date(self) -> str:
        return self.raw["decision_date" if self.event == "decision" else "outcome_date"]

    @model_validator(mode="after")
    def _legacy_contract(self):
        raw = self.raw
        if raw.get("schema_version") != LEGACY_SCHEMA_VERSION:
            raise ValueError("not an application_log_v1 line")
        if raw.get("event") == "decision":
            ok = raw.get("decision") in DECISIONS and isinstance(raw.get("snapshot"), dict) \
                and raw["snapshot"].get("job_id") == raw.get("job_id")
            _check_date_format(raw.get("decision_date"))          # the legacy contract, unchanged
        elif raw.get("event") == "outcome":
            ok = raw.get("outcome") in OUTCOMES
            _check_date_format(raw.get("outcome_date"))
        else:
            ok = False
        if not ok or not raw.get("job_id"):
            raise ValueError("line does not satisfy the application_log_v1 contract")
        return self


class VoidEvent(_Strict):
    """`coach_void_v1`: the decision or outcome with this event id was entered
    in error. It is never an application decision or outcome itself, and its
    reason is kept verbatim for the record only (never analysed)."""
    event: Literal["void"] = "void"
    schema_version: Literal["coach_void_v1"] = "coach_void_v1"
    event_id: StrictInt = Field(ge=0)               # 0-based position of the voided event in the log
    job_id: str                                     # must be the voided event's job (explicit cross-check)
    void_date: str
    reason: str

    @field_validator("void_date")
    @classmethod
    def _date(cls, v):
        return _check_date(v)

    @field_validator("reason")
    @classmethod
    def _reason(cls, v):
        if not v.strip():
            raise ValueError("a void needs a reason")
        return v


Event = CoachDecisionEvent | CoachOutcomeEvent | LegacyEvent | VoidEvent


def event_date(e: Event) -> str:
    if isinstance(e, LegacyEvent):
        return e.date
    if isinstance(e, CoachDecisionEvent):
        return e.decision_date
    if isinstance(e, CoachOutcomeEvent):
        return e.outcome_date
    return e.void_date


# -------------------------------------------------------------------- storage

def append_event(event: CoachDecisionEvent | CoachOutcomeEvent | VoidEvent, path: Path = DEFAULT_LOG) -> None:
    """Adds exactly one line; existing lines are never read back or rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(event.model_dump_json() + "\n")


def parse_line(data: dict) -> Event:
    """Dispatch strictly by the explicit schema_version value (B-7)."""
    version = data.get("schema_version")
    if version == SCHEMA_VERSION:
        kind = data.get("event")
        if kind == "decision":
            return CoachDecisionEvent.model_validate(data)
        if kind == "outcome":
            return CoachOutcomeEvent.model_validate(data)
        raise ValueError(f"unknown coach_v1 event {kind!r}")
    if version == VOID_SCHEMA_VERSION:
        if data.get("event") != "void":
            raise ValueError(f"unknown coach_void_v1 event {data.get('event')!r}")
        return VoidEvent.model_validate(data)
    if version == LEGACY_SCHEMA_VERSION:
        return LegacyEvent(raw=data)
    raise ValueError(f"unknown or missing schema_version {version!r}")


def voids(events: list[Event]) -> dict[int, tuple[int, VoidEvent]]:
    """{voided event id: (void event id, void)}. Every void must name an
    EARLIER decision/outcome of the same job that no earlier void names."""
    found: dict[int, tuple[int, VoidEvent]] = {}
    for seq, e in enumerate(events):
        if not isinstance(e, VoidEvent):
            continue
        if e.event_id >= seq:
            raise ValueError(f"event {seq}: a void can only name an earlier event, not {e.event_id}")
        target = events[e.event_id]
        if isinstance(target, VoidEvent):
            raise ValueError(f"event {seq}: event {e.event_id} is itself a void; a void cannot be voided")
        if target.job_id != e.job_id:
            raise ValueError(f"event {seq}: event {e.event_id} belongs to {target.job_id}, not {e.job_id}")
        if e.event_id in found:
            raise ValueError(f"event {seq}: event {e.event_id} is already voided by event {found[e.event_id][0]}")
        found[e.event_id] = (seq, e)
    return found


def load_events(path: Path = DEFAULT_LOG) -> list[Event]:
    """Every event in file order; an event's id is its index in this list."""
    if not path.exists():
        return []
    events = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if line.strip():
            try:
                events.append(parse_line(json.loads(line)))
            except ValueError as e:
                raise ValueError(f"{path}:{number}: {e}") from e
    try:
        voids(events)                                   # a log with an invalid void is refused as a whole
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e
    return events


# -------------------------------------------------------------- reconstruction

class ApplicationHistory(_Strict):
    """One job's history folded from its events in file order. Every event that
    has not been voided is kept; the latest recorded outcome is current; voided
    events are listed separately and take no part."""
    job_id: str
    decisions: list[tuple[str, str, str]] = Field(default_factory=list)     # (schema_version, date, decision)
    outcomes: list[tuple[str, str, str]] = Field(default_factory=list)      # (schema_version, date, outcome)
    current_decision: str | None = None
    current_outcome: str = NOT_STARTED
    first_outcome_dates: dict[str, str] = Field(default_factory=dict)
    voided: list[int] = Field(default_factory=list)                         # ids of this job's voided events


def reconstruct(events: list[Event]) -> dict[str, ApplicationHistory]:
    histories: dict[str, ApplicationHistory] = {}
    voided = voids(events)
    for seq, e in enumerate(events):
        h = histories.setdefault(e.job_id, ApplicationHistory(job_id=e.job_id))
        if isinstance(e, VoidEvent):
            continue
        if seq in voided:
            h.voided.append(seq)
            continue
        if isinstance(e, LegacyEvent):
            version, kind, value, when = LEGACY_SCHEMA_VERSION, e.event, e.value, e.date
        elif isinstance(e, CoachDecisionEvent):
            version, kind, value, when = SCHEMA_VERSION, "decision", e.decision, e.decision_date
        else:
            version, kind, value, when = SCHEMA_VERSION, "outcome", e.outcome, e.outcome_date
        if kind == "decision":
            h.decisions.append((version, when, value))
            h.current_decision = value
        else:
            h.outcomes.append((version, when, value))
            h.current_outcome = value
            h.first_outcome_dates.setdefault(value, when)
    return histories


# ------------------------------------------------------ run + identity (B-2, B-4)

def load_run(run_dir: Path) -> dict[str, CareerCoachDecision]:
    """The saved decisions of one run, keyed by job_id (schema-validated)."""
    decisions = [CareerCoachDecision.model_validate(d) for d in json.loads((run_dir / "decisions.json").read_text())]
    return {d.job.job_id: d for d in decisions}


def check_job_known(job_id: str, run: dict[str, CareerCoachDecision], events: list[Event]) -> None:
    """B-4: valid if in the referenced run OR already in the application log
    (through an event that has not been voided)."""
    voided = voids(events)
    logged = {e.job_id for seq, e in enumerate(events) if not isinstance(e, VoidEvent) and seq not in voided}
    if job_id not in run and job_id not in logged:
        raise ValueError(f"unknown job_id {job_id!r}: not in the referenced run and not in the application log")


def snapshot_from(decision: CareerCoachDecision, run_dir: Path) -> CoachSnapshot:
    cv, rd, ca = decision.career_value, decision.readiness, decision.candidate_alignment
    tier = next((m.declared_tier for m in cv.matched_tracks if m.track == cv.deciding_track), None) if cv else None
    inv = decision.application_investment
    return CoachSnapshot(
        run_dir=str(run_dir), run=decision.run, job=decision.job, evaluation_scope=decision.evaluation_scope,
        viability=decision.application_viability.state,
        blocking_rule=decision.blocking_reason.rule_id if decision.blocking_reason else None,
        career_value=cv.state if cv else None, deciding_track=cv.deciding_track if cv else None, declared_tier=tier,
        readiness=rd.state if rd else None, readiness_rule=rd.deciding_rule if rd else None,
        candidate_measure=ca.experience.candidate_measure if ca else None,
        seniority=ca.seniority.state if ca else None,
        practical_constraint=decision.practical_constraint.state if decision.practical_constraint else None,
        priority=decision.application_priority, justified_investment=inv.justified, required_investment=inv.required,
        investment_fit=inv.fit, resume_track=decision.resume_track, resume_effort=decision.resume_effort,
        research_effort=decision.research_effort, next_action=decision.next_action,
        primary_reason_rule=decision.primary_reason.rule_id,
        live_record_ids=sorted({f.live_record_id for f in decision.application_viability.factors if f.live_record_id}),
    )


def decision_event(run_dir: Path, job_id: str, decision: str, decision_date: str, declared_effort: str = "UNKNOWN",
                   notes: str = "", log: Path = DEFAULT_LOG, today: date | None = None) -> CoachDecisionEvent:
    """A candidate decision with its snapshot copied from a saved OPERATIONAL run."""
    run = load_run(run_dir)
    check_job_known(job_id, run, load_events(log))
    if job_id not in run:
        raise ValueError(f"{job_id} has no decision in {run_dir}; a decision snapshot must come from that run")
    source = run[job_id]
    if source.run.run_kind != "OPERATIONAL":
        raise ValueError(f"{run_dir} is a {source.run.run_kind} run; only OPERATIONAL runs can back a logged decision")
    event = CoachDecisionEvent(job_id=job_id, decision_date=decision_date, decision=decision,
                               declared_effort=declared_effort, notes=notes, snapshot=snapshot_from(source, run_dir))
    check_not_future(decision_date, today)
    return event


def outcome_event(run_dir: Path, job_id: str, outcome: str, outcome_date: str, notes: str = "",
                  log: Path = DEFAULT_LOG, today: date | None = None) -> CoachOutcomeEvent:
    check_job_known(job_id, load_run(run_dir), load_events(log))
    event = CoachOutcomeEvent(job_id=job_id, outcome=outcome, outcome_date=outcome_date, notes=notes)
    check_not_future(outcome_date, today)
    return event


def void_event(event_id: int, job_id: str, void_date: str, reason: str, log: Path = DEFAULT_LOG,
               today: date | None = None) -> VoidEvent:
    """Marks the decision or outcome with this event id as entered in error.
    Nothing is inferred: the id and the job must both be given and must agree."""
    events = load_events(log)
    if not 0 <= event_id < len(events):
        raise ValueError(f"no event {event_id} in {log} (it holds events 0 to {len(events) - 1})" if events
                         else f"no event {event_id}: {log} holds no events")
    target = events[event_id]
    if isinstance(target, VoidEvent):
        raise ValueError(f"event {event_id} is itself a void; a void cannot be voided")
    if target.job_id != job_id:
        raise ValueError(f"event {event_id} belongs to {target.job_id}, not {job_id}")
    voided = voids(events)
    if event_id in voided:
        raise ValueError(f"event {event_id} is already voided by event {voided[event_id][0]}")
    event = VoidEvent(event_id=event_id, job_id=job_id, void_date=void_date, reason=reason)
    if void_date < event_date(target):
        raise ValueError(f"void date {void_date} is before the date of event {event_id} ({event_date(target)})")
    check_not_future(void_date, today)
    return event
