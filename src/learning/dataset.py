"""Analysis dataset (Gate 5 V1, layer D): one row per job_id (D6).

Column groups are never mixed:
  src_*  provenance (event schema versions, snapshot source, archive identity)
  ctx_*  decision-time values ONLY — copied from the selected decision's
         snapshot, or from the immutable run archive that snapshot names (D1)
  out_*  the recorded outcome history (post-decision facts)

Decision-time context follows D5. Legacy (`application_log_v1`) context is
kept in its own ctx_legacy_* columns and never merged into coach_v1 columns
(D9). Nothing is inferred: a value that is not recorded stays None.
"""

from pathlib import Path

from src.career_coach.application_log import LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, CoachDecisionEvent
from src.career_coach.run_archive import ARCHIVE_ROOT, find_archive, load_archived_decisions
from src.learning.outcome_state import MILESTONES, EventRecord, JobOutcomeState

LEGACY_SNAPSHOT_FIELDS = ("frozen_action", "tier", "overall_score", "trajectory_direction", "queue_tier")


def select_context(state: JobOutcomeState) -> tuple[str, EventRecord | None]:
    """D5 for submitted jobs. The anchor is the date of the FIRST recorded
    APPLIED outcome (later APPLIED outcomes or decisions never move it). Only
    decisions dated on or before the anchor are eligible — a decision after the
    first submission is never decision-time context:
      1. the latest eligible APPLIED decision;
      2. otherwise the latest eligible decision of any kind;
      3. otherwise NONE.
    Ties on the same date follow file order. No outcome is paired with a
    decision beyond this date rule.

    For jobs without a submission: the latest decision, used only for
    decision-mix and conversion counts."""
    decisions = state.decisions
    if not state.submitted:
        return ("LATEST_DECISION", decisions[-1]) if decisions else ("NONE", None)
    anchor = state.first_dates["APPLIED"]
    eligible = [d for d in decisions if d.date <= anchor]
    applied = [d for d in eligible if d.value == "APPLIED"]
    if applied:
        return "APPLIED_DECISION", max(applied, key=lambda d: (d.date, d.seq))
    if eligible:
        return "LATEST_DECISION_BEFORE_SUBMISSION", max(eligible, key=lambda d: (d.date, d.seq))
    return "NONE", None


def _empty_ctx() -> dict:
    keys = ["ctx_schema_version", "ctx_coach_version", "ctx_decision", "ctx_decision_date", "ctx_declared_effort",
            "ctx_run_as_of", "ctx_run_kind", "ctx_run_dir", "ctx_config_hash", "ctx_profile_hash", "ctx_raw_corpus_hash",
            "ctx_country", "ctx_priority", "ctx_next_action", "ctx_career_value", "ctx_deciding_track",
            "ctx_declared_tier", "ctx_readiness", "ctx_seniority", "ctx_practical_constraint",
            "ctx_coach_resume_effort", "ctx_coach_research_effort",
            "ctx_evidence_gaps", "ctx_matched_tracks", "ctx_domain_terms", "ctx_employer", "ctx_network",
            "ctx_compensation_state"] + [f"ctx_legacy_{f}" for f in LEGACY_SNAPSHOT_FIELDS]
    return dict.fromkeys(keys)


def _coach_context(event: CoachDecisionEvent, archive_root: Path) -> tuple[dict, dict]:
    s = event.snapshot
    ctx = {"ctx_schema_version": SCHEMA_VERSION, "ctx_coach_version": s.run.coach_version,
           "ctx_decision": event.decision, "ctx_decision_date": event.decision_date,
           "ctx_declared_effort": event.declared_effort, "ctx_run_as_of": s.run.as_of_date.isoformat(),
           "ctx_run_kind": s.run.run_kind, "ctx_run_dir": s.run_dir,
           "ctx_config_hash": s.run.input_hashes.config, "ctx_profile_hash": s.run.input_hashes.profile,
           "ctx_raw_corpus_hash": s.run.input_hashes.raw_corpus, "ctx_country": s.job.country,
           "ctx_priority": s.priority, "ctx_next_action": s.next_action, "ctx_career_value": s.career_value,
           "ctx_deciding_track": s.deciding_track, "ctx_declared_tier": s.declared_tier, "ctx_readiness": s.readiness,
           "ctx_seniority": s.seniority, "ctx_practical_constraint": s.practical_constraint,
           "ctx_coach_resume_effort": s.resume_effort, "ctx_coach_research_effort": s.research_effort}
    archive = find_archive(s.run.as_of_date.isoformat(), s.run.run_kind, s.run.input_hashes.model_dump(), archive_root)
    src = {"src_archive_id": archive.name if archive else None}
    if archive is None:
        src["src_archive_status"] = "NOT_ARCHIVED"            # context beyond the snapshot is unavailable
        return ctx, src
    decision = load_archived_decisions(archive).get(event.job_id)
    if decision is None:
        src["src_archive_status"] = "JOB_NOT_IN_ARCHIVE"
        return ctx, src
    src["src_archive_status"] = "MATCHED"
    components = decision.practical_constraint.components if decision.practical_constraint else {}
    ctx.update({
        "ctx_evidence_gaps": [g.name for g in decision.evidence_gaps],
        "ctx_matched_tracks": [m.track for m in decision.career_value.matched_tracks] if decision.career_value else [],
        "ctx_domain_terms": decision.candidate_alignment.domain.terms if decision.candidate_alignment else [],
        "ctx_employer": components.get("employer"), "ctx_network": components.get("network"),
        "ctx_compensation_state": components.get("compensation"),
    })
    return ctx, src


def _legacy_context(record: EventRecord) -> tuple[dict, dict]:
    raw = record.event.raw
    snap = raw.get("snapshot", {})
    ctx = {"ctx_schema_version": LEGACY_SCHEMA_VERSION, "ctx_decision": raw["decision"],
           "ctx_decision_date": raw["decision_date"], "ctx_declared_effort": raw.get("declared_effort"),
           "ctx_run_as_of": snap.get("as_of_date")}
    ctx.update({f"ctx_legacy_{f}": snap.get(f) for f in LEGACY_SNAPSHOT_FIELDS})
    return ctx, {"src_archive_id": None, "src_archive_status": "NOT_APPLICABLE"}


def build_rows(states: dict[str, JobOutcomeState], archive_root: Path = ARCHIVE_ROOT) -> list[dict]:
    rows = []
    for job_id, s in states.items():
        context, record = select_context(s)
        row = {"job_id": job_id, "src_event_schema_versions": "+".join(s.schema_versions),
               "src_voided_event_ids": [v.seq for v in s.voided],       # raw events excluded as entered in error
               "src_decision_context": context, "src_context_event_seq": record.seq if record else None,
               "src_archive_id": None, "src_archive_status": "NOT_APPLICABLE", **_empty_ctx()}
        if record is not None:
            ctx, src = (_coach_context(record.event, archive_root) if record.schema_version == SCHEMA_VERSION
                        else _legacy_context(record))
            row.update(ctx)
            row.update(src)
        row.update({
            "out_submitted": s.submitted,
            "out_decided_applied_without_submission": s.decided_applied_without_submission,
            "out_decisions": [d.value for d in s.decisions],
            "out_resolution": s.resolution, "out_current_status": s.current_status,
            "out_terminal_outcome": s.terminal_outcome,
            **{f"out_reached_{m.lower()}": s.reached[m] for m in MILESTONES},
            **{f"out_first_{m.lower()}_date": s.first_dates.get(m) for m in MILESTONES},
            "out_last_event_date": s.last_event_date, "out_days_since_last_event": s.days_since_last_event,
            "out_anomalies": list(s.anomalies),
        })
        rows.append(row)
    return rows
