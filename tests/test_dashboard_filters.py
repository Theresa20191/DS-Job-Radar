"""Dashboard job browser — view-only filters over stored decisions. One row per
Coach job_id across the comparable operational runs; each row shows the job's
latest stored decision and, separately, every placement it ever had. All runs
are SYNTHETIC TEST FIXTURES in tmp_path; nothing reads or writes production data
or the application log."""

import ast
import html
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from src.career_coach import application_log
from src.career_coach import dashboard as dash
from src.career_coach import run_archive as ra
from src.career_coach.operational import history_runs, load_run_dir, render_dashboard
from tests.test_career_coach_dashboard import JOBS, _job
from tests.test_dashboard_v1 import _archive, _record, _sg, _write

ROOT = Path(__file__).resolve().parent.parent
D5, D6, D7 = date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)
HOLD = dict(role_level="Principal", employer_clarity="RECRUITER")             # SELECTIVE + research HIGH -> HOLD
BLOCKED = dict(salary_min=5000.0, salary_max=7000.0)                          # V-3 -> DO_NOT_INVEST


def _without(job_id):
    return [j for j in JOBS if j.job_id != job_id]


def _render(tmp_path, runs, current_jobs, current_as_of=D7):
    """TEST FIXTURE: archive each (as_of, jobs, live) run, then render the current run."""
    root = tmp_path / "archive"
    for n, (as_of, jobs, live) in enumerate(runs, start=1):
        _archive(_write(tmp_path / f"r{n}", jobs, as_of=as_of, live=live), root)
    run_dir = _write(tmp_path / "now", current_jobs, as_of=current_as_of)
    return render_dashboard(run_dir, archive_root=root), run_dir, root


@pytest.fixture
def three_runs(tmp_path):
    """job_today: 5 Oct TODAY -> 6 Oct ON_HOLD -> 7 Oct DO_NOT_INVEST; job_old only on 1 Sep."""
    runs = [(date(2026, 9, 1), JOBS + [_job("job_old")], ()),
            (D5, JOBS, ()),
            (D6, _without("job_today") + [_job("job_today", **HOLD)], ())]
    d, run_dir, root = _render(tmp_path, runs, _without("job_today") + [_job("job_today", **BLOCKED)])
    return d, run_dir, root


def _rows(d):
    return {r["job_id"]: r for r in d["browser"]["rows"]}


# ---------------------------------------------------- latest decision and history

def test_latest_decision_is_the_latest_runs_stored_placement_and_one_row_per_job(three_runs):
    d, run_dir, _ = three_runs
    rows = _rows(d)
    assert len(d["browser"]["rows"]) == len(rows)                                  # one row per job_id
    today = rows["job_today"]
    assert today["path"] == [("2026-09-01", "TODAY"), ("2026-10-05", "TODAY"), ("2026-10-06", "ON_HOLD"),
                             ("2026-10-07", "DO_NOT_INVEST")]
    current = {x.job.job_id: x for x in load_run_dir(run_dir)[0]}
    assert today["decision"] == dash.primary_section(current["job_today"]) == "DO_NOT_INVEST"
    assert (today["priority"], today["next_action"]) == (current["job_today"].application_priority,
                                                          current["job_today"].next_action)
    assert today["first_run"] == "2026-09-01" and today["latest_run"] == "2026-10-07" and today["in_current_run"]
    for job_id, r in rows.items():                                                 # current run is authoritative
        if r["in_current_run"]:
            assert r["decision"] == dash.primary_section(current[job_id])


def test_a_job_absent_from_this_run_keeps_its_latest_stored_decision_and_never_enters_today(three_runs):
    d, _, _ = three_runs
    old = _rows(d)["job_old"]
    assert (old["decision"], old["latest_run"], old["in_current_run"], old["set"]) == ("TODAY", "2026-09-01", False, "historical")
    assert "job_old" not in [i["job_id"] for i in d["sections"]["TODAY"]]          # Today is this run only
    assert "job_today" not in [i["job_id"] for i in d["sections"]["TODAY"]]
    assert {i["job_id"] for i in d["all"]} == {r["job_id"] for r in d["browser"]["rows"] if r["in_current_run"]}


def test_same_day_runs_are_ordered_by_their_live_evidence_not_file_times(tmp_path):
    later_job = _sg("job_late", sid="d" * 32)
    early = (D6, JOBS + [_sg()], [_record(checked="2026-10-05")])
    late = (D6, JOBS + [_sg(), later_job], [_record(checked="2026-10-05"), _record(checked="2026-10-06")])
    root = tmp_path / "archive"
    _archive(_write(tmp_path / "late", late[1], as_of=D6, live=late[2]), root, mtime_ns=1)      # older file time
    _archive(_write(tmp_path / "early", early[1], as_of=D6, live=early[2]), root, mtime_ns=10**18)
    current = load_run_dir(_write(tmp_path / "now", JOBS, as_of=D7))[0][0].run.model_dump(mode="json")
    history = history_runs(current, root)
    assert len(history["runs"]) == 1 and history["notes"] == []
    assert "job_late" in {x.job.job_id for x in history["runs"][0]["decisions"]}            # the later evidence wins


def _corrupt(archive: Path) -> None:
    """TEST FIXTURE: break an archive's recorded hash (archived files are read-only)."""
    target = archive / "decisions.json"
    target.chmod(0o644)
    target.write_bytes(target.read_bytes() + b"\n")


def test_unverifiable_same_day_candidates_cannot_influence_the_choice(tmp_path):
    root = tmp_path / "archive"
    a = _archive(_write(tmp_path / "a", JOBS + [_sg(), _sg("job_late", sid="d" * 32)], as_of=D6,
                        live=[_record(checked="2026-10-05"), _record(checked="2026-10-06")]), root)
    b = _archive(_write(tmp_path / "b", JOBS + [_sg()], as_of=D6, live=[_record(checked="2026-10-05")]), root)
    from src.career_coach.operational import _same_day_key
    meta = lambda p: json.loads((p / "run_meta.json").read_text())                      # noqa: E731
    assert _same_day_key(a.name, meta(a)) > _same_day_key(b.name, meta(b))               # A would win on its metadata
    _corrupt(a)
    current = load_run_dir(_write(tmp_path / "now", JOBS, as_of=D7))[0][0].run.model_dump(mode="json")
    history = history_runs(current, root)
    assert [r["archive_id"] for r in history["runs"]] == [b.name]                        # B wins; A is excluded
    assert "job_late" not in {x.job.job_id for x in history["runs"][0]["decisions"]}
    assert any(n.startswith(f"{a.name} excluded: archive verification failed") for n in history["notes"])


def test_with_no_verifiable_earlier_run_the_history_is_unavailable_and_the_page_still_renders(tmp_path):
    root = tmp_path / "archive"
    _corrupt(_archive(_write(tmp_path / "a", JOBS, as_of=D6), root))
    run_dir = _write(tmp_path / "now", JOBS, as_of=D7)
    d = render_dashboard(run_dir, archive_root=root)
    assert d["browser"]["available"] is False and d["browser"]["runs"] == [("2026-10-07", "current run")]
    assert "Only this run is shown. No verified earlier comparable operational run is archived." in \
        html.unescape((run_dir / "opportunities.html").read_text())


def test_without_an_archive_the_browser_shows_only_this_run(tmp_path):
    run_dir = _write(tmp_path / "now", JOBS, as_of=D7)
    d = render_dashboard(run_dir)
    assert not d["browser"]["available"] and {r["job_id"] for r in d["browser"]["rows"]} == {j.job_id for j in JOBS}
    assert "Only this run is shown." in (run_dir / "opportunities.html").read_text()


# --------------------------------------------------------- filters (page script)

def _browse(page: str, dataset="recent", checked=(), when="", lo="", hi="") -> tuple[list[str], str]:
    """Runs the page's own browser filter script against its own rows in a minimal DOM."""
    rows = [dict(re.findall(r'data-([a-z]+)="([^"]*)"', attrs)) for attrs in re.findall(r"<tr class=hrow ([^>]*)>", page)]
    boxes = [{"f": f, "v": html.unescape(v), "checked": (f, html.unescape(v)) in checked}
             for f, v in re.findall(r'<input type=checkbox data-bfilter="([^"]+)" value="([^"]*)">', page)]
    assert dash.BROWSER_JS in page
    harness = f"""
const rowsData = {json.dumps(rows)}, boxesData = {json.dumps(boxes)};
const decode = s => s.replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#x27;/g,"'");
const hRows = rowsData.map(r => {{ const ds = {{}}; for (const [k,v] of Object.entries(r)) ds[k] = decode(v); return {{dataset: ds, hidden: false}}; }});
const hBoxes = boxesData.map(b => ({{dataset: {{bfilter: b.f}}, value: b.v, checked: b.checked, addEventListener(){{}}}}));
const hSets = ['recent', 'historical'].map(v => ({{value: v, checked: v === {json.dumps(dataset)}, addEventListener(){{}}}}));
const el = v => ({{value: v, addEventListener(){{}}}});
const ids = {{hb: {{querySelectorAll: () => hRows}}, hdate: el({json.dumps(when)}), hfrom: el({json.dumps(lo)}),
             hto: el({json.dumps(hi)}), hbcount: {{textContent: ''}}}};
global.document = {{getElementById: id => ids[id],
  querySelectorAll: s => s.startsWith('input[data-bfilter') ? hBoxes : s.startsWith('input[name=hset') ? hSets : []}};
{dash.BROWSER_JS}
console.log(JSON.stringify([hRows.filter(r => !r.hidden).map(r => r.dataset.job), ids.hbcount.textContent]));
"""
    out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, check=True).stdout
    shown, label = json.loads(out)
    return shown, label


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@pytest.fixture
def page(three_runs):
    d, run_dir, _ = three_runs
    return d, (run_dir / "opportunities.html").read_text()


def _ids(d, dataset="recent", **match):
    return sorted(r["job_id"] for r in d["browser"]["rows"] if r["set"] == dataset
                  and all((v in r[k]) if k == "ever" else r[k] == v for k, v in match.items()))


@needs_node
def test_default_shows_every_recent_job_and_counts_the_dataset(page):
    d, html_page = page
    shown, label = _browse(html_page)
    assert sorted(shown) == _ids(d) and label == f"Showing {len(shown)} of {len(_ids(d))} jobs"
    hist, _ = _browse(html_page, dataset="historical")
    assert hist == ["job_old"]


@needs_node
def test_decision_filter_single_and_multi_select(page):
    d, html_page = page
    shown, _ = _browse(html_page, checked=[("decision", "ON_HOLD")])
    assert sorted(shown) == _ids(d, decision="ON_HOLD")
    both, _ = _browse(html_page, checked=[("decision", "ON_HOLD"), ("decision", "DO_NOT_INVEST")])
    assert sorted(both) == sorted(_ids(d, decision="ON_HOLD") + _ids(d, decision="DO_NOT_INVEST"))     # OR within a filter


@needs_node
def test_latest_decision_and_ever_placed_are_different_queries(page):
    d, html_page = page
    latest_today, _ = _browse(html_page, checked=[("decision", "TODAY")])
    ever_today, _ = _browse(html_page, checked=[("ever", "TODAY")])
    assert "job_today" not in latest_today and "job_today" in ever_today
    assert sorted(ever_today) == _ids(d, ever="TODAY")


@needs_node
@pytest.mark.parametrize("key", ["employer", "resume", "source", "jobtype"])
def test_attribute_filters_match_the_stored_latest_value(page, key):
    d, html_page = page
    for value in sorted({r[key] for r in d["browser"]["rows"] if r["set"] == "recent"}):
        shown, _ = _browse(html_page, checked=[(key, value)])
        assert sorted(shown) == _ids(d, **{key: value})


@needs_node
def test_filters_combine_with_and_across_filters(page):
    d, html_page = page
    shown, _ = _browse(html_page, checked=[("decision", "ON_HOLD"), ("employer", "RECRUITER_POSTING")])
    assert sorted(shown) == _ids(d, decision="ON_HOLD", employer="RECRUITER_POSTING")


@needs_node
def test_date_filter_uses_the_latest_run_date_with_presets_and_custom_range(page):
    d, html_page = page
    cut = d["browser"]["cutoffs"]
    assert cut == {7: "2026-09-30", 30: "2026-09-07", 90: "2026-07-09"}           # relative to the run, not the clock
    assert _browse(html_page, dataset="historical", when=cut[30])[0] == []
    assert _browse(html_page, dataset="historical", when=cut[90])[0] == ["job_old"]
    assert _browse(html_page, dataset="historical", when="custom", lo="2026-09-01", hi="2026-09-01")[0] == ["job_old"]
    assert _browse(html_page, dataset="historical", when="custom", lo="2026-09-02")[0] == []


def test_rows_from_older_runs_are_labelled_with_their_own_run_date(page):
    d, html_page = page
    row = lambda job_id: re.search(rf'<tr class=hrow data-job="{job_id}"[^>]*>(.*?)</tr>', html_page).group(1)  # noqa: E731
    old = re.sub(r"<[^>]+>", " ", row("job_old"))
    assert "not in this run" in old and "as of the 2026-09-01 run" in old
    assert d["browser"]["as_of"] == "2026-10-07" and "2026-10-07" not in old            # never the current run's date
    current = re.sub(r"<[^>]+>", " ", row("job_today"))
    assert "not in this run" not in current and "as of the" not in current


DOM_HARNESS = r"""
class El {
  constructor(tag, attrs = {}, parent = null) {
    this.tagName = tag.toUpperCase(); this.attrs = {...attrs}; this.children = []; this.hidden = false;
    this.textContent = ''; this.value = attrs.value || ''; this.checked = !!attrs.checked; this.listeners = {};
    if (parent) parent.children.push(this);
    const self = this;
    this.dataset = new Proxy({}, {
      get: (_, k) => self.attrs['data-' + String(k).replace(/[A-Z]/g, c => '-' + c.toLowerCase())],
      set: (_, k, v) => { self.attrs['data-' + String(k).replace(/[A-Z]/g, c => '-' + c.toLowerCase())] = v; return true; }});
  }
  get className() { return this.attrs['class'] || ''; }
  addEventListener(type, f) { (this.listeners[type] = this.listeners[type] || []).push(f); }
  querySelectorAll(sel) { return descendants(this).filter(e => matches(e, sel)); }
}
const descendants = e => e.children.flatMap(c => [c, ...descendants(c)]);
function matches(e, sel) {
  const m = sel.match(/^([a-z]*)((?:\.[\w-]+)*)((?:\[[^\]]+\])*)$/i);
  if (!m) throw new Error('unsupported selector ' + sel);
  const [, tag, cls, attrs] = m;
  if (tag && e.tagName !== tag.toUpperCase()) return false;
  for (const c of cls.split('.').filter(Boolean)) if (!e.className.split(' ').includes(c)) return false;
  for (const a of (attrs.match(/\[[^\]]+\]/g) || [])) {
    const [k, v] = a.slice(1, -1).split('=');
    if (!(k in e.attrs)) return false;
    if (v !== undefined && e.attrs[k] !== v.replace(/['"]/g, '')) return false;
  }
  return true;
}
const body = new El('body');
const today = new El('article', {'class': 'card', 'data-job': 'job_today'}, body);
const full = new El('tbody', {id: 'full'}, new El('table', {}, body));
const fullRow = new El('tr', {'data-job': 'job_today', 'data-section': 'TODAY'}, full);
const decoy = new El('tr', {'class': 'hrow', 'data-job': 'decoy', 'data-set': 'recent', 'data-decision': 'TODAY',
                            'data-ever': 'TODAY', 'data-seen': '2026-10-07'}, full);
const hb = new El('tbody', {id: 'hb'}, new El('table', {}, body));
const row = (job, decision) => new El('tr', {'class': 'hrow', 'data-job': job, 'data-set': 'recent', 'data-decision': decision,
                                             'data-ever': decision, 'data-seen': '2026-10-07'}, hb);
const hToday = row('job_a', 'TODAY'), hHold = row('job_b', 'ON_HOLD');
const box = new El('input', {type: 'checkbox', 'data-bfilter': 'decision', value: 'ON_HOLD'}, body);
new El('input', {type: 'radio', name: 'hset', value: 'recent', checked: true}, body);
new El('input', {type: 'radio', name: 'hset', value: 'historical'}, body);
new El('select', {id: 'hdate'}, body); new El('input', {id: 'hfrom'}, body); new El('input', {id: 'hto'}, body);
const count = new El('span', {id: 'hbcount'}, body); new El('span', {'data-bsum': 'decision'}, body);
global.document = {getElementById: id => descendants(body).find(e => e.attrs.id === id),
                   querySelectorAll: s => descendants(body).filter(e => matches(e, s))};
const outside = () => JSON.stringify([today, fullRow, decoy].map(e => [e.hidden, e.attrs, e.textContent]));
const before = outside();
__SCRIPT__
const initial = [hToday.hidden, hHold.hidden, count.textContent];
box.checked = true; box.listeners.change.forEach(f => f());
console.log(JSON.stringify({initial, after: [hToday.hidden, hHold.hidden, count.textContent],
                            outsideUnchanged: outside() === before, decoyHidden: decoy.hidden}));
"""


@needs_node
def test_browser_script_changes_only_browser_rows_and_its_own_count():
    out = subprocess.run(["node", "-e", DOM_HARNESS.replace("__SCRIPT__", dash.BROWSER_JS)],
                         capture_output=True, text=True, check=True).stdout
    result = json.loads(out)
    assert result["initial"] == [False, False, "Showing 2 of 2 jobs"]
    assert result["after"] == [True, False, "Showing 1 of 2 jobs"]                       # the ON_HOLD filter applied
    assert result["outsideUnchanged"] and result["decoyHidden"] is False                # Today, full view, decoy untouched


def test_employer_and_resume_values_come_from_the_stored_decision(three_runs):
    d, run_dir, _ = three_runs
    current = {x.job.job_id: x for x in load_run_dir(run_dir)[0]}
    for r in d["browser"]["rows"]:
        if r["in_current_run"]:
            x = current[r["job_id"]]
            assert r["employer"] == (x.practical_constraint.components["employer"] if x.practical_constraint else "—")
            assert r["resume"] == (x.resume_effort or "—")
            assert r["jobtype"] == ((x.career_value.deciding_track if x.career_value else None) or "—")


# --------------------------------------------------------------- invariants

def test_rendering_the_browser_changes_no_decision_or_archive_and_is_deterministic(tmp_path):
    runs = [(D5, JOBS, ()), (D6, _without("job_today") + [_job("job_today", **HOLD)], ())]
    _, run_dir, root = _render(tmp_path, runs, JOBS)
    decisions_before = (run_dir / "decisions.json").read_bytes()
    archive_before = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    first = (run_dir / "opportunities.html").read_bytes()
    render_dashboard(run_dir, archive_root=root)
    assert (run_dir / "decisions.json").read_bytes() == decisions_before
    assert {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()} == archive_before
    assert all(ra.verify_archive(p) for p in root.iterdir())
    assert (run_dir / "opportunities.html").read_bytes() == first                   # byte-identical re-render


def test_browser_text_states_no_causes_or_judgements(page):
    _, html_page = page
    section = html_page[html_page.index("id=s8"):html_page.index("Full text dashboard")]
    text = re.sub(r"<[^>]+>", " ", section)
    assert not re.search(r"\b(improved|worsened|better|worse|likely to succeed|caused by|led to|because|best use)\b", text, re.I)
    assert not re.search(r"last[ _]seen", html_page, re.I)


@pytest.mark.parametrize("path", ["src/career_coach/dashboard.py", "src/career_coach/operational.py"])
def test_browser_code_never_touches_the_application_log(path):
    tree = ast.parse((ROOT / path).read_text())
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
              {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not {m for m in modules if m.startswith(("src.career_coach.application_log", "src.learning"))}
    assert "applications.jsonl" not in (ROOT / path).read_text()


def test_rendering_writes_no_application_log(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(application_log, "append_event", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(application_log, "load_events", lambda *a, **k: calls.append(a) or [])
    existed = application_log.DEFAULT_LOG.exists()
    _render(tmp_path, [(D5, JOBS, ())], JOBS)
    assert calls == [] and application_log.DEFAULT_LOG.exists() == existed
