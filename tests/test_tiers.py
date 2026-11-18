import pytest

from src.enrichment.skill_evidence import build_evidence
from src.models.processed_job import ProcessedJob
from src.scoring.job_quality import ROLE_TYPE_BASE
from src.scoring.overall import score_jobs
from src.scoring.tiers import tier_thresholds


def enriched(**fields) -> ProcessedJob:
    """Attach evidence-layer fields the way enrichment does."""
    evidence, sections = build_evidence(fields["description_raw"], fields["requirements_raw"])
    return ProcessedJob(**fields, skill_evidence=evidence, requirements_section_status=sections.status)

PROFILE = {
    "years_experience_current_role": 1,
    "education_level_rank": 3,
    "target_locations": ["Malaysia", "Singapore", "Kuala Lumpur"],
    "skills": {
        "strong": ["Python", "SQL", "Scikit-learn", "XGBoost", "Machine Learning"],
        "moderate": ["R", "Power BI"],
    },
    "techniques": {"strong": ["Machine Learning", "Predictive Modeling"], "moderate": []},
    "domain": ["Banking", "Fraud", "AML", "Risk", "Fintech"],
}

SCORING_CFG = {
    "weights": {
        "job_quality": 0.30, "interview_fit": 0.10, "compensation": 0.08,
        "company_maturity": 0.07, "location": 0.05,
    },
    "network_bonus_max": 10,
    "job_risk_penalty_max": 20,
    "opportunity_types": {
        "strong_interview_fit_min_interview_fit": 70,
        "strong_interview_fit_max_seniority_gap_years": 1,
        "stretch_min_seniority_gap_years": 2,
        "domain_advantage_min_band": "SUBSTANTIVE",
        "high_compensation_min_percentile": 75,
    },
    "job_quality": {
        "positive_keywords": ["machine learning", "model deployment", "feature engineering", "production ml"],
        "negative_keywords": ["dashboard maintenance", "mis reporting"],
    },
    "completeness": {
        "completeness_fields": [
            "title", "company", "location", "description_min_length",
            "requirements_present", "salary_known", "years_experience_known",
            "employment_type_known", "skills_extracted",
        ]
    },
}


def strong_job(job_id="job_strong") -> ProcessedJob:
    return enriched(
        job_id=job_id,
        job_title="Data Scientist",
        company="Acme Bank",
        company_normalized="acme bank",
        role_type="TRUE_DS",
        role_type_confidence=0.8,
        role_level="Mid/Unspecified",
        description_raw="You will build production ml models for fraud detection and AML/banking risk, own feature engineering and model deployment end to end.",
        requirements_raw="Required: Python, SQL, Scikit-learn, XGBoost, machine learning, predictive modelling. 1+ years experience. Bachelor degree.",
        required_skills=["Python", "SQL", "Scikit-learn", "XGBoost", "Machine Learning"],
        preferred_skills=[],
        fraud=True, risk=True, fintech=True,
        country="Malaysia", city="Kuala Lumpur", location_normalized="Kuala Lumpur", remote_type="Onsite",
        years_experience_required=1.0,
        education_required="Bachelor",
        employment_type="Full-Time",
        salary_min_myr=15000, salary_max_myr=20000, salary_unknown=False,
        job_age_days=5,
    )


def weak_job(job_id="job_weak") -> ProcessedJob:
    return enriched(
        job_id=job_id,
        job_title="Data Scientist",
        company="Beta Corp",
        company_normalized="beta corp",
        role_type="DS_TITLE_BUT_ANALYTICS_HEAVY",
        role_type_confidence=0.5,
        role_level="Mid/Unspecified",
        description_raw="Maintain dashboards and mis reporting for stakeholders, ad-hoc reporting on demand." * 3,
        requirements_raw="Required: SQL, Power BI. 5+ years experience. Master degree required.",
        required_skills=["Power BI"],
        preferred_skills=[],
        fraud=False, risk=False, fintech=False,
        country="Malaysia", city="Kuala Lumpur", location_normalized="Kuala Lumpur", remote_type="Onsite",
        years_experience_required=5.0,
        education_required="Master",
        employment_type="Contract",
        salary_min_myr=None, salary_max_myr=None, salary_unknown=True,
        job_age_days=90,
    )


def test_strong_job_scores_high_tier():
    result = score_jobs([strong_job(), weak_job()], PROFILE, SCORING_CFG)
    strong = next(j for j in result if j.job_id == "job_strong")
    assert strong.technical_alignment.band == "SUBSTANTIVE"      # Predictive Modeling, listed strong, required
    assert strong.domain_alignment.band == "SUBSTANTIVE"         # fraud detection is the job's own work
    assert strong.overall_score >= tier_thresholds(SCORING_CFG["weights"])["B"]
    assert strong.tier in ("A", "B")


def test_weak_job_scores_low_tier():
    result = score_jobs([strong_job(), weak_job()], PROFILE, SCORING_CFG)
    weak = next(j for j in result if j.job_id == "job_weak")
    assert weak.role_type == "DS_TITLE_BUT_ANALYTICS_HEAVY"       # eligible now, but job_quality base 20
    assert weak.personal_transition_fit.known is False            # no REQUIRED technique -> unknown, not zero
    assert weak.tier in ("C", "D")


def test_tier_ordering_consistent_with_score():
    result = score_jobs([strong_job(), weak_job()], PROFILE, SCORING_CFG)
    strong = next(j for j in result if j.job_id == "job_strong")
    weak = next(j for j in result if j.job_id == "job_weak")
    assert strong.overall_score > weak.overall_score


def test_ds_substance_opportunity_type():
    result = score_jobs([strong_job()], PROFILE, SCORING_CFG)
    strong = result[0]
    assert strong.role_type == "TRUE_DS"
    assert "DS_SUBSTANCE" in strong.opportunity_types
    assert "CAREER_ACCELERATOR" not in strong.opportunity_types


@pytest.mark.parametrize("role_type", sorted(ROLE_TYPE_BASE))
def test_ds_substance_is_exactly_the_old_career_accelerator_rule(role_type):
    """D-5 label-only rename: DS_SUBSTANCE fires for exactly the jobs the old
    `job_quality >= 75 and role_type == TRUE_DS` rule flagged."""
    job = strong_job().model_copy(update={"role_type": role_type})
    result = score_jobs([job], PROFILE, SCORING_CFG)[0]
    old_rule = result.job_quality_score >= 75 and role_type == "TRUE_DS"
    assert ("DS_SUBSTANCE" in result.opportunity_types) == old_rule


def test_domain_advantage_opportunity_type():
    result = score_jobs([strong_job()], PROFILE, SCORING_CFG)
    strong = result[0]
    assert strong.domain_alignment.band in ("SUBSTANTIVE", "CORE")
    assert "DOMAIN_ADVANTAGE" in strong.opportunity_types


def test_domain_advantage_not_triggered_by_limited():
    job = strong_job()
    job = job.model_copy(update={"description_raw": "We serve clients in banking. You will build production ml models."})
    result = score_jobs([job], PROFILE, SCORING_CFG)[0]
    assert result.domain_alignment.band == "LIMITED"
    assert "DOMAIN_ADVANTAGE" not in result.opportunity_types


def test_strong_interview_fit_replaces_high_probability():
    result = score_jobs([strong_job()], PROFILE, SCORING_CFG)[0]
    assert "HIGH_PROBABILITY" not in result.opportunity_types
    assert result.interview_fit_score >= 70 and "STRONG_INTERVIEW_FIT" in result.opportunity_types


def test_networking_opportunity_type():
    contacts = {"acme bank": [{"contact_name": "Jane", "relationship": "friend", "strength": "strong"}]}
    result = score_jobs([strong_job()], PROFILE, SCORING_CFG, contacts_by_company=contacts)
    strong = result[0]
    assert "NETWORKING_OPPORTUNITY" in strong.opportunity_types
    assert strong.recommended_action == "APPLY + NETWORK"


def test_recommended_action_skip_for_tier_d():
    weak = weak_job()
    weak.description_raw = "short desc " * 20
    result = score_jobs([weak], PROFILE, SCORING_CFG)
    job = result[0]
    if job.tier == "D":
        assert job.recommended_action == "SKIP"


def test_completeness_lower_for_sparse_job():
    result = score_jobs([strong_job(), weak_job()], PROFILE, SCORING_CFG)
    strong = next(j for j in result if j.job_id == "job_strong")
    weak = next(j for j in result if j.job_id == "job_weak")
    assert strong.data_completeness_pct >= weak.data_completeness_pct


def test_why_apply_and_key_gap_populated():
    result = score_jobs([strong_job(), weak_job()], PROFILE, SCORING_CFG)
    strong = next(j for j in result if j.job_id == "job_strong")
    weak = next(j for j in result if j.job_id == "job_weak")
    assert len(strong.why_apply) > 0
    assert len(weak.key_gap) > 0
