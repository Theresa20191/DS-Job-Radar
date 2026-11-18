"""Ties every sub-score together into ScoredJob: Base, Personal Transition
Fit (PTF), overall score, tier, opportunity types, decision stability, and
the why_apply/why_not/key_gap/recommended_action explanation.

Frozen architecture (ptf_support_v1):
  - technical/domain evidence is scored ONCE, through PTF
    (technical_alignment + domain_alignment); it never enters Base.
  - Overall = clamp(0.60*Base + 0.40*PTF + network - risk); an unknown PTF
    contributes 0 (see scoring/aggregation.py).
  - tier cutoffs are derived from the reference job (scoring/tiers.py).
  - decision stability is computed after scoring and never feeds back.
Hard constraints (HC-1, HC-2) are evaluated upstream in
pipeline/hard_filters.py — nothing here can rescue an excluded job."""

from src.models.processed_job import ProcessedJob
from src.models.ptf import PTFResult
from src.models.scored_job import ScoredJob
from src.scoring import (
    company_maturity,
    compensation,
    completeness,
    domain_alignment,
    interview_fit,
    job_quality,
    job_risk,
    location,
    network_bonus,
    stability,
    technical_alignment,
)
from src.scoring.aggregation import base_score, overall_score
from src.scoring.ptf_scale import band_at_least, combine
from src.scoring.tiers import tier_for, tier_thresholds


def personal_transition_fit(technical, domain) -> PTFResult:
    assessed = [d.name for d in (technical, domain) if d.known]
    value = combine(technical.score, domain.score)
    if value is None:
        reasons = ["Personal transition fit could not be assessed (neither dimension known) — excluded, not zero"]
    else:
        reasons = [f"Assessed on: {', '.join(assessed)}"]
        if len(assessed) == 2:
            reasons.append("Both dimensions known: stronger value plus one third of the weaker (BR-2 gain)")
    return PTFResult(known=value is not None, score=value, assessed=assessed, reasons=reasons)


def _opportunity_types(job: ScoredJob, seniority_gap: float, comp_percentile: float | None, cfg: dict, thresholds: dict) -> list[str]:
    ot_cfg = cfg["opportunity_types"]
    types = []

    # DS_SUBSTANCE (formerly CAREER_ACCELERATOR — renamed, D-5): the frozen
    # classifier judged the content genuine DS work. It says nothing about
    # career movement; trajectory semantics live in the Transition Lens.
    # Behaviour-identical to the old `job_quality >= 75 and TRUE_DS` rule,
    # since TRUE_DS is the only role_type whose job_quality base is 75.
    if job.role_type == "TRUE_DS":
        types.append("DS_SUBSTANCE")

    if (
        job.interview_fit_score >= ot_cfg["strong_interview_fit_min_interview_fit"]
        and seniority_gap <= ot_cfg["strong_interview_fit_max_seniority_gap_years"]
    ):
        types.append("STRONG_INTERVIEW_FIT")

    # STRETCH minimum = the exact Tier C cutoff (T-3), never a rounded copy.
    if seniority_gap >= ot_cfg["stretch_min_seniority_gap_years"] and job.overall_score >= thresholds["C"]:
        types.append("STRETCH")

    if band_at_least(job.domain_alignment.band, ot_cfg["domain_advantage_min_band"]):
        types.append("DOMAIN_ADVANTAGE")

    if comp_percentile is not None and comp_percentile >= ot_cfg["high_compensation_min_percentile"]:
        types.append("HIGH_COMPENSATION")

    if job.network_bonus > 0:
        types.append("NETWORKING_OPPORTUNITY")

    return types


def _recommended_action(tier: str, opportunity_types: list[str]) -> str:
    if tier in ("A", "B"):
        return "APPLY + NETWORK" if "NETWORKING_OPPORTUNITY" in opportunity_types else "APPLY"
    if tier == "C":
        return "STRETCH APPLY"
    return "SKIP"


def _build_explanations(job: ScoredJob) -> tuple[list[str], list[str], list[str]]:
    why_apply, why_not, key_gap = [], [], []

    if job.job_quality_score >= 60:
        why_apply.extend(job.job_quality_reasons[:2])
    else:
        why_not.extend(job.job_quality_reasons[:2])

    # O-3: only SUBSTANTIVE or CORE evidence becomes a reason to apply.
    for dimension in (job.domain_alignment, job.technical_alignment):
        if band_at_least(dimension.band, "SUBSTANTIVE"):
            why_apply.extend(dimension.reasons[:1])

    # D5 wording; settled UNKNOWN and LIMITED are never presented as gaps.
    key_gap.extend(job.technical_alignment.gaps)

    if job.network_bonus > 0:
        why_apply.extend(job.network_reasons)

    if job.compensation_known and (job.compensation_score or 0) >= 70:
        why_apply.extend(job.compensation_reasons[:1])

    if job.job_risk_reasons:
        key_gap.extend(job.job_risk_reasons[:2])

    if job.interview_fit_score < 40:
        key_gap.extend(job.interview_fit_reasons[:1])

    # Eligible but the posting states a requirement/preference/contradiction
    # (UNKNOWN with evidence): surface it for the candidate's own check.
    if job.work_authorization_status == "UNKNOWN" and job.work_authorization_evidence:
        key_gap.append(job.work_authorization_reason)

    # de-dup while preserving order, cap length for readability
    why_apply = list(dict.fromkeys(why_apply))[:5]
    why_not = list(dict.fromkeys(why_not))[:5]
    key_gap = list(dict.fromkeys(key_gap))[:5]
    return why_apply, why_not, key_gap


def score_jobs(jobs: list[ProcessedJob], profile: dict, scoring_config: dict, contacts_by_company: dict | None = None) -> list[ScoredJob]:
    contacts_by_company = contacts_by_company or {}
    weights = scoring_config["weights"]
    thresholds = tier_thresholds(weights)
    comp_results = compensation.score_batch(jobs)

    scored: list[ScoredJob] = []
    for job in jobs:
        jq = job_quality.score(job, profile, scoring_config)
        itf = interview_fit.score(job, profile, scoring_config)
        comp = comp_results[job.job_id]
        cm = company_maturity.score(job, profile, scoring_config)
        loc = location.score(job, profile, scoring_config)
        net = network_bonus.score(job, contacts_by_company, scoring_config)
        risk = job_risk.score(job, scoring_config)
        tech = technical_alignment.score(job, profile)
        dom = domain_alignment.score(job, profile)
        ptf = personal_transition_fit(tech, dom)

        # UNKNOWN components are None and excluded from Base — never 0.
        components = {
            "job_quality": jq["score"] if jq["known"] else None,
            "interview_fit": itf["score"] if itf["known"] else None,
            "compensation": comp["score"] if comp["known"] else None,
            "company_maturity": cm["score"] if cm["known"] else None,
            "location": loc["score"] if loc["known"] else None,
        }
        base = base_score(components, weights)
        overall = overall_score(base, ptf.score, net["bonus"], risk["penalty"])
        tier = tier_for(overall, thresholds)
        completeness_pct, unknown_fields = completeness.compute_completeness(job, scoring_config)

        candidate_years = profile.get("years_experience_current_role", 1)
        seniority_gap = max(0.0, (job.years_experience_required or candidate_years) - candidate_years)
        comp_percentile = comp["score"] if comp["known"] else None

        scored_job = ScoredJob(
            **job.model_dump(),
            job_quality_score=jq["score"], job_quality_known=jq["known"], job_quality_reasons=jq["reasons"],
            interview_fit_score=itf["score"], interview_fit_known=itf["known"], interview_fit_reasons=itf["reasons"],
            compensation_score=comp["score"], compensation_known=comp["known"], compensation_reasons=comp["reasons"],
            company_maturity_score=cm["score"], company_maturity_known=cm["known"], company_maturity_reasons=cm["reasons"],
            location_score=loc["score"], location_known=loc["known"], location_reasons=loc["reasons"],
            base_score=round(base, 2),
            technical_alignment=tech, domain_alignment=dom, personal_transition_fit=ptf,
            network_bonus=net["bonus"], network_reasons=net["reasons"],
            job_risk_score=risk["score"], job_risk_penalty=risk["penalty"], job_risk_reasons=risk["reasons"],
            data_completeness_pct=completeness_pct, unknown_fields=unknown_fields,
            overall_score=round(overall, 1), tier=tier,
        )

        opportunity_types = _opportunity_types(scored_job, seniority_gap, comp_percentile, scoring_config, thresholds)
        why_apply, why_not, key_gap = _build_explanations(scored_job)
        recommended_action = _recommended_action(tier, opportunity_types)
        decision = stability.decision_stability(
            components, weights, tech, dom, net["bonus"], risk["penalty"], thresholds, tier)

        scored_job = scored_job.model_copy(
            update={
                "opportunity_types": opportunity_types,
                "why_apply": why_apply,
                "why_not": why_not,
                "key_gap": key_gap,
                "recommended_action": recommended_action,
                "decision_stability": decision,
            }
        )
        scored.append(scored_job)

    return scored
