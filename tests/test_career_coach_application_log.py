"""Career Coach Gate 4: `coach_v1` application logging — append-only factual
history, explicit candidate events only, snapshots copied from a saved
OPERATIONAL run, identity checks (B-4), dispatch by schema_version with the
legacy `application_log_v1` format, and no legacy-layer dependency."""

import ast
import copy
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.career_coach import application_log as log
from src.career_coach import dashboard as dash
from src.career_coach.engine import Coach
from src.career_coach.inputs import build_population
from src.config import load_career_coach, load_profile, load_scoring
from src.models.processed_job import ProcessedJob

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_LEGACY = ROOT / "tests" / "fixtures" / "application_log_v1_golden.jsonl"
WORK = ("Responsibilities:\nBuild credit risk models to support lending decisions across the portfolio.\n"
        "Develop scorecards and validate credit risk models with the model risk team.\n")


def _job(job_id, **overrides) -> ProcessedJob:
    fields = dict(job_id=job_id, company=f"Co {job_id}", company_normalized=f"co {job_id}", job_title="Data Scientist",
                  country="Malaysia", city="Kuala Lumpur", location_normalized="Kuala Lumpur, Malaysia",
                  role_family="Data Scientist", role_level="Mid/Unspecified", role_type="TRUE_DS", sources=["hiredly"],
                  canonical_url=f"https://example.test/{job_id}", description_raw=WORK, salary_min=9000.0,
                  salary_max=12000.0, salary_currency="MYR", salary_period="Monthly", salary_unknown=False,
                  job_age_days=5, employer_clarity="DIRECT_EMPLOYER", posted_date="2026-09-15")
    fields.update(overrides)
    return ProcessedJob(**fields)


JOBS = [_job("job_a"), _job("job_b", role_level="Principal"), _job("job_rejected", country="Indonesia", city="Jakarta",
                                                                      location_normalized="Jakarta, Indonesia")]


def _write_run(path: Path, jobs=JOBS, run_kind="OPERATIONAL", as_of=date(2026, 9, 25)) -> Path:
    coach = Coach(copy.deepcopy(load_profile()), load_career_coach(), set())
    decisions, meta = coach.run(build_population(list(jobs), load_scoring()), as_of, run_kind, [], "raw")
    path.mkdir(parents=True, exist_ok=True)
    (path / "decisions.json").write_text(json.dumps([d.model_dump(mode="json") for d in decisions], sort_keys=True))
    (path / "run_meta.json").write_text(json.dumps(meta, sort_keys=True))
    return path


@pytest.fixture
def run(tmp_path):
    return _write_run(tmp_path / "run")


@pytest.fixture
def logfile(tmp_path):
    return tmp_path / "applications.jsonl"


def _decide(run, logfile, job_id, decision, when, **kw):
    event = log.decision_event(run, job_id, decision, when, log=logfile, **kw)
    log.append_event(event, logfile)
    return event


def _outcome(run, logfile, job_id, outcome, when, notes=""):
    event = log.outcome_event(run, job_id, outcome, when, notes, log=logfile)
    log.append_event(event, logfile)
    return event


# -------------------------------------------------------------- core logging

def test_append_load_round_trip_and_schema_version(run, logfile):
    d = _decide(run, logfile, "job_a", "SAVED", "2026-09-26", declared_effort="MEDIUM", notes="Applied with DS-focused resume")
    o = _outcome(run, logfile, "job_a", "INTERVIEW", "2026-09-30", "Recruiter reached out")
    assert log.load_events(logfile) == [d, o]
    assert all(e.schema_version == "coach_v1" for e in (d, o))
    assert (d.decision_date, o.outcome_date) == ("2026-09-26", "2026-09-30")
    assert (d.notes, o.notes) == ("Applied with DS-focused resume", "Recruiter reached out")


def test_every_append_adds_exactly_one_line_and_never_rewrites(run, logfile):
    previous = b""
    for n, (decision, when) in enumerate([("SAVED", "2026-09-26"), ("APPLIED", "2026-09-27"),
                                          ("APPLIED", "2026-09-27")], start=1):
        _decide(run, logfile, "job_a", decision, when)
        data = logfile.read_bytes()
        assert data.startswith(previous) and len(data.splitlines()) == n
        previous = data
    _outcome(run, logfile, "job_a", "INTERVIEW", "2026-09-30")
    assert logfile.read_bytes().startswith(previous) and len(logfile.read_bytes().splitlines()) == 4


def test_missing_and_empty_logs_both_mean_zero_events(tmp_path):
    """The log is created lazily by the first valid event: a missing file and an
    empty file are the same zero-event state, and reading never creates it."""
    missing, empty = tmp_path / "missing.jsonl", tmp_path / "empty.jsonl"
    empty.write_text("")
    for path in (missing, empty):
        events = log.load_events(path)
        assert events == [] and log.reconstruct(events) == {} and log.voids(events) == {}
    assert not missing.exists() and empty.read_bytes() == b""


def test_dates_must_be_iso_days():
    with pytest.raises(ValidationError):
        log.CoachOutcomeEvent(job_id="job_a", outcome="APPLIED", outcome_date="2026-09-26T10:00")


# ------------------------------------------------------------------ lifecycle

def test_full_lifecycle_reconstructs_to_offer(run, logfile):
    _decide(run, logfile, "job_a", "SAVED", "2026-09-26")
    for stage, when in [("APPLIED", "2026-09-27"), ("INTERVIEW", "2026-10-01"), ("FINAL_ROUND", "2026-10-05"),
                        ("OFFER", "2026-10-09")]:
        _outcome(run, logfile, "job_a", stage, when)
    h = log.reconstruct(log.load_events(logfile))["job_a"]
    assert (h.current_decision, h.current_outcome) == ("SAVED", "OFFER")
    assert [o[2] for o in h.outcomes] == ["APPLIED", "INTERVIEW", "FINAL_ROUND", "OFFER"]


def test_applied_then_rejected(run, logfile):
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-27")
    _outcome(run, logfile, "job_a", "REJECTED", "2026-10-02")
    h = log.reconstruct(log.load_events(logfile))["job_a"]
    assert h.current_outcome == "REJECTED" and [o[2] for o in h.outcomes] == ["APPLIED", "REJECTED"]


@pytest.mark.parametrize("decision", log.DECISIONS)
def test_every_decision_is_recorded_and_creates_no_outcome(run, logfile, decision):
    _decide(run, logfile, "job_a", decision, "2026-09-26")
    h = log.reconstruct(log.load_events(logfile))["job_a"]
    assert h.current_decision == decision and h.outcomes == [] and h.current_outcome == "NOT_STARTED"


@pytest.mark.parametrize("outcome", log.OUTCOMES)
def test_every_outcome_is_recorded(run, logfile, outcome):
    _outcome(run, logfile, "job_a", outcome, "2026-09-26")
    assert log.reconstruct(log.load_events(logfile))["job_a"].current_outcome == outcome


@pytest.mark.parametrize("bad", ["SAVED", "SKIPPED", "DEFERRED", "EXPIRED", "NOT_STARTED"])
def test_decisions_are_not_outcomes(run, logfile, bad):
    with pytest.raises(ValidationError):
        log.outcome_event(run, "job_a", bad, "2026-09-26", log=logfile)


def test_duplicates_stay_explicit(run, logfile):
    _decide(run, logfile, "job_a", "SAVED", "2026-09-26")
    _decide(run, logfile, "job_a", "APPLIED", "2026-09-27")
    _decide(run, logfile, "job_a", "APPLIED", "2026-09-27")
    _outcome(run, logfile, "job_a", "INTERVIEW", "2026-09-30")
    _outcome(run, logfile, "job_a", "INTERVIEW", "2026-09-30")
    h = log.reconstruct(log.load_events(logfile))["job_a"]
    assert [d[2] for d in h.decisions] == ["SAVED", "APPLIED", "APPLIED"]
    assert [o[2] for o in h.outcomes] == ["INTERVIEW", "INTERVIEW"] and len(log.load_events(logfile)) == 5


@pytest.mark.parametrize("sequence", [["OFFER", "APPLIED"], ["REJECTED", "INTERVIEW"]])
def test_out_of_order_events_are_recorded_as_entered(run, logfile, sequence):
    for n, stage in enumerate(sequence):
        _outcome(run, logfile, "job_a", stage, f"2026-09-2{6 + n}")
    h = log.reconstruct(log.load_events(logfile))["job_a"]
    assert [o[2] for o in h.outcomes] == sequence and h.current_outcome == sequence[-1]


def test_notes_are_kept_verbatim(run, logfile):
    notes = "  Waiting for response — «HR» said 2 weeks; score=99?  "
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26", notes)
    assert log.load_events(logfile)[0].notes == notes


# ------------------------------------------------------------ identity (B-4)

def test_job_in_the_run_is_accepted_and_unknown_job_is_refused(run, logfile):
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26")
    with pytest.raises(ValueError, match="unknown job_id"):
        log.outcome_event(run, "job_typo", "APPLIED", "2026-09-26", log=logfile)
    with pytest.raises(ValueError, match="unknown job_id"):
        log.decision_event(run, "job_typo", "SAVED", "2026-09-26", log=logfile)


def test_disappeared_job_keeps_history_and_gets_no_automatic_outcome(run, logfile, tmp_path):
    _decide(run, logfile, "job_b", "APPLIED", "2026-09-26")
    _outcome(run, logfile, "job_b", "APPLIED", "2026-09-26")
    later = _write_run(tmp_path / "later", jobs=[JOBS[0]], as_of=date(2026, 10, 1))      # job_b is gone
    assert "job_b" not in log.load_run(later)
    before = log.reconstruct(log.load_events(logfile))["job_b"]
    assert before.current_outcome == "APPLIED"                                       # nothing invented
    _outcome(later, logfile, "job_b", "INTERVIEW", "2026-10-03")                     # known from history
    h = log.reconstruct(log.load_events(logfile))["job_b"]
    assert [o[2] for o in h.outcomes] == ["APPLIED", "INTERVIEW"]
    with pytest.raises(ValueError, match="no decision in"):                          # snapshot needs the run
        log.decision_event(later, "job_b", "DEFERRED", "2026-10-03", log=logfile)


# --------------------------------------------------------- snapshots (B-2)

def test_snapshot_is_copied_from_the_operational_run(run, logfile):
    e = _decide(run, logfile, "job_b", "SAVED", "2026-09-26")
    source = log.load_run(run)["job_b"]
    s = e.snapshot
    assert (s.run_dir, s.run, s.job) == (str(run), source.run, source.job)
    assert (s.priority, s.next_action, s.readiness, s.seniority) == \
           (source.application_priority, source.next_action, source.readiness.state,
            source.candidate_alignment.seniority.state)
    assert s.run.as_of_date.isoformat() == "2026-09-25" and s.run.run_kind == "OPERATIONAL"


def test_hard_filter_rejected_job_can_be_logged_from_its_run_decision(run, logfile):
    s = _decide(run, logfile, "job_rejected", "SKIPPED", "2026-09-26").snapshot
    assert (s.evaluation_scope, s.priority, s.blocking_rule, s.career_value) == \
           ("HARD_FILTER_REJECTED", "DO_NOT_INVEST", "V-1", None)


def test_historical_runs_cannot_back_a_decision(tmp_path, logfile):
    replay = _write_run(tmp_path / "replay", run_kind="HISTORICAL_REPLAY")
    with pytest.raises(ValueError, match="only OPERATIONAL runs"):
        log.decision_event(replay, "job_a", "SAVED", "2026-09-26", log=logfile)


def test_snapshot_survives_changes_to_the_run(run, logfile):
    _decide(run, logfile, "job_a", "APPLIED", "2026-09-26")
    stored = logfile.read_bytes()
    _write_run(run, jobs=[_job("job_a", role_level="Principal")], as_of=date(2026, 10, 1))   # run rewritten
    assert log.load_run(run)["job_a"].application_priority != log.load_events(logfile)[0].snapshot.priority
    assert logfile.read_bytes() == stored


# ------------------------------------------------------ mixed versions (B-7/B-9)

def test_golden_legacy_lines_parse_under_their_own_contract():
    events = log.load_events(GOLDEN_LEGACY)
    assert [type(e).__name__ for e in events] == ["LegacyEvent", "LegacyEvent"]
    raw = [json.loads(line) for line in GOLDEN_LEGACY.read_text().splitlines()]
    assert [e.raw for e in events] == raw                                            # kept verbatim, not coerced


def test_mixed_file_dispatches_by_schema_version(run, logfile):
    logfile.write_bytes(GOLDEN_LEGACY.read_bytes())
    _decide(run, logfile, "job_a", "SAVED", "2026-09-26")
    _outcome(run, logfile, "job_0f1e2d3c4b5a6978", "INTERVIEW", "2026-09-30")       # known only from legacy history
    events = log.load_events(logfile)
    assert [e.schema_version if hasattr(e, "schema_version") else e.raw["schema_version"] for e in events] == \
           ["application_log_v1", "application_log_v1", "coach_v1", "coach_v1"]
    h = log.reconstruct(events)["job_0f1e2d3c4b5a6978"]
    assert h.decisions == [("application_log_v1", "2026-09-26", "SAVED")]
    assert h.outcomes == [("application_log_v1", "2026-09-27", "APPLIED"), ("coach_v1", "2026-09-30", "INTERVIEW")]
    assert log.load_events(logfile) == events                                        # deterministic


@pytest.mark.parametrize("line", [
    {"event": "outcome", "job_id": "x", "outcome": "APPLIED", "outcome_date": "2026-09-26"},       # missing version
    {"event": "outcome", "schema_version": "coach_v2", "job_id": "x", "outcome": "APPLIED", "outcome_date": "2026-09-26"},
    {"event": "outcome", "schema_version": "application_log_v1", "job_id": "x", "outcome": "EXPIRED",
     "outcome_date": "2026-09-26"},                                                                 # breaks legacy contract
])
def test_unknown_or_malformed_versions_are_refused(tmp_path, line):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(line) + "\n")
    with pytest.raises(ValueError):
        log.load_events(path)


# ---------------------------------------------------------- isolation / safety

FORBIDDEN = ("src.scoring", "src.transition", "src.reporting", "src.strategy", "src.models.ptf", "src.models.transition",
             "src.models.scored_job", "src.models.strategy", "src.pipeline", "src.calibration")


@pytest.mark.parametrize("path", ["src/career_coach/application_log.py", "scripts/coach_log.py"])
def test_logging_code_imports_no_legacy_layer(path):
    tree = ast.parse((ROOT / path).read_text())
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
              {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not {m for m in modules if m.startswith(FORBIDDEN)}


def test_logging_loads_no_legacy_module_at_runtime(run, logfile):
    code = ("import sys; sys.argv=['coach_log.py','decision','--run',%r,'--job-id','job_a','--decision','APPLIED',"
            "'--date','2026-09-26','--log',%r]; import runpy; runpy.run_path('scripts/coach_log.py', run_name='__main__');"
            "print(sorted(m for m in sys.modules if m.startswith(%r)))") % (str(run), str(logfile), FORBIDDEN)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.strip().splitlines()[-1] == "[]"
    assert len(log.load_events(logfile)) == 1


def test_logging_does_not_change_run_outputs_or_dashboard(run, logfile):
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    decisions = list(log.load_run(run).values())
    meta = json.loads((run / "run_meta.json").read_text())
    rendered = dash.render_markdown(dash.build(decisions, meta))
    _decide(run, logfile, "job_a", "APPLIED", "2026-09-26")
    _outcome(run, logfile, "job_a", "INTERVIEW", "2026-09-30")
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    assert dash.render_markdown(dash.build(list(log.load_run(run).values()), meta)) == rendered


def test_dashboard_rendering_creates_no_events(run, logfile):
    dash.render_markdown(dash.build(list(log.load_run(run).values()), json.loads((run / "run_meta.json").read_text())))
    assert not logfile.exists()


def test_default_log_is_the_gitignored_applications_file():
    assert log.DEFAULT_LOG == ROOT / "data" / "applications.jsonl"
    ignored = subprocess.run(["git", "check-ignore", "data/applications.jsonl"], cwd=ROOT, capture_output=True, text=True)
    assert ignored.returncode == 0
