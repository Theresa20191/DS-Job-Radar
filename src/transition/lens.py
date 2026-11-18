"""Transition Lens entry point (Career Transition V1).

ANNOTATION-ONLY (D-4). apply_lens() runs after score_jobs() and returns
copies of the scored jobs with ONLY `transition_lens` filled in. It never
changes Overall, tier, ranking, recommended_action, PTF, compensation,
job_quality, role_type, hard-filter results, stability, why_apply or key_gap
— regression tests enforce this. Nothing in scoring or hard filtering
imports this package.

Candidate-side inputs come only from what the candidate declared:
profile.yaml `transition`, the profile `techniques` (through the same
candidate_techniques helper the frozen technical_alignment uses) and
`years_experience_current_role` (for the seniority-reach flag, as frozen
scoring already uses it). Nothing is inferred from current_role,
target_roles, education, location or any other field; a missing
`transition` block makes every candidate-relative output UNKNOWN
(not_configured).
"""

from src.config import load_scoring
from src.models.scored_job import ScoredJob
from src.models.transition import TARGET_TIERS, TransitionLens
from src.transition.constraints import constraint_checks, validate_constraint_config
from src.transition.flags import strategy_flags
from src.transition.gap_ledger import build_gap_ledger, validate_gap_classifications, with_market_frequency
from src.transition.tracks import build_trajectory, validate_vocabulary
from src.transition.work_profile import build_work_profile, work_sentences


def validate_transition_config(profile: dict, vocab: dict) -> None:
    """Fail loudly on a malformed `transition` block (it is candidate-declared
    configuration, so a typo must not silently change annotations)."""
    transition = profile.get("transition")
    if transition is None:
        return
    targets = transition.get("target_tracks") or {}
    unknown_tiers = set(targets) - set(TARGET_TIERS)
    if unknown_tiers:
        raise ValueError(f"transition.target_tracks: unknown tiers {sorted(unknown_tiers)}")
    seen: dict[str, str] = {}
    for tier in TARGET_TIERS:
        for track in targets.get(tier) or []:
            if track not in vocab["tracks"]:
                raise ValueError(f"transition.target_tracks.{tier}: unknown track {track}")
            if track in seen:
                raise ValueError(f"track {track} listed under both {seen[track]} and {tier}")
            seen[track] = tier
    validate_constraint_config(transition)
    validate_gap_classifications(transition.get("gap_classifications"))


def lens_for(job: ScoredJob, profile: dict, vocab: dict) -> TransitionLens:
    transition = profile.get("transition")
    targets = (transition or {}).get("target_tracks")
    sentences = work_sentences(job)
    work_profile = build_work_profile(job)
    return TransitionLens(work_profile=work_profile,
                          trajectory=build_trajectory(job, work_profile, sentences, vocab, targets),
                          gap_ledger=build_gap_ledger(job, profile),
                          constraint_checks=constraint_checks(job, transition))


def apply_lens(scored_jobs: list[ScoredJob], profile: dict, vocab: dict) -> list[ScoredJob]:
    validate_vocabulary(vocab)
    validate_transition_config(profile, vocab)
    candidate_years = profile.get("years_experience_current_role", 1)
    stretch_gap = load_scoring()["opportunity_types"]["stretch_min_seniority_gap_years"]

    lenses = [lens_for(job, profile, vocab) for job in scored_jobs]
    ledgers = with_market_frequency([lens.gap_ledger for lens in lenses])
    lenses = [lens.model_copy(update={"gap_ledger": ledger}) for lens, ledger in zip(lenses, ledgers)]
    lenses = [lens.model_copy(update={"strategy_flags": strategy_flags(job, lens, candidate_years, stretch_gap)})
              for job, lens in zip(scored_jobs, lenses)]
    return [job.model_copy(update={"transition_lens": lens}) for job, lens in zip(scored_jobs, lenses)]
