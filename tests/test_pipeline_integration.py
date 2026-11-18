"""End-to-end integration test using synthetic fixtures only — never hits
live sites in tests (section 28)."""

import json
from datetime import datetime, timezone

from src.models.raw_job import RawJob
from src.pipeline.orchestrator import run
from src.reporting.markdown_report import generate_report


def _raw(job_id, source, title, company, description, requirements, location_raw, salary_raw, query, days_old=5):
    scraped_at = datetime.now(timezone.utc)
    return RawJob(
        job_id=job_id,
        source=source,
        source_job_id=job_id.split("_", 1)[1],
        source_url=f"https://example.com/{job_id}",
        company=company,
        job_title=title,
        job_description=description,
        location_raw=location_raw,
        salary_raw=salary_raw,
        posted_date_raw=scraped_at.date().isoformat(),
        scraped_at=scraped_at,
        query_used=query,
        source_extra={"requirements": requirements, "skills": [{"name": "Python"}]},
    )


def _write_jsonl(path, jobs):
    with open(path, "w") as f:
        for j in jobs:
            f.write(j.model_dump_json() + "\n")


def test_full_pipeline_end_to_end(tmp_path):
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)

    mcf_jobs = [
        _raw(
            "mycareersfuture_1", "mycareersfuture", "Data Scientist", "Example Bank",
            "Build production forecasting and regression models for e-commerce payments. Own feature engineering, model deployment and evaluation." * 2,
            "Required: Python, SQL, Scikit-learn, Statistics, Forecasting, Regression. 1+ years experience. Bachelor degree.",
            json.dumps({"districts": [{"region": "Central"}]}),
            json.dumps({"minimum": 6000, "maximum": 9000, "type": {"salaryType": "Monthly"}}),
            "Data Scientist",
        ),
        _raw(
            "mycareersfuture_2", "mycareersfuture", "Business Analyst", "Some Corp",
            "Generate reports and dashboards for stakeholders." * 5,
            "SQL, Excel required.",
            json.dumps({"districts": [{"region": "East"}]}),
            json.dumps({"minimum": None, "maximum": None, "type": {}}),
            "Data Scientist",
        ),
    ]
    hiredly_jobs = [
        _raw(
            "hiredly_1", "hiredly", "Data Scientist", "Acme Sdn Bhd",
            "Maintain dashboards and mis reporting for management, ad-hoc reporting daily." * 5,
            "SQL, Power BI. 5+ years experience. Master degree required.",
            "Kuala Lumpur | Kuala Lumpur",
            "Undisclosed",
            "Data Scientist",
        ),
        _raw(
            "hiredly_2", "hiredly", "Barista", "Coffee Co",
            "Make coffee and serve customers." * 5,
            "",
            "Selangor | Selangor",
            "2000 - 2500",
            "Machine Learning",
        ),
    ]

    _write_jsonl(raw_dir / "mycareersfuture_latest.jsonl", mcf_jobs)
    _write_jsonl(raw_dir / "hiredly_latest.jsonl", hiredly_jobs)

    result = run(tmp_path)

    assert result["pipeline_stats"]["jobs_scraped"] == 4
    assert len(result["scored_jobs"]) >= 1

    ds_job = next((j for j in result["scored_jobs"] if j.company == "Example Bank"), None)
    assert ds_job is not None
    assert ds_job.role_type == "TRUE_DS"
    assert ds_job.tier in ("A", "B")
    assert ds_job.overall_score > 60

    filtered_titles = [j.job_title for j, _ in result["rejected"]]
    assert "Business Analyst" in filtered_titles or "Barista" in filtered_titles

    report_md = generate_report(result["scored_jobs"], result["pipeline_stats"], result["source_completeness"])
    assert "Data Scientist Job Radar" in report_md
    assert "Example Bank" in report_md


def _ds_body(domain: str) -> str:
    # Distinct text per posting so the content-similarity dedup pass (which
    # merges recruiter reposts) keeps them as separate jobs.
    return (f"Our {domain} team builds production machine learning models. You will own feature engineering "
            f"for {domain} use cases, model deployment and model evaluation, and present {domain} results.")


def test_hard_filter_revision_end_to_end(tmp_path):
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    loc = "Kuala Lumpur | Kuala Lumpur"
    jobs = [
        # HC-1 revision: DS title, analytics-heavy scope -> eligible, role_type kept
        _raw("hiredly_ah", "hiredly", "Data Scientist", "Analytics Co",
             "Maintain dashboards and mis reporting for management, ad-hoc reporting daily." * 5,
             "SQL, Power BI.", loc, "Undisclosed", "Data Scientist"),
        # HC-2 FAIL: explicit nationality exclusion
        _raw("hiredly_nat", "hiredly", "Data Scientist", "Citizen Only Bhd",
             _ds_body("logistics forecasting"), "Python, SQL. Malaysian citizens only.", loc, "Undisclosed", "Data Scientist"),
        # No sponsorship is not an exclusion
        _raw("hiredly_nosp", "hiredly", "Data Scientist", "No Sponsor Bhd",
             _ds_body("retail pricing optimisation"), "Python, SQL. No visa sponsorship.", loc, "Undisclosed", "Data Scientist"),
        # Contradiction -> UNKNOWN, eligible
        _raw("hiredly_contra", "hiredly", "Data Scientist", "Mixed Signals Bhd",
             _ds_body("healthcare claims triage"), "Malaysian citizens only. Non-Malaysians with valid work permits may apply.", loc, "Undisclosed", "Data Scientist"),
    ]
    _write_jsonl(raw_dir / "hiredly_latest.jsonl", jobs)
    _write_jsonl(raw_dir / "mycareersfuture_latest.jsonl", [])

    result = run(tmp_path)
    scored = {j.company: j for j in result["scored_jobs"]}
    rejected = {j.company: (j, reasons) for j, reasons in result["rejected"]}

    ah = scored["Analytics Co"]
    assert ah.role_type == "DS_TITLE_BUT_ANALYTICS_HEAVY"
    assert ah.hc_substantive_ds_status == "PASS"
    assert ah.job_quality_score == 20.0   # existing low base, unchanged

    nat_job, nat_reasons = rejected["Citizen Only Bhd"]
    assert nat_job.hc_work_authorization_status == "FAIL"
    assert nat_job.work_authorization_restriction_type == "NATIONALITY_SPECIFIC"
    ev = nat_job.work_authorization_evidence[0]
    assert (ev.matched_text, ev.source_field) == ("Malaysian citizens only", "requirements_raw")
    assert "Malaysian citizens only" in ev.context
    assert any(r.startswith("HC-2") and "Malaysian citizens only" in r for r in nat_reasons)

    nosp = scored["No Sponsor Bhd"]
    assert nosp.hc_work_authorization_status == "UNKNOWN"
    assert nosp.work_authorization_restriction_type == "OTHER_EXPLICIT_RESTRICTION"
    assert nosp.work_authorization_reason in nosp.key_gap   # surfaced for the candidate's own check

    contra = scored["Mixed Signals Bhd"]
    assert contra.hc_work_authorization_status == "UNKNOWN"
    assert {e.restriction_type for e in contra.work_authorization_evidence} >= {"NATIONALITY_SPECIFIC", "OTHER_EXPLICIT_RESTRICTION"}

    stats = result["pipeline_stats"]
    assert stats["jobs_filtered_out_hc2_work_authorization"] == 1
    assert stats["jobs_filtered_out_hc1_substantive_ds"] == 0
    assert stats["work_authorization_nationality_specific"] == 1
    assert stats["work_authorization_fail"] == 1

    report_md = generate_report(result["scored_jobs"], stats, result["source_completeness"], result["rejected"])
    assert "## Excluded by hard constraints (1 jobs)" in report_md
    assert "HC-2 NATIONALITY_SPECIFIC: 'Malaysian citizens only' (requirements_raw" in report_md
    assert "work authorization = UNKNOWN [OTHER_EXPLICIT_RESTRICTION]" in report_md
