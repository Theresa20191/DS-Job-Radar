"""Career Coach input boundary (IN-1..IN-5) and live-check eligibility
(LC-1..LC-3).

The complete input is every enriched job paired with its hard-filter
evaluation, produced by the existing pipeline.hard_filters.filter_jobs —
consumed unchanged, never re-derived.
"""

import csv
import json
from datetime import date
from pathlib import Path

from src.models.career_coach import CoachInput, HardFilterEvaluation, JobProjection, LiveCheckRecord
from src.models.processed_job import ProcessedJob
from src.models.raw_job import RawJob
from src.normalizers.company_normalizer import normalize_company
from src.pipeline.hard_filters import filter_jobs


def load_contact_companies(path: Path) -> set[str]:
    """P-4: normalized company names that have at least one contact. Only the
    company column is read; a missing file means no contacts."""
    if not path.exists():
        return set()
    with open(path, newline="") as f:
        return {normalize_company(row["company"]) for row in csv.DictReader(f) if (row.get("company") or "").strip()}


def load_raw_jobs(raw_dir: Path, sources: list[str]) -> list[RawJob]:
    """The raw-layer read, identical in behaviour to the pipeline's own loader
    (one `<source>_latest.jsonl` per enabled source, in source order)."""
    jobs: list[RawJob] = []
    for source in sources:
        path = raw_dir / f"{source}_latest.jsonl"
        if path.exists():
            with open(path) as f:
                jobs.extend(RawJob.model_validate_json(line) for line in f if line.strip())
    return jobs


def build_population(enriched: list[ProcessedJob], hard_filter_config: dict) -> list[CoachInput]:
    """IN-1: every enriched job with its hard-filter evaluation (passed AND
    rejected). IN-5: each enriched job appears exactly once."""
    passed, rejected = filter_jobs(enriched, hard_filter_config)
    population = [_input(job, []) for job in passed] + [_input(job, reasons) for job, reasons in rejected]
    ids = [c.job.job_id for c in population]
    if len(ids) != len(set(ids)) or set(ids) != {j.job_id for j in enriched}:
        raise ValueError("IN-5: the evaluated population must contain every enriched job exactly once")
    return sorted(population, key=lambda c: c.job.job_id)


def _input(job: ProcessedJob, reasons: list[str]) -> CoachInput:
    return CoachInput(
        job=JobProjection.from_processed(job),
        hard_filter=HardFilterEvaluation(passed=not reasons, reasons=list(reasons),
                                         hc_substantive_ds_status=job.hc_substantive_ds_status,
                                         hc_work_authorization_status=job.hc_work_authorization_status),
    )


def load_live_checks(path: Path) -> list[LiveCheckRecord]:
    """LC-1: append-only records; a missing file means no records."""
    if not path.exists():
        return []
    with open(path) as f:
        return [LiveCheckRecord.model_validate(json.loads(line)) for line in f if line.strip()]


def eligible_live_checks(records: list[LiveCheckRecord], as_of_date: date, window_days: int
                         ) -> tuple[dict[str, LiveCheckRecord], list[tuple[str, str]]]:
    """LC-2 / LC-3. Returns ({job_id: the record used}, [(record_id, why ignored)]).
    A record is eligible only if checked_on <= as_of_date and it is at most
    `window_days` old; the latest eligible record wins, ties by file order."""
    used: dict[str, LiveCheckRecord] = {}
    ignored: list[tuple[str, str]] = []
    for record in records:
        if record.checked_on > as_of_date:
            ignored.append((record.record_id, "AFTER_AS_OF"))
        elif (as_of_date - record.checked_on).days > window_days:
            ignored.append((record.record_id, "OUTSIDE_WINDOW"))
        elif record.job_id not in used or record.checked_on >= used[record.job_id].checked_on:
            used[record.job_id] = record
    return used, ignored
