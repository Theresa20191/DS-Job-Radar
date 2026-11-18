"""interview_fit_score: a heuristic fit against THIS posting's stated
hiring bar — NOT a probability of getting an interview. There is no
historical application-outcome data to calibrate against, so no
probability semantics are claimed anywhere (D3).

Answers exactly one question: "can this candidate clear THIS posting's
stated hiring bar?" — years of experience, education, and role-level/
seniority evidence only. It does not consume personal-transition-fit
evidence (technical_alignment / domain_alignment): those answer a
different question and are scored once, through PTF.

Weights below (0.4 experience-gap / 0.2 education-gap, renormalized to
~0.667/0.333) preserve the original 2:1 relative emphasis between
experience and education."""

EDUCATION_RANK = {"Diploma": 1, "Bachelor": 2, "Master": 3, "PhD": 4}

EXPERIENCE_WEIGHT = 2 / 3
EDUCATION_WEIGHT = 1 / 3

# Declared neutral values ("not specified" in the posting). Also used to
# derive the tier reference job (scoring/tiers.py) — change with care.
EXPERIENCE_UNSPECIFIED_SCORE = 60.0
EDUCATION_UNSPECIFIED_SCORE = 70.0


def _experience_gap_score(years_required: float | None, candidate_years: float) -> tuple[float, str]:
    if years_required is None:
        return EXPERIENCE_UNSPECIFIED_SCORE, "Years of experience not specified — treated as neutral"
    gap = years_required - candidate_years
    if gap <= 0:
        return 90.0, f"You meet/exceed the {years_required:.0f}-year experience requirement"
    if gap <= 1:
        return 70.0, f"Small experience gap (~{gap:.0f} year) vs the {years_required:.0f}-year requirement"
    if gap <= 2:
        return 50.0, f"Moderate experience gap (~{gap:.0f} years) vs the {years_required:.0f}-year requirement"
    if gap <= 4:
        return 30.0, f"Sizeable experience gap (~{gap:.0f} years) vs the {years_required:.0f}-year requirement"
    return 15.0, f"Large experience gap (~{gap:.0f} years) vs the {years_required:.0f}-year requirement"


def _education_score(required: str | None, candidate_rank: int) -> tuple[float, str]:
    if not required:
        return EDUCATION_UNSPECIFIED_SCORE, "Education requirement not specified"
    required_rank = EDUCATION_RANK.get(required, 2)
    if candidate_rank >= required_rank:
        return 90.0, f"Your education meets the stated '{required}' requirement"
    return 40.0, f"Your education is below the stated '{required}' requirement"


def _role_level_penalty(role_level: str, candidate_years: float, has_explicit_yoe: bool) -> tuple[float, str | None]:
    """Title-based seniority is used ONLY as a fallback when no explicit
    YOE is stated — explicit YOE is stronger evidence than a title, so a
    "Senior Data Scientist, 2-4 years" posting must not also eat a flat
    title penalty on top of its already-accurate experience-gap score
    (the double-penalty this replaces)."""
    if has_explicit_yoe:
        return 0.0, None

    if role_level in ("Principal", "Lead") and candidate_years < 3:
        return -15.0, f"Role level '{role_level}' (no explicit YOE stated) typically expects more experience than {candidate_years:.0f} year(s)"
    if role_level == "Manager" and candidate_years < 2:
        return -15.0, f"Role level '{role_level}' (no explicit YOE stated) typically expects more experience than {candidate_years:.0f} year(s)"
    if role_level == "Senior" and candidate_years < 1:
        return -5.0, f"Role level '{role_level}' (no explicit YOE stated) suggests slightly more experience than {candidate_years:.0f} year(s)"
    return 0.0, None


def score(job, profile: dict, config: dict) -> dict:
    candidate_years = profile.get("years_experience_current_role", 1)
    candidate_edu_rank = profile.get("education_level_rank", 3)

    has_explicit_yoe = job.years_experience_required is not None
    exp_score, exp_reason = _experience_gap_score(job.years_experience_required, candidate_years)
    edu_score, edu_reason = _education_score(job.education_required, candidate_edu_rank)
    level_penalty, level_reason = _role_level_penalty(job.role_level, candidate_years, has_explicit_yoe)

    value = EXPERIENCE_WEIGHT * exp_score + EDUCATION_WEIGHT * edu_score + level_penalty
    value = max(0.0, min(100.0, value))

    evidence_source = (
        f"Experience evidence: explicit YOE stated in posting ({job.years_experience_required:.0f} years)"
        if has_explicit_yoe
        else f"Experience evidence: no explicit YOE stated; using role-level heuristic based on title ('{job.role_level}')"
    )

    reasons = [evidence_source, exp_reason, edu_reason]
    if level_reason:
        reasons.append(level_reason)

    return {"score": round(value, 1), "known": True, "reasons": reasons}
