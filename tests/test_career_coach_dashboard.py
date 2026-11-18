"""Career Coach Gate 3: the dashboard reconciles exactly to its decisions,
live-check precedence and the 7-day window hold in operational runs, live
checks are recorded append-only, and none of it depends on legacy layers or
human triage."""

import ast
import copy
import importlib.util
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from src.career_coach import dashboard as dash
from src.career_coach.engine import Coach
from src.career_coach.inputs import build_population, eligible_live_checks
from src.career_coach.run_archive import verify_archive
from src.config import load_career_coach, load_profile, load_scoring
from src.models.career_coach import CareerCoachDecision, LiveCheckRecord
from src.models.processed_job import ProcessedJob
from src.models.skill_evidence import SkillEvidence

ROOT = Path(__file__).resolve().parent.parent
AS_OF = date(2026, 9, 25)
WORK = ("Responsibilities:\nBuild churn prediction models for the e-commerce app.\n"
        "Develop and validate demand forecasting models for the payments business.\n"
        "Design A/B experiments to measure checkout changes.\n")
EMAIL = WORK + "Send your CV to jobs@acme.example.com.\n"


def _job(job_id, description=WORK, **overrides) -> ProcessedJob:
    fields = dict(job_id=job_id, company=f"Co {job_id}", company_normalized=f"co {job_id}", job_title="Data Scientist",
                  country="Malaysia", city="Kuala Lumpur", location_normalized="Kuala Lumpur, Malaysia",
                  role_family="Data Scientist", role_level="Mid/Unspecified", role_type="TRUE_DS", sources=["hiredly"],
                  canonical_url=f"https://example.test/{job_id}", description_raw=description, salary_min=9000.0,
                  salary_max=12000.0, salary_currency="MYR", salary_period="Monthly", salary_unknown=False,
                  job_age_days=5, employer_clarity="DIRECT_EMPLOYER", posted_date="2026-09-15")
    fields.update(overrides)
    return ProcessedJob(**fields)


def _gaps(n):
    return [SkillEvidence(name=x, kind="tool", importance="required", importance_reason="t", source_field="description_raw")
            for x in ["Kubernetes", "Spark", "Airflow", "Docker", "GCP", "Databricks"][:n]]


JOBS = [
    _job("job_today"),                                                                    # HIGH -> TAILOR_RESUME
    _job("job_verify", EMAIL, country="Singapore", city="Singapore", location_normalized="Singapore",
         sources=["mycareersfuture"], salary_currency="SGD"),                               # V-4b -> VERIFY_FIRST
    _job("job_hold", role_level="Principal", skill_evidence=_gaps(6)),                      # SELECTIVE + HEAVY -> HOLD
    _job("job_hold_research", role_level="Principal", employer_clarity="RECRUITER"),       # SELECTIVE + research HIGH -> HOLD
    _job("job_dni", salary_min=5000.0, salary_max=7000.0),                                  # V-3 -> DO_NOT_INVEST
    _job("job_rejected", country="Indonesia", city="Jakarta", location_normalized="Jakarta, Indonesia"),
]


def _run(jobs=JOBS, profile=None, live=(), as_of=AS_OF):
    coach = Coach(profile or copy.deepcopy(load_profile()), load_career_coach(), set())
    return coach.run(build_population(list(jobs), load_scoring()), as_of, "OPERATIONAL", list(live), "raw")


@pytest.fixture(scope="module")
def built():
    decisions, meta = _run()
    return decisions, meta, dash.build(decisions, meta)


# ------------------------------------------------------------- reconciliation

def test_every_decision_lands_in_exactly_one_section(built):
    decisions, _, d = built
    assert d["counts"] == {"TODAY": 1, "VERIFY_FIRST": 1, "ON_HOLD": 2, "DO_NOT_INVEST": 1, "HARD_FILTER_REJECTED": 1}
    placed = {i["job_id"]: s for s in dash.SECTIONS for i in d["sections"][s]}
    assert placed == {"job_today": "TODAY", "job_verify": "VERIFY_FIRST", "job_hold": "ON_HOLD",
                      "job_hold_research": "ON_HOLD", "job_dni": "DO_NOT_INVEST", "job_rejected": "HARD_FILTER_REJECTED"}
    assert sum(d["counts"].values()) == len(decisions) == len(d["all"])


def test_every_item_maps_back_to_its_decision(built):
    decisions, _, d = built
    by_id = {x.job.job_id: x for x in decisions}
    for i in d["all"]:
        x = by_id[i["job_id"]]
        assert (i["priority"], i["next_action"], i["why_rule"], i["resume_effort"] if x.resume_effort else "—") == \
               (x.application_priority, x.next_action, x.primary_reason.rule_id, x.resume_effort or "—")


@pytest.mark.parametrize("tamper, message", [
    (lambda d: d["sections"]["TODAY"].clear(), "sections hold"),
    (lambda d: d["all"].pop(), "full opportunity view"),
    (lambda d: d["sections"]["DO_NOT_INVEST"].append(dict(d["sections"]["TODAY"][0])), "sections hold"),
    (lambda d: d["all"][0].__setitem__("priority", "HIGH" if d["all"][0]["priority"] != "HIGH" else "SELECTIVE"),
     "displayed values differ"),
    (lambda d: d["counts"].__setitem__("TODAY", 5), "section counts"),
])
def test_dashboard_refuses_to_render_inconsistent_totals(built, tamper, message):
    decisions, _, d = built
    tampered = copy.deepcopy(d)
    tamper(tampered)
    with pytest.raises(ValueError, match=message):
        dash.reconcile(tampered, decisions)


# ---------------------------------------------------------- resume work view

def test_research_only_hold_is_not_resume_work(built):
    decisions, _, d = built
    by_id = {i["job_id"]: i for i in d["all"]}
    research, resume = by_id["job_hold_research"], by_id["job_hold"]
    assert (research["priority"], research["next_action"], research["hold_causes"]) == ("SELECTIVE", "HOLD", ["research"])
    assert research["hold_reason"] == "research effort HIGH exceeds the LIGHT_TOUCH investment that SELECTIVE allows"
    assert resume["hold_causes"] == ["resume"]
    assert [i["job_id"] for i in d["resume_work"]] == ["job_today", "job_hold"]


def test_resume_work_queue_holds_only_resume_work(built):
    for i in built[2]["resume_work"]:
        assert i["resume_effort"] in ("TARGETED", "HEAVY")
        assert i["section"] == "TODAY" or "resume" in i["hold_causes"]


def test_research_only_hold_is_shown_outside_the_resume_queue(built):
    md = dash.render_markdown(built[2])
    section3, section5 = md[md.index("## 3."):md.index("## 4.")], md[md.index("## 5."):]
    assert "Co job_hold_research" not in section3 and "Co job_hold ·" in section3
    assert "### On hold — not resume work (1)" in section5
    block = section5[section5.index("**Co job_hold_research"):]
    assert "Priority **SELECTIVE** · Next action **HOLD**" in block
    assert "Hold because: research effort HIGH exceeds the LIGHT_TOUCH investment that SELECTIVE allows" in block


def test_resume_queue_cannot_include_a_research_only_hold(built):
    decisions, _, d = built
    tampered = copy.deepcopy(d)
    tampered["resume_work"].append(next(i for i in tampered["all"] if i["job_id"] == "job_hold_research"))
    with pytest.raises(ValueError, match="without resume work"):
        dash.reconcile(tampered, decisions)


# ------------------------------------------------------------------ rendering

def test_markdown_has_the_five_sections_in_order(built):
    md = dash.render_markdown(built[2])
    heads = ["## 1. Today's Application Queue", "## 2. Verify First", "## 3. Resume Work Queue",
             "## 4. Do Not Invest", "## 5. Full Opportunity View"]
    positions = [md.index(h) for h in heads]
    assert positions == sorted(positions)


def test_every_displayed_job_shows_the_required_fields(built):
    md = dash.render_markdown(built[2])
    block = md[md.index("**Co job_today"):md.index("## 2.")]
    for needle in ("Why here:", "Career value: PRIMARY · Primary · EXPERIMENTATION_CAUSAL", "Priority **HIGH**",
                   "Next action **TAILOR_RESUME**", "Effort: resume TARGETED, research LOW",
                   "Investment: FULL allowed", "Resume track: Experimentation / Pricing (from EXPERIMENTATION_CAUSAL, Primary)",
                   "Readiness: UNASSESSED · seniority UNKNOWN (Mid/Unspecified)"):
        assert needle in block, needle
    verify = md[md.index("**Co job_verify"):]
    assert "Verify (Application channel)" in verify and "if confirmed:" in verify


def test_header_surfaces_undeclared_resume_variants(built):
    assert "`resume_variants_ready: null`" in dash.render_markdown(built[2])
    profile = copy.deepcopy(load_profile())
    profile["career_coach"]["resume_variants_ready"] = ["RISK"]
    decisions, meta = _run(profile=profile)
    assert "resume_variants_ready: null" not in dash.render_markdown(dash.build(decisions, meta))


def test_csv_and_html_carry_every_decision(built):
    decisions, _, d = built
    assert dash.render_csv(d).count("\n") == len(decisions) + 1
    html_text = dash.render_html(d)
    assert html_text.count("<tr data-") == len(decisions)
    assert all(f"/{x.job.job_id}" in html_text for x in decisions)


def test_rendering_is_deterministic(built):
    decisions, meta, d = built
    again = dash.build(decisions, meta)
    assert dash.render_markdown(d) == dash.render_markdown(again) and dash.render_html(d) == dash.render_html(again)


def test_decisions_round_trip_through_the_frozen_schema(built):
    for x in built[0]:
        assert CareerCoachDecision.model_validate(json.loads(x.model_dump_json())) == x


# --------------------------------------------------- live checks (operational)

def _record(record_id, checked_on, **overrides):
    fields = dict(record_id=record_id, job_id="job_verify", source_job_id="s1", checked_on=checked_on, method="test",
                  page_reviewable="YES", email_in_jd="YES", posting_status="OPEN")
    fields.update(overrides)
    return LiveCheckRecord.model_validate(fields)


def test_live_check_window_is_seven_days_inclusive():
    used, ignored = eligible_live_checks([_record("a", "2026-09-18"), _record("b", "2026-09-17", job_id="other")],
                                         AS_OF, 7)
    assert set(used) == {"job_verify"} and ignored == [("b", "OUTSIDE_WINDOW")]


def test_latest_eligible_record_wins_and_ties_follow_file_order():
    used, _ = eligible_live_checks([_record("new", "2026-09-24"), _record("old", "2026-09-20", posting_status="CLOSED")],
                                   AS_OF, 7)
    assert used["job_verify"].record_id == "new"
    used, _ = eligible_live_checks([_record("first", "2026-09-24"), _record("second", "2026-09-24")], AS_OF, 7)
    assert used["job_verify"].record_id == "second"


def test_operational_live_evidence_changes_the_channel_decision():
    before = {d.job.job_id: d for d in _run()[0]}["job_verify"]
    after = {d.job.job_id: d for d in _run(live=[_record("lc", "2026-09-25", posting_status="CLOSED")])[0]}["job_verify"]
    assert before.next_action == "VERIFY_APPLICATION_CHANNEL" and after.next_action == "VERIFY_POSTING_OPEN"
    viable = {d.job.job_id: d for d in _run(live=[_record("lc", "2026-09-25")])[0]}["job_verify"]
    assert viable.application_viability.state == "VIABLE"


def test_future_live_records_never_reach_an_earlier_run():
    base, _ = _run(as_of=date(2026, 9, 20))
    later, meta = _run(as_of=date(2026, 9, 20), live=[_record("lc", "2026-09-25")])
    assert [d.model_dump(mode="json") for d in base] == [d.model_dump(mode="json") for d in later]
    assert meta["live_checks_ignored"] == [("lc", "AFTER_AS_OF")]


# ------------------------------------------------------ live-check recorder

def _recorder():
    spec = importlib.util.spec_from_file_location("record_live_checks", ROOT / "scripts" / "record_live_checks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBS = {"source": "mycareersfuture", "source_job_id": "s1", "checked_on": "2026-09-25", "method": "test",
       "page_reviewable": "YES", "email_in_jd": "NO", "posting_status": "CLOSED"}


def test_recorder_is_append_only_and_idempotent(tmp_path):
    rec = _recorder()
    path = tmp_path / "live_checks.jsonl"
    records = rec.build_records([OBS], {"s1": "job_verify"})
    assert rec.append(records, path) == (1, 0)
    first = path.read_bytes()
    assert rec.append(records, path) == (0, 1) and path.read_bytes() == first
    conflicting = rec.build_records([{**OBS, "email_in_jd": "YES"}], {"s1": "job_verify"})
    with pytest.raises(ValueError, match="different content"):
        rec.append(conflicting, path)
    assert path.read_bytes() == first


def test_recorder_refuses_unknown_jobs():
    with pytest.raises(ValueError, match="not in the stored raw data"):
        _recorder().build_records([OBS], {})


# ------------------------------------------------------------ independence

FORBIDDEN = ("src.scoring", "src.transition", "src.reporting", "src.strategy", "src.models.ptf", "src.models.transition",
             "src.models.scored_job", "src.models.strategy", "src.pipeline.orchestrator", "src.calibration")


@pytest.mark.parametrize("path", ["src/career_coach/dashboard.py", "scripts/career_coach_dashboard.py",
                                  "scripts/record_live_checks.py"])
def test_gate3_code_has_no_legacy_or_triage_imports(path):
    tree = ast.parse((ROOT / path).read_text())
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
              {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not {m for m in modules if m.startswith(FORBIDDEN)}
    assert "triage" not in (ROOT / path).read_text().lower()


# ------------------------------------------- verified corpus regression (local)
# These read the immutable run archives of the verified runs (built from the
# verified raw corpus), never the data/output folders that every production
# run replaces; they skip only when those archives are not present.

VERIFIED_RAW = "9c75bb6dc7c294d17cf823e3df61f72dedf34c4cd7b5df3cf26e3ea8818c170e"
VERIFIED = {"career_coach_shadow": "5332a13e4cfa67e6eecd6f95064dac2c7aa5daa9d03359d363ea99e15ac652e6",
            "career_coach_operational": "e4fee14b5991a25b7151dbc73dfa4b00d55e253221ebae97868c4ec7f21572ba"}
VERIFIED_ARCHIVES = {"career_coach_shadow": "2026-09-20_HISTORICAL_REPLAY_6c1d6db28a382bd3",
                     "career_coach_operational": "2026-09-25_OPERATIONAL_ea3c8ebdc2ba97cc"}
RN_CARE = "job_5bb96e5a6c806f78"


def _verified_run(name):
    run_dir = ROOT / "data" / "archive" / "runs" / VERIFIED_ARCHIVES[name]
    if not (run_dir / "archive.json").exists():
        pytest.skip(f"{VERIFIED_ARCHIVES[name]} archive not present")
    verify_archive(run_dir)                                  # read-only; fails if any archived file changed
    raw = json.loads((run_dir / "decisions.json").read_text())
    assert raw[0]["run"]["input_hashes"]["raw_corpus"] == VERIFIED_RAW
    return run_dir, raw


@pytest.mark.parametrize("name", sorted(VERIFIED))
def test_verified_decision_outputs_are_byte_identical(name):
    import hashlib
    run_dir, _ = _verified_run(name)
    assert hashlib.sha256((run_dir / "decisions.json").read_bytes()).hexdigest() == VERIFIED[name]


def test_rn_care_is_a_research_hold_not_resume_work():
    run_dir, raw = _verified_run("career_coach_operational")
    decisions = [CareerCoachDecision.model_validate(x) for x in raw]
    d = dash.build(decisions, json.loads((run_dir / "run_meta.json").read_text()))
    rn = next(i for i in d["all"] if i["job_id"] == RN_CARE)
    assert (rn["priority"], rn["next_action"], rn["section"]) == ("SELECTIVE", "HOLD", "ON_HOLD")
    assert rn["hold_causes"] == ["research"] and not dash.needs_resume_work(rn)
    assert RN_CARE not in {i["job_id"] for i in d["resume_work"]}
    assert sum(d["counts"].values()) == len(d["all"]) == 118


# ------------------------------------------- dashboard taxonomy (contract)
# The five named sections are the product contract. Placement statuses map to
# exactly one named section each; section 3 is never a primary placement.

NAMED_SECTIONS = ("Today's Application Queue", "Verify First", "Resume Work Queue", "Do Not Invest",
                  "Full Opportunity View")
PRIMARY_NAMED_SECTION = {"TODAY": 1, "VERIFY_FIRST": 2, "DO_NOT_INVEST": 4, "HARD_FILTER_REJECTED": 4, "ON_HOLD": 5}


def _named_sections(md: str) -> dict[int, str]:
    import re
    starts = [(int(m.group(1)), m.start()) for m in re.finditer(r"^## (\d)\. ", md, re.M)]
    return {n: md[s:(starts[k + 1][1] if k + 1 < len(starts) else len(md))] for k, (n, s) in enumerate(starts)}


def test_the_five_named_sections_are_the_only_numbered_sections(built):
    import re
    md = dash.render_markdown(built[2])
    assert tuple(re.findall(r"^## \d\. (.+?) \(", md, re.M)) == NAMED_SECTIONS
    others = [h for h in re.findall(r"^## (.+)$", md, re.M) if not re.match(r"\d\. ", h)]
    assert others == ["Run status", "What to work on today", "Profile declarations to resolve"]   # unnumbered context
    assert set(PRIMARY_NAMED_SECTION) == set(dash.SECTIONS)      # every placement status is mapped


def test_every_job_has_exactly_one_primary_named_section(built):
    decisions, _, d = built
    placement = {i["job_id"]: PRIMARY_NAMED_SECTION[i["section"]] for i in d["all"]}
    assert len(placement) == len(decisions) and 3 not in placement.values()
    assert sum(Counter(placement.values()).values()) == len(decisions)


def test_each_job_appears_in_its_primary_named_section(built):
    _, _, d = built
    sections = _named_sections(dash.render_markdown(d))
    rejected = d["sections"]["HARD_FILTER_REJECTED"]
    for i in d["all"]:
        home = sections[PRIMARY_NAMED_SECTION[i["section"]]]
        if i["section"] == "HARD_FILTER_REJECTED":             # collapsed subsection of Do Not Invest (M12)
            assert f"### Rejected by the hard filter ({len(rejected)} jobs" in home
        else:
            assert f"{i['company']} · {i['title']}" in home, i["job_id"]


def test_on_hold_and_rejected_are_subsections_not_sections(built):
    sections = _named_sections(dash.render_markdown(built[2]))
    assert "### Rejected by the hard filter" in sections[4]
    assert "### On hold — not resume work" in sections[5]
    assert "Co job_hold_research" in sections[5] and "Co job_hold_research" not in sections[3]


def test_resume_work_queue_is_a_secondary_view(built):
    _, _, d = built
    section3 = _named_sections(dash.render_markdown(d))[3]
    for i in d["resume_work"]:
        assert PRIMARY_NAMED_SECTION[i["section"]] in (1, 5) and f"{i['company']} · {i['title']}" in section3
    for i in d["all"]:
        if i not in d["resume_work"]:
            assert f"{i['company']} · {i['title']}" not in section3, i["job_id"]


def test_full_opportunity_view_lists_every_job_exactly_once(built):
    decisions, _, d = built
    section5 = _named_sections(dash.render_markdown(d))[5]
    rows = [line for line in section5.splitlines() if line.startswith("| ") and not line.startswith("| Section")]
    assert len(rows) == len(decisions)
    for i in d["all"]:
        assert sum(f"| {i['company']} · {i['title']} |" in r for r in rows) == 1, i["job_id"]


@pytest.mark.parametrize("name, expected", [
    ("career_coach_operational", {1: 1, 2: 13, 4: 103, 5: 1}),
    ("career_coach_shadow", {1: 1, 2: 14, 4: 103}),
])
def test_verified_primary_placements_total_118(name, expected):
    run_dir, raw = _verified_run(name)
    d = dash.build([CareerCoachDecision.model_validate(x) for x in raw], json.loads((run_dir / "run_meta.json").read_text()))
    placements = Counter(PRIMARY_NAMED_SECTION[i["section"]] for i in d["all"])
    assert dict(placements) == expected and sum(placements.values()) == 118
