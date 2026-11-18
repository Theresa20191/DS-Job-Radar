import pytest

from src.models.raw_job import RawJob
from src.pipeline.quality_report import _has_text, source_completeness


def make_raw(job_id, requirements, description="Short desc.", source="mycareersfuture"):
    return RawJob(
        job_id=job_id, source=source, source_job_id=job_id, source_url="u", company="Acme",
        job_title="Data Scientist", job_description=description,
        source_extra={"requirements": requirements, "skills": []},
    )


@pytest.mark.parametrize("value", [None, "", "   ", "None", "none", "null", [], 0, "<p></p>", "<p> </p><br/>"])
def test_has_text_false_for_missing_values(value):
    assert _has_text(value) is False


def test_has_text_true_for_real_text():
    assert _has_text("Python and SQL") is True


def test_none_requirements_is_not_complete():
    # Regression: bool(str(None).strip()) is True, which reported
    # MyCareersFuture requirements as 100% complete when every record had
    # requirements=None.
    report = source_completeness({"mycareersfuture": [make_raw("a", None), make_raw("b", None)]})
    assert report["mycareersfuture"]["requirements_completeness_pct"] == 0.0


def test_empty_html_shell_is_not_complete():
    # Regression: Hiredly stores "<p></p>" for 39 of 65 real records; a plain
    # non-empty-string check reported them as complete.
    report = source_completeness({"hiredly": [make_raw("a", "<p></p>"), make_raw("b", "<p>Python required.</p>")]})
    assert report["hiredly"]["requirements_completeness_pct"] == 50.0


def test_empty_requirements_is_not_complete():
    report = source_completeness({"hiredly": [make_raw("a", ""), make_raw("b", "  ")]})
    assert report["hiredly"]["requirements_completeness_pct"] == 0.0


def test_real_requirements_counted_and_mixed_ratio():
    jobs = [make_raw("a", "Python required."), make_raw("b", None)]
    report = source_completeness({"hiredly": jobs})
    assert report["hiredly"]["requirements_completeness_pct"] == 50.0


def test_recoverable_from_description_reported_separately():
    desc = "<p>About the role</p><p><strong>Requirements</strong></p><ul><li>Python and SQL experience for this role</li></ul>"
    report = source_completeness({"mycareersfuture": [make_raw("a", None, description=desc), make_raw("b", None)]})
    m = report["mycareersfuture"]
    assert m["requirements_completeness_pct"] == 0.0                  # source field still absent
    assert m["requirements_recoverable_from_description_pct"] == 50.0  # one JD has a detectable section
