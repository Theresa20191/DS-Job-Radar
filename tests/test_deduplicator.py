from datetime import datetime, timezone

from datetime import date

from src.dedup.deduplicator import deduplicate
from src.models.raw_job import RawJob

AS_OF = date(2026, 8, 24)


def make_raw(job_id, query, source="hiredly", title="Data Scientist", company="Acme Sdn Bhd", loc="Kuala Lumpur | Kuala Lumpur"):
    return RawJob(
        job_id=job_id,
        source=source,
        source_job_id=job_id.split("_", 1)[1],
        source_url=f"https://example.com/{job_id}",
        company=company,
        job_title=title,
        job_description="We build machine learning models for fraud detection." * 3,
        location_raw=loc,
        salary_raw="10000 - 15000",
        posted_date_raw="2026-08-01T00:00:00+08:00",
        scraped_at=datetime.now(timezone.utc),
        query_used=query,
    )


def test_same_job_multiple_queries_dedupes_to_one():
    raws = [
        make_raw("hiredly_abc", "Data Scientist"),
        make_raw("hiredly_abc", "Machine Learning"),
        make_raw("hiredly_abc", "Analytics Scientist"),
    ]
    result = deduplicate(raws, AS_OF)
    assert len(result) == 1
    assert set(result[0].matched_queries) == {"Data Scientist", "Machine Learning", "Analytics Scientist"}
    assert result[0].sources == ["hiredly"]


def test_different_jobs_stay_separate():
    raws = [
        make_raw("hiredly_abc", "Data Scientist", title="Data Scientist", company="Acme Sdn Bhd"),
        make_raw("hiredly_xyz", "Data Scientist", title="ML Engineer", company="Beta Pte Ltd"),
    ]
    result = deduplicate(raws, AS_OF)
    assert len(result) == 2


def test_provenance_preserved_not_discarded():
    raws = [make_raw("hiredly_abc", "Data Scientist"), make_raw("hiredly_abc", "Decision Scientist")]
    result = deduplicate(raws, AS_OF)
    job = result[0]
    assert "hiredly_abc" in job.source_job_ids or "abc" in job.source_job_ids
    assert len(job.source_urls) >= 1
    assert job.first_seen_at is not None
    assert job.last_seen_at is not None


def test_cross_source_same_identity_merges():
    # Two distinct (hypothetical) sources not in the recognized geo map,
    # so location parsing falls back to the same "unknown" normalization
    # for both — isolating the identity-merge mechanism itself from
    # source-specific location parsing (which real sources are geo-siloed
    # for, by construction).
    a = make_raw("companycareers_1", "Data Scientist", source="companycareers", title="Data Scientist", company="Acme Sdn Bhd")
    b = make_raw("newboard_2", "Data Scientist", source="newboard", title="Data Scientist", company="Acme Sdn Bhd")
    result = deduplicate([a, b], AS_OF)
    assert len(result) == 1
    assert set(result[0].sources) == {"companycareers", "newboard"}


# --- P0-2: content-similarity dedup (recruiter reposts) ---

MANUFACTURING_JD = (
    "We are looking for a Data Scientist with 6+ years experience in manufacturing "
    "analytics. Hands-on experience in manufacturing / industrial / plant environments. "
    "Strong working knowledge of Seeq for time-series analysis. Must know Python, SQL."
) * 2


def make_full_raw(job_id, source, title, company, description, requirements, location_raw="Central | Singapore", query="Data Scientist"):
    return RawJob(
        job_id=job_id,
        source=source,
        source_job_id=job_id.split("_", 1)[1],
        source_url=f"https://example.com/{job_id}",
        company=company,
        job_title=title,
        job_description=description,
        location_raw=location_raw,
        salary_raw="",
        posted_date_raw="2026-08-01T00:00:00+08:00",
        scraped_at=datetime.now(timezone.utc),
        query_used=query,
        source_extra={"requirements": requirements},
    )


def test_recruiter_repost_same_job_merges():
    a = make_full_raw("mycareersfuture_1", "mycareersfuture", "Data Scientist - Manufacturing Analytics", "Unison Consulting Pte Ltd", MANUFACTURING_JD, "")
    b = make_full_raw("mycareersfuture_2", "mycareersfuture", "Data Scientist - Manufacturing Analytics", "Jeet Analytics Pte Ltd", MANUFACTURING_JD, "")
    result = deduplicate([a, b], AS_OF)
    assert len(result) == 1
    assert any("Jeet Analytics" in c or "Unison Consulting" in c for c in result[0].duplicate_companies)


def test_same_title_different_job_does_not_merge():
    a = make_full_raw(
        "mycareersfuture_1", "mycareersfuture", "Data Scientist", "Company A",
        "We are looking for a Data Scientist with 6+ years experience in manufacturing analytics. Python, SQL, Seeq." * 2,
        "",
    )
    b = make_full_raw(
        "mycareersfuture_2", "mycareersfuture", "Data Scientist", "Company B",
        "Join our fintech startup as a Data Scientist. Build fraud detection models using XGBoost, work with the risk team on transaction monitoring." * 2,
        "",
    )
    result = deduplicate([a, b], AS_OF)
    assert len(result) == 2


def test_similar_but_materially_different_jds_do_not_merge():
    a = make_full_raw(
        "mycareersfuture_1", "mycareersfuture", "Data Scientist", "Company A",
        "We are looking for a Data Scientist with 6+ years experience in manufacturing analytics. Must know Python, SQL, Seeq. On behalf of our client. "
        "You will own dashboards for plant managers and build sensor forecasting models for predictive maintenance across factories.",
        "",
    )
    b = make_full_raw(
        "mycareersfuture_2", "mycareersfuture", "Data Scientist", "Company B",
        "We are looking for a Data Scientist with 5+ years experience in manufacturing analytics. Must know Python, SQL, PowerBI. On behalf of our client. "
        "You will own quality control models for defect detection and build yield optimization models across production lines.",
        "",
    )
    result = deduplicate([a, b], AS_OF)
    assert len(result) == 2


def test_content_dedup_preserves_all_provenance():
    a = make_full_raw("mycareersfuture_1", "mycareersfuture", "Data Scientist - Manufacturing Analytics", "Unison Consulting Pte Ltd", MANUFACTURING_JD, "", query="Data Scientist")
    b = make_full_raw("mycareersfuture_2", "mycareersfuture", "Data Scientist - Manufacturing Analytics", "Jeet Analytics Pte Ltd", MANUFACTURING_JD, "", query="Analytics Scientist")
    result = deduplicate([a, b], AS_OF)
    job = result[0]
    assert set(job.source_urls) == {a.source_url, b.source_url}
    assert set(job.source_job_ids) == {"1", "2"}
    assert set(job.matched_queries) == {"Data Scientist", "Analytics Scientist"}
