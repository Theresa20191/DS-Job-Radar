"""Career Coach Gate 1: the V1.2 schema and config are frozen, the profile's
`career_coach:` declarations are valid and exactly as approved, and the new
code is independent of the legacy layers."""

import ast
import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.career_coach.setup import parse_config, parse_declarations, validate_setup
from src.config import load_career_coach, load_profile
from src.models import career_coach as m
from src.models.processed_job import ProcessedJob

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "fixtures" / "career_coach_schema_v1_2.json"
SNAPSHOT_MODELS = (m.CareerCoachDecision, m.CoachInput, m.JobProjection, m.HardFilterEvaluation,
                   m.LiveCheckRecord, m.CoachDeclarations, m.CoachConfig)

FORBIDDEN_MODULES = ("src.scoring", "src.transition", "src.reporting", "src.strategy",
                     "src.models.ptf", "src.models.transition", "src.models.scored_job", "src.models.strategy",
                     "src.pipeline.orchestrator", "src.pipeline.quality_report")
FORBIDDEN_FIELDS = {"overall_score", "base_score", "tier", "tier_floor", "tier_current", "tier_ceiling",
                    "recommended_action", "opportunity_type", "opportunity_types", "personal_transition_fit",
                    "decision_stability", "transition_lens", "job_quality_score", "job_risk_penalty",
                    "data_completeness_pct", "role_type_confidence", "technical_alignment", "domain_alignment"}


def _setup():
    profile = load_profile()
    return parse_config(load_career_coach()), parse_declarations(profile), profile


def _problems_with(mutate) -> list[str]:
    profile = copy.deepcopy(load_profile())
    mutate(profile["career_coach"])
    return validate_setup(parse_config(load_career_coach()), parse_declarations(profile), profile)


# ------------------------------------------------------------ frozen schema

def test_schema_snapshot_is_frozen():
    """Any schema change needs a new coach_version and a new snapshot."""
    current = {"coach_version": m.COACH_VERSION,
               "schemas": {cls.__name__: cls.model_json_schema() for cls in SNAPSHOT_MODELS}}
    assert current == json.loads(SNAPSHOT.read_text())


def test_config_version_matches_schema_version():
    assert load_career_coach()["version"] == m.COACH_VERSION == "career_coach_v1.2"


# ------------------------------------------------------ real setup is valid

def test_real_config_and_declarations_are_valid():
    config, declarations, profile = _setup()
    assert validate_setup(config, declarations, profile) == []


def test_declarations_copied_from_legacy_sections():
    """M2: copied, not moved — the old sections are still present and equal."""
    profile = load_profile()
    coach, old = profile["career_coach"], profile["transition"]
    assert coach["target_tracks"] == old["target_tracks"]
    assert coach["work_authorization"] == old["work_authorization"]
    my = coach["compensation_floor"]["Malaysia"]
    assert {k: my[k] for k in ("currency", "monthly_amount")} == old["compensation_floor"]["Malaysia"]
    assert my["hard"] is profile["application_strategy"]["hard_constraints"]["compensation_floor"]["Malaysia"]
    assert coach["compensation_floor"]["Singapore"] is None and old["compensation_floor"]["Singapore"] is None
    assert coach["experience"]["current_role_years"] == profile["years_experience_current_role"]


def test_approved_decisions_are_encoded_exactly():
    config, d, _ = _setup()
    # O-2
    assert (d.experience.relevant_ds_years, d.experience.relevant_ml_years) == (1, 2)
    assert d.experience.relevant_domain_years == {}
    assert config.thresholds.k_stretch_years == 2
    # O-11
    assert config.thresholds.live_check_window_days == 7
    # O-12
    assert (d.seniority.aligned_levels, d.seniority.stretch_levels) == (["Junior"], ["Senior", "Lead"])
    assert config.level_rungs == {"Intern": 0, "Junior": 1, "Senior": 3, "Lead": 4, "Staff": 4,
                                  "Manager": 4, "Principal": 5}
    # O-1
    assert {k: v.tracks for k, v in config.positionings.items()} == {
        "FRAUD_FINCRIME": ["FRAUD_FINCRIME_DS"],
        "RISK": ["RISK_DS", "CREDIT_RISK_DS"],
        "EXPERIMENTATION_PRICING": ["EXPERIMENTATION_CAUSAL", "PRICING_FORECASTING", "PRODUCT_DS", "DECISION_SCIENTIST"],
        "ML_ORIENTED": ["APPLIED_SCIENTIST", "ML_SCIENTIST", "ML_ENGINEER"],
        "APPLIED_DS": ["APPLIED_DS"],
    }
    assert d.resume_variants_ready is None           # readiness not declared yet
    # RT-2 specificity classes
    specificity = {t: s.specificity for t, s in config.tracks.items() if s.specificity}
    assert specificity == {
        "FRAUD_FINCRIME_DS": "DOMAIN", "RISK_DS": "DOMAIN", "CREDIT_RISK_DS": "DOMAIN",
        "PRODUCT_DS": "FUNCTION", "DECISION_SCIENTIST": "FUNCTION", "EXPERIMENTATION_CAUSAL": "FUNCTION",
        "PRICING_FORECASTING": "FUNCTION",
        "APPLIED_DS": "GENERAL", "APPLIED_SCIENTIST": "GENERAL", "ML_SCIENTIST": "GENERAL", "ML_ENGINEER": "GENERAL",
    }
    assert {t for t, s in config.tracks.items() if s.defining} == {"MLOPS_PLATFORM", "MIS_REPORTING", "ANALYTICS_HEAVY"}


def test_applied_ds_requires_central_modelling_not_presence():
    config, _, _ = _setup()
    match = config.tracks["APPLIED_DS"].match
    assert match.central_work_in == ["MODELLING"] and match.activity_present is None


# ------------------------------------------------------------- CV-0 / SN-0

def test_cv0_undeclared_track_is_rejected():
    problems = _problems_with(lambda c: c["target_tracks"]["primary"].remove("APPLIED_DS"))
    assert any("CV-0" in p and "APPLIED_DS" in p for p in problems)


def test_cv0_track_in_two_tiers_is_rejected():
    problems = _problems_with(lambda c: c["target_tracks"]["secondary"].append("RISK_DS"))
    assert any("CV-0" in p and "more than one tier" in p for p in problems)


def test_cv0_unknown_declared_track_is_rejected():
    problems = _problems_with(lambda c: c["target_tracks"]["adjacent_ok"].append("QUANT_RESEARCHER"))
    assert any("CV-0" in p and "QUANT_RESEARCHER" in p for p in problems)


@pytest.mark.parametrize("seniority", [
    {"aligned_levels": ["Mid/Unspecified"], "stretch_levels": []},      # not declarable
    {"aligned_levels": ["Junior"], "stretch_levels": ["Junior"]},        # overlap
    {"aligned_levels": ["Junior", "Junior"], "stretch_levels": []},      # duplicate
    {"aligned_levels": ["Director"], "stretch_levels": []},              # not a classifier level
    {"aligned_levels": ["Junior"]},                                      # stretch_levels omitted is fine...
])
def test_sn0_seniority_validation(seniority):
    profile = copy.deepcopy(load_profile())
    profile["career_coach"]["seniority"] = seniority
    if seniority == {"aligned_levels": ["Junior"]}:
        assert parse_declarations(profile).seniority.stretch_levels == []    # ...and means none
        return
    with pytest.raises(ValidationError):
        parse_declarations(profile)


@pytest.mark.parametrize("experience", [
    {"current_role_years": 1, "relevant_ds_years": 0},                                          # missing key
    {"current_role_years": 1, "relevant_ds_years": -1, "relevant_ml_years": 0},                 # negative
    {"current_role_years": 1, "relevant_ds_years": "unknown", "relevant_ml_years": 0},          # not UNKNOWN
    {"current_role_years": 1, "relevant_ds_years": 0, "relevant_ml_years": 0, "total_years": 3},  # extra key
])
def test_experience_declaration_validation(experience):
    profile = copy.deepcopy(load_profile())
    profile["career_coach"]["experience"] = experience
    with pytest.raises(ValidationError):
        parse_declarations(profile)


def test_unknown_years_stay_unknown():
    profile = copy.deepcopy(load_profile())
    profile["career_coach"]["experience"]["relevant_ml_years"] = "UNKNOWN"
    assert parse_declarations(profile).experience.relevant_ml_years == "UNKNOWN"


def test_missing_career_coach_section_is_an_error_not_a_default():
    profile = copy.deepcopy(load_profile())
    del profile["career_coach"]
    with pytest.raises(ValueError, match="career_coach"):
        parse_declarations(profile)


def test_resume_variant_must_name_a_positioning():
    problems = _problems_with(lambda c: c.__setitem__("resume_variants_ready", ["QUANT"]))
    assert any("O-1" in p for p in problems)


# ------------------------------------------------ classifier level contract

def test_classifier_levels_match_role_classifier():
    """SN-1: the level set is exactly what classify_role_level can return."""
    tree = ast.parse((ROOT / "src" / "enrichment" / "role_classifier.py").read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "classify_role_level")
    returned = {n.value.value for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)}
    assert returned == set(m.CLASSIFIER_LEVELS)
    assert set(m.DECLARABLE_LEVELS) == returned - {"Mid/Unspecified"}


# --------------------------------------------------- input boundary (IN-*)

def test_projection_whitelist_is_a_subset_of_processed_job():
    assert set(m.JobProjection.model_fields) <= set(ProcessedJob.model_fields)
    assert not set(m.JobProjection.model_fields) & FORBIDDEN_FIELDS
    assert not set(m.HardFilterEvaluation.model_fields) & FORBIDDEN_FIELDS


def _processed(**overrides) -> ProcessedJob:
    return ProcessedJob(job_id="job_x", company="Acme", job_title="Data Scientist", country="Singapore",
                        role_level="Junior", **overrides)


def test_projection_from_exact_processed_job():
    projected = m.JobProjection.from_processed(_processed())
    assert projected.job_id == "job_x" and projected.role_level == "Junior"


def test_projection_refuses_any_processed_job_subclass():
    class CarriesLegacyFields(ProcessedJob):
        overall_score: float = 99.0

    job = CarriesLegacyFields(job_id="job_x", company="Acme", job_title="DS", country="Singapore")
    with pytest.raises(TypeError, match="IN-3"):
        m.JobProjection.from_processed(job)


def test_projection_is_read_only():
    projected = m.JobProjection.from_processed(_processed())
    with pytest.raises(ValidationError):
        projected.job_title = "changed"


def test_hard_filter_evaluation_passed_iff_no_reasons():
    m.HardFilterEvaluation(passed=True, reasons=[], hc_substantive_ds_status="PASS", hc_work_authorization_status="UNKNOWN")
    m.HardFilterEvaluation(passed=False, reasons=["x"], hc_substantive_ds_status="FAIL", hc_work_authorization_status="UNKNOWN")
    for passed, reasons in ((True, ["x"]), (False, [])):
        with pytest.raises(ValidationError):
            m.HardFilterEvaluation(passed=passed, reasons=reasons, hc_substantive_ds_status="PASS",
                                   hc_work_authorization_status="UNKNOWN")


def test_live_check_record_is_strict():
    record = {"record_id": "lc1", "job_id": "job_x", "source_job_id": "abc", "checked_on": "2026-09-25",
              "method": "MCF public API + page", "page_reviewable": "YES", "email_in_jd": "NO", "posting_status": "CLOSED"}
    assert m.LiveCheckRecord.model_validate(record).checked_on.isoformat() == "2026-09-25"
    with pytest.raises(ValidationError):
        m.LiveCheckRecord.model_validate({**record, "email_in_jd": "maybe"})
    with pytest.raises(ValidationError):
        m.LiveCheckRecord.model_validate({**record, "note": "extra"})


# ------------------------------------------------------------ independence

def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_career_coach_never_imports_legacy_layers():
    files = [ROOT / "src" / "models" / "career_coach.py", *sorted((ROOT / "src" / "career_coach").glob("*.py"))]
    for path in files:
        bad = {i for i in _imports(path) if i.startswith(FORBIDDEN_MODULES)}
        assert not bad, f"{path.name} imports {sorted(bad)}"


# --------------------------------------------------- output invariants (N)

def _prov():
    return {name: {"rules": ["TEST"]} for name in m.PROVENANCE_FIELDS}


def _run():
    return {"as_of_date": "2026-09-20", "run_kind": "HISTORICAL_REPLAY", "coach_version": m.COACH_VERSION,
            "input_hashes": {"raw_corpus": "a", "config": "b", "profile": "c", "eligible_live_checks": "d"}}


def _job():
    return {"job_id": "job_x", "company": "Acme", "job_title": "Data Scientist", "country": "Singapore",
            "canonical_url": "https://example.test/job", "posted_date": "2026-08-20"}


def _full(**overrides) -> dict:
    decision = {
        "job": _job(), "run": _run(), "evaluation_scope": "FULL",
        "application_viability": {"state": "VIABLE", "factors": []},
        "blocking_reason": None,
        "work_substance": {"status": "ESTABLISHED", "basis": "DESCRIPTION_SENTENCES", "central_work": "MODELLING",
                           "activities": {"MODELLING": 3}, "title_family": "Data Scientist"},
        "career_value": {"state": "PRIMARY", "deciding_rule": "CV-3", "deciding_track": "RISK_DS",
                         "matched_tracks": [{"track": "RISK_DS", "declared_tier": "primary", "specificity": "DOMAIN",
                                             "defining": False, "clause": "c", "evidence_count": 2}],
                         "explanation": "e"},
        "resume_track": {"positioning": "RISK", "source_track": "RISK_DS", "declared_tier": "primary",
                         "runner_up": None, "deciding_key": "RT-1"},
        "candidate_alignment": {
            "technical": {"state": "PARTIAL"}, "domain": {"state": "MATCH"},
            "experience": {"state": "NOT_STATED_BY_JOB", "job_required_years": None, "job_evidence_id": None,
                           "candidate_measure": "relevant_ds_years", "candidate_value": 0},
            "seniority": {"state": "UNKNOWN", "job_level": "Mid/Unspecified", "direction": None},
            "education": {"state": "NOT_STATED"}},
        "readiness": {"state": "NO_GAP", "deciding_rule": "R-6"},
        "practical_constraint": {"state": "CLEAR"},
        "resume_effort": "TARGETED", "research_effort": "LOW",
        "application_priority": "HIGH",
        "application_investment": {"justified": "FULL", "required": "MODERATE", "fit": "WITHIN_BUDGET"},
        "next_action": "TAILOR_RESUME",
        "primary_reason": {"rule_id": "PR-9", "text": "t"},
        "uncertainty_state": "RESOLVED",
        "provenance": _prov(),
    }
    decision.update(overrides)
    return decision


def _rejected(**overrides) -> dict:
    decision = {
        "job": _job(), "run": _run(), "evaluation_scope": "HARD_FILTER_REJECTED",
        "application_viability": {"state": "BLOCKED", "factors": [
            {"rule_id": "V-1", "factor": "hard_filter", "result": "FAIL", "detail": "HC-1 FAILED"}]},
        "blocking_reason": {"rule_id": "V-1", "text": "HC-1 FAILED"},
        **{name: None for name in m.LAYER_FIELDS},
        "application_priority": "DO_NOT_INVEST",
        "application_investment": {"justified": "NONE", "required": None, "fit": "NOT_APPLICABLE"},
        "next_action": "DO_NOT_INVEST",
        "primary_reason": {"rule_id": "PR-1", "text": "t"},
        "uncertainty_state": "RESOLVED",
        "provenance": _prov(),
    }
    decision.update(overrides)
    return decision


def test_valid_full_and_rejected_decisions():
    m.CareerCoachDecision.model_validate(_full())
    m.CareerCoachDecision.model_validate(_rejected())


def _cv(state, rule, track, tier):
    return {"state": state, "deciding_rule": rule, "deciding_track": track, "explanation": "e",
            "matched_tracks": [{"track": track, "declared_tier": tier, "specificity": "FUNCTION",
                                "defining": False, "clause": "c", "evidence_count": 1}] if track else []}


@pytest.mark.parametrize("overrides, rule", [
    # PR-11: SECONDARY never reaches HIGH
    ({"career_value": _cv("SECONDARY", "CV-4", "PRICING_FORECASTING", "secondary"),
      "resume_track": {"positioning": "EXPERIMENTATION_PRICING", "source_track": "PRICING_FORECASTING",
                       "declared_tier": "secondary", "runner_up": None, "deciding_key": "RT-1"}}, "PR-11"),
    # PR-7: ADJACENT never above SELECTIVE
    ({"career_value": _cv("ADJACENT", "CV-5", "ML_ENGINEER", "adjacent_ok"),
      "resume_track": {"positioning": "ML_ORIENTED", "source_track": "ML_ENGINEER",
                       "declared_tier": "adjacent_ok", "runner_up": None, "deciding_key": "RT-1"},
      "application_priority": "PRIORITY", "application_investment":
          {"justified": "STANDARD", "required": "MODERATE", "fit": "WITHIN_BUDGET"},
      "primary_reason": {"rule_id": "PR-10", "text": "t"}}, "PR-7"),
    # PR-1: BLOCKED is DO_NOT_INVEST
    ({"application_viability": {"state": "BLOCKED", "factors": []},
      "blocking_reason": {"rule_id": "V-4a", "text": "no email"}}, "PR-1"),
    # I-1: justified investment follows priority
    ({"application_investment": {"justified": "STANDARD", "required": "MODERATE", "fit": "WITHIN_BUDGET"}}, "I-1"),
    # NA-10: VERIFY_FIRST needs a VERIFY_* action
    ({"application_priority": "VERIFY_FIRST", "application_investment":
        {"justified": "VERIFY_ONLY", "required": "MODERATE", "fit": "NOT_APPLICABLE"},
      "primary_reason": {"rule_id": "PR-8", "text": "t"}}, "NA-10"),
    # NA-5: HOLD exactly when effort exceeds the budget
    ({"next_action": "HOLD"}, "NA-5"),
    # RT-5: resume track must come from the deciding track
    ({"resume_track": {"positioning": "APPLIED_DS", "source_track": "APPLIED_DS", "declared_tier": "primary",
                       "runner_up": None, "deciding_key": "RT-2"}}, "RT-5"),
    # primary_reason must be a PR rule
    ({"primary_reason": {"rule_id": "CV-3", "text": "t"}}, "primary_reason"),
])
def test_full_decision_invariants(overrides, rule):
    with pytest.raises(ValidationError, match=rule):
        m.CareerCoachDecision.model_validate(_full(**overrides))


def test_missing_provenance_is_rejected():
    prov = _prov()
    del prov["readiness"]
    with pytest.raises(ValidationError, match="provenance missing"):
        m.CareerCoachDecision.model_validate(_full(provenance=prov))


def test_rejected_job_layers_are_not_evaluated():
    with pytest.raises(ValidationError, match="IN-4"):
        m.CareerCoachDecision.model_validate(_rejected(readiness={"state": "NO_GAP", "deciding_rule": "R-6"}))
    with pytest.raises(ValidationError, match="IN-4"):
        m.CareerCoachDecision.model_validate(_rejected(blocking_reason={"rule_id": "V-4a", "text": "x"}))


def test_full_scope_requires_every_layer():
    with pytest.raises(ValidationError, match="FULL scope"):
        m.CareerCoachDecision.model_validate(_full(readiness=None))
