"""Application-log corrections: real calendar dates, no future dates (the CLI
supplies today's date), and append-only `coach_void_v1` events that mark one
earlier event as entered in error without touching it. All logs here are
SYNTHETIC TEST FIXTURES in tmp_path; the production log is never touched."""

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.career_coach import application_log as log
from tests.test_career_coach_application_log import _decide, _outcome, logfile, run  # noqa: F401 (fixtures)
from tests.test_learning_v1 import REPORT_DAY, _d, _o, _report, _rows, _states, env  # noqa: F401 (fixtures)

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 1)


def _void(path, event_id, job_id, when="2026-10-01", reason="entered in error", today=TODAY):
    event = log.void_event(event_id, job_id, when, reason, log=path, today=today)
    log.append_event(event, path)
    return event


def _cli(monkeypatch, argv, today=TODAY):
    spec = importlib.util.spec_from_file_location("coach_log", ROOT / "scripts" / "coach_log.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Today(date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(module, "date", Today)
    monkeypatch.setattr("sys.argv", ["coach_log.py", *argv])
    try:
        module.main()
    except SystemExit as e:
        return str(e.code)
    return None


# ------------------------------------------------------------ date validation

@pytest.mark.parametrize("day", ["2026-02-28", "2024-02-29", "2026-12-31", "2026-01-01"])
def test_real_calendar_days_are_accepted(day):
    assert log.CoachOutcomeEvent(job_id="job_a", outcome="APPLIED", outcome_date=day).outcome_date == day


@pytest.mark.parametrize("day", ["2026-02-29", "2026-02-30", "2026-04-31", "2026-13-01", "2026-00-10",
                                 "2026-2-3", "02/10/2026", "2026-10-01T09:00", "20261001", "", "2026-10-1"])
def test_impossible_or_malformed_dates_are_refused_for_every_event(run, day):
    with pytest.raises(ValidationError):
        log.CoachOutcomeEvent(job_id="job_a", outcome="APPLIED", outcome_date=day)
    with pytest.raises(ValidationError):
        log.VoidEvent(event_id=0, job_id="job_a", void_date=day, reason="x")
    with pytest.raises(ValueError):
        log.decision_event(run, "job_a", "SAVED", day, log=run / "none.jsonl")


def test_legacy_lines_keep_their_own_date_contract():
    line = {"event": "outcome", "schema_version": "application_log_v1", "job_id": "job_x",
            "outcome": "APPLIED", "outcome_date": "2026-09-27", "notes": ""}
    assert log.parse_line(line).date == "2026-09-27"


def test_future_dates_are_refused_and_past_dates_kept():
    log.check_not_future("2026-10-01", TODAY)                          # today
    log.check_not_future("2025-03-14", TODAY)                          # a genuine past event, recorded late
    log.check_not_future("2030-01-01", None)                           # no date supplied: library stays clock-free
    with pytest.raises(ValueError, match="future-dated"):
        log.check_not_future("2026-10-02", TODAY)


def test_builders_refuse_future_dates_before_anything_is_appended(run, logfile):
    with pytest.raises(ValueError, match="future-dated"):
        log.decision_event(run, "job_a", "APPLIED", "2026-10-02", log=logfile, today=TODAY)
    with pytest.raises(ValueError, match="future-dated"):
        log.outcome_event(run, "job_a", "APPLIED", "2026-10-02", log=logfile, today=TODAY)
    assert not logfile.exists()


def test_cli_refuses_future_and_impossible_dates_and_appends_nothing(monkeypatch, run, logfile):
    base = ["outcome", "--run", str(run), "--job-id", "job_a", "--outcome", "APPLIED", "--log", str(logfile)]
    assert "future-dated" in _cli(monkeypatch, base + ["--date", "2026-10-02"])
    assert "real calendar day" in _cli(monkeypatch, base + ["--date", "2026-02-30"])
    assert not logfile.exists()
    assert _cli(monkeypatch, base + ["--date", "2026-10-01"]) is None
    assert _cli(monkeypatch, base + ["--date", "2026-09-20"]) is None   # past dates stay valid
    assert len(log.load_events(logfile)) == 2


# ------------------------------------------------------------------ voiding

def test_a_void_appends_one_line_and_leaves_the_original_byte_identical(run, logfile):
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26")
    before = logfile.read_bytes()
    void = _void(logfile, 0, "job_a", reason="logged against the wrong job")
    after = logfile.read_bytes()
    assert after.startswith(before) and len(after.splitlines()) == 2
    stored = json.loads(after.splitlines()[1])
    assert stored == {"event": "void", "schema_version": "coach_void_v1", "event_id": 0, "job_id": "job_a",
                      "void_date": "2026-10-01", "reason": "logged against the wrong job"}
    assert log.load_events(logfile)[1] == void


@pytest.mark.parametrize("event_id", [1, 5])
def test_unknown_event_ids_are_refused(run, logfile, event_id):
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26")
    before = logfile.read_bytes()
    with pytest.raises(ValueError, match=f"no event {event_id}"):
        log.void_event(event_id, "job_a", "2026-10-01", "x", log=logfile, today=TODAY)
    assert logfile.read_bytes() == before


def test_voiding_an_empty_or_missing_log_is_refused(logfile):
    with pytest.raises(ValueError, match="holds no events"):
        log.void_event(0, "job_a", "2026-10-01", "x", log=logfile, today=TODAY)
    assert not logfile.exists()


def test_the_job_must_match_the_voided_event(run, logfile):
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26")
    with pytest.raises(ValueError, match="belongs to job_a, not job_b"):
        log.void_event(0, "job_b", "2026-10-01", "x", log=logfile, today=TODAY)


def test_an_event_can_be_voided_once_and_a_void_never(run, logfile):
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26")
    _void(logfile, 0, "job_a")
    before = logfile.read_bytes()
    with pytest.raises(ValueError, match="already voided by event 1"):
        log.void_event(0, "job_a", "2026-10-01", "again", log=logfile, today=TODAY)
    with pytest.raises(ValueError, match="itself a void"):
        log.void_event(1, "job_a", "2026-10-01", "undo", log=logfile, today=TODAY)
    assert logfile.read_bytes() == before


@pytest.mark.parametrize("when, match", [("2026-02-30", "real calendar day"), ("2026-10-02", "future-dated"),
                                         ("2026-09-25", "before the date of event 0")])
def test_void_dates_are_validated(run, logfile, when, match):
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26")
    with pytest.raises(ValueError, match=match):
        log.void_event(0, "job_a", when, "x", log=logfile, today=TODAY)


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_void_needs_a_reason(run, logfile, reason):
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26")
    with pytest.raises(ValidationError, match="needs a reason"):
        log.void_event(0, "job_a", "2026-10-01", reason, log=logfile, today=TODAY)


def test_cli_void_refusals_append_nothing(monkeypatch, run, logfile):
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26")
    before = logfile.read_bytes()
    base = ["void", "--job-id", "job_a", "--date", "2026-10-01", "--log", str(logfile)]
    assert _cli(monkeypatch, base + ["--event-id", "0"]) == "missing --reason"
    assert "must be an event id" in _cli(monkeypatch, base + ["--event-id", "first", "--reason", "x"])
    assert "must be an event id" in _cli(monkeypatch, base + ["--event-id", "-1", "--reason", "x"])
    assert "no event 7" in _cli(monkeypatch, base + ["--event-id", "7", "--reason", "x"])
    assert "needs a reason" in _cli(monkeypatch, base + ["--event-id", "0", "--reason", " "])
    assert logfile.read_bytes() == before
    assert _cli(monkeypatch, base + ["--event-id", "0", "--reason", "wrong job"]) is None
    assert logfile.read_bytes().startswith(before) and len(log.load_events(logfile)) == 2


def test_history_lists_event_ids_and_marks_voided_events(monkeypatch, run, logfile, capsys):
    _decide(run, logfile, "job_a", "APPLIED", "2026-09-25")
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26")
    _void(logfile, 1, "job_a", reason="not actually submitted")
    _cli(monkeypatch, ["history", "--log", str(logfile)])
    out = capsys.readouterr().out
    assert "job_a: decision APPLIED · outcome NOT_STARTED" in out
    assert "  event 1  2026-09-26  coach_v1 outcome APPLIED  [VOIDED by event 2]" in out
    assert "  event 2  2026-10-01  coach_void_v1 void of event 1: not actually submitted" in out


def test_reconstruct_excludes_voided_events(run, logfile):
    _outcome(run, logfile, "job_a", "APPLIED", "2026-09-26")
    _outcome(run, logfile, "job_a", "REJECTED", "2026-09-28")
    _void(logfile, 1, "job_a")
    h = log.reconstruct(log.load_events(logfile))["job_a"]
    assert (h.current_outcome, h.outcomes, h.voided) == ("APPLIED", [("coach_v1", "2026-09-26", "APPLIED")], [1])


def test_a_job_known_only_through_voided_events_is_not_known(run, logfile, tmp_path):
    from tests.test_career_coach_application_log import JOBS, _write_run
    _outcome(run, logfile, "job_b", "APPLIED", "2026-09-26")
    _void(logfile, 0, "job_b", reason="wrong job id")
    other = _write_run(tmp_path / "other", jobs=[JOBS[0]])               # job_b is not in this run
    with pytest.raises(ValueError, match="unknown job_id 'job_b'"):
        log.outcome_event(other, "job_b", "INTERVIEW", "2026-09-30", log=logfile)


@pytest.mark.parametrize("void", [
    {"event_id": 0, "job_id": "job_a"},                                # names itself / a later event
    {"event_id": 0, "job_id": "job_b"},                                # wrong job
])
def test_a_log_with_an_invalid_void_is_refused_when_read(tmp_path, void):
    path = tmp_path / "applications.jsonl"
    lines = [{"event": "void", "schema_version": "coach_void_v1", "void_date": "2026-10-01", "reason": "x", **void}]
    if void["job_id"] == "job_b":
        lines.insert(0, {"event": "outcome", "schema_version": "coach_v1", "job_id": "job_a", "outcome": "APPLIED",
                         "outcome_date": "2026-09-26", "notes": ""})
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    with pytest.raises(ValueError):
        log.load_events(path)


def test_a_second_void_of_the_same_event_is_refused_when_read(tmp_path):
    path = tmp_path / "applications.jsonl"
    outcome = {"event": "outcome", "schema_version": "coach_v1", "job_id": "job_a", "outcome": "APPLIED",
               "outcome_date": "2026-09-26", "notes": ""}
    void = {"event": "void", "schema_version": "coach_void_v1", "event_id": 0, "job_id": "job_a",
            "void_date": "2026-10-01", "reason": "x"}
    path.write_text("".join(json.dumps(line) + "\n" for line in (outcome, void, void)))
    with pytest.raises(ValueError, match="already voided by event 1"):
        log.load_events(path)


# ------------------------------------------------------ canonical learning

def _void_env(env, event_id, job="job_a", when="2026-10-15"):
    _void(env[1], event_id, job, when=when, today=REPORT_DAY)


def test_a_voided_applied_outcome_is_not_a_submission(env):
    _d(env, "job_a", "APPLIED", "2026-09-25")
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _void_env(env, 1)
    s = _states(env)["job_a"]
    assert (s.submitted, s.resolution, s.current_status) == (False, "NO_SUBMISSION", "NOT_STARTED")
    assert s.decided_applied_without_submission and [v.seq for v in s.voided] == [1]
    r = _rows(env)["job_a"]
    assert r["out_submitted"] is False and r["src_voided_event_ids"] == [1]


def test_a_later_valid_applied_outcome_still_establishes_the_submission(env):
    _o(env, "job_a", "APPLIED", "2026-09-20")                          # wrong date
    _void_env(env, 0)
    _o(env, "job_a", "APPLIED", "2026-09-26")
    s = _states(env)["job_a"]
    assert (s.submitted, s.first_dates["APPLIED"], s.resolution) == (True, "2026-09-26", "OPEN")


@pytest.mark.parametrize("stage", ["INTERVIEW", "FINAL_ROUND"])
def test_a_voided_milestone_is_not_reached(env, stage):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", stage, "2026-10-01")
    _void_env(env, 1)
    s = _states(env)["job_a"]
    assert s.reached[stage] is False and s.current_status == "APPLIED" and stage not in s.first_dates


@pytest.mark.parametrize("terminal", ["OFFER", "REJECTED", "WITHDRAWN", "CLOSED"])
def test_a_voided_terminal_outcome_does_not_resolve_the_application(env, terminal):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", terminal, "2026-10-01")
    _void_env(env, 1)
    s = _states(env)["job_a"]
    assert (s.resolution, s.terminal_outcome, s.current_status) == ("OPEN", None, "APPLIED")
    assert s.days_since_last_event == (REPORT_DAY - date(2026, 9, 26)).days


def test_voiding_a_closed_without_submission_leaves_no_submission(env):
    _o(env, "job_a", "CLOSED", "2026-09-26")
    _d(env, "job_a", "SAVED", "2026-09-25")
    _void_env(env, 0)
    s = _states(env)["job_a"]
    assert (s.resolution, s.current_status, s.terminal_outcome) == ("NO_SUBMISSION", "NOT_STARTED", None)


def test_voided_duplicates_and_out_of_order_events_leave_no_flags(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "APPLIED", "2026-09-26")                          # duplicate
    _o(env, "job_a", "REJECTED", "2026-10-01")
    _o(env, "job_a", "INTERVIEW", "2026-10-02")                        # after a terminal: out of order
    assert set(_states(env)["job_a"].anomalies) == {"DUPLICATE_STAGE", "OUT_OF_ORDER"}
    _void_env(env, 1)
    _void_env(env, 3)
    s = _states(env)["job_a"]
    assert s.anomalies == [] and [o.value for o in s.outcomes] == ["APPLIED", "REJECTED"]
    assert s.resolution == "TERMINAL"


def test_the_order_of_remaining_events_is_never_repaired(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "OFFER", "2026-10-05")
    _o(env, "job_a", "INTERVIEW", "2026-10-01")                        # entered after OFFER
    _o(env, "job_a", "CLOSED", "2026-10-06")
    _void_env(env, 3)
    s = _states(env)["job_a"]
    assert [o.value for o in s.outcomes] == ["APPLIED", "OFFER", "INTERVIEW"] and "OUT_OF_ORDER" in s.anomalies


def test_the_void_itself_is_never_a_decision_or_outcome(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_a", "INTERVIEW", "2026-10-01")
    _void_env(env, 1)
    s = _states(env)["job_a"]
    assert [(e.kind, e.value) for e in s.events] == [("outcome", "APPLIED")]
    assert s.schema_versions == ["coach_v1"] and s.last_event_date == "2026-09-26"


def test_d5_skips_a_voided_applied_decision(env):
    _d(env, "job_a", "SAVED", "2026-09-24")
    _d(env, "job_a", "APPLIED", "2026-09-25")                          # entered for the wrong job
    _o(env, "job_a", "APPLIED", "2026-09-27")
    _void_env(env, 1)
    r = _rows(env)["job_a"]
    assert (r["src_decision_context"], r["ctx_decision"], r["ctx_decision_date"]) == \
           ("LATEST_DECISION_BEFORE_SUBMISSION", "SAVED", "2026-09-24")


def test_d5_anchor_moves_to_the_first_valid_applied_outcome(env):
    _o(env, "job_a", "APPLIED", "2026-09-20")                          # voided: not the anchor
    _d(env, "job_a", "APPLIED", "2026-09-25")
    _o(env, "job_a", "APPLIED", "2026-09-27")
    _void_env(env, 0)
    r = _rows(env)["job_a"]
    assert (r["src_decision_context"], r["ctx_decision_date"], r["out_first_applied_date"]) == \
           ("APPLIED_DECISION", "2026-09-25", "2026-09-27")


def test_the_raw_log_stays_intact(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    raw = env[1].read_bytes()
    _void_env(env, 0)
    _states(env)
    _rows(env)
    assert env[1].read_bytes().startswith(raw) and len(log.load_events(env[1])) == 2


def _build_report(env):
    spec = importlib.util.spec_from_file_location("outcome_report", ROOT / "scripts" / "outcome_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(env[1], env[2], REPORT_DAY)


def test_report_lists_voids_and_counts_only_valid_events(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _o(env, "job_b", "APPLIED", "2026-09-26")
    _void(env[1], 1, "job_b", when="2026-10-15", reason="Reason-Text-Kept-Out-Of-Learning", today=REPORT_DAY)
    report, rows, states = _build_report(env)
    assert "- Voided events (entered in error; still in the log, excluded from every figure below): 1" in report
    assert "  - `job_b` — event 1 (coach_v1 outcome APPLIED, 2026-09-26), voided by event 2 on 2026-10-15" in report
    assert "Population: 1 jobs with a recorded APPLIED outcome" in report
    assert [r["job_id"] for r in rows] == ["job_a"] and "job_b" not in states
    assert "Reason-Text-Kept-Out-Of-Learning" not in report + json.dumps(rows, default=str) + json.dumps(states)


def test_report_when_every_event_was_voided(env):
    _o(env, "job_a", "APPLIED", "2026-09-26")
    _void_env(env, 0)
    report, rows, _ = _build_report(env)
    assert rows == [] and "Every recorded event has been voided, so every population below is empty." in report
