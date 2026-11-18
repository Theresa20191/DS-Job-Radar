"""Hard filters run BEFORE scoring. A failed hard constraint makes a job
ineligible regardless of any weighted score — no score, however high,
can rescue a job that fails here (section 9 of this turn's spec).

Two named hard constraints are approved (section 1):

HC-1 (substantive DS): excludes non-DS roles — role_type ANALYST_NOT_DS,
SOFTWARE_ENGINEER, or NO_DS_SIGNAL. Implemented via role_type, the
existing authoritative content classification — not a new classifier.
DS_TITLE_BUT_ANALYTICS_HEAVY is NOT excluded (HC-1 revision): a job whose
title is explicitly a DS title stays eligible even when its scope reads
analytics-heavy. The role_type is kept and job_quality still scores it
low (base 20) — this is an eligibility change only. DS_TITLE_UNCLEAR is
not excluded either — insufficient evidence of substantive DS is not the
same as confirmed evidence of non-substantive DS.

HC-2 (work authorization): excludes a job ONLY when the posting
explicitly denies eligibility to foreign applicants ("Malaysian citizens
only", "No foreigners", ...), uncontradicted. Purely job-side — no
candidate nationality data exists or is inferred. Sponsorship/work-permit
requirements, preferences, "SC/PR only", and contradictory wording are
UNKNOWN and always eligible. See enrichment/work_authorization.py.

Status fields and their relationship:
  work_authorization_status / _restriction_type / _evidence / _reason
      the classifier's result and its supporting evidence (source of truth)
  hc_work_authorization_status
      the HC-2 decision, derived here from work_authorization_status —
      never computed independently
  hc_substantive_ds_status
      the HC-1 decision, derived here from role_type
All are fields on ProcessedJob, so they survive the ProcessedJob ->
ScoredJob conversion in scoring/overall.py.

Everything below these two is a pre-existing filter (geography, posting
staleness, reject-listed role families) that predates the HC-1/HC-2 work
and is unchanged here.
"""

from src.enrichment.work_authorization import classify_work_authorization
from src.models.processed_job import ProcessedJob

# HC-1: role_type values that fail the substantive-DS constraint.
HC1_REJECT_ROLE_TYPES = {"ANALYST_NOT_DS", "SOFTWARE_ENGINEER", "NO_DS_SIGNAL"}


def _hc2_reason(job: ProcessedJob) -> str:
    """Structured HC-2 failure reason: type, matched text, source, context."""
    ev = next(e for e in job.work_authorization_evidence if e.restriction_type == job.work_authorization_restriction_type)
    return (
        f"HC-2 (work authorization) FAILED: {ev.restriction_type} — '{ev.matched_text}' "
        f"(source: {ev.source_field}; context: \"{ev.context}\")"
    )


def evaluate(job: ProcessedJob, config: dict) -> tuple[bool, list[str], ProcessedJob]:
    """Returns (passed, reasons, job) — job is returned with the HC-1/HC-2
    statuses and HC-2 evidence attached, so both eligible and rejected
    jobs can report them."""
    cfg = config["hard_filters"]
    reasons: list[str] = []

    # HC-1: substantive DS
    hc1_status = "FAIL" if job.role_type in HC1_REJECT_ROLE_TYPES else "PASS"
    if hc1_status == "FAIL":
        reasons.append(f"HC-1 (substantive DS) FAILED: role_type '{job.role_type}' has no meaningful DS/modelling component")

    # HC-2: work authorization
    wa = classify_work_authorization(job)
    job = job.model_copy(update={
        "work_authorization_status": wa.status,
        "work_authorization_reason": wa.reason,
        "work_authorization_restriction_type": wa.restriction_type,
        "work_authorization_evidence": wa.evidence,
        "hc_substantive_ds_status": hc1_status,
        "hc_work_authorization_status": wa.status,
    })
    if job.hc_work_authorization_status == "FAIL":
        reasons.append(_hc2_reason(job))

    # Pre-existing filters (unchanged)
    if job.role_family in cfg["reject_role_families"]:
        reasons.append(f"role_family '{job.role_family}' is in the reject list")

    if len(job.description_raw.strip()) < cfg["min_description_length_chars"]:
        reasons.append("description too short to evaluate")

    if job.job_age_days is not None and job.job_age_days > cfg["max_job_age_days"]:
        reasons.append(f"posting is {job.job_age_days} days old (> {cfg['max_job_age_days']} day limit)")

    country_ok = job.country in cfg["required_countries"]
    remote_ok = cfg.get("allow_remote_regardless_of_country", False) and job.remote_type == "Remote"
    if not country_ok and not remote_ok:
        reasons.append(f"country '{job.country}' is outside target geography and not remote")

    return len(reasons) == 0, reasons, job


def filter_jobs(jobs: list[ProcessedJob], config: dict) -> tuple[list[ProcessedJob], list[tuple[ProcessedJob, list[str]]]]:
    passed, rejected = [], []
    for job in jobs:
        ok, reasons, job = evaluate(job, config)
        if ok:
            passed.append(job)
        else:
            rejected.append((job, reasons))
    return passed, rejected
