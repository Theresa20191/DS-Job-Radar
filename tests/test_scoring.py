import inspect

from src.models.processed_job import ProcessedJob
from src.scoring import (
    company_maturity,
    compensation,
    completeness,
    interview_fit,
    job_quality,
    job_risk,
    location,
    network_bonus,
)

PROFILE = {
    "years_experience_current_role": 1,
    "education_level_rank": 3,
    "target_locations": ["Malaysia", "Singapore", "Kuala Lumpur"],
    "skills": {
        "strong": ["Python", "SQL", "Scikit-learn", "XGBoost", "Machine Learning"],
        "moderate": ["R", "Power BI"],
    },
    "domain": ["Banking", "Fraud", "AML", "Risk", "Fintech"],
}

SCORING_CFG = {
    "job_quality": {
        "positive_keywords": ["machine learning", "model deployment", "feature engineering"],
        "negative_keywords": ["dashboard maintenance", "mis reporting"],
    },
    "network_bonus_max": 10,
    "job_risk_penalty_max": 20,
    "completeness": {
        "completeness_fields": [
            "title", "company", "location", "description_min_length",
            "requirements_present", "salary_known", "years_experience_known",
            "employment_type_known", "skills_extracted",
        ]
    },
}


def true_ds_job(**overrides) -> ProcessedJob:
    defaults = dict(
        job_id="job_1",
        job_title="Data Scientist",
        company="Acme Bank",
        company_normalized="acme bank",
        role_type="TRUE_DS",
        role_type_confidence=0.7,
        role_level="Mid/Unspecified",
        description_raw="We build machine learning models for fraud detection, own feature engineering and model deployment." * 2,
        requirements_raw="Required: Python, SQL, scikit-learn. 2+ years experience. Bachelor degree required.",
        required_skills=["Python", "SQL", "Scikit-learn"],
        preferred_skills=["AWS"],
        fraud=True, risk=True, fintech=True,
        country="Malaysia", city="Kuala Lumpur", location_normalized="Kuala Lumpur", remote_type="Onsite",
        years_experience_required=2.0,
        education_required="Bachelor",
        employment_type="Full-Time",
        salary_min_myr=8000, salary_max_myr=12000, salary_unknown=False,
        job_age_days=5,
    )
    defaults.update(overrides)
    return ProcessedJob(**defaults)


# --- job_quality (renamed/rescoped from career_transition) ---

def test_job_quality_true_ds_scores_high():
    job = true_ds_job()
    result = job_quality.score(job, PROFILE, SCORING_CFG)
    assert result["score"] > 60
    assert result["known"] is True


def test_job_quality_analytics_heavy_scores_low():
    job = true_ds_job(role_type="DS_TITLE_BUT_ANALYTICS_HEAVY", description_raw="Maintain dashboards and mis reporting daily.")
    result = job_quality.score(job, PROFILE, SCORING_CFG)
    assert result["score"] < 40


def test_job_quality_candidate_independent():
    # Same job, two very different profiles — job_quality must not move.
    job = true_ds_job()
    profile_a = PROFILE
    profile_b = {**PROFILE, "skills": {"strong": [], "moderate": []}, "domain": []}
    result_a = job_quality.score(job, profile_a, SCORING_CFG)
    result_b = job_quality.score(job, profile_b, SCORING_CFG)
    assert result_a["score"] == result_b["score"]


def test_job_quality_score_equals_role_type_base_only():
    # Numeric value must come solely from role_type's base — keyword hits
    # may only appear in reasons text, never move the number (the fix
    # for the role_type/keyword-bonus double-count found in the audit).
    job_many_keywords = true_ds_job(
        description_raw="machine learning model deployment feature engineering " * 5,
    )
    job_no_keywords = true_ds_job(description_raw="We do genuinely substantive DS work with real modelling ownership here.")
    # Force both to the same role_type so only keyword density differs
    result_many = job_quality.score(job_many_keywords, PROFILE, SCORING_CFG)
    result_few = job_quality.score(job_no_keywords, PROFILE, SCORING_CFG)
    assert result_many["score"] == result_few["score"] == job_quality.ROLE_TYPE_BASE["TRUE_DS"]


# technical_fit and domain_fit were retired in the PTF migration; their
# replacements are covered in test_technical_alignment.py and
# test_domain_alignment.py (AML disambiguation included).


def test_skill_extractor_no_longer_extracts_domain_terms():
    # Taxonomy cleanup: Fraud/AML/Risk/Credit Risk removed from
    # skills_taxonomy.yaml (domain-exclusive ownership) — skill_extractor
    # must not extract them as skills at all any more.
    from src.enrichment.skill_extractor import extract_skills
    skills = extract_skills("This role focuses on fraud, AML, risk, and credit risk analytics.")
    assert "Fraud" not in skills
    assert "AML" not in skills
    assert "Risk" not in skills
    assert "Credit Risk" not in skills


# --- interview_fit: candidate-specific, independent of PTF evidence ---

def test_interview_fit_signature_has_no_borrowed_scores():
    # Architecture guarantee: no top-level score depends on another
    # already-weighted score — interview_fit never consumes PTF evidence.
    params = set(inspect.signature(interview_fit.score).parameters)
    assert params == {"job", "profile", "config"}


def test_interview_fit_has_no_probability_field():
    from src.models.scored_job import ScoredJob
    assert "interview_fit_score" in ScoredJob.model_fields
    assert "interview_probability_score" not in ScoredJob.model_fields


def test_interview_fit_meets_experience():
    job = true_ds_job(years_experience_required=1.0)
    result = interview_fit.score(job, PROFILE, SCORING_CFG)
    assert result["known"] is True
    assert result["score"] > 50


def test_interview_fit_large_gap_scores_lower():
    job_small_gap = true_ds_job(years_experience_required=1.0)
    job_large_gap = true_ds_job(years_experience_required=8.0)
    small = interview_fit.score(job_small_gap, PROFILE, SCORING_CFG)
    large = interview_fit.score(job_large_gap, PROFILE, SCORING_CFG)
    assert large["score"] < small["score"]


def test_interview_fit_explicit_yoe_2_to_4_vs_6_to_8_distinct():
    job_2_4 = true_ds_job(job_title="Senior Data Scientist", role_level="Senior", years_experience_required=3.0)
    job_6_8 = true_ds_job(job_title="Senior Data Scientist", role_level="Senior", years_experience_required=7.0)
    result_2_4 = interview_fit.score(job_2_4, PROFILE, SCORING_CFG)
    result_6_8 = interview_fit.score(job_6_8, PROFILE, SCORING_CFG)
    assert result_2_4["score"] > result_6_8["score"]
    assert any("explicit YOE" in r for r in result_2_4["reasons"])


def test_interview_fit_senior_title_without_explicit_yoe_not_exact():
    # Section 6: title-based seniority is a fallback heuristic, never
    # treated as if it were an exact YOE requirement.
    job = true_ds_job(job_title="Senior Data Scientist", role_level="Senior", years_experience_required=None)
    result = interview_fit.score(job, PROFILE, SCORING_CFG)
    assert any("no explicit YOE stated" in r for r in result["reasons"])


def test_interview_fit_explicit_yoe_suppresses_title_penalty():
    # The core YOE-vs-seniority fix: a "Senior" title with an explicit,
    # achievable YOE range must not ALSO eat a flat title-based penalty
    # on top of its already-accurate experience-gap score.
    job = true_ds_job(job_title="Senior Data Scientist", role_level="Senior", years_experience_required=1.0)
    result = interview_fit.score(job, PROFILE, SCORING_CFG)
    assert not any("no explicit YOE stated" in r for r in result["reasons"])
    # candidate (1 yr) meets a 1-yr explicit requirement -> should score
    # like a met requirement, not be dragged down by a title penalty
    assert result["score"] >= 80


# --- compensation: unknown must never be treated as lowest ---

def test_compensation_batch_percentiles():
    low = true_ds_job(job_id="low", salary_min_myr=4000, salary_max_myr=5000, salary_unknown=False)
    high = true_ds_job(job_id="high", salary_min_myr=15000, salary_max_myr=20000, salary_unknown=False)
    unknown_sal = true_ds_job(job_id="unk", salary_min_myr=None, salary_max_myr=None, salary_unknown=True)
    results = compensation.score_batch([low, high, unknown_sal])
    assert results["low"]["score"] < results["high"]["score"]
    assert results["unk"]["known"] is False
    assert results["unk"]["score"] is None  # never a misleading 0.0 / "lowest possible"


def test_company_maturity_always_unknown():
    job = true_ds_job()
    result = company_maturity.score(job, PROFILE, SCORING_CFG)
    assert result["known"] is False


def test_location_target_match():
    job = true_ds_job(country="Malaysia", city="Kuala Lumpur", location_normalized="Kuala Lumpur")
    result = location.score(job, PROFILE, SCORING_CFG)
    assert result["score"] >= 80


def test_location_remote():
    job = true_ds_job(remote_type="Remote", country="United States")
    result = location.score(job, PROFILE, SCORING_CFG)
    assert result["score"] >= 90


def test_location_outside_geography():
    job = true_ds_job(country="United States", city="New York", location_normalized="New York", remote_type="Onsite")
    result = location.score(job, PROFILE, SCORING_CFG)
    assert result["score"] < 50


def test_network_bonus_no_contacts():
    job = true_ds_job()
    result = network_bonus.score(job, {}, SCORING_CFG)
    assert result["bonus"] == 0.0


def test_network_bonus_strong_contact():
    job = true_ds_job(company_normalized="acme bank")
    contacts = {"acme bank": [{"contact_name": "Jane", "relationship": "ex-colleague", "strength": "strong"}]}
    result = network_bonus.score(job, contacts, SCORING_CFG)
    assert result["bonus"] == 10.0


# --- job_risk: evidence ownership (DS_TITLE_BUT_ANALYTICS_HEAVY and
# DS_TITLE_UNCLEAR branches removed as confirmed double-counts with
# job_quality/HC-1 — see job_risk.py docstring) ---

def test_job_risk_clean_job_low_risk():
    job = true_ds_job()
    result = job_risk.score(job, SCORING_CFG)
    assert result["score"] < 30


def test_job_risk_no_longer_penalizes_role_type_directly():
    # These role_types are now either hard-excluded (DS_TITLE_BUT_ANALYTICS_HEAVY)
    # or already scored low by job_quality (DS_TITLE_UNCLEAR) — job_risk
    # must not also penalize role_type directly any more.
    job_unclear = true_ds_job(role_type="DS_TITLE_UNCLEAR")
    result = job_risk.score(job_unclear, SCORING_CFG)
    assert not any("DS content" in r or "analytics" in r.lower() for r in result["reasons"])


def test_job_risk_flags_implausible_salary():
    job = true_ds_job(salary_min_myr=157300, salary_max_myr=442000, salary_unknown=False)
    result = job_risk.score(job, SCORING_CFG)
    assert result["score"] > 0
    assert any("implausible" in r.lower() for r in result["reasons"])


def test_completeness_full_job():
    job = true_ds_job(requirements_section_status="DETECTED")
    pct, unknown = completeness.compute_completeness(job, SCORING_CFG)
    assert pct == 100.0
    assert unknown == []


def test_completeness_sparse_job():
    job = true_ds_job(salary_unknown=True, years_experience_required=None, required_skills=[], preferred_skills=[])
    pct, unknown = completeness.compute_completeness(job, SCORING_CFG)
    assert pct < 100.0
    assert "salary_known" in unknown


def test_requirements_present_follows_section_detection_not_raw_field():
    # C-4: detected in the description with an empty source field -> present
    detected = true_ds_job(requirements_raw="", requirements_section_status="DETECTED")
    assert "requirements_present" not in completeness.compute_completeness(detected, SCORING_CFG)[1]
    # non-empty source field but no section detected -> not present
    undetected = true_ds_job(requirements_raw="Python.", requirements_section_status="UNKNOWN")
    assert "requirements_present" in completeness.compute_completeness(undetected, SCORING_CFG)[1]
