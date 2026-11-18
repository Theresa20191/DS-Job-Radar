"""technical_alignment (frozen D5/D5-A, J-2, J-3a, J-5)."""

from src.models.processed_job import ProcessedJob
from src.models.skill_evidence import SkillEvidence
from src.scoring import technical_alignment
from src.scoring.technical_alignment import UMBRELLA_TECHNIQUES

PROFILE = {
    "skills": {"strong": ["Machine Learning", "Predictive Modeling", "Time Series"], "moderate": ["Deep Learning", "LSTM"]},
    "techniques": {"strong": ["Machine Learning", "Predictive Modeling", "Time Series"], "moderate": ["Deep Learning", "LSTM"]},
}


def ev(name, importance="required", kind="technique"):
    return SkillEvidence(name=name, kind=kind, importance=importance, importance_reason="test", source_field="requirements_raw")


def result(*evidence):
    return technical_alignment.score(ProcessedJob(job_id="j", skill_evidence=list(evidence)), PROFILE)


def test_umbrella_set_frozen():
    assert UMBRELLA_TECHNIQUES == {"Machine Learning", "Deep Learning"}


def test_umbrella_only_is_limited():
    assert result(ev("Machine Learning")).band == "LIMITED"
    assert result(ev("Deep Learning")).band == "LIMITED"


def test_specific_strong_is_substantive():
    assert result(ev("Predictive Modeling")).band == "SUBSTANTIVE"
    assert result(ev("Time Series"), ev("Machine Learning")).band == "SUBSTANTIVE"


def test_specific_moderate_is_limited_j5():
    assert result(ev("LSTM")).band == "LIMITED"


def test_count_does_not_raise_band_and_core_unreachable():
    r = result(ev("Predictive Modeling"), ev("Time Series"), ev("Machine Learning"), ev("LSTM"))
    assert r.band == "SUBSTANTIVE"


def test_tools_only_is_unknown():
    r = result(ev("Python", kind="tool"), ev("SQL", kind="tool"))
    assert (r.known, r.band, r.score, r.unknown_reason) == (False, None, None, "vacuous")


def test_preferred_only_is_unknown():
    r = result(ev("Machine Learning", importance="preferred"))
    assert r.known is False and r.unknown_reason == "vacuous"


def test_unevidenced_required_is_unknown_with_d5_gap():
    r = result(ev("Statistics"))
    assert (r.known, r.score, r.unknown_reason) == (False, None, "unevidenced_required")
    assert r.gaps == ["No evidence in your profile for Statistics (required by this job)"]


def test_gaps_surface_even_when_known_and_are_not_penalties():
    with_gap = result(ev("Predictive Modeling"), ev("Statistics"), ev("NLP"))
    without_gap = result(ev("Predictive Modeling"))
    assert with_gap.band == without_gap.band == "SUBSTANTIVE"
    assert with_gap.gaps == ["No evidence in your profile for NLP, Statistics (required by this job)"]
    assert not any("lack" in g.lower() for g in with_gap.gaps)


def test_provenance_lists_evidenced_required_techniques():
    r = result(ev("Predictive Modeling"), ev("Machine Learning"))
    assert {p["technique"] for p in r.provenance} == {"Predictive Modeling", "Machine Learning"}
    assert all(p["candidate_level"] == "strong" for p in r.provenance)
