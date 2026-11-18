"""compensation_score is percentile-based within the currently scored
batch (salary-known jobs only) rather than against an invented target
salary — we don't know the candidate's current pay and won't guess a
'fair' band. Comparing jobs to each other is honest; comparing to a
made-up number isn't."""


# Batch median percentile by construction. Also the tier reference job's
# neutral compensation (scoring/tiers.py).
NEUTRAL_PERCENTILE = 50.0


def _midpoint_myr(job) -> float | None:
    if job.salary_min_myr is None and job.salary_max_myr is None:
        return None
    if job.salary_min_myr is None:
        return job.salary_max_myr
    if job.salary_max_myr is None:
        return job.salary_min_myr
    return (job.salary_min_myr + job.salary_max_myr) / 2


def score_batch(jobs: list) -> dict[str, dict]:
    """Returns {job_id: {"score", "known", "reasons"}} for the whole batch."""
    known_jobs = [(j, _midpoint_myr(j)) for j in jobs if not j.salary_unknown]
    known_jobs = [(j, mid) for j, mid in known_jobs if mid is not None]
    known_jobs.sort(key=lambda pair: pair[1])

    n = len(known_jobs)
    percentile_by_id = {}
    for i, (j, mid) in enumerate(known_jobs):
        pct = NEUTRAL_PERCENTILE if n <= 1 else round(100 * i / (n - 1), 1)
        percentile_by_id[j.job_id] = (pct, mid)

    results = {}
    for job in jobs:
        if job.job_id in percentile_by_id:
            pct, mid = percentile_by_id[job.job_id]
            results[job.job_id] = {
                "score": pct,
                "known": True,
                "reasons": [
                    f"~MYR {mid:,.0f}/month midpoint — {pct:.0f}th percentile among salary-disclosed jobs in this run"
                ],
            }
        else:
            # None, not 0.0 — unknown salary must never display or be
            # readable as "lowest possible pay". known=False already
            # excludes this from overall_score's weighted average; this
            # only affects what a raw export/report shows for the field.
            results[job.job_id] = {
                "score": None,
                "known": False,
                "reasons": ["Salary not disclosed by employer"],
            }
    return results
