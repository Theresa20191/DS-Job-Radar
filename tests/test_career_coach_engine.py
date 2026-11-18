"""Career Coach Gate 2: engine rules on synthetic jobs, regression cases for
every Gate 2 lexical fix (T-1..T-11), live-check contamination, input
perturbation and determinism."""

import copy
import json
import random
from datetime import date

import pytest

from src.career_coach.engine import Coach
from src.career_coach.inputs import build_population
from src.career_coach.setup import parse_config
from src.career_coach.substance import analyse
from src.config import load_career_coach, load_profile, load_scoring
from src.enrichment.requirement_extractor import extract_years_experience
from src.models.career_coach import JobProjection, LiveCheckRecord
from src.models.processed_job import ProcessedJob
from src.models.skill_evidence import SkillEvidence

AS_OF = date(2026, 9, 20)
RISK_WORK = ("Responsibilities:\nBuild credit risk models to support lending decisions across the portfolio.\n"
             "Develop scorecards and validate credit risk models with the model risk team.\n")


def _job(description=RISK_WORK, **overrides) -> ProcessedJob:
    fields = dict(job_id="job_t", company="Acme Bank", company_normalized="acme bank", job_title="Data Scientist",
                  country="Malaysia", city="Kuala Lumpur", location_normalized="Kuala Lumpur, Malaysia",
                  role_family="Data Scientist", role_level="Mid/Unspecified", role_type="TRUE_DS",
                  sources=["hiredly"], canonical_url="https://example.test/job", description_raw=description,
                  salary_min=9000.0, salary_max=12000.0, salary_currency="MYR", salary_period="Monthly",
                  salary_unknown=False, job_age_days=5, employer_clarity="DIRECT_EMPLOYER", posted_date="2026-09-15")
    fields.update(overrides)
    return ProcessedJob(**fields)


def _sg(description, **overrides) -> ProcessedJob:
    return _job(description, country="Singapore", city="Singapore", location_normalized="Singapore",
                sources=["mycareersfuture"], salary_currency="SGD", salary_min=8000.0, salary_max=12000.0, **overrides)


def _profile(**career_coach) -> dict:
    profile = copy.deepcopy(load_profile())
    for key, value in career_coach.items():
        profile["career_coach"][key] = value
    return profile


def _run(jobs, profile=None, live=(), as_of=AS_OF):
    coach = Coach(profile or _profile(), load_career_coach(), set())
    population = build_population(list(jobs), load_scoring())
    return coach.run(population, as_of, "HISTORICAL_REPLAY", list(live), "raw-hash")


def _decide(job, **kwargs):
    decisions, _ = _run([job], **kwargs)
    return decisions[0]


def _activities(description: str) -> dict[str, int]:
    sub = analyse(JobProjection.from_processed(_job(description)), parse_config(load_career_coach()))
    return {a: sub.count(a) for a in sub.hits}


def _domains(description: str, title: str = "Data Scientist") -> dict[str, set[str]]:
    sub = analyse(JobProjection.from_processed(_job(description, job_title=title)), parse_config(load_career_coach()))
    return sub.domain_placements


def _gaps(n: int) -> list[SkillEvidence]:
    names = ["Kubernetes", "Spark", "Airflow", "Docker", "GCP", "Databricks", "Tableau"][:n]
    return [SkillEvidence(name=x, kind="tool", importance="required", importance_reason="t",
                          source_field="description_raw") for x in names]


def _live(**overrides) -> LiveCheckRecord:
    record = dict(record_id="lc1", job_id="job_t", source_job_id="abc", checked_on="2026-09-20",
                  method="test", page_reviewable="YES", email_in_jd="YES", posting_status="OPEN")
    record.update(overrides)
    return LiveCheckRecord.model_validate(record)


EMAIL_WORK = RISK_WORK + "Send your CV to jobs@acme.example.com.\n"


# ------------------------------------------------ Gate 2 lexical regression

def test_t1_t2_nouns_are_not_verbs():
    assert "PROJECT_DELIVERY" not in _activities("Ensure the successful delivery of deliverables for the business.")
    assert "RESEARCH" not in _activities("Stay abreast of research advancements in the field.")
    assert _activities("Manage project timelines and dependencies.").get("PROJECT_DELIVERY") == 1


def test_t3_collaboration_with_stakeholders_is_not_project_delivery():
    assert "PROJECT_DELIVERY" not in _activities("Partner with business and technical stakeholders to lead discussions.")


def test_t4_wrapped_sentence_is_one_sentence():
    wrapped = "Responsibilities:\nLeverage, customise, and fine-tune state-of-the-art\nvision and language foundation models for clients\n"
    assert _activities(wrapped).get("MODELLING") == 1
    separate = "Responsibilities:\nLeverage, customise, and fine-tune.\nFoundation models are our focus.\n"
    assert "MODELLING" not in _activities(separate)


def test_t12_agentic_systems_are_genai_work():
    assert _activities("Develop agentic AI systems for reasoning and tool use.").get("GENAI_APPS") == 1


def test_t5_nominalised_verbs_state_work():
    assert _activities("Support the development, training, and evaluation of machine learning models.").get("MODELLING") == 1
    assert _activities("Lead the design, development, and deployment of AI/ML solutions.").get("DEPLOYMENT") == 1
    assert not _activities("Apply AI-assisted development to improve delivery speed and quality.")


def test_t6_about_the_team_is_work_but_about_us_is_not():
    team = "About the Team\nWe build machine learning models for search ranking every day.\n"
    company = "About us\nWe build machine learning models for search ranking every day.\n"
    assert _activities(team).get("MODELLING") == 1
    assert not _activities(company)


def test_t7_scale_in_compounds_is_not_a_verb():
    assert "DEPLOYMENT" not in _activities("Own large-scale speech data pipelines for the team.")
    assert "DEPLOYMENT" not in _activities("Maintain data pipelines at scale for the business.")
    assert _activities("Scale machine learning pipelines to new markets.").get("DEPLOYMENT") == 1


def test_t8_planning_noun_is_not_a_verb():
    assert "PROJECT_DELIVERY" not in _activities("Prepare reports on project outcomes for strategic planning.")


def test_t9_advanced_adjective_is_not_a_verb():
    assert "RESEARCH" not in _activities("Evaluate advanced techniques for retrieval.")


def test_t10_applied_machine_learning_is_not_aml():
    assert "AML" not in _domains("Responsibilities:\nThe Applied Machine Learning (AML) team builds models for ads.\n")
    assert "AML" not in _domains("Responsibilities:\nData AML is our machine learning platform.\n")
    assert "OWN_WORK" in _domains("Responsibilities:\nBuild AML transaction monitoring models for the bank.\n")["AML"]


def test_t11_risk_is_an_analytic_subject_not_governance():
    assert "Risk" not in _domains("Responsibilities:\nPartner with Legal, Risk, Compliance teams on AI governance.\n")
    assert "Risk" not in _domains("Responsibilities:\nSupport risk management processes with deal teams.\n")
    assert "OWN_WORK" in _domains("Responsibilities:\nDevelop credit risk models for retail lending.\n")["Risk"]
    assert "DEFINING" in _domains("", title="Risk Data Scientist")["Risk"]


def test_m16_requirements_text_never_creates_work():
    assert not _activities("Requirements:\nBuild machine learning models in Python.\nDeploy models to production.\n")


# ------------------------------------------------------ viability / SG rule

def test_sg_mcf_without_email_is_blocked():
    d = _decide(_sg(RISK_WORK))
    assert d.application_viability.state == "BLOCKED" and d.blocking_reason.rule_id == "V-4a′"
    assert (d.application_priority, d.next_action) == ("DO_NOT_INVEST", "DO_NOT_INVEST")


def test_sg_mcf_email_without_live_check_needs_channel_verification():
    d = _decide(_sg(EMAIL_WORK))
    assert (d.application_priority, d.next_action) == ("VERIFY_FIRST", "VERIFY_APPLICATION_CHANNEL")
    assert [q.code for q in d.verification_queue] == ["VQ-APPLICATION_CHANNEL"]


def test_eligible_live_check_confirms_channel_or_blocks():
    assert _decide(_sg(EMAIL_WORK), live=[_live()]).application_viability.state == "VIABLE"
    closed = _decide(_sg(EMAIL_WORK), live=[_live(posting_status="CLOSED")])
    assert (closed.application_priority, closed.next_action) == ("VERIFY_FIRST", "VERIFY_POSTING_OPEN")
    no_email = _decide(_sg(EMAIL_WORK), live=[_live(email_in_jd="NO")])
    assert no_email.blocking_reason.rule_id == "V-4a"


def test_live_check_after_as_of_date_cannot_contaminate_a_replay():
    base, _ = _run([_sg(EMAIL_WORK)])
    later, meta = _run([_sg(EMAIL_WORK)], live=[_live(checked_on="2026-09-25")])
    assert json.dumps([d.model_dump(mode="json") for d in base]) == json.dumps([d.model_dump(mode="json") for d in later])
    assert meta["live_checks_ignored"] == [("lc1", "AFTER_AS_OF")]


def test_live_check_older_than_window_is_ignored():
    _, meta = _run([_sg(EMAIL_WORK)], live=[_live(checked_on="2026-09-12")])
    assert meta["live_checks_ignored"] == [("lc1", "OUTSIDE_WINDOW")]


def test_malaysia_hard_floor():
    below = _decide(_job(salary_min=5000.0, salary_max=7000.0))
    assert below.blocking_reason.rule_id == "V-3" and below.application_priority == "DO_NOT_INVEST"
    undisclosed = _decide(_job(salary_min=None, salary_max=None, salary_currency=None, salary_period=None,
                               salary_unknown=True))
    assert (undisclosed.application_priority, undisclosed.next_action) == ("VERIFY_FIRST", "VERIFY_COMPENSATION")


def test_hard_filter_rejected_job_is_short_circuited():
    d = _decide(_job(country="Indonesia", location_normalized="Jakarta, Indonesia", city="Jakarta"))
    assert d.evaluation_scope == "HARD_FILTER_REJECTED" and d.career_value is None
    assert d.blocking_reason.rule_id == "V-1"


# ------------------------------------------------ career value / resume track

PRIMARY_WORK = ("Responsibilities:\nBuild churn prediction models for the e-commerce app.\n"
        "Develop and validate demand forecasting models for the payments business.\n"
        "Design A/B experiments to measure checkout changes.\n")


def test_primary_job_and_resume_track_precedence():
    d = _decide(_job(PRIMARY_WORK))
    assert d.career_value.state == "PRIMARY" and d.career_value.deciding_track == "EXPERIMENTATION_CAUSAL"
    tiers = {m.track: m.declared_tier for m in d.career_value.matched_tracks}
    assert tiers["PRICING_FORECASTING"] == "secondary"             # secondary kept, identifiable
    assert d.resume_track.positioning == "EXPERIMENTATION_PRICING" and d.resume_track.deciding_key == "RT-2"
    assert d.resume_track.runner_up == "APPLIED_DS"


def test_secondary_only_job_stays_secondary_and_never_high():
    d = _decide(_job("Responsibilities:\nDeploy machine learning models into production services.\n"
                     "Integrate models with the payments platform.\n"))
    assert d.career_value.state == "SECONDARY" and d.career_value.deciding_track == "ML_ENGINEER"
    assert d.application_priority == "PRIORITY" and d.resume_track.declared_tier == "secondary"


def test_primary_beats_more_specific_secondary():
    d = _decide(_job("Responsibilities:\nBuild machine learning models for demand across stores.\n"
                     "Develop predictive models for churn.\nDevelop pricing models to forecast price elasticity.\n"))
    assert d.career_value.state == "PRIMARY" and d.resume_track.source_track == "APPLIED_DS"
    assert d.resume_track.deciding_key == "RT-1" and d.resume_track.runner_up == "PRICING_FORECASTING"


def test_applied_ds_needs_central_modelling_not_presence():
    d = _decide(_job("Responsibilities:\nAnalyse customer behaviour to uncover insights for marketing.\n"
                     "Identify trends and patterns in sales data.\nBuild a churn model once per year.\n"))
    assert "APPLIED_DS" not in [m.track for m in d.career_value.matched_tracks]


# ----------------------------------------------- seniority / readiness / priority

@pytest.mark.parametrize("level, seniority, readiness, priority", [
    ("Junior", "ALIGNED", "NO_GAP", "HIGH"),
    ("Senior", "STRETCH", "STRETCH", "PRIORITY"),
    ("Principal", "MATERIAL_GAP", "MATERIAL_GAP", "SELECTIVE"),
    ("Intern", "MATERIAL_GAP", "MATERIAL_GAP", "SELECTIVE"),
    ("Mid/Unspecified", "UNKNOWN", "UNASSESSED", "HIGH"),          # UNKNOWN is never negative
])
def test_seniority_drives_priority(level, seniority, readiness, priority):
    d = _decide(_job(role_level=level))
    assert d.candidate_alignment.seniority.state == seniority
    assert d.readiness.state == readiness and d.application_priority == priority


def test_seniority_direction():
    assert _decide(_job(role_level="Principal")).candidate_alignment.seniority.direction == "ABOVE"
    assert _decide(_job(role_level="Intern")).candidate_alignment.seniority.direction == "BELOW"


def test_undeclared_levels_are_neutral():
    profile = _profile(seniority={"aligned_levels": [], "stretch_levels": []})
    d = _decide(_job(role_level="Principal"), profile=profile)
    assert d.candidate_alignment.seniority.state == "UNKNOWN" and d.application_priority == "HIGH"
    assert "PD-TARGET_LEVELS" in d.profile_items_affecting_job


def test_level_conflict_goes_to_verification():
    profile = _profile(experience={"current_role_years": 1, "relevant_ds_years": 3, "relevant_ml_years": 0,
                                   "relevant_domain_years": {}})
    d = _decide(_job(role_level="Principal", years_experience_required=1.0), profile=profile)
    assert d.readiness.state == "LEVEL_CONFLICT"
    assert (d.application_priority, d.next_action) == ("VERIFY_FIRST", "VERIFY_ROLE_LEVEL")
    assert [q.code for q in d.verification_queue] == ["VQ-ROLE_LEVEL"]


def test_seniority_never_overrides_a_hard_block():
    d = _decide(_job(role_level="Junior", salary_min=5000.0, salary_max=7000.0))
    assert d.application_priority == "DO_NOT_INVEST" and d.primary_reason.rule_id == "PR-1"


@pytest.mark.parametrize("years, state, priority", [(5.0, "MATERIAL_GAP", "SELECTIVE"), (2.0, "WITHIN_STRETCH", "PRIORITY")])
def test_experience_uses_declared_relevant_years(years, state, priority):
    d = _decide(_job(years_experience_required=years))
    assert d.candidate_alignment.experience.state == state and d.application_priority == priority
    assert d.candidate_alignment.experience.candidate_measure == "relevant_ds_years"


def test_tenure_is_never_compared():
    profile = _profile(experience={"current_role_years": 10, "relevant_ds_years": 0, "relevant_ml_years": 0,
                                   "relevant_domain_years": {}})
    assert _decide(_job(years_experience_required=5.0), profile=profile).candidate_alignment.experience.state == "MATERIAL_GAP"


def test_ml_track_uses_relevant_ml_years():
    d = _decide(_job("Responsibilities:\nDeploy machine learning models to production services.\n"
                     "Integrate models into customer-facing services.\n",
                     role_family="ML Engineer", job_title="Machine Learning Engineer", years_experience_required=3.0))
    assert d.career_value.deciding_track == "ML_ENGINEER"
    assert d.candidate_alignment.experience.candidate_measure == "relevant_ml_years"


def _years_decision(requirements: str):
    """A decision for a job whose years requirement is extracted from `requirements`
    exactly as enrichment does, so the cited sentence must justify that value."""
    return _decide(_job(RISK_WORK + "Requirements:\n" + requirements,
                        years_experience_required=extract_years_experience(requirements)))


def _years_citation(d) -> str | None:
    exp = d.candidate_alignment.experience
    cited = [e for e in d.key_evidence if e.kind == "years_requirement"]
    assert [e.id for e in cited] == ([exp.job_evidence_id] if exp.job_evidence_id else [])
    return cited[0].text if cited else None


@pytest.mark.parametrize("sentence, years", [
    ("Five or more years of experience in data science or proven experience.", 5.0),   # written number (Zurich)
    ("2-5 years of experience in manufacturing analytics.", 2.0),                       # range -> lower bound
    ("2–5 years of experience in manufacturing analytics.", 2.0),                       # en dash (Renesas)
    ("Minimum 3 years of hands-on experience in machine learning.", 3.0),               # digit minimum (unchanged)
    ("At least three (3) years of deployment experience.", 3.0),                         # numeric echo (Talentsis)
])
def test_extracted_years_requirement_cites_its_source_sentence(sentence, years):
    d = _years_decision(sentence)
    assert d.candidate_alignment.experience.job_required_years == years
    assert _years_citation(d) == sentence


@pytest.mark.parametrize("requirements", [
    "Open to candidates with up to 4 years of experience.",
    "Founded 20 years ago, we have delivered a 2 year project for every client.",
])
def test_no_years_citation_without_a_minimum_requirement(requirements):
    d = _years_decision(requirements)
    assert d.candidate_alignment.experience.job_required_years is None
    assert _years_citation(d) is None


@pytest.mark.parametrize("requirements, cited", [
    ("Juniors with up to 5 years are welcome.\nSeniors need 5+ years of experience in risk modelling.",
     "Seniors need 5+ years of experience in risk modelling."),
    ("This is a 1 year contract.\nAt least 1 year of experience in Python.", "At least 1 year of experience in Python."),
])
def test_years_citation_skips_phrases_the_extractor_ignores(requirements, cited):
    assert _years_citation(_years_decision(requirements)) == cited


def test_education_below_is_a_material_gap():
    d = _decide(_job(education_required="PhD"))
    assert d.readiness.deciding_rule == "R-1" and d.application_priority == "SELECTIVE"


# ---------------------------------------------------------- value vs effort

def test_effort_never_changes_priority():
    light = _decide(_job())
    heavy = _decide(_job(skill_evidence=_gaps(6)))
    assert light.application_priority == heavy.application_priority == "HIGH"
    assert heavy.resume_effort == "HEAVY" and light.resume_effort == "TARGETED"


def test_effort_beyond_justified_investment_holds():
    d = _decide(_job(role_level="Principal", skill_evidence=_gaps(6)))
    assert d.application_priority == "SELECTIVE" and d.application_investment.fit == "EXCEEDS_BUDGET"
    assert d.next_action == "HOLD"


def test_resume_variant_declaration_is_a_profile_item_until_declared():
    assert "PD-RESUME_VARIANTS" in _decide(_job(PRIMARY_WORK)).profile_items_affecting_job
    ready = _decide(_job(PRIMARY_WORK), profile=_profile(resume_variants_ready=["EXPERIMENTATION_PRICING"]))
    assert "PD-RESUME_VARIANTS" not in ready.profile_items_affecting_job
    assert (ready.resume_effort, ready.next_action) == ("LIGHT", "APPLY")


# ------------------------------------------------ independence / determinism

def test_output_is_deterministic():
    jobs = [_job(), _sg(EMAIL_WORK, job_id="job_s"), _job(job_id="job_p", role_level="Principal")]
    first, _ = _run(jobs)
    second, _ = _run(jobs)
    assert json.dumps([d.model_dump(mode="json") for d in first]) == json.dumps([d.model_dump(mode="json") for d in second])


def test_fields_outside_the_projection_cannot_change_a_decision():
    """Perturbation: every ProcessedJob field the Coach does not read is
    randomised; the decision must be byte-identical."""
    base = _job(skill_evidence=_gaps(2), years_experience_required=2.0)
    reference = _decide(base).model_dump(mode="json")
    readable = set(JobProjection.model_fields) | {"hc_substantive_ds_status", "hc_work_authorization_status",
                                                  "role_type", "job_age_days", "remote_type"}   # read by the hard filter
    rng = random.Random(7)
    changes = {}
    for name, info in ProcessedJob.model_fields.items():
        if name in readable:
            continue
        value = getattr(base, name)
        if isinstance(value, bool):
            changes[name] = not value
        elif isinstance(value, (int, float)) and value is not None:
            changes[name] = value + rng.randint(1, 50)
        elif isinstance(value, str):
            changes[name] = value + "-perturbed"
        elif isinstance(value, list):
            changes[name] = value + (["perturbed"] if not value or isinstance(value[0], str) else [])
    perturbed = base.model_copy(update=changes)
    assert _decide(perturbed).model_dump(mode="json") == reference
