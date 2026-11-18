"""Application log (Application Strategy V1, D4) — schema and local storage.

The candidate records what they decided and what happened; the system never
infers a decision, an effort or an outcome, and builds no prediction or
calibration. Storage is an append-only JSON Lines file (default
data/applications.jsonl, kept out of git like data/contacts.csv): one event
per line, so history is never rewritten.

A decision event carries a snapshot of the frozen and lens VALUES at decision
time. Dates are explicit arguments (the wall clock is never read), so the same
inputs always produce the same bytes.
"""

import json
from pathlib import Path

from src.models.scored_job import ScoredJob
from src.models.strategy import (
    ApplicationRecord, ApplicationStrategy, DecisionEvent, JobSnapshot, OutcomeEvent, StabilitySnapshot,
)

DEFAULT_LOG = Path(__file__).resolve().parent.parent.parent / "data" / "applications.jsonl"


def snapshot(job: ScoredJob, strategy: ApplicationStrategy) -> JobSnapshot:
    if strategy.job_id != job.job_id:
        raise ValueError(f"strategy {strategy.job_id} does not belong to job {job.job_id}")
    stability = job.decision_stability
    return JobSnapshot(
        job_id=job.job_id, company=job.company, job_title=job.job_title, canonical_url=job.canonical_url,
        as_of_date=job.as_of_date, frozen_action=job.recommended_action, tier=job.tier, overall_score=job.overall_score,
        decision_stability=StabilitySnapshot(stability_label=stability.stability_label, tier_floor=stability.tier_floor,
                                             tier_ceiling=stability.tier_ceiling,
                                             open_unknowns=list(strategy.frozen_axis.open_unknowns)),
        trajectory_direction=job.transition_lens.trajectory.direction,
        matched_tracks=list(strategy.career_axis.tracks), queue_tier=strategy.career_axis.queue_tier,
        declared_blocks=list(strategy.declared_blocks),
        transition_lens_version=job.transition_lens.version, ptf_scale=job.personal_transition_fit.scale,
        card_version=strategy.card_version, strategy_version=strategy.version,
    )


def decision_event(job: ScoredJob, strategy: ApplicationStrategy, decision: str, decision_date: str,
                   declared_effort: str = "UNKNOWN", notes: str = "") -> DecisionEvent:
    return DecisionEvent(job_id=job.job_id, decision_date=decision_date, decision=decision,
                         declared_effort=declared_effort, notes=notes, snapshot=snapshot(job, strategy))


def outcome_event(job_id: str, outcome: str, outcome_date: str, notes: str = "") -> OutcomeEvent:
    return OutcomeEvent(job_id=job_id, outcome=outcome, outcome_date=outcome_date, notes=notes)


def append_event(event: DecisionEvent | OutcomeEvent, path: Path = DEFAULT_LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(event.model_dump_json() + "\n")


def load_events(path: Path = DEFAULT_LOG) -> list[DecisionEvent | OutcomeEvent]:
    if not path.exists():
        return []
    events = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        data = json.loads(line)
        kind = data.get("event")
        if kind == "decision":
            events.append(DecisionEvent.model_validate(data))
        elif kind == "outcome":
            events.append(OutcomeEvent.model_validate(data))
        else:
            raise ValueError(f"{path}:{number}: unknown event {kind!r}")
    return events


def fold(events: list[DecisionEvent | OutcomeEvent]) -> dict[str, ApplicationRecord]:
    """One record per job, in file order: every decision kept, the latest
    outcome current, the first date of each outcome stage kept."""
    records: dict[str, ApplicationRecord] = {}
    for event in events:
        record = records.setdefault(event.job_id, ApplicationRecord(job_id=event.job_id))
        if isinstance(event, DecisionEvent):
            record.decisions.append(event)
        else:
            record.outcome_events.append(event)
            record.outcome = event.outcome
            record.outcome_dates.setdefault(event.outcome, event.outcome_date)
    return records
