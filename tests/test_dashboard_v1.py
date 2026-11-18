"""Dashboard Improvement V1: presentation only. Run status (health, live
coverage, daily delta), job freshness / posting identity / live evidence /
compensation display, keyword search and grouped job detail — none of which
changes a decision, a queue or an order. All runs here are SYNTHETIC TEST
FIXTURES written to tmp_path; production data is never read or written."""

import ast
import copy
import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from src.career_coach import dashboard as dash
from src.career_coach import run_archive as ra
from src.career_coach.operational import dashboard_context, load_run_dir, previous_run, render_dashboard, write_run
from src.models.career_coach import CareerCoachDecision, LiveCheckRecord
from tests.test_career_coach_dashboard import EMAIL, JOBS, _job, _run
from tests.test_run_archive import CONFIG_BYTES, PROFILE_BYTES

ROOT = Path(__file__).resolve().parent.parent
SID, OLD_SID = "a" * 32, "b" * 32
MCF_URL = "https://www.mycareersfuture.gov.sg/job/data/data-scientist-acme-{}"


def _sg(job_id="job_sg", sid=SID, description=EMAIL, **overrides):
    fields = dict(country="Singapore", city="Singapore", location_normalized="Singapore", sources=["mycareersfuture"],
                  salary_currency="SGD", canonical_url=MCF_URL.format(sid), source_job_ids=[sid])
    return _job(job_id, description, **{**fields, **overrides})


def _record(job_id="job_sg", sid=SID, checked="2026-09-24", **overrides):
    fields = dict(record_id=f"lc-{checked}-{sid}", job_id=job_id, source_job_id=sid, checked_on=checked, method="test",
                  page_reviewable="YES", email_in_jd="YES", posting_status="OPEN")
    fields.update(overrides)
    return LiveCheckRecord.model_validate(fields)


def _write(path: Path, jobs, as_of=date(2026, 9, 25), live=()) -> Path:
    decisions, meta = _run(jobs, live=live, as_of=as_of)
    write_run(path, decisions, meta)
    return path


def _archive(run_dir: Path, root: Path, mtime_ns: int | None = None) -> Path:
    path = ra.archive_run(run_dir, CONFIG_BYTES, PROFILE_BYTES, root)
    if mtime_ns is not None:
        os.utime(path / "archive.json", ns=(mtime_ns, mtime_ns))
    return path


def _live_file(path: Path, records) -> Path:
    path.write_text("".join(json.dumps(r.model_dump(mode="json")) + "\n" for r in records))
    return path


def _built(tmp_path, jobs, live=(), as_of=date(2026, 9, 25), archive_root=None):
    run_dir = _write(tmp_path / "run", jobs, as_of, live)
    decisions, meta = load_run_dir(run_dir)
    context = dashboard_context(decisions, archive_root, _live_file(tmp_path / "live.jsonl", live))
    return decisions, meta, dash.build(decisions, meta, context)


# ------------------------------------------------------- decision invariance

def test_context_changes_no_decision_queue_or_order(tmp_path):
    live = [_record()]
    decisions, meta, with_context = _built(tmp_path, JOBS + [_sg()], live)
    before = [d.model_dump(mode="json") for d in decisions]
    plain = dash.build(decisions, meta)
    assert [d.model_dump(mode="json") for d in decisions] == before                 # nothing mutated
    keys = ("job_id", "section", "viability", "priority", "next_action", "why_rule", "career_value", "resume_effort",
            "research_effort", "investment_fit", "verification_code")
    assert [{k: i[k] for k in keys} for i in plain["all"]] == [{k: i[k] for k in keys} for i in with_context["all"]]
    assert {s: [i["job_id"] for i in v] for s, v in plain["sections"].items()} == \
           {s: [i["job_id"] for i in v] for s, v in with_context["sections"].items()}
    assert [i["job_id"] for i in plain["resume_work"]] == [i["job_id"] for i in with_context["resume_work"]]


def test_rendering_with_context_leaves_the_run_files_untouched(tmp_path):
    run_dir = _write(tmp_path / "run", JOBS)
    _archive(run_dir, tmp_path / "archive")
    before = {n: (run_dir / n).read_bytes() for n in ("decisions.json", "run_meta.json")}
    archived = {p.name: p.read_bytes() for p in next((tmp_path / "archive").iterdir()).iterdir()}
    render_dashboard(run_dir, archive_root=tmp_path / "archive", live_path=tmp_path / "none.jsonl")
    assert {n: (run_dir / n).read_bytes() for n in before} == before
    assert {p.name: p.read_bytes() for p in next((tmp_path / "archive").iterdir()).iterdir()} == archived


# ---------------------------------------------------------------- run health

def test_run_health_reports_the_verified_archive(tmp_path):
    run_dir = _write(tmp_path / "run", JOBS)
    archive = _archive(run_dir, tmp_path / "archive")
    d = render_dashboard(run_dir, archive_root=tmp_path / "archive", live_path=None)
    assert d["health"]["archive"] == {"status": "VERIFIED", "archive_id": archive.name}
    md = (run_dir / "dashboard.md").read_text()
    assert f"archive: VERIFIED (`{archive.name}`)" in md and "Jobs evaluated: 6" in md


def test_an_unarchived_run_is_reported_as_not_archived(tmp_path):
    run_dir = _write(tmp_path / "run", JOBS)
    (tmp_path / "archive").mkdir()
    assert render_dashboard(run_dir, archive_root=tmp_path / "archive")["health"]["archive"]["status"] == "NOT ARCHIVED"


# --------------------------------------------------------------- daily delta

def test_no_previous_run_is_reported_honestly(tmp_path):
    run_dir = _write(tmp_path / "run", JOBS)
    d = render_dashboard(run_dir, archive_root=tmp_path / "empty")
    assert d["delta"] == {"available": False, "reason": "No run archive was given to the dashboard."}
    (tmp_path / "archive").mkdir()
    d = render_dashboard(run_dir, archive_root=tmp_path / "archive")
    assert "No comparable previous operational run available. No earlier operational run is archived." in \
           (run_dir / "dashboard.md").read_text()
    assert "No comparable previous operational run available." in (run_dir / "opportunities.html").read_text()


def test_an_incompatible_previous_run_is_excluded_with_its_reason(tmp_path):
    run_dir = _write(tmp_path / "run", JOBS, as_of=date(2026, 9, 26))
    current = load_run_dir(run_dir)[0][0].run.model_dump(mode="json")
    fake = tmp_path / "archive" / "2026-09-25_OPERATIONAL_0000000000000000"
    fake.mkdir(parents=True)
    (fake / "archive.json").write_text(json.dumps({
        "archive_id": fake.name, "run_kind": "OPERATIONAL", "as_of_date": "2026-09-25", "coach_version": "career_coach_v1.2",
        "input_hashes": {**current["input_hashes"], "config": "0" * 64}}))
    prev = previous_run(current, tmp_path / "archive")
    assert prev == {"status": "UNAVAILABLE",
                    "reason": f"{fake.name} excluded: its Coach config hash differs from this run's."}


def test_the_previous_run_is_the_latest_earlier_operational_run_most_recently_archived(tmp_path):
    root = tmp_path / "archive"
    early = _archive(_write(tmp_path / "r1", JOBS, as_of=date(2026, 9, 24)), root, mtime_ns=1)
    first = _archive(_write(tmp_path / "r2", JOBS[:4], as_of=date(2026, 9, 25)), root, mtime_ns=2)
    final = _archive(_write(tmp_path / "r3", JOBS, as_of=date(2026, 9, 25), live=[_record("job_verify", checked="2026-09-25")]),
                     root, mtime_ns=3)
    same_day = _archive(_write(tmp_path / "r4", JOBS[:2], as_of=date(2026, 9, 26)), root, mtime_ns=4)
    current = load_run_dir(_write(tmp_path / "now", JOBS[:5], as_of=date(2026, 9, 26)))[0][0].run.model_dump(mode="json")
    prev = previous_run(current, root)
    assert prev["status"] == "COMPARED" and prev["archive_id"] == final.name and prev["as_of"] == "2026-09-25"
    assert same_day.name != prev["archive_id"] and first.name != prev["archive_id"]
    assert prev["earlier_job_ids"] == {j.job_id for j in JOBS}                      # from the other earlier runs
    assert early.exists()


def test_delta_counts_and_reappearance_are_descriptive_and_deterministic(tmp_path):
    root = tmp_path / "archive"
    _archive(_write(tmp_path / "r1", JOBS, as_of=date(2026, 9, 23)), root, mtime_ns=1)           # job_dni seen here
    prev_jobs = [j for j in JOBS if j.job_id != "job_dni"] + [_job("job_gone")]
    _archive(_write(tmp_path / "r2", prev_jobs, as_of=date(2026, 9, 24)), root, mtime_ns=2)
    now = [_job("job_today", salary_min=5000.0, salary_max=7000.0)] + \
          [j for j in JOBS if j.job_id != "job_today"] + [_job("job_new")]
    run_dir = _write(tmp_path / "now", now, as_of=date(2026, 9, 25))
    d1 = render_dashboard(run_dir, archive_root=root)
    md1 = (run_dir / "dashboard.md").read_text()
    d2 = render_dashboard(run_dir, archive_root=root)
    assert d1["delta"] == d2["delta"] and (run_dir / "dashboard.md").read_text() == md1
    dl = d1["delta"]
    assert [c["job_id"] for c in dl["new"]] == ["job_new"]
    assert [c["job_id"] for c in dl["reappeared"]] == ["job_dni"]
    assert [c["job_id"] for c in dl["removed"]] == ["job_gone"]
    assert [(c["job_id"], c["before"], c["after"]) for c in dl["priority_changed"]] == \
           [("job_today", "HIGH", "DO_NOT_INVEST")]
    assert [(c["job_id"], c["before"], c["after"]) for c in dl["placement_changed"]] == \
           [("job_today", "TODAY", "DO_NOT_INVEST")]
    assert dl["unchanged"] == 4 and dl["posting_changed"] == []
    assert "| Reappeared | 1 |" in md1 and "| Decision unchanged | 4 |" in md1
    changes = md1[md1.index("### Changes since"):md1.index("## What to work on today")]
    assert not re.search(r"\b(better|worse|improved|worsened|caused by|led to)\b", changes, re.I)


def test_historical_replays_are_never_comparison_runs(tmp_path):
    root = tmp_path / "archive"
    decisions, meta = _run(JOBS, as_of=date(2026, 9, 24))
    replay = [d.model_copy(update={"run": d.run.model_copy(update={"run_kind": "HISTORICAL_REPLAY"})}) for d in decisions]
    write_run(tmp_path / "replay", replay, meta)
    _archive(tmp_path / "replay", root)
    current = load_run_dir(_write(tmp_path / "now", JOBS))[0][0].run.model_dump(mode="json")
    assert previous_run(current, root)["status"] == "UNAVAILABLE"


# ------------------------------------------------------------- live coverage

def test_coverage_denominator_is_the_current_sg_mcf_passed_population(tmp_path):
    jobs = JOBS + [_sg(), _sg("job_sg_fallback", sid="c" * 32)]                       # job_verify is SG MCF too
    _, _, d = _built(tmp_path, jobs, live=[_record()])
    live = d["live"]
    assert (live["sg_passed"], live["covered"], live["missing"], live["missing_no_record"]) == (3, 1, 2, 2)
    assert live["historical_eligible_records"] == 1 and live["latest_observation"] == "2026-09-24"
    assert live["valid_through"] == "2026-10-01"
    by_id = {i["job_id"]: i for i in d["all"]}
    assert by_id["job_sg_fallback"]["live"]["state"] == "NONE"
    md = dash.render_markdown(d)
    assert "Coverage: 1 / 3" in md and "Historical eligible live records: 1" in md
    assert "live checks used" not in md


def test_an_observation_of_an_earlier_posting_is_not_current_coverage(tmp_path):
    _, _, d = _built(tmp_path, [_sg()], live=[_record(sid=OLD_SID)])
    i = d["all"][0]
    assert i["live"]["state"] == "OTHER_POSTING" and i["live"]["observed_posting_id"] == OLD_SID
    assert i["posting_id"] == SID and (d["live"]["covered"], d["live"]["missing_other_posting"]) == (0, 1)
    assert f"not the current posting ID {SID} — not current coverage" in dash.render_markdown(d)


def test_live_evidence_shows_the_observed_values_and_current_posting(tmp_path):
    _, _, d = _built(tmp_path, [_sg()], live=[_record(email_in_jd="YES", posting_status="CLOSED")])
    i = d["all"][0]
    assert (i["live"]["checked_on"], i["live"]["observed_posting_id"], i["live"]["page_reviewable"],
            i["live"]["email_in_jd"], i["live"]["posting_status"]) == ("2026-09-24", SID, "YES", "YES", "CLOSED")
    assert i["posting_id"] == SID and i["source"] == "MyCareersFuture"
    assert f"checked 2026-09-24 · posting {SID} (current posting) · page reviewable YES · email in JD YES · status CLOSED" \
           in dash.render_markdown(d)


def test_missing_live_evidence_is_never_shown_as_an_observation(tmp_path):
    _, _, d = _built(tmp_path, [_sg(description=EMAIL.replace("jobs@acme.example.com", "us"))], live=[])
    i = d["all"][0]
    assert i["live"]["state"] == "NONE" and "email_in_jd" not in i["live"]
    text = dash._live_text(i["live"])
    assert text.startswith("NONE — decision uses the stored-JD fallback") and "email in JD" not in text


def test_without_the_record_file_observed_values_stay_unknown(tmp_path):
    run_dir = _write(tmp_path / "run", [_sg()], live=[_record()])
    decisions, meta = load_run_dir(run_dir)
    d = dash.build(decisions, meta, dashboard_context(decisions, None, tmp_path / "missing.jsonl"))
    live = d["all"][0]["live"]
    assert live["checked_on"] == "2026-09-24" and live["page_reviewable"] is None and live["state"] == "CURRENT"
    assert "observed values not available in this render" in dash.render_markdown(d)


# ------------------------------------------------- freshness and compensation

def test_posted_date_and_age_are_never_invented(tmp_path):
    jobs = [_job("job_dated"), _job("job_undated", posted_date=None)]
    _, _, d = _built(tmp_path, jobs)
    by_id = {i["job_id"]: i for i in d["all"]}
    assert (by_id["job_dated"]["posted_date"], by_id["job_dated"]["age_days"]) == ("2026-09-15", 10)
    assert (by_id["job_undated"]["posted_date"], by_id["job_undated"]["age_days"]) == (None, None)
    assert dash._posted(by_id["job_undated"]) == "not recorded"
    assert dash._age_days("15/09/2026", date(2026, 9, 25)) is None
    assert by_id["job_dated"]["seen_in_current_run"] == "2026-09-25"


def test_missing_salary_stays_missing(tmp_path):
    jobs = [_sg(), _job("job_undisclosed", salary_min=None, salary_max=None, salary_unknown=True),
            _job("job_low", salary_min=5000.0, salary_max=7000.0)]
    _, _, d = _built(tmp_path, jobs, live=[_record()])
    by_id = {i["job_id"]: i for i in d["all"]}
    assert by_id["job_sg"]["compensation"].endswith("posted pay is not recorded in the run output")
    assert by_id["job_undisclosed"]["compensation"] == "Hard floor MYR 7500/month: salary not disclosed"
    assert by_id["job_undisclosed"]["compensation_status"] == "VERIFY"
    assert by_id["job_low"]["compensation_status"] == "BELOW HARD FLOOR"
    assert "12000" not in by_id["job_sg"]["compensation"]                       # nothing reconstructed


# ----------------------------------------------------------------- HTML / UX

def test_html_keeps_one_row_per_job_with_grouped_detail_and_queue_cards(tmp_path):
    decisions, _, d = _built(tmp_path, JOBS)
    page = dash.render_html(d)
    assert page.count("<tr data-") == len(decisions) and page.count("<details class=detail>") >= len(decisions)
    for heading in ("<h4>Decision</h4>", "<h4>Readiness</h4>", "<h4>Verification</h4>", "<h4>Job facts</h4>"):
        assert heading in page                                 # V2: effort sits in Readiness
    assert page.count("<article class=card data-job=") == d["counts"]["TODAY"] + d["counts"]["ON_HOLD"]
    assert page.count("<tr class=vrow data-job=") == d["counts"]["VERIFY_FIRST"]   # V2: verify-first rows


def test_html_keeps_dark_mode_and_narrow_layout(tmp_path):
    page = dash.render_html(_built(tmp_path, JOBS)[2])
    assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in page
    assert "@media (prefers-color-scheme:dark)" in page and "@media (max-width:700px)" in page
    assert '<input id="q" type="search"' in page and 'id="count"' in page


def _filter_with_node(page: str, select_values: dict, query: str) -> tuple[int, int]:
    """Runs the page's own filter script against its own rows in a minimal DOM."""
    rows = [dict(re.findall(r'data-([a-z_-]+)="([^"]*)"', attrs)) for attrs in re.findall(r"<tr (data-[^>]*)>", page)]
    script = re.search(r"<script>(.*)</script>", page, re.S).group(1)
    harness = f"""
const rowsData = {json.dumps(rows)}; const selects = {json.dumps(select_values)};
const decode = s => s.replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#x27;/g,"'");
const camel = k => k.replace(/[-_](\\w)/g,(m,c)=>c.toUpperCase());
const hRows = rowsData.map(r => {{ const ds = {{}}; for (const [k,v] of Object.entries(r)) ds[camel(k)] = decode(v); return {{dataset: ds, hidden: false}}; }});
const hSels = Object.entries(selects).map(([f,v]) => ({{dataset: {{filter: f}}, value: v, addEventListener(){{}}}}));
const hQ = {{value: {json.dumps(query)}, addEventListener(){{}}}}, hCount = {{textContent: ''}};
global.document = {{ querySelectorAll: s => s.startsWith('select') ? hSels : hRows, getElementById: id => id === 'q' ? hQ : hCount }};
{{
{script}
}}
console.log(JSON.stringify([hRows.filter(r => !r.hidden).length, hCount.textContent]));
"""
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True).stdout
    shown, label = json.loads(out)
    assert label == f"Showing {shown} of {len(rows)} jobs"
    return shown, len(rows)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_keyword_search_is_case_insensitive_and_cumulative_with_filters(tmp_path):
    jobs = JOBS + [_job("job_other", company="Zeta Analytics", country="Malaysia")]
    decisions, _, d = _built(tmp_path, jobs)
    page = dash.render_html(d)
    assert _filter_with_node(page, {}, "") == (len(decisions), len(decisions))
    assert _filter_with_node(page, {}, "ZETA") == (1, len(decisions))
    assert _filter_with_node(page, {}, "co job_hold") == (2, len(decisions))                    # every term must match
    assert _filter_with_node(page, {"market": "Malaysia"}, "zeta")[0] == 1
    assert _filter_with_node(page, {"market": "Singapore"}, "zeta")[0] == 0                       # filters AND search
    assert _filter_with_node(page, {"section": "ON_HOLD"}, "")[0] == 2
    assert dash.render_html(d) == page                                                            # search never reorders


def test_search_text_holds_only_displayed_fields(tmp_path):
    i = next(i for i in _built(tmp_path, JOBS)[2]["all"] if i["job_id"] == "job_today")
    text = dash.search_text(i)
    assert text == text.lower() and "co job_today" in text and "tailor_resume" in text


# ------------------------------------------------------------- boundaries

FORBIDDEN = ("src.scoring", "src.transition", "src.reporting", "src.strategy", "src.models.ptf", "src.models.transition",
             "src.models.scored_job", "src.models.strategy", "src.pipeline.orchestrator", "src.calibration", "src.learning")


@pytest.mark.parametrize("path", ["src/career_coach/dashboard.py", "src/career_coach/operational.py",
                                  "scripts/career_coach_dashboard.py"])
def test_dashboard_code_imports_no_legacy_or_learning_layer(path):
    tree = ast.parse((ROOT / path).read_text())
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
              {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not {m for m in modules if m.startswith(FORBIDDEN)}


def test_dashboard_module_reads_no_files_or_clock():
    source = (ROOT / "src" / "career_coach" / "dashboard.py").read_text()
    for needle in ("open(", "read_text", "write_text", "date.today", "datetime.now", "requests"):
        assert needle not in source, needle


# --------------------------------------------- V1.1 acceptance: semantics

def test_seen_in_current_run_replaces_the_misleading_last_seen_label(tmp_path):
    _, _, d = _built(tmp_path, JOBS)
    md, page, table = dash.render_markdown(d), dash.render_html(d), dash.render_csv(d)
    for text in (md, page, table):
        assert not re.search(r"last[ _]seen", text, re.I)
    assert "seen in current run 2026-09-25" in md and "<dt>Seen in current run</dt><dd>2026-09-25</dd>" in page
    assert "seen_in_current_run" in table.splitlines()[0].split(",")
    assert all(i["seen_in_current_run"] == "2026-09-25" and "last_seen" not in i for i in d["all"])


def _sequence(tmp_path, runs, current_jobs, current_as_of=date(2026, 9, 26)):
    """TEST FIXTURE: archive each (as_of, jobs) in order, then render the current run."""
    root = tmp_path / "archive"
    for n, (as_of, jobs) in enumerate(runs, start=1):
        _archive(_write(tmp_path / f"r{n}", jobs, as_of=as_of), root, mtime_ns=n)
    return render_dashboard(_write(tmp_path / "now", current_jobs, as_of=current_as_of), archive_root=root)["delta"]


def _ids(dl, key):
    return [c["job_id"] for c in dl[key]]


def test_case1_present_earlier_absent_previously_present_now_is_reappeared(tmp_path):
    dl = _sequence(tmp_path, [(date(2026, 9, 24), [_job("job_x"), _job("job_keep")]), (date(2026, 9, 25), [_job("job_keep")])],
                   [_job("job_x"), _job("job_keep")])
    assert _ids(dl, "reappeared") == ["job_x"] and _ids(dl, "new") == [] and _ids(dl, "removed") == []


def test_case2_never_archived_before_is_new(tmp_path):
    dl = _sequence(tmp_path, [(date(2026, 9, 24), [_job("job_keep")]), (date(2026, 9, 25), [_job("job_keep")])],
                   [_job("job_x"), _job("job_keep")])
    assert _ids(dl, "new") == ["job_x"] and _ids(dl, "reappeared") == []


def test_case3_same_job_with_a_different_posting_is_posting_changed_only(tmp_path):
    dl = _sequence(tmp_path, [(date(2026, 9, 25), [_sg("job_sg", sid=OLD_SID)])], [_sg("job_sg", sid=SID)])
    assert [(c["job_id"], c["before"], c["after"]) for c in dl["posting_changed"]] == [("job_sg", OLD_SID, SID)]
    assert _ids(dl, "new") == _ids(dl, "reappeared") == _ids(dl, "removed") == []


def test_case4_present_in_the_previous_run_is_neither_new_nor_reappeared(tmp_path):
    dl = _sequence(tmp_path, [(date(2026, 9, 24), [_job("job_x")]), (date(2026, 9, 25), [_job("job_x")])], [_job("job_x")])
    assert _ids(dl, "new") == _ids(dl, "reappeared") == _ids(dl, "posting_changed") == [] and dl["unchanged"] == 1


def test_removed_is_present_previously_and_absent_now(tmp_path):
    dl = _sequence(tmp_path, [(date(2026, 9, 25), [_job("job_x"), _job("job_gone")])], [_job("job_x")])
    assert _ids(dl, "removed") == ["job_gone"] and dl["unchanged"] == 1


def test_only_comparable_earlier_archives_make_a_job_reappeared(tmp_path):
    root = tmp_path / "archive"
    _archive(_write(tmp_path / "prev", [_job("job_keep")], as_of=date(2026, 9, 25)), root)
    current = load_run_dir(_write(tmp_path / "now", [_job("job_x"), _job("job_keep")], as_of=date(2026, 9, 26)))
    run = current[0][0].run.model_dump(mode="json")
    other = root / "2026-09-24_OPERATIONAL_1111111111111111"                    # an earlier run under another config
    other.mkdir()
    (other / "archive.json").write_text(json.dumps({
        "archive_id": other.name, "run_kind": "OPERATIONAL", "as_of_date": "2026-09-24", "coach_version": "career_coach_v1.2",
        "input_hashes": {**run["input_hashes"], "config": "0" * 64}}))
    (other / "decisions.json").write_text(json.dumps([{"job": {"job_id": "job_x"}}]))
    prev = previous_run(run, root)
    assert prev["status"] == "COMPARED" and "job_x" not in prev["earlier_job_ids"]
    dl = dash.delta(current[0], prev)
    assert _ids(dl, "new") == ["job_x"] and _ids(dl, "reappeared") == []


def test_delta_text_states_only_observable_changes(tmp_path):
    _sequence(tmp_path, [(date(2026, 9, 24), [_job("job_x")]), (date(2026, 9, 25), [_job("job_keep")])],
              [_job("job_x"), _job("job_keep")])
    md = (tmp_path / "now" / "dashboard.md").read_text()
    changes = md[md.index("### Changes since"):md.index("## What to work on today")]
    assert "- Reappeared: Co job_x · Data Scientist (`job_x`)" in changes
    assert not re.search(r"\b(because|reposted|renewed|reopened|returned|caused|led to)\b", changes, re.I)


@pytest.mark.parametrize("url, expected", [
    (MCF_URL.format(SID), SID),                                                   # normal MyCareersFuture URL
    (MCF_URL.format(SID) + "/", SID),
    (MCF_URL.format(OLD_SID), OLD_SID),                                           # a changed posting URL
    ("https://www.mycareersfuture.gov.sg/job/data/data-scientist-acme", None),    # no usable identifier
    ("https://www.mycareersfuture.gov.sg/job/data/x-" + "A" * 32, None),          # not the lower-case 32-hex form
    ("https://example.test/job/" + SID, None),                                    # not a MyCareersFuture posting
    ("", None),                                                                   # missing URL
])
def test_posting_id_comes_only_from_the_jobs_own_url(url, expected):
    assert dash.posting_id(url) == expected


def test_a_missing_posting_id_and_url_are_shown_as_not_recorded(tmp_path):
    _, _, d = _built(tmp_path, [_job("job_no_id", canonical_url="https://www.mycareersfuture.gov.sg/job/data/data-scientist-acme")])
    i = d["all"][0]
    assert i["posting_id"] is None
    assert "canonical posting ID not recorded" in dash.render_markdown(d)
    assert "<dt>Canonical posting ID</dt><dd>not recorded</dd>" in dash.render_html(d)
    blank = dict(i, url="", source=dash._source(""))
    assert blank["source"] == "not recorded" and "<dt>Job URL</dt><dd>not recorded</dd>" in dash._detail_html(blank)


def test_a_stale_live_check_id_is_never_the_current_posting_id(tmp_path):
    _, _, d = _built(tmp_path, [_sg(sid=SID)], live=[_record(sid=OLD_SID)])
    i = d["all"][0]
    assert i["posting_id"] == i["live"]["current_posting_id"] == SID and i["live"]["observed_posting_id"] == OLD_SID


def test_a_job_without_a_current_posting_id_is_never_covered(tmp_path):
    job = _sg(canonical_url="https://www.mycareersfuture.gov.sg/job/data/data-scientist-acme")
    _, _, d = _built(tmp_path, [job], live=[_record()])
    assert d["all"][0]["live"]["state"] == "OTHER_POSTING" and d["live"]["covered"] == 0


def test_older_posting_evidence_counts_as_history_not_as_current_coverage(tmp_path):
    """The older posting has a valid eligible record; the current posting has none."""
    _, meta, d = _built(tmp_path, [_sg(sid=SID)], live=[_record(sid=OLD_SID, checked="2026-09-24")])
    live = d["live"]
    assert live["historical_eligible_records"] == len(meta["live_checks_used"]) == 1
    assert (live["sg_passed"], live["covered"], live["missing"], live["missing_other_posting"]) == (1, 0, 1, 1)
    assert live["latest_observation"] is None and live["valid_through"] is None       # no current check to date
    md = dash.render_markdown(d)
    assert "- Coverage: 0 / 1" in md and "- Historical eligible live records: 1" in md
    assert "Current SG live coverage</dt><dd>0 / 1" in dash.render_html(d)
