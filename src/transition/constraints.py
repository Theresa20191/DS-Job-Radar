"""Constraint checks (Transition Lens V1, annotation-only — they never
exclude, re-rank or re-score anything).

Compensation floor (D-2), per country, in the posting's own currency:
  PASS     salary_min >= floor
  BELOW    salary_max <  floor
  UNKNOWN  range straddles the floor, salary undisclosed, no floor for the
           country, currency or period mismatch, or not configured.
  No FX conversion and no cost-of-living inference. Singapore has no floor
  yet (a deferred economic decision), so Singapore postings are UNKNOWN
  (no_floor_for_country). UNKNOWN is never a negative signal. Only the
  frozen parsed salary fields are read — never the compensation percentile.

Work authorization (D-3): candidate-declared only (PASS | UNKNOWN |
NOT_ELIGIBLE) from profile.yaml transition.work_authorization, per country.
Never inferred from nationality, location, education, employer or any
other field. The frozen job-side HC-2 result is attached for context only.
"""

from src.models.transition import (
    WORK_AUTH_STATUSES, CompensationFloorCheck, ConstraintChecks, EvidenceRef, FloorRule,
    SalaryEvidence, WorkAuthorizationCheck,
)

FLOOR_PATH = "profile.transition.compensation_floor"
AUTH_PATH = "profile.transition.work_authorization"


def validate_constraint_config(transition: dict) -> None:
    for country, rule in (transition.get("compensation_floor") or {}).items():
        if rule is None:
            continue
        if not isinstance(rule, dict) or set(rule) != {"currency", "monthly_amount"}:
            raise ValueError(f"{FLOOR_PATH}.{country}: expected {{currency, monthly_amount}} or null")
        if not isinstance(rule["monthly_amount"], (int, float)) or rule["monthly_amount"] <= 0:
            raise ValueError(f"{FLOOR_PATH}.{country}.monthly_amount must be a positive number")
    for country, status in (transition.get("work_authorization") or {}).items():
        if status not in WORK_AUTH_STATUSES:
            raise ValueError(f"{AUTH_PATH}.{country}: '{status}' is not one of {WORK_AUTH_STATUSES}")


def compensation_floor_check(job, transition: dict | None) -> CompensationFloorCheck:
    evidence = SalaryEvidence(salary_raw=job.salary_raw, salary_currency=job.salary_currency,
                              salary_min=job.salary_min, salary_max=job.salary_max, salary_period=job.salary_period)
    floors = (transition or {}).get("compensation_floor")
    if floors is None:
        return CompensationFloorCheck(status="UNKNOWN", reason="not_configured", evidence=evidence)
    spec = floors.get(job.country)
    if spec is None:
        return CompensationFloorCheck(status="UNKNOWN", reason="no_floor_for_country", evidence=evidence)
    rule = FloorRule(country=job.country, currency=spec["currency"], monthly_amount=float(spec["monthly_amount"]),
                     config_path=f"{FLOOR_PATH}.{job.country}")
    low = job.salary_min if job.salary_min is not None else job.salary_max
    high = job.salary_max if job.salary_max is not None else job.salary_min
    if job.salary_unknown or low is None:
        reason = "salary_undisclosed"
    elif job.salary_currency != rule.currency:
        reason = "currency_mismatch"
    elif (job.salary_period or "").lower() != "monthly":
        reason = "period_not_monthly"
    elif low >= rule.monthly_amount:
        return CompensationFloorCheck(status="PASS", reason="min_meets_floor", rule=rule, evidence=evidence)
    elif high < rule.monthly_amount:
        return CompensationFloorCheck(status="BELOW", reason="max_below_floor", rule=rule, evidence=evidence)
    else:
        reason = "range_straddles_floor"
    return CompensationFloorCheck(status="UNKNOWN", reason=reason, rule=rule, evidence=evidence)


def work_authorization_check(job, transition: dict | None) -> WorkAuthorizationCheck:
    declared = (transition or {}).get("work_authorization") or {}
    job_side = [EvidenceRef(field=e.source_field, text=e.matched_text, rule_id=f"HC-2:{e.restriction_type}")
                for e in job.work_authorization_evidence]
    common = dict(country=job.country, job_side_status=job.hc_work_authorization_status,
                  job_side_restriction_type=job.work_authorization_restriction_type, job_side_evidence=job_side)
    if job.country in declared:
        return WorkAuthorizationCheck(status=declared[job.country], source=f"{AUTH_PATH}.{job.country}", **common)
    return WorkAuthorizationCheck(status="UNKNOWN", source="not_declared", **common)


def constraint_checks(job, transition: dict | None) -> ConstraintChecks:
    return ConstraintChecks(compensation_floor=compensation_floor_check(job, transition),
                            work_authorization=work_authorization_check(job, transition))
