"""job_risk_score / job_risk_penalty (section 20). Detects signals that a
posting may be lower-quality or riskier to invest time in — never used to
silently hide a job, always surfaced with reasons."""


def score(job, config: dict) -> dict:
    """Evidence-ownership note (scoring architecture audit): two branches
    that used to live here were removed as confirmed double-counts of
    job_quality's own evidence, not because the underlying signal was
    wrong:
      - role_type == DS_TITLE_BUT_ANALYTICS_HEAVY: job_quality already
        scores this role_type low (base 20) for the same evidence, so a
        job_risk penalty would double-count it. (It was briefly an HC-1
        hard exclusion; that was revoked — the job is eligible and
        job_quality alone owns the signal.)
      - role_type == DS_TITLE_UNCLEAR: job_quality already scores this
        role_type low (base 45) for the exact same reason (insufficient
        description detail) — penalizing it again here double-counted
        the same evidence across two top-level scores. job_quality is
        the semantically appropriate owner of this signal, so it was
        removed from here rather than duplicated.
    Every remaining check below was audited and found to use evidence
    not already owned by job_quality, personal-fit, interview_fit, or
    employer_clarity — see the scoring architecture audit for the full
    per-check trace (description length here is a *posting-quality* red
    flag at a much looser 300-char threshold, distinct from job_quality's
    50-char "can we even classify this" threshold; salary_unknown here is
    a *transparency* signal, distinct from compensation_score's *pay
    level* question, which already treats unknown as excluded, not low)."""
    reasons: list[str] = []
    points = 0.0

    if len(job.description_raw.strip()) < 300:
        points += 10
        reasons.append("Job description is quite short/vague")

    if len(job.required_skills) > 15:
        points += 10
        reasons.append("Unusually long required-skills list — may be an unrealistic/copy-pasted JD")

    employment_lower = (job.employment_type or "").lower()
    if any(k in employment_lower for k in ("contract", "temporary", "intern")):
        points += 15
        reasons.append(f"Employment type is '{job.employment_type}', not a standard full-time role")

    if job.job_age_days is not None and job.job_age_days > 60:
        points += 10
        reasons.append(f"Posting is {job.job_age_days} days old — may be stale or already filled")

    if not job.company or len(job.company.strip()) < 2:
        points += 15
        reasons.append("Employer name is unclear or missing")

    if job.salary_unknown:
        points += 5
        reasons.append("Compensation not disclosed")
    elif (job.salary_max_myr or 0) > 100_000:
        # Seen in the wild: a source-side data entry error (e.g. an annual
        # figure mistagged as monthly) rather than a real monthly salary.
        # We don't silently discard/rewrite it — just flag it plainly.
        points += 20
        reasons.append(
            f"Salary figures look implausible (~MYR {job.salary_max_myr:,.0f}/month) — likely a data entry error at the source, not a real offer"
        )

    risk_score = max(0.0, min(100.0, points))
    penalty = round(risk_score / 100 * config.get("job_risk_penalty_max", 20), 1)

    return {"score": risk_score, "penalty": penalty, "reasons": reasons}
