"""Gate 5 V1: canonical outcome state, analysis dataset and descriptive
report. All application logs here are SYNTHETIC TEST FIXTURES built in
tmp_path — no production outcome data is created or read."""

import ast
import json
import re
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.career_coach import application_log as log
from src.career_coach import run_archive as ra
from src.learning.dataset import build_rows
from src.learning.outcome_state import build_states
from src.learning.reports import exact_interval, render
from tests.test_run_archive import CONFIG_BYTES, PROFILE_BYTES, _job, write_run

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_LEGACY = ROOT / "tests" / "fixtures" / "application_log_v1_golden.jsonl"
REPORT_DAY = date(2026, 10, 20)


@pytest.fixture
def env(tmp_path):
    """TEST FIXTURE: an archived OPERATIONAL run and an empty log."""
    run = write_run(tmp_path / "run")
    archive = tmp_path / "archive"
    ra.archive_run(run, CONFIG_BYTES, PROFILE_BYTES, archive)
    return run, tmp_path / "applications.jsonl", archive


def _d(env, job, decision, day, **kw):
    run, path, _ = env
    log.append_event(log.decision_event(run, job, decision, day, log=path, **kw), path)


def _o(env, job, outcome, day):
    run, path, _ = env
    log.append_event(log.outcome_event(run, job, outcome, day, log=path), path)


def _states(env, day=REPORT_DAY):
    return build_states(log.load_events(env[1]), day)


def _rows(env, day=REPORT_DAY, archive=None):
    return {r["job_id"]: r for r in build_rows(_states(env, day), archive if archive is not None else env[2])}


def _report(env, archive=None):
    rows = list(_rows(env, archive=archive).values())
    return render(rows, {"report_date": REPORT_DAY.isoformat(), "log": "test", "archive_root": "test",
                         "event_count": len(log.load_events(env[1])), "events_by_schema": {}, "archive_count": 1})


# --------------------------------------------------------------- D2 submission

def test_applied_outcome_is_a_submission_but_applied_decision_alone_is_not(env):
    _d(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_b", "APPLIED", "2026-09-26")
    s = _states(env)
    assert (s["job_a"].submitted, s["job_a"].decided_applied_without_submission, s["job_a"].resolution) == \
           (False, True, "NO_SUBMISSION")
    assert (s["job_b"].submitted, s["job_b"].resolution) == (True, "OPEN")
    assert "## 4. Decided to apply, submission not recorded (1)" in _report(env)


# --------------------------------------------------------- D3 terminal / D4 OPEN

@pytest.mark.parametrize("terminal", ["OFFER", "REJECTED", "WITHDRAWN", "CLOSED"])
def test_terminal_outcomes_resolve_the_application(env, terminal):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", terminal, "2026-10-01")
    s = _states(env)["job_a"]
    assert (s.resolution, s.terminal_outcome, s.days_since_last_event) == ("TERMINAL", terminal, None)


def test_contradicting_events_after_a_terminal_are_kept_and_flagged(env):
    for outcome, day in [("APPLIED", "2026-09-26"), ("REJECTED", "2026-09-30"), ("INTERVIEW", "2026-10-02")]:
        _o(env, "job_a", outcome, day)
    s = _states(env)["job_a"]
    assert [e.value for e in s.outcomes] == ["APPLIED", "REJECTED", "INTERVIEW"]
    assert (s.resolution, s.current_status, s.terminal_outcome) == ("TERMINAL", "INTERVIEW", "REJECTED")
    assert "OUT_OF_ORDER" in s.anomalies


def test_open_applications_never_time_out(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "INTERVIEW", "2026-10-01")
    for day, days in [(REPORT_DAY, 19), (date(2027, 12, 31), 456)]:
        s = _states(env, day)["job_a"]
        assert (s.resolution, s.current_status, s.terminal_outcome, s.days_since_last_event) == \
               ("OPEN", "INTERVIEW", None, days)


def test_a_job_with_no_submission_is_not_an_unsuccessful_application(env):
    _d(env, "job_a", "SKIPPED", "2026-09-26")
    _d(env, "job_b", "SAVED", "2026-09-26")
    _o(env, "job_b", "CLOSED", "2026-09-30")                          # closed before any submission
    rows = _rows(env)
    assert all(not r["out_submitted"] and r["out_resolution"] == "NO_SUBMISSION" for r in rows.values())
    report = _report(env)
    assert "Population: 0 jobs with a recorded APPLIED outcome" in report
    assert "## 5. Other histories without a submission (2)" in report


# ------------------------------------------------------------ D5 context choice

def test_d5_uses_the_applied_decision_snapshot(env):
    _d(env, "job_a", "SAVED", "2026-09-25")
    _d(env, "job_a", "APPLIED", "2026-09-26", declared_effort="HIGH")
    _d(env, "job_a", "DEFERRED", "2026-09-26")
    _o(env, "job_a", "APPLIED", "2026-09-27")
    r = _rows(env)["job_a"]
    assert (r["src_decision_context"], r["ctx_decision"], r["ctx_declared_effort"]) == ("APPLIED_DECISION", "APPLIED", "HIGH")


def test_d5_falls_back_to_the_latest_decision_on_or_before_submission(env):
    _d(env, "job_a", "SAVED", "2026-09-25")
    _d(env, "job_a", "DEFERRED", "2026-09-27")
    _o(env, "job_a", "APPLIED", "2026-09-27")
    _d(env, "job_a", "SKIPPED", "2026-09-28")                        # after submission: never used
    r = _rows(env)["job_a"]
    assert (r["src_decision_context"], r["ctx_decision"]) == ("LATEST_DECISION_BEFORE_SUBMISSION", "DEFERRED")


def test_d5_without_decision_context_counts_in_funnel_but_not_in_strata(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "INTERVIEW", "2026-09-30")
    _d(env, "job_a", "SAVED", "2026-10-01")                           # only after submission
    r = _rows(env)["job_a"]
    assert r["src_decision_context"] == "NONE" and r["ctx_schema_version"] is None
    report = _report(env)
    assert "- Recorded INTERVIEW (in any order): 1 of 1" in report
    assert "## 7. Decision-time context — —" in report                  # no stratum built from it


def _context(env):
    r = _rows(env)["job_a"]
    return r["src_decision_context"], r["ctx_decision"], r["ctx_decision_date"]


def test_d5_single_decision_before_first_submission(env):
    _d(env, "job_a", "SAVED", "2026-09-25")
    _o(env, "job_a", "APPLIED", "2026-09-27")
    assert _context(env) == ("LATEST_DECISION_BEFORE_SUBMISSION", "SAVED", "2026-09-25")


def test_d5_multiple_decisions_before_first_submission(env):
    for decision, day in [("SAVED", "2026-09-24"), ("DEFERRED", "2026-09-25"), ("SKIPPED", "2026-09-26")]:
        _d(env, "job_a", decision, day)
    _o(env, "job_a", "APPLIED", "2026-09-27")
    assert _context(env) == ("LATEST_DECISION_BEFORE_SUBMISSION", "SKIPPED", "2026-09-26")


def test_d5_eligible_applied_decision_is_preferred(env):
    _d(env, "job_a", "APPLIED", "2026-09-23")
    _d(env, "job_a", "SAVED", "2026-09-25")
    _o(env, "job_a", "APPLIED", "2026-09-27")
    assert _context(env) == ("APPLIED_DECISION", "APPLIED", "2026-09-23")


def test_d5_decision_on_the_submission_date_is_eligible(env):
    _o(env, "job_a", "APPLIED", "2026-09-27")
    _d(env, "job_a", "SAVED", "2026-09-27")                           # logged after, same day
    assert _context(env) == ("LATEST_DECISION_BEFORE_SUBMISSION", "SAVED", "2026-09-27")


def test_d5_only_post_submission_decisions_give_no_context(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _d(env, "job_a", "APPLIED", "2026-09-28")                         # an APPLIED decision after submission
    _d(env, "job_a", "SAVED", "2026-09-29")
    r = _rows(env)["job_a"]
    assert (r["src_decision_context"], r["src_context_event_seq"]) == ("NONE", None)
    assert all(v is None for k, v in r.items() if k.startswith("ctx_"))
    assert r["src_archive_status"] == "NOT_APPLICABLE"


def test_d5_anchor_is_the_first_of_several_applied_outcomes(env):
    _o(env, "job_a", "APPLIED", "2026-09-20")
    _d(env, "job_a", "APPLIED", "2026-09-25")                         # before the second, after the first
    _o(env, "job_a", "APPLIED", "2026-09-30")
    assert _context(env) == ("NONE", None, None)
    _d(env, "job_b", "SAVED", "2026-09-18")
    _o(env, "job_b", "APPLIED", "2026-09-20")
    _o(env, "job_b", "APPLIED", "2026-09-30")
    assert _rows(env)["job_b"]["ctx_decision_date"] == "2026-09-18"


def test_d5_no_decisions_at_all(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    assert _context(env) == ("NONE", None, None)


# ------------------------------------------------------- resolution contract

def test_resolution_and_current_status_are_separate_fields(env):
    events = [("APPLIED", "2026-09-26"), ("REJECTED", "2026-09-30"), ("INTERVIEW", "2026-10-02")]
    for outcome, day in events:
        _o(env, "job_a", outcome, day)
    s = _states(env)["job_a"]
    assert (s.resolution, s.current_status, "OUT_OF_ORDER" in s.anomalies) == ("TERMINAL", "INTERVIEW", True)
    assert [(e.value, e.date) for e in s.outcomes] == events           # raw events unchanged, in order


def test_terminal_non_submission_event_stays_no_submission(env):
    _d(env, "job_a", "SAVED", "2026-09-25")
    _o(env, "job_a", "CLOSED", "2026-09-28")
    s = _states(env)["job_a"]
    assert (s.resolution, s.current_status, s.submitted) == ("NO_SUBMISSION", "CLOSED", False)


def test_terminal_recorded_before_a_submission_still_resolves_it(env):
    _o(env, "job_a", "CLOSED", "2026-09-25")
    _o(env, "job_a", "APPLIED", "2026-09-26")
    s = _states(env)["job_a"]
    assert (s.resolution, s.current_status, s.terminal_outcome) == ("TERMINAL", "APPLIED", "CLOSED")
    assert "OUT_OF_ORDER" in s.anomalies


def test_open_iff_submitted_and_no_terminal_ever(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "FINAL_ROUND", "2026-10-01")
    assert _states(env)["job_a"].resolution == "OPEN"


def test_salary_limitation_is_stated(env):
    assert "salary values unavailable because they were not preserved in the approved archived artefacts" in _report(env)


# --------------------------------------------------------------- D6 one per job

def test_reapplication_stays_one_history_with_every_event(env):
    for outcome, day in [("APPLIED", "2026-09-26"), ("REJECTED", "2026-09-30"), ("APPLIED", "2026-10-05")]:
        _o(env, "job_a", outcome, day)
    rows = build_rows(_states(env), env[2])
    assert len(rows) == 1 and [e.value for e in _states(env)["job_a"].outcomes] == ["APPLIED", "REJECTED", "APPLIED"]


# ------------------------------------------------- zero-event state (lazy log)

def test_a_missing_and_an_empty_log_give_the_same_zero_event_report(tmp_path):
    """outcome_report.build treats a missing log and an empty log identically:
    zero events, no rows, no states — and reading creates no file."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("outcome_report", ROOT / "scripts" / "outcome_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    missing, empty = tmp_path / "missing.jsonl", tmp_path / "empty.jsonl"
    empty.write_text("")
    (r1, rows1, states1), (r2, rows2, states2) = (module.build(p, tmp_path / "no_archive", REPORT_DAY)
                                                  for p in (missing, empty))
    assert rows1 == rows2 == [] and states1 == states2 == {}
    assert r1.replace(str(missing), "LOG") == r2.replace(str(empty), "LOG")
    assert "- Events read: 0 (none)" in r1 and "No application events are recorded yet" in r1
    assert not missing.exists() and empty.read_bytes() == b""


# ------------------------------------------------------------- D8 no REVIEWED

def test_reviewed_is_not_a_decision_and_is_reported_unavailable(env):
    with pytest.raises(ValidationError):
        log.decision_event(env[0], "job_a", "REVIEWED", "2026-09-26", log=env[1])
    assert "Surfaced → reviewed: review is not recorded (D8)" in _report(env)


# ------------------------------------------------------------- D9 versions

def test_legacy_and_coach_records_are_never_pooled(env):
    env[1].write_bytes(GOLDEN_LEGACY.read_bytes())                       # synthetic application_log_v1 lines
    _d(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "APPLIED", "2026-09-26")
    rows = _rows(env)
    legacy, coach = rows["job_0f1e2d3c4b5a6978"], rows["job_a"]
    assert legacy["ctx_schema_version"] == "application_log_v1" and legacy["ctx_legacy_frozen_action"] == "STRETCH APPLY"
    assert legacy["ctx_priority"] is None and legacy["src_archive_status"] == "NOT_APPLICABLE"
    assert coach["ctx_schema_version"] == "coach_v1" and coach["ctx_legacy_frozen_action"] is None
    assert coach["ctx_coach_version"] == "career_coach_v1.2"
    report = _report(env)
    assert "### Event schema: application_log_v1" in report and "### Event schema: coach_v1" in report
    assert "## 7. Decision-time context — application_log_v1" in report and "## 7. Decision-time context — coach_v1" in report


# ------------------------------------------------------------- D10 anomalies

def test_anomalies_are_derived_and_the_log_is_untouched(env):
    _o(env, "job_a", "OFFER", "2026-09-26")
    _o(env, "job_a", "APPLIED", "2026-09-27")
    _o(env, "job_b", "INTERVIEW", "2026-09-26")
    _o(env, "job_b", "INTERVIEW", "2026-09-28")
    before = env[1].read_bytes()
    s = _states(env)
    assert s["job_a"].anomalies == ["OUT_OF_ORDER"]
    assert s["job_b"].anomalies == ["DUPLICATE_STAGE", "OUTCOME_WITHOUT_SUBMISSION"]
    _report(env)
    assert env[1].read_bytes() == before and "OUT_OF_ORDER" not in before.decode()


def test_sequence_metrics_exclude_flagged_histories_but_milestone_counts_keep_them(env):
    for job, events in {"job_a": [("APPLIED", "2026-09-26"), ("INTERVIEW", "2026-10-06")],
                        "job_b": [("INTERVIEW", "2026-09-26"), ("APPLIED", "2026-09-27")]}.items():
        for outcome, day in events:
            _o(env, job, outcome, day)
    report = _report(env)
    assert "- Recorded INTERVIEW (in any order): 2 of 2" in report
    assert "- APPLIED → INTERVIEW: 1 histories (1 excluded) · min 10, median 10, max 10" in report


# ---------------------------------------------------- D1 archive-backed context

def test_archive_recovers_context_beyond_the_snapshot(env, tmp_path):
    _d(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "APPLIED", "2026-09-26")
    matched = _rows(env)["job_a"]
    assert matched["src_archive_status"] == "MATCHED" and matched["ctx_employer"] == "DIRECT"
    assert matched["ctx_matched_tracks"] and matched["ctx_compensation_state"] == "HARD_FLOOR_SEE_VIABILITY"
    missing = _rows(env, archive=tmp_path / "no_archive")["job_a"]
    assert missing["src_archive_status"] == "NOT_ARCHIVED"
    assert (missing["ctx_employer"], missing["ctx_evidence_gaps"], missing["ctx_network"]) == (None, None, None)


def test_decision_context_ignores_the_live_output_folder(env):
    _d(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "APPLIED", "2026-09-26")
    before = _rows(env)["job_a"]
    write_run(env[0], as_of=date(2026, 10, 1), jobs=[_job("job_a", role_level="Principal")])   # folder overwritten
    after = _rows(env)["job_a"]
    ctx = {k: v for k, v in before.items() if k.startswith(("ctx_", "src_"))}
    assert ctx == {k: v for k, v in after.items() if k.startswith(("ctx_", "src_"))}


def test_dataset_columns_stay_in_their_groups(env):
    _d(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "APPLIED", "2026-09-26")
    row = _rows(env)["job_a"]
    assert all(k == "job_id" or k.startswith(("src_", "ctx_", "out_")) for k in row)
    assert not any(k.startswith("ctx_") and "outcome" in k for k in row)


# ----------------------------------------------------- reporting constraints

# Approved wording (Gate 5 V1): a statement about data availability, not an
# outcome claim — the only text exempt from the language scan.
APPROVED_DATA_LIMITATION = "salary values unavailable because they were not preserved in the approved archived artefacts"
FORBIDDEN_WORDS = r"\b(probab\w*|likel\w*|chance\w*|predict\w*|caus\w*|because|due to|led to|drove|driver\w*|" \
                  r"effect\w*|impact\w*|better|worse|best|worst|success rate|would have|should|rank\w*)\b"


def test_report_uses_only_descriptive_language(env):
    env[1].write_bytes(GOLDEN_LEGACY.read_bytes())
    _d(env, "job_a", "SAVED", "2026-09-25")
    _d(env, "job_a", "APPLIED", "2026-09-26", declared_effort="LOW", notes="Recruiter reached out — because referral")
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "INTERVIEW", "2026-10-01")
    _o(env, "job_b", "APPLIED", "2026-09-27")
    for text in (_report(env), render([], {"report_date": "2026-10-20", "log": "x", "archive_root": "x",
                                           "event_count": 0, "events_by_schema": {}, "archive_count": 0})):
        assert text.count(APPROVED_DATA_LIMITATION) == 1                 # the one approved data-availability sentence
        scanned = text.replace(APPROVED_DATA_LIMITATION, "")
        assert not re.search(FORBIDDEN_WORDS, scanned, re.I), re.search(FORBIDDEN_WORDS, scanned, re.I)
    assert "referral" not in _report(env)                               # notes are never parsed or echoed


def test_every_rate_is_k_of_n_with_an_exact_interval(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "INTERVIEW", "2026-09-30")
    _o(env, "job_b", "APPLIED", "2026-09-26")
    report = _report(env)
    assert "- Recorded INTERVIEW (in any order): 1 of 2 [0.01–0.99]" in report
    assert report.count("%") == report.count("95% binomial interval")   # no percentage figures, only the level
    assert exact_interval(0, 0) is None


def test_outputs_are_deterministic(env):
    _d(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "APPLIED", "2026-09-26")
    assert json.dumps(build_rows(_states(env), env[2]), sort_keys=True, default=str) == \
           json.dumps(build_rows(_states(env), env[2]), sort_keys=True, default=str)
    assert _report(env) == _report(env)


# ---------------------------------------------------------- import boundaries

FORBIDDEN_IMPORTS = ("src.scoring", "src.transition", "src.reporting", "src.strategy", "src.models.ptf",
                     "src.models.transition", "src.models.scored_job", "src.models.strategy", "src.pipeline",
                     "src.calibration", "src.career_coach.engine", "src.career_coach.dashboard")


@pytest.mark.parametrize("path", sorted(str(p.relative_to(ROOT)) for p in (ROOT / "src" / "learning").glob("*.py"))
                         + ["scripts/outcome_report.py", "src/career_coach/run_archive.py"])
def test_learning_layer_imports_no_legacy_engine_or_triage_code(path):
    tree = ast.parse((ROOT / path).read_text())
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
              {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not {m for m in modules if m.startswith(FORBIDDEN_IMPORTS)}


def test_coach_engine_never_imports_the_learning_layer():
    for path in (ROOT / "src" / "career_coach").glob("*.py"):
        assert "src.learning" not in path.read_text(), path.name
