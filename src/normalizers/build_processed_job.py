"""Turns a single RawJob into the flat field-dict used both as a dedup
grouping key input and as the base for constructing a ProcessedJob.

`as_of_date` (the scoring-run date) is explicit: job_age_days is computed
against it, never against the current clock."""

from datetime import date

from src.models.raw_job import RawJob
from src.normalizers.company_normalizer import normalize_company
from src.normalizers.text_cleaner import clean_html
from src.normalizers.title_normalizer import normalize_title
from src.parsers.date_parser import parse_posted_date
from src.parsers.location_parser import parse_location
from src.parsers.salary_parser import parse_salary, to_myr


def extract_fields(raw: RawJob, as_of_date: date) -> dict:
    salary = parse_salary(raw.salary_raw, raw.source)
    salary_min_myr, salary_max_myr = to_myr(
        salary["salary_min"], salary["salary_max"], salary["salary_currency"]
    )
    location = parse_location(raw.location_raw, raw.source)
    date_fields = parse_posted_date(raw.posted_date_raw, as_of_date)

    requirements_html = raw.source_extra.get("requirements", "") if raw.source_extra else ""

    return {
        "job_title_normalized": normalize_title(raw.job_title),
        "company_normalized": normalize_company(raw.company),
        "description_raw": clean_html(raw.job_description),
        "requirements_raw": clean_html(requirements_html),
        "responsibilities_raw": "",  # neither source cleanly separates this out
        "employment_type": raw.employment_type_raw or "unknown",
        **salary,
        "salary_min_myr": salary_min_myr,
        "salary_max_myr": salary_max_myr,
        **location,
        **date_fields,
        "as_of_date": as_of_date.isoformat(),
    }
