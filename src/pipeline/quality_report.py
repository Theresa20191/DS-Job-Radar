"""Data quality reporting (section 29). Source-level completeness is kept
explicit — never averaged away — per this turn's requirement #4."""

from collections import Counter
from datetime import date

from src.config import load_scoring
from src.models.raw_job import RawJob
from src.models.scored_job import ScoredJob
from src.normalizers.text_cleaner import clean_html
from src.parsers.salary_parser import parse_salary
from src.parsers.date_parser import parse_posted_date
from src.scoring.ptf_scale import SCALE_VERSION
from src.scoring.stability import SYSTEM_LIMITATIONS
from src.scoring.tiers import display_thresholds, tier_thresholds


def _has_salary(raw: RawJob) -> bool:
    return not parse_salary(raw.salary_raw, raw.source)["salary_unknown"]


def _has_text(value) -> bool:
    """True only for a non-empty string that still has text AFTER HTML
    cleaning. None, "", whitespace, "None"/"null", and empty HTML shells
    like "<p></p>" are all incomplete.

    History: the original check, bool(str(value).strip()), turned None into
    the truthy string "None" (MyCareersFuture requirements reported 100%
    complete while every record was None), and even after that fix Hiredly
    still reported 100% because 39 of 65 records hold "<p></p>"."""
    if not isinstance(value, str):
        return False
    cleaned = clean_html(value).strip()
    return bool(cleaned) and cleaned.lower() not in ("none", "null")


def _requirements_recoverable(raw: RawJob) -> bool:
    """Source requirements field present, OR a requirements/preferred
    section confidently detected inside the description text."""
    if _has_text((raw.source_extra or {}).get("requirements")):
        return True
    from src.enrichment.requirements_sections import analyze
    return analyze(clean_html(raw.job_description), "").status == "DETECTED"


def source_completeness(raw_jobs_by_source: dict[str, list[RawJob]]) -> dict[str, dict]:
    report = {}
    for source, jobs in raw_jobs_by_source.items():
        unique = list({j.job_id: j for j in jobs}.values())
        n = len(unique)
        if n == 0:
            report[source] = {"jobs_returned": 0, "unique": 0}
            continue

        def pct(cond_fn):
            return round(100 * sum(1 for j in unique if cond_fn(j)) / n, 1)

        report[source] = {
            "jobs_returned_raw": len(jobs),
            "unique": n,
            "duplicate_rate_pct": round(100 * (1 - n / len(jobs)), 1) if jobs else 0.0,
            "title_completeness_pct": pct(lambda j: bool(j.job_title.strip())),
            "company_completeness_pct": pct(lambda j: bool(j.company.strip())),
            "description_completeness_pct": pct(lambda j: len(j.job_description.strip()) > 50),
            "requirements_completeness_pct": pct(lambda j: _has_text((j.source_extra or {}).get("requirements"))),
            "requirements_recoverable_from_description_pct": pct(_requirements_recoverable),
            "salary_completeness_pct": pct(_has_salary),
            "skills_completeness_pct": pct(lambda j: bool(j.source_extra.get("skills"))),
        }
    return report


def pipeline_summary(
    raw_jobs: list[RawJob],
    unique_processed_count: int,
    rejected: list,  # list[tuple[ProcessedJob, list[str]]] from hard_filters.filter_jobs
    scored_jobs: list[ScoredJob],
    as_of_date: date | None = None,
) -> dict:
    tier_counts = Counter(j.tier for j in scored_jobs)
    country_counts = Counter(j.country for j in scored_jobs)

    # Counted from the structured HC statuses set by hard_filters, not by
    # parsing reason strings. A job can fail both HCs (counted in both).
    hc1_excluded = sum(1 for j, _ in rejected if j.hc_substantive_ds_status == "FAIL")
    hc2_excluded = sum(1 for j, _ in rejected if j.hc_work_authorization_status == "FAIL")
    other_filter_excluded = sum(
        1 for j, _ in rejected if j.hc_substantive_ds_status != "FAIL" and j.hc_work_authorization_status != "FAIL"
    )

    all_evaluated = [j for j, _ in rejected] + list(scored_jobs)
    wa_counts = Counter(j.work_authorization_status for j in all_evaluated)
    restriction_counts = Counter(j.work_authorization_restriction_type for j in all_evaluated)

    return {
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "jobs_scraped": len(raw_jobs),
        "jobs_unique_after_dedup": unique_processed_count,
        "jobs_duplicate": len(raw_jobs) - unique_processed_count,
        "jobs_filtered_out": len(rejected),
        "jobs_filtered_out_hc1_substantive_ds": hc1_excluded,
        "jobs_filtered_out_hc2_work_authorization": hc2_excluded,
        "jobs_filtered_out_other_pre_existing_filters": other_filter_excluded,
        "work_authorization_pass": wa_counts.get("PASS", 0),
        "work_authorization_fail": wa_counts.get("FAIL", 0),
        "work_authorization_unknown": wa_counts.get("UNKNOWN", 0),
        "work_authorization_foreigners_not_accepted": restriction_counts.get("FOREIGNERS_NOT_ACCEPTED", 0),
        "work_authorization_nationality_specific": restriction_counts.get("NATIONALITY_SPECIFIC", 0),
        "work_authorization_other_explicit_restriction": restriction_counts.get("OTHER_EXPLICIT_RESTRICTION", 0),
        "jobs_scored": len(scored_jobs),
        "ptf_scale": SCALE_VERSION,
        "tier_cutoffs (display; stored exactly)": display_thresholds(tier_thresholds(load_scoring()["weights"])),
        "decision_stability": dict(sorted(Counter(
            j.decision_stability.stability_label for j in scored_jobs if j.decision_stability).items())),
        "system_limitations": "; ".join(SYSTEM_LIMITATIONS),
        "tier_a": tier_counts.get("A", 0),
        "tier_b": tier_counts.get("B", 0),
        "tier_c": tier_counts.get("C", 0),
        "tier_d": tier_counts.get("D", 0),
        "malaysia_jobs": country_counts.get("Malaysia", 0),
        "singapore_jobs": country_counts.get("Singapore", 0),
        "other_country_jobs": sum(v for k, v in country_counts.items() if k not in ("Malaysia", "Singapore")),
    }
