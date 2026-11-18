"""Application Strategy V1 — per-job strategy records (additive, read-only).

Reads the frozen ScoredJob (after apply_lens) and ONE candidate declaration
block, profile.yaml `application_strategy.hard_constraints`, which says which
configured compensation floors the candidate treats as hard stops. The
Transition Lens floor rule itself is not touched.

Rules (D1–D5, approved):
  - frozen action and career direction are copied as two independent axes;
    no mode, winner, score, rank or tier is derived from them;
  - declared blocks: work authorization NOT_ELIGIBLE (candidate-declared)
    always; a configured floor with status BELOW only when that country's
    floor is declared hard. UNKNOWN, PASS or no configured floor never block;
  - profile actions: an open technical_alignment unknown (up_only) while the
    frozen stability is COULD_RISE / COULD_MOVE;
  - verify refs: the existing Decision Card V1–V6 codes, never re-derived;
  - network: only when the frozen network mechanism found an actual contact;
  - no effort and no capacity (V2), no decision until the candidate records one.
Nothing in scoring, hard filters, the pipeline or the Transition Lens imports
this module.
"""

from collections import Counter

from src.models.scored_job import ScoredJob
from src.models.strategy import (
    DIRECTION_TO_QUEUE_TIER, FROZEN_ACTIONS, QUEUE_TIERS, ApplicationRecord, CareerAxis, DeclaredBlock, FrozenAxis, GapStrategy,
    LeverageEntry, NetworkSignal, OpenUnknownRef, ProfileAction, QueueCell, RunStrategy, TrackRef, ApplicationStrategy,
)
from src.models.transition import TARGET_TIERS
from src.reporting import decision_card as dc
from src.scoring.technical_alignment import gap_text
from src.transition.gap_ledger import gap_market

HARD_PATH = "profile.application_strategy.hard_constraints"
_ALLOWED = {"hard_constraints": {"compensation_floor"}}


def validate_strategy_config(profile: dict) -> None:
    """Fail loudly on a malformed `application_strategy` block: only declared
    hard constraints exist in V1 (no effort, capacity or preference keys)."""
    block = profile.get("application_strategy")
    if block is None:
        return
    unknown = set(block) - set(_ALLOWED)
    if unknown:
        raise ValueError(f"profile.application_strategy: unknown keys {sorted(unknown)} (V1 supports only hard_constraints)")
    hard = block.get("hard_constraints") or {}
    unknown = set(hard) - _ALLOWED["hard_constraints"]
    if unknown:
        raise ValueError(f"{HARD_PATH}: unknown constraints {sorted(unknown)}")
    floors = ((profile.get("transition") or {}).get("compensation_floor")) or {}
    for country, value in (hard.get("compensation_floor") or {}).items():
        if not isinstance(value, bool):
            raise ValueError(f"{HARD_PATH}.compensation_floor.{country} must be true or false")
        if floors.get(country) is None:
            raise ValueError(f"{HARD_PATH}.compensation_floor.{country}: no compensation floor is configured for {country}")


def _hard_floors(profile: dict) -> dict[str, bool]:
    return ((profile.get("application_strategy") or {}).get("hard_constraints") or {}).get("compensation_floor") or {}


def _declared_blocks(job: ScoredJob, hard_floors: dict[str, bool]) -> list[DeclaredBlock]:
    checks = job.transition_lens.constraint_checks
    blocks = []
    auth = checks.work_authorization
    if auth.status == "NOT_ELIGIBLE":
        blocks.append(DeclaredBlock(constraint="WORK_AUTH", status="NOT_ELIGIBLE",
                                    source=[auth.source, "transition_lens.constraint_checks.work_authorization"]))
    floor = checks.compensation_floor
    if floor.status == "BELOW" and floor.rule and hard_floors.get(job.country) is True:
        blocks.append(DeclaredBlock(constraint="COMP_FLOOR", status="BELOW",
                                    source=[f"{HARD_PATH}.compensation_floor.{job.country}", floor.rule.config_path,
                                            "transition_lens.constraint_checks.compensation_floor"]))
    return blocks


def _profile_actions(job: ScoredJob) -> list[ProfileAction]:
    stability = job.decision_stability
    if stability.stability_label not in ("COULD_RISE", "COULD_MOVE"):
        return []
    techniques = [g.technique for g in job.transition_lens.gap_ledger.required_unevidenced]
    return [ProfileAction(kind="ADD_EVIDENCE_OPEN_UNKNOWN", technique=techniques,
                          source_path=f"decision_stability.open_unknowns[{i}]")
            for i, u in enumerate(stability.open_unknowns)
            if u.field == "technical_alignment" and u.direction == "up_only" and techniques]


def build_strategy(job: ScoredJob, profile: dict, run_has_classified_gaps: bool = False,
                   record: ApplicationRecord | None = None) -> ApplicationStrategy:
    stability = job.decision_stability
    trajectory = job.transition_lens.trajectory
    present = {m.target_tier for m in trajectory.tracks}
    card = dc.build_card(job, run_has_classified_gaps)
    network = None
    if job.network_bonus > 0:
        network = NetworkSignal(network_bonus=job.network_bonus, reasons=list(job.network_reasons),
                                networking_opportunity="NETWORKING_OPPORTUNITY" in job.opportunity_types)
    return ApplicationStrategy(
        job_id=job.job_id,
        as_of_date=job.as_of_date,
        card_version=dc.CARD_VERSION,
        frozen_axis=FrozenAxis(
            recommended_action=job.recommended_action, tier=job.tier, stability_label=stability.stability_label,
            tier_floor=stability.tier_floor, tier_ceiling=stability.tier_ceiling,
            open_unknowns=[OpenUnknownRef(field=u.field, direction=u.direction, reason=u.reason) for u in stability.open_unknowns]),
        career_axis=CareerAxis(
            tiers_present=[t for t in list(TARGET_TIERS) + ["UNTARGETED"] if t in present],
            tracks=[TrackRef(track_id=m.track_id, target_tier=m.target_tier) for m in trajectory.tracks],
            direction=trajectory.direction,
            queue_tier=DIRECTION_TO_QUEUE_TIER[trajectory.direction]),
        declared_blocks=_declared_blocks(job, _hard_floors(profile)),
        profile_actions=_profile_actions(job),
        gap_strategy=[GapStrategy(technique=g.technique, candidate_classification=g.candidate_classification)
                      for g in job.transition_lens.gap_ledger.required_unevidenced],
        verify_refs=[l.verify for l in card.main if l.verify],
        network=network,
        decision_record=record,
    )


def build_strategies(jobs: list[ScoredJob], profile: dict,
                     records: dict[str, ApplicationRecord] | None = None) -> list[ApplicationStrategy]:
    """One strategy record per job, in frozen ranking order. Reads nothing but
    the frozen jobs, the candidate's `application_strategy` declarations and,
    when supplied, the candidate's own application-log records."""
    validate_strategy_config(profile)
    dc._check_inputs(jobs)
    ordered = sorted(jobs, key=lambda j: j.overall_score, reverse=True)
    classified = dc._run_has_classified_gaps(ordered)
    records = records or {}
    return [build_strategy(j, profile, classified, records.get(j.job_id)) for j in ordered]


# --- run level: queue view (D1) and profile leverage --------------------------------------------

def _jobs(n: int) -> str:
    return f"{n} job{'s' if n != 1 else ''}"


def _queue(strategies: list[ApplicationStrategy]) -> list[QueueCell]:
    """Group by (queue tier, frozen action); cells in queue-tier then frozen
    tier-rule order, jobs in frozen ranking order. Nothing is re-ranked."""
    cells: dict[tuple[str, str], list[str]] = {}
    for s in strategies:
        cells.setdefault((s.career_axis.queue_tier, s.frozen_axis.recommended_action), []).append(s.job_id)
    tier_order = {t: i for i, t in enumerate(QUEUE_TIERS)}
    action_order = {a: i for i, a in enumerate(FROZEN_ACTIONS)}
    keys = sorted(cells, key=lambda k: (tier_order.get(k[0], len(tier_order)), action_order.get(k[1], len(action_order)), k))
    return [QueueCell(queue_tier=t, frozen_action=a, job_ids=cells[(t, a)]) for t, a in keys]


def _profile_leverage(jobs: list[ScoredJob], strategies: list[ApplicationStrategy]) -> list[LeverageEntry]:
    """Counts of job records touched by each undeclared constraint, each open
    technical unknown and each required evidence gap. Declarations first, then
    open unknowns, then gaps in the existing gap-market order."""
    entries: list[LeverageEntry] = []
    auth_path = "[*].transition_lens.constraint_checks.work_authorization"
    for (country, source), n in Counter(
            (j.country, j.transition_lens.constraint_checks.work_authorization.source) for j in jobs
            if j.transition_lens.constraint_checks.work_authorization.status == "UNKNOWN").items():
        auth = next(j.transition_lens.constraint_checks.work_authorization for j in jobs
                    if (j.country, j.transition_lens.constraint_checks.work_authorization.source) == (country, source))
        entries.append(LeverageEntry(kind="WORK_AUTH_DECLARATION", subject=country, jobs_affected=n, source_path=auth_path,
                                     text=f"Work authorization ({country}): {dc._auth_display(auth)} — {_jobs(n)}"))
    for (country, reason), n in Counter(
            (j.country, j.transition_lens.constraint_checks.compensation_floor.reason) for j in jobs
            if j.transition_lens.constraint_checks.compensation_floor.reason in ("no_floor_for_country", "not_configured")).items():
        floor = next(j.transition_lens.constraint_checks.compensation_floor for j in jobs
                     if (j.country, j.transition_lens.constraint_checks.compensation_floor.reason) == (country, reason))
        entries.append(LeverageEntry(kind="COMP_FLOOR_CONFIGURATION", subject=country, jobs_affected=n,
                                     source_path="[*].transition_lens.constraint_checks.compensation_floor",
                                     text=f"Compensation floor: {dc._floor_display(floor, country)} — {_jobs(n)}"))
    open_unknown = sum(bool(s.profile_actions) for s in strategies)
    if open_unknown:
        entries.append(LeverageEntry(kind="OPEN_TECHNICAL_UNKNOWN", subject="technical_alignment", jobs_affected=open_unknown,
                                     source_path="[*].decision_stability.open_unknowns",
                                     text=f"Open unknown technical_alignment (up_only) with stability COULD_RISE / COULD_MOVE "
                                          f"— {_jobs(open_unknown)}"))
    unclassified = Counter(g.technique for j in jobs for g in j.transition_lens.gap_ledger.required_unevidenced
                           if g.candidate_classification == "UNCLASSIFIED")
    for technique, n in gap_market([j.transition_lens.gap_ledger for j in jobs]):
        entries.append(LeverageEntry(kind="REQUIRED_EVIDENCE_GAP", subject=technique, jobs_affected=n,
                                     source_path="[*].transition_lens.gap_ledger.required_unevidenced",
                                     text=f"{gap_text([technique])} — {_jobs(n)}; classification not declared on {unclassified[technique]}"))
    return entries


def build_run_strategy(jobs: list[ScoredJob], strategies: list[ApplicationStrategy]) -> RunStrategy:
    dc._check_inputs(jobs)
    ordered = sorted(jobs, key=lambda j: j.overall_score, reverse=True)
    return RunStrategy(as_of_date=ordered[0].as_of_date if ordered else None,
                       queue=_queue(strategies),
                       profile_leverage=_profile_leverage(ordered, strategies),
                       network_job_ids=[s.job_id for s in strategies if s.network is not None])
