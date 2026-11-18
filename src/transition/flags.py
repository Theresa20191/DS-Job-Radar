"""Strategy flags (Transition Lens V1, annotation-only).

Neutral descriptors for the decision card — no severity, no score, and no
effect on tier, ranking or recommended_action. Wording never says "low",
"lacks" or "weak": an UNKNOWN is described as not established.
"""

import re

from src.models.transition import EvidenceRef, StrategyFlag

_SENIOR_LEVELS = {"Principal", "Lead", "Staff", "Manager"}
_SENIOR_TITLE = re.compile(r"\bdirector\b|\bhead of\b|\bchief\b|\bvp\b|vice president", re.I)
_TRAJECTORY_FLAGS = {"NON_TARGET": "TRAJECTORY_NON_TARGET", "OFF_VOCABULARY": "TRAJECTORY_OFF_VOCABULARY",
                     "UNTARGETED": "TRAJECTORY_UNTARGETED", "UNKNOWN": "TRAJECTORY_UNKNOWN"}


def strategy_flags(job, lens, candidate_years: float, stretch_gap_years: float) -> list[StrategyFlag]:
    flags: list[StrategyFlag] = []

    reach_evidence = []
    if job.role_level in _SENIOR_LEVELS:
        reach_evidence.append(EvidenceRef(field="role_level", text=job.role_level, rule_id="senior_role_level"))
    if _SENIOR_TITLE.search(job.job_title or ""):
        reach_evidence.append(EvidenceRef(field="job_title", text=job.job_title, rule_id="senior_title"))
    if job.years_experience_required is not None and job.years_experience_required - candidate_years >= stretch_gap_years:
        reach_evidence.append(EvidenceRef(field="years_experience_required", text=f"{job.years_experience_required:g}",
                                          rule_id=f"experience_gap>={stretch_gap_years:g}"))
    if reach_evidence:
        flags.append(StrategyFlag(flag="SENIORITY_REACH", evidence=reach_evidence,
                                  reason="Role seniority is above your current level (readiness, not trajectory)"))

    if job.employer_clarity == "RECRUITER":
        flags.append(StrategyFlag(flag="RECRUITER_POSTED", reason=job.employer_clarity_reason or "Posted by a recruiter",
                                  evidence=[EvidenceRef(field="employer_clarity", text=job.employer_clarity, rule_id="employer_clarity")]))

    trajectory = lens.trajectory
    if trajectory.direction in _TRAJECTORY_FLAGS:
        flags.append(StrategyFlag(flag=_TRAJECTORY_FLAGS[trajectory.direction],
                                  reason=f"Trajectory: {trajectory.direction}" + (f" ({trajectory.unknown_reason})" if trajectory.unknown_reason else "")))
    if trajectory.mixed:
        flags.append(StrategyFlag(flag="TRAJECTORY_MIXED", reason="Both a target track and a non-target track match this work"))

    floor = lens.constraint_checks.compensation_floor
    if floor.status == "BELOW":
        flags.append(StrategyFlag(flag="COMP_BELOW_FLOOR",
                                  reason=f"Posted maximum is under your {floor.rule.currency} {floor.rule.monthly_amount:,.0f}/month floor"))
    elif floor.status == "UNKNOWN":
        flags.append(StrategyFlag(flag="COMP_UNDETERMINED", reason=f"Floor check not established ({floor.reason})"))

    auth = lens.constraint_checks.work_authorization
    if auth.status == "NOT_ELIGIBLE":
        flags.append(StrategyFlag(flag="WORK_AUTH_NOT_ELIGIBLE", reason=f"You declared NOT_ELIGIBLE for {auth.country}"))
    elif auth.status == "UNKNOWN":
        flags.append(StrategyFlag(flag="WORK_AUTH_UNDECLARED", reason=f"Work authorization for {auth.country} not declared ({auth.source})"))

    unclassified = [g.technique for g in lens.gap_ledger.required_unevidenced if g.candidate_classification == "UNCLASSIFIED"]
    if unclassified:
        flags.append(StrategyFlag(flag="EVIDENCE_GAPS",
                                  reason=f"No evidence in your profile for {', '.join(unclassified)} (required by this job) — unclassified"))
    return flags
