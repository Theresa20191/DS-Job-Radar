"""Scoring-run reproducibility: posting age (and therefore the staleness
filter and job_risk's staleness flag) is computed against an explicit
as_of_date, never the wall clock. Thresholds themselves are unchanged."""

import ast
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.models.raw_job import RawJob
from src.parsers.date_parser import parse_posted_date
from src.pipeline.orchestrator import run
from src.scoring import job_risk

SCORING_CFG = {"network_bonus_max": 10, "job_risk_penalty_max": 20}
SRC = Path(__file__).resolve().parent.parent / "src"


# --- no implicit clock reads in scoring-relevant code -------------------------------

def test_no_scoring_code_reads_the_current_date():
    """date.today()/datetime.now() must not appear where an explicit
    scoring context is required. Model defaults for collection timestamps
    (scraped_at/first_seen_at/last_seen_at) are not scoring inputs."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.parts[-2:] == ("models", "raw_job.py"):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            call = node.func.attr
            base = getattr(node.func.value, "id", getattr(node.func.value, "attr", ""))
            if call in ("today", "now", "utcnow") and base in ("date", "datetime"):
                line = path.read_text().splitlines()[node.lineno - 1]
                if "scraped_at" in line or "first_seen_at" in line or "last_seen_at" in line:
                    continue          # collection timestamps, not scoring inputs
                if path.name == "orchestrator.py" and "as_of_date or" in line:
                    continue          # the single explicit boundary default
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}: {line.strip()}")
    assert offenders == [], offenders


# --- deterministic ages and staleness boundary ---------------------------------------

@pytest.mark.parametrize("posted, as_of, expected", [
    ("2026-07-24", date(2026, 9, 22), 60),          # exactly at the boundary
    ("2026-07-23", date(2026, 9, 22), 61),          # one day over
    ("2026-07-24T23:59:00+08:00", date(2026, 9, 22), 60),   # time of day must not matter
    ("2026-07-24T00:01:00+08:00", date(2026, 9, 22), 60),
])
def test_age_is_calendar_days_against_as_of_date(posted, as_of, expected):
    assert parse_posted_date(posted, as_of)["job_age_days"] == expected


def test_same_posting_different_as_of_dates_age_deterministically():
    posted = "2026-07-24T17:54:22+08:00"
    ages = {d: parse_posted_date(posted, date(2026, 9, d))["job_age_days"] for d in (20, 21, 22, 23)}
    assert ages == {20: 58, 21: 59, 22: 60, 23: 61}


def test_staleness_penalty_threshold_unchanged_at_60_days():
    """job_risk business rule is untouched: > 60 days adds the stale flag."""
    class J:
        description_raw = "x" * 400
        required_skills = []
        employment_type = "Full-Time"
        company = "Acme"
        salary_unknown = False
        salary_max_myr = 20000
        job_age_days = 60
    at_60 = job_risk.score(J(), SCORING_CFG)
    J.job_age_days = 61
    at_61 = job_risk.score(J(), SCORING_CFG)
    assert not any("days old" in r for r in at_60["reasons"])
    assert any("61 days old" in r for r in at_61["reasons"])
    assert at_61["score"] - at_60["score"] == 10


# --- end-to-end reproducibility -------------------------------------------------------

def _raw(job_id, posted, days_desc="Build production machine learning models for fraud detection."):
    return RawJob(
        job_id=job_id, source="hiredly", source_job_id=job_id, source_url=f"https://example.com/{job_id}",
        company=f"Co {job_id}", job_title="Data Scientist", job_description=days_desc * 6,
        location_raw="Kuala Lumpur | Kuala Lumpur", salary_raw="Undisclosed", posted_date_raw=posted,
        scraped_at=datetime(2026, 9, 20, tzinfo=timezone.utc), query_used="Data Scientist",
        source_extra={"requirements": "Required: Python, machine learning. 1+ years experience.", "skills": []},
    )


@pytest.fixture
def corpus(tmp_path):
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    # distinct text so the content-similarity dedup pass keeps them separate
    jobs = [_raw("a", "2026-07-24T17:54:22+08:00", "Build fraud detection models for the payments team. "),
            _raw("b", "2026-09-01", "Build forecasting models for the supply chain team. ")]
    (raw_dir / "hiredly_latest.jsonl").write_text("\n".join(j.model_dump_json() for j in jobs))
    (raw_dir / "mycareersfuture_latest.jsonl").write_text("")
    return tmp_path


def test_same_corpus_same_as_of_date_is_reproducible(corpus):
    first = run(corpus, as_of_date=date(2026, 9, 22))
    second = run(corpus, as_of_date=date(2026, 9, 22))
    dump = lambda result: [j.model_dump(mode="json") for j in result["scored_jobs"]]
    assert dump(first) == dump(second)
    assert first["as_of_date"] == second["as_of_date"] == "2026-09-22"
    assert first["pipeline_stats"]["as_of_date"] == "2026-09-22"


def test_only_date_sensitive_fields_change_across_as_of_dates(corpus):
    before = {j.job_id: j for j in run(corpus, as_of_date=date(2026, 9, 21))["scored_jobs"]}
    after = {j.job_id: j for j in run(corpus, as_of_date=date(2026, 9, 23))["scored_jobs"]}
    assert set(before) == set(after)
    date_sensitive = {"job_age_days", "as_of_date", "job_risk_score", "job_risk_penalty", "job_risk_reasons",
                      "overall_score", "tier", "opportunity_types", "recommended_action", "key_gap",
                      "decision_stability"}
    for job_id, old in before.items():
        changed = {k for k, v in old.model_dump(mode="json").items() if after[job_id].model_dump(mode="json")[k] != v}
        assert changed <= date_sensitive, (job_id, changed - date_sensitive)
    # the PTF evidence chain itself is date-independent
    for job_id, old in before.items():
        assert old.personal_transition_fit == after[job_id].personal_transition_fit
        assert old.technical_alignment == after[job_id].technical_alignment
        assert old.domain_alignment == after[job_id].domain_alignment
        assert old.base_score == after[job_id].base_score


def test_job_ages_are_recorded_with_the_as_of_date_used(corpus):
    result = run(corpus, as_of_date=date(2026, 9, 22))
    for job in result["scored_jobs"]:
        assert job.as_of_date == "2026-09-22"
    ages = {j.posted_date: j.job_age_days for j in result["scored_jobs"]}
    assert ages == {"2026-07-24": 60, "2026-09-01": 21}
