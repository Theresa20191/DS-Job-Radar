import pytest

from src.models.processed_job import ProcessedJob
from src.pipeline.hard_filters import HC1_REJECT_ROLE_TYPES, evaluate, filter_jobs

CONFIG = {
    "hard_filters": {
        "reject_role_families": ["Software Engineer", "Business Analyst", "BI Analyst"],
        "min_description_length_chars": 50,
        "max_job_age_days": 120,
        "required_countries": ["Malaysia", "Singapore"],
        "allow_remote_regardless_of_country": True,
    }
}


def make_job(**overrides) -> ProcessedJob:
    defaults = dict(
        job_id="job_1",
        job_title="Data Scientist",
        role_family="Data Scientist",
        role_type="TRUE_DS",
        description_raw="x" * 200,
        job_age_days=10,
        country="Malaysia",
        remote_type="Onsite",
    )
    defaults.update(overrides)
    return ProcessedJob(**defaults)


def test_true_ds_passes():
    passed, rejected = filter_jobs([make_job()], CONFIG)
    assert len(passed) == 1
    assert len(rejected) == 0


def test_software_engineer_family_rejected():
    passed, rejected = filter_jobs([make_job(role_family="Software Engineer")], CONFIG)
    assert len(passed) == 0
    assert len(rejected) == 1


def test_analyst_not_ds_role_type_rejected():
    passed, rejected = filter_jobs([make_job(role_type="ANALYST_NOT_DS")], CONFIG)
    assert len(passed) == 0


def test_short_description_rejected():
    passed, rejected = filter_jobs([make_job(description_raw="too short")], CONFIG)
    assert len(passed) == 0


def test_stale_job_rejected():
    passed, rejected = filter_jobs([make_job(job_age_days=500)], CONFIG)
    assert len(passed) == 0


def test_wrong_country_rejected():
    passed, rejected = filter_jobs([make_job(country="United States")], CONFIG)
    assert len(passed) == 0


def test_remote_allowed_regardless_of_country():
    passed, rejected = filter_jobs([make_job(country="United States", remote_type="Remote")], CONFIG)
    assert len(passed) == 1


def test_senior_title_not_auto_rejected():
    passed, rejected = filter_jobs([make_job(job_title="Senior Data Scientist", role_family="Senior Data Scientist")], CONFIG)
    assert len(passed) == 1


# --- HC-1: substantive DS ---

def test_hc1_analytics_heavy_not_in_reject_set():
    assert "DS_TITLE_BUT_ANALYTICS_HEAVY" not in HC1_REJECT_ROLE_TYPES
    assert HC1_REJECT_ROLE_TYPES == {"ANALYST_NOT_DS", "SOFTWARE_ENGINEER", "NO_DS_SIGNAL"}


def test_hc1_analytics_heavy_ds_title_eligible():
    # HC-1 revision: a DS-titled job whose scope reads analytics-heavy is
    # eligible; job_quality (not a hard filter) scores it low.
    ok, reasons, job = evaluate(make_job(role_type="DS_TITLE_BUT_ANALYTICS_HEAVY"), CONFIG)
    assert ok is True
    assert not any("HC-1" in r for r in reasons)
    assert job.hc_substantive_ds_status == "PASS"
    assert job.role_type == "DS_TITLE_BUT_ANALYTICS_HEAVY"   # classification preserved


@pytest.mark.parametrize("role_type", ["ANALYST_NOT_DS", "SOFTWARE_ENGINEER", "NO_DS_SIGNAL"])
def test_hc1_non_ds_role_types_still_excluded(role_type):
    ok, reasons, job = evaluate(make_job(role_type=role_type), CONFIG)
    assert ok is False
    assert job.hc_substantive_ds_status == "FAIL"
    assert any(r.startswith("HC-1") for r in reasons)


def test_hc1_genuine_ds_eligible():
    passed, rejected = filter_jobs([make_job(role_type="TRUE_DS")], CONFIG)
    assert len(passed) == 1
    assert passed[0].hc_substantive_ds_status == "PASS"


def test_hc1_ambiguous_role_type_not_excluded():
    # Insufficient evidence of substantive DS is not the same as confirmed
    # evidence of non-substantive DS — UNKNOWN must not be treated as FAIL,
    # applied consistently with HC-2's principle.
    passed, rejected = filter_jobs([make_job(role_type="DS_TITLE_UNCLEAR")], CONFIG)
    assert len(passed) == 1


# --- HC-2: work authorization ---
# DESC is long enough to pass the description-length filter on its own.
DESC = "Build and deploy machine learning models for our analytics platform. " * 3


def test_hc2_missing_authorization_info_is_eligible():
    passed, rejected = filter_jobs([make_job()], CONFIG)
    assert len(passed) == 1
    assert passed[0].work_authorization_status == "UNKNOWN"
    assert passed[0].hc_work_authorization_status == "UNKNOWN"


def test_hc2_explicit_compatible_authorization_eligible():
    passed, rejected = filter_jobs([make_job(description_raw=DESC + "Open to all nationalities.")], CONFIG)
    assert len(passed) == 1
    assert passed[0].work_authorization_status == "PASS"
    assert passed[0].hc_work_authorization_status == "PASS"


def test_hc2_explicit_exclusion_rejected_with_structured_reason():
    job = make_job(description_raw=DESC, requirements_raw="Eligibility: Malaysian citizens only.")
    ok, reasons, job = evaluate(job, CONFIG)
    assert ok is False
    assert job.hc_work_authorization_status == "FAIL"
    assert job.work_authorization_restriction_type == "NATIONALITY_SPECIFIC"
    hc2 = [r for r in reasons if r.startswith("HC-2")]
    assert len(hc2) == 1
    assert "NATIONALITY_SPECIFIC" in hc2[0]
    assert "'Malaysian citizens only'" in hc2[0]
    assert "requirements_raw" in hc2[0]
    assert "Eligibility: Malaysian citizens only." in hc2[0]     # context
    ev = job.work_authorization_evidence[0]
    assert (ev.matched_text, ev.source_field, ev.restriction_type) == ("Malaysian citizens only", "requirements_raw", "NATIONALITY_SPECIFIC")


@pytest.mark.parametrize("text", ["No visa sponsorship.", "Local candidates preferred.", "SC/PR only.",
                                  "Malaysian citizens only. Non-Malaysians with valid work permits may apply."])
def test_hc2_non_exclusion_language_stays_eligible(text):
    ok, reasons, job = evaluate(make_job(description_raw=DESC, requirements_raw=text), CONFIG)
    assert ok is True
    assert job.hc_work_authorization_status == "UNKNOWN"
    assert job.work_authorization_evidence            # evidence kept for the candidate's own check
    assert not any(r.startswith("HC-2") for r in reasons)


def test_hc2_status_is_derived_from_classifier_not_recomputed():
    # hc_work_authorization_status must equal the classifier's status for
    # every outcome — one source of truth.
    for text, expected in [("", "UNKNOWN"), ("Open to all nationalities.", "PASS"), ("No foreigners.", "FAIL")]:
        _, _, job = evaluate(make_job(description_raw=DESC, requirements_raw=text), CONFIG)
        assert job.work_authorization_status == job.hc_work_authorization_status == expected


def test_both_hard_constraints_can_fail_together():
    ok, reasons, job = evaluate(make_job(role_type="NO_DS_SIGNAL", requirements_raw="Malaysian citizen only"), CONFIG)
    assert ok is False
    assert (job.hc_substantive_ds_status, job.hc_work_authorization_status) == ("FAIL", "FAIL")
    assert any(r.startswith("HC-1") for r in reasons) and any(r.startswith("HC-2") for r in reasons)
