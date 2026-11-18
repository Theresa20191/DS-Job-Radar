"""Transition Lens V1 — Work Profile, Trajectory, and the D-4 annotation-only
invariants.

Pinned expectations use 39 real postings (tests/fixtures/transition_lens_jobs.json,
extracted from the 2026-09-20 corpus replay) replayed through the real
score_jobs(). Expectations are must-include / must-exclude so rules can be
refined without churn; each one is grounded in the posting's task statements.
"""

# Public copy: tests that need the private real-posting fixture (transition_lens_jobs.json) are not included;
# the tests below do not use it and are unchanged apart from fictional example-profile values.

import ast
import copy
import json
from pathlib import Path

import pytest

from src.config import load_profile, load_scoring, load_tracks
from src.models.processed_job import ProcessedJob
from src.scoring.overall import score_jobs
from src.transition.lens import apply_lens, validate_transition_config
from src.transition.work_profile import build_work_profile, work_sentences

ROOT = Path(__file__).resolve().parent.parent


def profile(**overrides):
    p = copy.deepcopy(load_profile())
    p.update(overrides)
    return p


def labels(job):
    return {l.label for l in job.transition_lens.work_profile.labels}


def tracks(job):
    return {m.track_id for m in job.transition_lens.trajectory.tracks}


# --- pinned real-posting expectations -------------------------------------------------



def evidence(job, label):
    return [e.text for l in job.transition_lens.work_profile.labels if l.label == label for e in l.evidence]


# --- evidence-source behaviour on real postings -----------------------------------------


# --- synthetic rules --------------------------------------------------------------------

def synthetic(title="Data Scientist", description="", requirements="", role_family="Data Scientist"):
    job = ProcessedJob(job_id="s", job_title=title, role_family=role_family, role_type="TRUE_DS",
                       company="Acme", company_normalized="acme", description_raw=description,
                       requirements_raw=requirements, country="Malaysia", location_normalized="Kuala Lumpur")
    return apply_lens(score_jobs([job], profile(), load_scoring()), profile(), load_tracks())[0].transition_lens


def test_reporting_dominant_ds_title_is_analytics_heavy():
    lens = synthetic(description="Responsibilities\nMaintain weekly management reports for the sales team.\n"
                                 "Build dashboards and visualisations in Power BI.")
    assert {m.track_id for m in lens.trajectory.tracks} >= {"ANALYTICS_HEAVY", "MIS_REPORTING"}
    assert lens.trajectory.direction == "NON_TARGET"


def test_product_context_alone_does_not_map_product_ds():
    lens = synthetic(title="Product Data Scientist",
                     description="Responsibilities\nGenerate insights on user behaviour and share recommendations for product decisions.")
    assert "PRODUCT_DS" not in {m.track_id for m in lens.trajectory.tracks}
    assert [g.track_id for g in lens.trajectory.guard_failures] == ["PRODUCT_DS"]


def test_model_operations_and_mentions_are_not_modelling():
    lens = synthetic(description="Responsibilities\nEnsure day-to-day stability and performance of ML models and pipelines.\n"
                                 "Manage model lifecycle controls, including versioning, lineage and governance.\n"
                                 "Participate in code reviews of machine learning solutions.\n"
                                 "Mentor junior analysts on dashboards and statistical models.")
    assert not lens.work_profile.has("APPLIED_MODELLING")
    assert lens.work_profile.has("ML_PLATFORM")
    assert not lens.work_profile.has("REPORTING")        # mentoring topics are not the job's work


def test_foundation_model_work_is_ai_application_not_modelling():
    genai = synthetic(description="Responsibilities\nFine-tune foundation models for document understanding.\n"
                                  "Develop large language models for customer support.")
    assert not genai.work_profile.has("APPLIED_MODELLING") and genai.work_profile.has("AI_APPLICATION_DEV")
    # sentence-based: GenAI elsewhere in the posting does not veto real modelling
    mixed = synthetic(description="Responsibilities\nTrain gradient boosting models for credit default.\n"
                                  "Build RAG chatbots for internal teams.")
    assert mixed.work_profile.has("APPLIED_MODELLING") and mixed.work_profile.has("AI_APPLICATION_DEV")


def test_model_evaluation_is_not_decision_experimentation():
    lens = synthetic(description="Responsibilities\nRun experiments to test the performance of deployed models.\n"
                                 "Design and execute experiments to evaluate existing LLMs.")
    assert not lens.work_profile.has("EXPERIMENTATION_DECISION")
    assert synthetic(description="Responsibilities\nDesign A/B tests for checkout changes.").work_profile.has("EXPERIMENTATION_DECISION")


def test_pricing_forecasting_needs_the_same_work_statement():
    other_sentence = synthetic(description="Responsibilities\nBuild churn prediction models.\n"
                                           "Analyse competitor pricing data for insights.")
    list_item = synthetic(description="Responsibilities\nBuild classification, forecasting, clustering, and regression models.")
    subject = synthetic(description="Responsibilities\nBuild demand forecasting models for the regional network.")
    assert "PRICING_FORECASTING" not in {m.track_id for m in other_sentence.trajectory.tracks}
    assert "PRICING_FORECASTING" not in {m.track_id for m in list_item.trajectory.tracks}
    assert "PRICING_FORECASTING" in {m.track_id for m in subject.trajectory.tracks}


def test_department_names_are_not_optimisation_work():
    lens = synthetic(description="Responsibilities\nPartner with Revenue Management and Pricing teams to find AI use cases.")
    assert not lens.work_profile.has("OPTIMISATION")


def test_product_context_with_experimentation_maps_product_ds():
    lens = synthetic(title="Product Data Scientist",
                     description="Responsibilities\nDesign and analyse A/B tests on user engagement for product decisions.")
    assert "PRODUCT_DS" in {m.track_id for m in lens.trajectory.tracks}


def test_imperative_duty_under_requirements_counts_but_experience_asks_do_not():
    lens = synthetic(requirements="Requirements\nDevelop and deploy fraud detection models.\n"
                                  "Building credit risk models in a bank.\n3+ years of experience with Python.")
    evidence = [e.text for l in lens.work_profile.labels for e in l.evidence]
    assert "Develop and deploy fraud detection models." in evidence
    assert not any(t.startswith("Building credit risk models") for t in evidence)


def test_no_work_statements_is_unknown_not_off_vocabulary():
    lens = synthetic(description="About us\nWe are a great company with great benefits.")
    assert lens.work_profile.unknown_reason == "no_task_statements"
    assert (lens.trajectory.direction, lens.trajectory.unknown_reason) == ("UNKNOWN", "work_profile_unknown")


def test_mixed_when_primary_and_non_target_both_match():
    lens = synthetic(description="Responsibilities\nBuild predictive models for demand.\n"
                                 "Maintain weekly management reports.\nBuild dashboards for executives.\n"
                                 "Create visualisations of KPIs.")
    assert lens.trajectory.mixed and lens.trajectory.direction == "TOWARD_PRIMARY"


# --- configuration (D-1: declared, never inferred) ---------------------------------------

def test_real_profile_targets_are_exactly_d1():
    targets = load_profile()["transition"]["target_tracks"]
    assert targets == {
        "primary": ["PRODUCT_DS", "DECISION_SCIENTIST", "EXPERIMENTATION_CAUSAL", "APPLIED_DS"],
        "secondary": ["PRICING_FORECASTING", "RISK_DS", "ML_ENGINEER"],
        "adjacent_ok": ["FRAUD_FINCRIME_DS", "CREDIT_RISK_DS", "APPLIED_SCIENTIST", "ML_SCIENTIST"],
        "non_target": ["DATA_ANALYST", "BI_ANALYST", "MIS_REPORTING", "ANALYTICS_HEAVY", "MLOPS_PLATFORM"],
    }


@pytest.mark.parametrize("targets, message", [
    ({"primary": ["NOT_A_TRACK"]}, "unknown track"),
    ({"primary": ["APPLIED_DS"], "secondary": ["APPLIED_DS"]}, "listed under both"),
    ({"favourite": ["APPLIED_DS"]}, "unknown tiers"),
])
def test_malformed_transition_config_fails_loudly(targets, message):
    with pytest.raises(ValueError, match=message):
        validate_transition_config({"transition": {"target_tracks": targets}}, load_tracks())


# --- D-4 invariants: the lens can never change frozen outputs ------------------------------

def _without_lens(job):
    return job.model_dump(mode="json", exclude={"transition_lens"})


def test_I3_pipeline_with_and_without_lens(tmp_path):
    from datetime import date
    from src.pipeline.orchestrator import run
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True)
    from src.models.raw_job import RawJob
    raws = [RawJob(job_id=f"hiredly_{i}", source="hiredly", source_job_id=str(i), source_url=f"https://x/{i}",
                   company=f"Co {i}", job_title=t, job_description=d * 3, location_raw="Kuala Lumpur | Kuala Lumpur",
                   salary_raw="Undisclosed", posted_date_raw="2026-09-01", query_used="Data Scientist",
                   source_extra={"requirements": "Required: machine learning, Python.", "skills": []})
            for i, (t, d) in enumerate([("Data Scientist", "You will build fraud detection models for payments. "),
                                        ("Business Analyst", "Maintain weekly management reports. "),
                                        ("Data Scientist", "Design and analyse A/B tests on user engagement. ")])]
    (raw_dir / "hiredly_latest.jsonl").write_text("\n".join(r.model_dump_json() for r in raws))
    (raw_dir / "mycareersfuture_latest.jsonl").write_text("")
    on = run(tmp_path, as_of_date=date(2026, 9, 20))
    off = run(tmp_path, as_of_date=date(2026, 9, 20), transition_lens=False)
    assert [(j.job_id, r) for j, r in on["rejected"]] == [(j.job_id, r) for j, r in off["rejected"]]
    assert [_without_lens(j) for j in on["scored_jobs"]] == [_without_lens(j) for j in off["scored_jobs"]]
    assert all(j.transition_lens is not None for j in on["scored_jobs"])
    assert all(j.transition_lens is None for j in off["scored_jobs"])


SRC_TRANSITION = ROOT / "src" / "transition"


def test_I7_lens_reads_only_declared_profile_inputs():
    """The lens reads profile["transition"] and years_experience_current_role
    (for SENIORITY_REACH, as frozen scoring does). Techniques are read only
    through src.candidate.candidate_techniques — the helper frozen
    technical_alignment uses. current_*, target_roles, education, location
    and domain are never read."""
    read_keys = set()
    for path in SRC_TRANSITION.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get" \
                    and getattr(node.func.value, "id", "") == "profile" and node.args \
                    and isinstance(node.args[0], ast.Constant):
                read_keys.add(node.args[0].value)
            if isinstance(node, ast.Subscript) and getattr(node.value, "id", "") == "profile" \
                    and isinstance(node.slice, ast.Constant):
                read_keys.add(node.slice.value)
    assert read_keys == {"transition", "years_experience_current_role"}, read_keys


FORBIDDEN_READS = {"band", "score", "job_quality_score", "role_type", "personal_transition_fit",
                   "overall_score", "tier", "compensation_score", "base_score", "decision_stability"}


def test_I10_lens_never_reads_frozen_scores_or_bands():
    for path in SRC_TRANSITION.glob("*.py"):
        attrs = {n.attr for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Attribute)}
        assert not (attrs & FORBIDDEN_READS), (path.name, attrs & FORBIDDEN_READS)


def test_scoring_and_hard_filters_never_import_the_lens():
    for path in list((ROOT / "src" / "scoring").glob("*.py")) + [ROOT / "src" / "pipeline" / "hard_filters.py"]:
        assert "src.transition" not in path.read_text(), path.name
