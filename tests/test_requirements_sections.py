from src.enrichment.requirements_sections import analyze, classify_heading
from src.enrichment.skill_evidence import build_evidence
from src.enrichment.skill_extractor import extract_required_and_preferred


def by_name(evidence):
    return {e.name: e for e in evidence}


# --- heading recognition --------------------------------------------------

def test_recognises_common_headings():
    assert classify_heading("Requirements").kind == "required"
    assert classify_heading("Minimum Qualifications:").kind == "required"
    assert classify_heading("What we're looking for").kind == "required"
    assert classify_heading("About you").kind == "required"
    assert classify_heading("Nice to have").kind == "preferred"
    assert classify_heading("Preferred Qualifications").kind == "preferred"
    assert classify_heading("Key Responsibilities").kind == "other"
    assert classify_heading("Responsibilities & Requirements").kind == "mixed"


def test_travel_requirements_is_not_a_requirements_heading():
    assert classify_heading("Travel requirements").kind == "other"


def test_sentences_and_quantities_are_not_headings():
    assert classify_heading("3 years of relevant experience") is None
    assert classify_heading("Experience with agile/scrum methodologies is a plus.") is None
    assert classify_heading("We are looking for a talented") is None
    assert classify_heading("Business requirements: translate needs into models") is None


def test_inline_heading_with_content():
    h = classify_heading("Requirements: Python and SQL")
    assert h.kind == "required" and h.remainder == "Python and SQL"


# --- required / preferred / unknown --------------------------------------

def test_explicit_requirements_section():
    desc = "About the role\nWe build models.\nRequirements\nStrong Python and SQL.\nBenefits\nFree lunch."
    evidence, analysis = build_evidence(desc, "")
    ev = by_name(evidence)
    assert analysis.status == "DETECTED" and analysis.source == "description_heading"
    assert ev["Python"].importance == "required"
    assert ev["Python"].section_heading == "Requirements"
    assert "Requirements" in ev["Python"].importance_reason


def test_what_were_looking_for_section():
    desc = "What we're looking for\n- Hands-on scikit-learn and XGBoost.\nWhat we offer\nGreat team."
    ev = by_name(build_evidence(desc, "")[0])
    assert ev["Scikit-learn"].importance == "required"
    assert ev["XGBoost"].importance == "required"


def test_nice_to_have_section_is_preferred():
    desc = "Requirements\nPython.\nNice to have\nSpark and Airflow."
    ev = by_name(build_evidence(desc, "")[0])
    assert ev["Python"].importance == "required"
    assert ev["Spark"].importance == "preferred"
    assert ev["Airflow"].importance == "preferred"


def test_mixed_required_and_preferred_sections():
    desc = ("Minimum Qualifications\n- Python\n- SQL\nPreferred Qualifications\n- Docker\n- Kubernetes\n"
            "About Us\nWe use Tableau.")
    ev = by_name(build_evidence(desc, "")[0])
    assert (ev["Python"].importance, ev["SQL"].importance) == ("required", "required")
    assert (ev["Docker"].importance, ev["Kubernetes"].importance) == ("preferred", "preferred")
    assert ev["Tableau"].importance == "unknown"      # under a non-requirement section


def test_sentence_cue_overrides_zone():
    desc = "Requirements\nPython required.\nExperience with Spark is a plus."
    ev = by_name(build_evidence(desc, "")[0])
    assert ev["Python"].importance == "required"
    assert ev["Spark"].importance == "preferred"
    assert "plus" in ev["Spark"].importance_reason


def test_no_detectable_section_is_unknown():
    desc = "We are a fast growing team. You will use Python and SQL every day to build models."
    evidence, analysis = build_evidence(desc, "")
    assert analysis.status == "UNKNOWN" and analysis.source == "none"
    assert analysis.requirements_section_raw == ""
    assert all(e.importance == "unknown" for e in evidence)
    assert {"Python", "SQL"} <= {e.name for e in evidence}


def test_description_only_mention_is_not_automatically_preferred():
    desc = "Our stack is Python and Spark. Responsibilities\nBuild pipelines in Python."
    ev = by_name(build_evidence(desc, "")[0])
    assert ev["Python"].importance == "unknown"
    assert ev["Spark"].importance == "unknown"


def test_legacy_lists_still_conflate_unknown_with_preferred():
    # Documents the LEGACY lists' behaviour (no longer read by scoring): description-
    # only mentions land in preferred_skills. The evidence layer says unknown.
    desc = "Our stack is Python and Spark."
    required, preferred = extract_required_and_preferred(desc, "")
    assert required == [] and {"Python", "Spark"} <= set(preferred)
    assert all(e.importance == "unknown" for e in build_evidence(desc, "")[0])


def test_responsibilities_mention_is_unknown_not_required():
    desc = "Responsibilities\nBuild forecasting models in Python.\nRequirements\nSQL."
    ev = by_name(build_evidence(desc, "")[0])
    assert ev["Python"].importance == "unknown"
    assert ev["Forecasting"].importance == "unknown"
    assert ev["SQL"].importance == "required"


def test_mixed_heading_is_unknown():
    desc = "Responsibilities & Requirements\nWork with Python and SQL."
    ev = by_name(build_evidence(desc, "")[0])
    assert ev["Python"].importance == "unknown"
    assert "mixed" in ev["Python"].importance_reason


def test_required_beats_preferred_across_mentions():
    desc = "Requirements\nPython.\nNice to have\nPython notebooks and Spark."
    ev = by_name(build_evidence(desc, "")[0])
    assert ev["Python"].importance == "required"
    assert "also mentioned" in ev["Python"].importance_reason
    assert ev["Python"].mention_count == 2


def test_source_requirements_field_is_required_zone():
    evidence, analysis = build_evidence("Some description.", "Python and SQL.\nNice to have\nSpark")
    ev = by_name(evidence)
    assert analysis.source == "source_field"
    assert ev["Python"].importance == "required"
    assert ev["Python"].source_field == "requirements_raw"
    assert "source requirements field" in ev["Python"].importance_reason
    assert ev["Spark"].importance == "preferred"


def test_content_line_with_trailing_cue_is_not_swallowed_as_heading():
    # Regression: "Python and SQL. Nice to have" was once classified as a
    # heading (residual-word tolerance), silently dropping Python and SQL.
    evidence, _ = build_evidence("", "Python and SQL. Nice to have")
    ev = by_name(evidence)
    assert "Python" in ev and "SQL" in ev


def test_provenance_fields_present():
    evidence, _ = build_evidence("Requirements\nStrong Python skills.", "")
    e = by_name(evidence)["Python"]
    assert e.matched_text.lower() == "python"
    assert "Strong Python" in e.context
    assert e.source_field == "description_raw"


def test_tools_and_techniques_split_on_job():
    evidence, _ = build_evidence("Requirements\nPython, XGBoost, and classification models.", "")
    kinds = {e.name: e.kind for e in evidence}
    assert kinds["Python"] == "tool" and kinds["XGBoost"] == "tool"
    assert kinds["Classification"] == "technique"


def test_empty_inputs():
    evidence, analysis = analyze("", ""), None
    assert evidence.status == "UNKNOWN"
    assert build_evidence("", "")[0] == []


# --- detector hardening found by real-corpus false-positive checks --------

def test_closing_boilerplate_does_not_leak_into_required_zone():
    # A tool/technique named in a closing culture blurb must not be "required".
    desc = ("Requirements\nStrong Python.\n"
            "SAP is proud to be an equal opportunity workplace.\n"
            "Join us at Portcast and shape the future with predictive analytics!")
    ev = by_name(build_evidence(desc, "")[0])
    assert ev["Python"].importance == "required"
    assert ev["Predictive Modeling"].importance == "unknown"


def test_question_style_headings():
    assert classify_heading("What you will need?").kind == "required"
    assert classify_heading("What will you do?").kind == "other"


def test_what_youll_bring_to_company_heading():
    assert classify_heading("WHAT YOU'LL BRING TO ALCON:").kind == "required"


def test_ideal_experience_heading_and_tech_stack_is_not_a_requirement():
    assert classify_heading("Ideal Experience").kind == "required"
    assert classify_heading("Tech Stack").kind == "other"
    ev = by_name(build_evidence("Tech Stack\nPython\nPyTorch / JAX", "")[0])
    assert ev["Python"].importance == "unknown"


def test_parenthetical_continuation_is_not_a_heading_and_keeps_its_cue():
    assert classify_heading("(is a bonus)") is None
    desc = "Requirements\nPython.\nExperience deploying models into production (cloud or on-prem)\n(is a bonus)\nIf you meet the above requirements, apply."
    ev = by_name(build_evidence(desc, "")[0])
    assert ev["Python"].importance == "required"
    assert ev["Model Deployment"].importance == "preferred"
