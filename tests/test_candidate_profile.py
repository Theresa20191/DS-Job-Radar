from src.candidate import candidate_techniques, validate_profile_evidence
from src.config import load_profile


def test_real_profile_is_internally_consistent():
    assert validate_profile_evidence(load_profile()) == []


def test_only_explicitly_supported_techniques_populated():
    techniques = candidate_techniques(load_profile())
    assert techniques == {
        "Statistics": "strong",
        "A/B Testing": "strong",
        "Regression": "strong",
        "Forecasting": "strong",
        "Classification": "moderate",
        "Causal Inference": "moderate",
    }


def test_unstated_techniques_stay_unknown():
    techniques = candidate_techniques(load_profile())
    for name in ("Machine Learning", "Deep Learning", "Anomaly Detection", "NLP", "Computer Vision",
                 "Time Series", "Recommendation Systems", "Optimisation", "Model Deployment",
                 "Clustering", "Predictive Modeling"):
        assert name not in techniques   # unknown — neither present nor absent


def test_no_technique_inferred_from_degree_or_title():
    profile = {
        "current_role": "Data Analyst",
        "education": ["Bachelor of Statistics (fictional)"],
        "skills": {"strong": ["Python"], "moderate": []},
    }
    assert candidate_techniques(profile) == {}


def test_missing_techniques_section_means_all_unknown():
    assert candidate_techniques({"skills": {"strong": ["Machine Learning"], "moderate": []}}) == {}


def test_validation_flags_inferred_or_misclassified_entries():
    profile = {
        "skills": {"strong": ["Python", "Machine Learning"], "moderate": []},
        "techniques": {"strong": ["Machine Learning", "Classification"], "moderate": ["Python"]},
    }
    problems = " | ".join(validate_profile_evidence(profile))
    assert "'Classification' is not listed under skills" in problems
    assert "'Python' is not a technique" in problems


def test_validation_flags_missing_technique_view():
    profile = {"skills": {"strong": ["Machine Learning"], "moderate": []}, "techniques": {"strong": [], "moderate": []}}
    assert any("missing from the techniques section" in p for p in validate_profile_evidence(profile))
