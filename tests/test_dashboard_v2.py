"""Dashboard V2 — Daily Action Workspace: presentation only. Queue membership,
order and every decision value are unchanged; action labels, evidence status
and workload counts are fixed relabellings / counts of existing fields. All
runs are SYNTHETIC TEST FIXTURES in tmp_path."""

import re
import shutil
from datetime import date

import pytest

from src.career_coach import dashboard as dash
from src.career_coach.operational import load_run_dir, render_dashboard
from tests.test_career_coach_dashboard import JOBS, _job
from tests.test_dashboard_v1 import OLD_SID, SID, _built, _filter_with_node, _record, _sequence, _sg, _write


def _section(page: str, n: int) -> str:
    start = page.index(f"id=s{n}")
    end = page.index(f"id=s{n + 1}") if f"id=s{n + 1}" in page else page.index("Full text dashboard")
    return page[start:end]


def _jobs_in(fragment: str, tag: str) -> list[str]:
    return re.findall(rf'<{tag}[^>]*data-job="([^"]+)"', fragment)


@pytest.fixture
def workspace(tmp_path):
    decisions, meta, d = _built(tmp_path, JOBS)
    return decisions, d, dash.render_html(d)


# ------------------------------------------------------- membership and order

def test_today_and_on_hold_cards_are_exactly_their_placements_in_order(workspace):
    _, d, page = workspace
    assert _jobs_in(_section(page, 1), "article") == [i["job_id"] for i in d["sections"]["TODAY"]]
    assert _jobs_in(_section(page, 3), "article") == [i["job_id"] for i in d["sections"]["ON_HOLD"]]


def test_verify_first_rows_are_exactly_its_placement_in_existing_order_per_code(tmp_path):
    jobs = JOBS + [_sg("job_sg2", sid="d" * 32), _job("job_undisclosed", salary_min=None, salary_max=None, salary_unknown=True)]
    _, _, d = _built(tmp_path, jobs)
    shown = _jobs_in(_section(dash.render_html(d), 2), "tr")
    expected = d["sections"]["VERIFY_FIRST"]
    assert sorted(shown) == sorted(i["job_id"] for i in expected) and len(shown) == len(expected)
    for code in dash.VERIFY_LABEL:                                            # grouped by code, order kept within
        in_code = [i["job_id"] for i in expected if i["verification_code"] == code]
        assert [j for j in shown if j in in_code] == in_code


def test_full_view_keeps_the_existing_row_order(workspace):
    decisions, d, page = workspace
    body = page[page.index("<tbody id=full>"):page.index("</tbody>", page.index("<tbody id=full>"))]
    urls = re.findall(r'<tr data-[^>]*>.*?<a href="([^"]+)"', body)
    assert urls == [i["url"] for i in d["all"]] and len(urls) == len(decisions)


def test_collapsed_sections_still_hold_every_job_and_default_states_are_fixed(workspace):
    _, d, page = workspace
    for n in (1, 2, 3, 4, 5):
        assert f"id=s{n} open>" in page
    for n in (6, 7):
        assert f"id=s{n}>" in page and f"id=s{n} open" not in page
    assert _jobs_in(_section(page, 6), "li") == [i["job_id"] for i in d["sections"]["DO_NOT_INVEST"]]
    assert _jobs_in(_section(page, 7), "li") == [i["job_id"] for i in d["sections"]["HARD_FILTER_REJECTED"]]


# ------------------------------------------------------------ item content

def test_cards_show_why_blocker_and_effort_exactly_as_stored(workspace):
    _, d, page = workspace
    for i in d["sections"]["TODAY"] + d["sections"]["ON_HOLD"]:
        card = page[page.index(f'<article class=card data-job="{i["job_id"]}"'):]
        card = card[:card.index("</article>")]
        assert f"<p class=effort>Resume: {i['resume_effort']} · Research: {i['research_effort']}</p>" in card
        assert f"<b>Why</b> {i['why']}" in card.replace("&#x27;", "'")
        assert "<b>Blocker</b>" in card and f"Priority {i['priority']} · Next action {i['next_action']}" in card
    hold = next(i for i in d["sections"]["ON_HOLD"] if i["job_id"] == "job_hold_research")
    assert f"Hold because: {hold['hold_reason']}" in page


def test_action_labels_are_a_fixed_relabelling_of_placement_and_next_action(workspace):
    _, d, _ = workspace
    expected = {"TODAY": {"TAILOR_RESUME": "RESUME WORK"}, "VERIFY_FIRST": "VERIFY", "ON_HOLD": "HOLD",
                "DO_NOT_INVEST": "STOP", "HARD_FILTER_REJECTED": "STOP"}
    for i in d["all"]:
        want = expected[i["section"]]
        assert i["action"] == (want.get(i["next_action"], "APPLY / PREPARE") if isinstance(want, dict) else want)
    for action in ("APPLY", "NETWORK", "RESEARCH_EMPLOYER"):
        assert dash.action_label({"section": "TODAY", "next_action": action}) == "APPLY / PREPARE"


def test_workload_counts_existing_categories_without_weighting(workspace):
    _, d, page = workspace
    w = d["workload"]
    assert w == {"apply_prepare": d["counts"]["TODAY"], "verify": d["counts"]["VERIFY_FIRST"],
                 "resume_work": len(d["resume_work"]), "research_work": 1, "on_hold": d["counts"]["ON_HOLD"],
                 "stop": d["counts"]["DO_NOT_INVEST"], "rejected": d["counts"]["HARD_FILTER_REJECTED"]}
    assert [i["job_id"] for i in d["all"] if dash.needs_research_work(i)] == ["job_hold_research"]
    assert "Research work required <b>1</b>" in page
    assert "**Research work required:** 1" in dash.render_markdown(d)


# --------------------------------------------------------------- evidence

def test_evidence_status_comes_only_from_current_posting_coverage(tmp_path):
    jobs = [_sg("job_cur", sid=SID), _sg("job_old", sid="e" * 32), _sg("job_none", sid="f" * 32), _job("job_my")]
    live = [_record("job_cur", sid=SID), _record("job_old", sid=OLD_SID)]
    _, _, d = _built(tmp_path, jobs, live=live)
    status = {i["job_id"]: i["evidence_status"] for i in d["all"]}
    assert status == {"job_cur": "Current", "job_old": "Missing", "job_none": "Missing", "job_my": "Not applicable"}
    assert d["live"]["historical_eligible_records"] == 2 and d["live"]["covered"] == 1
    cur = next(i for i in d["all"] if i["job_id"] == "job_cur")
    assert cur["live"]["valid_through"] == "2026-10-01"                  # 2026-09-24 + 7-day window


# ------------------------------------------------------------------ delta

def test_decision_changes_show_exact_stored_before_and_after_values(tmp_path):
    prev_jobs = JOBS
    now = [_job("job_today", salary_min=5000.0, salary_max=7000.0)] + [j for j in JOBS if j.job_id != "job_today"]
    dl = _sequence(tmp_path, [(date(2026, 9, 25), prev_jobs)], now)
    prev = {d.job.job_id: d for d in load_run_dir(tmp_path / "r1")[0]}
    cur = {d.job.job_id: d for d in load_run_dir(tmp_path / "now")[0]}
    change = next(c for c in dl["decision_changed"] if c["job_id"] == "job_today")
    assert ("priority", prev["job_today"].application_priority, cur["job_today"].application_priority) in change["changes"]
    assert ("next action", prev["job_today"].next_action, cur["job_today"].next_action) in change["changes"]
    assert ("viability", prev["job_today"].application_viability.state,
            cur["job_today"].application_viability.state) in change["changes"]
    page = (tmp_path / "now" / "opportunities.html").read_text()
    assert "priority: HIGH → DO_NOT_INVEST" in page and "placement: TODAY → DO_NOT_INVEST" in page
    assert "<details class=chg><summary>Unchanged (5)</summary>" in page                      # collapsed by default


def test_workspace_text_states_no_causes_or_judgements(tmp_path):
    now = [_job("job_today", salary_min=5000.0, salary_max=7000.0)] + [j for j in JOBS if j.job_id != "job_today"]
    _sequence(tmp_path, [(date(2026, 9, 25), JOBS)], now)
    page = (tmp_path / "now" / "opportunities.html").read_text()
    head = page[:page.index("id=s5")]                                           # header, queues, changes
    text = re.sub(r"<[^>]+>", " ", head).replace("Hold because:", "")          # the existing Gate 3 hold-rule label
    assert not re.search(r"\b(improved|worsened|better|worse|likely to succeed|caused by|led to|because|best use)\b",
                         text, re.I)


# ------------------------------------------------------- search and safety

@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_search_is_scoped_to_the_full_view_and_unchanged(tmp_path):
    jobs = JOBS + [_job("job_other", company="Zeta Analytics")]
    decisions, _, d = _built(tmp_path, jobs)
    page = dash.render_html(d)
    assert "querySelectorAll('#full tr')" in page
    body = page[page.index("<tbody id=full>"):page.index("</tbody>", page.index("<tbody id=full>"))]
    assert body.count("<tr data-") == len(decisions) == page.count("<tr data-")
    assert _filter_with_node(page, {}, "zeta") == (1, len(decisions))
    assert _filter_with_node(page, {"market": "Indonesia"}, "co") == (1, len(decisions))


def test_rendering_adds_no_decision_fields_and_writes_only_presentation_files(tmp_path):
    run_dir = _write(tmp_path / "run", JOBS)
    before = {p.name: p.read_bytes() for p in run_dir.iterdir()}
    render_dashboard(run_dir)
    after = {p.name: p.read_bytes() for p in run_dir.iterdir()}
    assert set(after) - set(before) == {"dashboard.md", "opportunities.csv", "opportunities.html"}
    assert {n: after[n] for n in before} == before
    raw = after["decisions.json"].decode()
    for display_only in ('"action"', '"evidence_status"', '"workload"', '"seen_in_current_run"'):
        assert display_only not in raw
