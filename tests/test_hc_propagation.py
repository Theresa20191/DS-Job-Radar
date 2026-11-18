"""Regression for the HC data-flow bug: hc_* statuses were declared only on
ScoredJob, so values hard_filters set on a ProcessedJob (via model_copy)
were not declared fields, were dropped by model_dump(), and ScoredJob fell
back to its defaults (hc_work_authorization_status always "UNKNOWN").
Every hard-filter result must now survive ProcessedJob -> ScoredJob
unchanged."""

import pytest

from src.config import load_profile, load_scoring
from src.models.processed_job import ProcessedJob
from src.models.scored_job import ScoredJob
from src.models.work_authorization_evidence import WorkAuthorizationEvidence
from src.pipeline.hard_filters import evaluate
from src.scoring.overall import score_jobs

HC_FIELDS = (
    "hc_substantive_ds_status", "hc_work_authorization_status", "work_authorization_status",
    "work_authorization_reason", "work_authorization_restriction_type", "work_authorization_evidence",
)

EVIDENCE = [WorkAuthorizationEvidence(
    matched_text="Malaysian citizens only", restriction_type="NATIONALITY_SPECIFIC",
    source_field="requirements_raw", context="Eligibility: Malaysian citizens only. Strong Python.",
)]


def processed(**overrides) -> ProcessedJob:
    base = dict(job_id="job_1", job_title="Data Scientist", role_family="Data Scientist", role_type="TRUE_DS",
                country="Malaysia", description_raw="Build machine learning models. " * 10)
    base.update(overrides)
    return ProcessedJob(**base)


def test_hc_fields_are_declared_on_processed_job():
    # The root cause: these must be real ProcessedJob fields, or model_dump drops them.
    for field in HC_FIELDS:
        assert field in ProcessedJob.model_fields


def test_model_copy_values_survive_model_dump():
    job = processed().model_copy(update={"hc_work_authorization_status": "FAIL"})
    assert job.model_dump()["hc_work_authorization_status"] == "FAIL"


@pytest.mark.parametrize("status, restriction_type, evidence", [
    ("FAIL", "NATIONALITY_SPECIFIC", EVIDENCE),
    ("PASS", "NONE", [WorkAuthorizationEvidence(matched_text="Open to all nationalities", restriction_type="NONE",
                                                source_field="description_raw", context="Open to all nationalities.")]),
    ("UNKNOWN", "UNKNOWN", []),
])
def test_processed_to_scored_preserves_every_hc_field(status, restriction_type, evidence):
    job = processed(
        hc_substantive_ds_status="PASS",
        hc_work_authorization_status=status,
        work_authorization_status=status,
        work_authorization_reason=f"reason for {status}",
        work_authorization_restriction_type=restriction_type,
        work_authorization_evidence=evidence,
    )
    # The real conversion path used by scoring/overall.py.
    scored = score_jobs([job], load_profile(), load_scoring())[0]
    assert isinstance(scored, ScoredJob)
    for field in HC_FIELDS:
        assert getattr(scored, field) == getattr(job, field), field


def test_hard_filter_result_survives_into_scored_job():
    # End of the lifecycle for eligible jobs: evaluate() -> ProcessedJob -> ScoredJob.
    cfg = load_scoring()
    for text, expected in [("Open to all nationalities.", "PASS"), ("No visa sponsorship.", "UNKNOWN")]:
        ok, _, job = evaluate(processed(requirements_raw=text), cfg)
        assert ok
        scored = score_jobs([job], load_profile(), cfg)[0]
        assert scored.hc_work_authorization_status == job.hc_work_authorization_status == expected
        assert scored.work_authorization_evidence == job.work_authorization_evidence
        assert scored.work_authorization_evidence   # both texts carry evidence


def test_scored_fail_state_is_not_reset():
    # FAIL jobs are never scored in the pipeline, but the model conversion
    # itself must not turn FAIL into UNKNOWN.
    _, _, job = evaluate(processed(requirements_raw="Malaysian citizens only."), load_scoring())
    assert job.hc_work_authorization_status == "FAIL"
    scored = ScoredJob(**job.model_dump())
    assert scored.hc_work_authorization_status == "FAIL"
    assert scored.work_authorization_restriction_type == "NATIONALITY_SPECIFIC"
    assert scored.work_authorization_evidence == job.work_authorization_evidence
    assert scored.work_authorization_evidence[0].matched_text == "Malaysian citizens only"
