"""P0-1: run manifest, audit gates and archive format v2. Every gate has a
passing and a non-passing test; the daily-run tests show that FAIL stops the
run before it is archived, that INVESTIGATE completes it without reporting it
as succeeded, and that v1 archives still verify. Raw corpora are SYNTHETIC
TEST FIXTURES in tmp_path unless a test says it uses the real corpus (read
only, copied into tmp_path)."""

import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.career_coach import run_audit as audit
from src.career_coach.operational import DASHBOARD_FILES, load_population, load_run_dir, render_dashboard, run_coach
from src.career_coach.run_archive import (ARCHIVE_ROOT, ARCHIVE_VERSION, ARCHIVE_VERSION_V2, MANIFEST, OUTPUT_FILES,
                                          archive_id, archive_run, find_archive, verify_archive)
from src.models.raw_job import RawJob
from tests.test_run_all import DESC, run_all

ROOT = Path(__file__).resolve().parent.parent
CLEAN = {"commit": "c" * 40, "tree_clean": True, "tree_fingerprint": None}
OTHER_CODE = {"commit": "d" * 40, "tree_clean": True, "tree_fingerprint": None}
MCF = "0123456789abcdef0123456789abcdef"
MCF2 = "fedcba9876543210fedcba9876543210"
HID = "11111111-2222-3333-4444-555555555555"
HID2 = "11111111-2222-3333-4444-666666666666"
SNAP = "20260925T010000Z"
CAUSAL = r"\b(improved|worsened|better|worse|likely to succeed|caused by|led to|because|best use)\b"


def _url(source, sid):
    return f"https://www.mycareersfuture.gov.sg/job/data/x-{sid}" if source == "mycareersfuture" else f"https://my.hiredly.com/jobs/{sid}"


def _raw(source, sid, title, company, loc, desc=DESC, job_id=None) -> str:
    return RawJob(job_id=job_id or f"{source}_{sid}", source=source, source_job_id=sid, source_url=_url(source, sid),
                  company=company, job_title=title, job_description=desc, location_raw=loc, posted_date_raw="2026-09-15",
                  scraped_at=datetime(2026, 9, 20, tzinfo=timezone.utc)).model_dump_json() + "\n"


# Malaysian Data Scientist (evaluated in full) + a Singapore job the hard filter rejects: no live check is needed.
MY_ONLY = {"mycareersfuture": _raw("mycareersfuture", MCF, "Software Engineer", "Other Pte Ltd", "Singapore"),
           "hiredly": _raw("hiredly", HID, "Data Scientist", "Acme Sdn Bhd", "Kuala Lumpur")}
# Adds a Singapore Data Scientist (evaluated in full): it needs live evidence.
WITH_SG = {"mycareersfuture": _raw("mycareersfuture", MCF2, "Data Scientist", "Lion Analytics Pte Ltd", "Singapore"),
           "hiredly": MY_ONLY["hiredly"]}


def _report(raw: Path, failed=None, snapshot=SNAP) -> None:
    sources = {}
    for s in ("mycareersfuture", "hiredly"):
        data = (raw / f"{s}_latest.jsonl").read_bytes()
        sources[s] = {"queries": 3, "failed_queries": (failed or {}).get(s, []), "raw_records": data.count(b"\n"),
                      "unique_postings": data.count(b"\n"), "empty_descriptions": 0, "detail_requests": 1,
                      "detail_retries": 0, "detail_recovered": 0, "file": f"{s}_{snapshot}.jsonl",
                      "sha256": audit.sha256(data)}
    (raw / "scrape_report_latest.json").write_text(json.dumps({"schema_version": "scrape_report_v1", "snapshot": snapshot,
                                                               "sources": sources}, indent=1, sort_keys=True))


def _root(tmp_path: Path, corpus: dict, report=True) -> Path:
    """TEST FIXTURE: raw latest files, a matching snapshot of each and a scrape report."""
    raw = tmp_path / "root" / "data" / "raw"
    raw.mkdir(parents=True)
    for s, text in corpus.items():
        (raw / f"{s}_latest.jsonl").write_text(text)
        (raw / f"{s}_{SNAP}.jsonl").write_text(text)
    if report:
        _report(raw)
    return tmp_path / "root"


def _daily(tmp_path: Path, root: Path, code=CLEAN, skip_scrape=True, live=None, out="out") -> "run_all.DailyRun":
    return run_all.DailyRun(root=root, as_of=date(2026, 9, 25), out_dir=tmp_path / out, archive_root=tmp_path / "archive",
                            live_path=live or tmp_path / "no_live.jsonl", skip_scrape=skip_scrape, code=dict(code))


def _no_scrape(run):              # stands in for the scraper: the fixture raw files are already published
    run.info["scrape"] = "OK (test fixture)"


def _stages(initial=False):
    return [(n, _no_scrape if (n == "scrape" and initial) else f) for n, f in run_all.STAGES]


def _evaluated(tmp_path, corpus=MY_ONLY, live=None):
    """(population, decisions) of the fixture corpus, built by the real pipeline and Coach."""
    root = _root(tmp_path, corpus)
    population, raw_hash, _ = load_population(root, date(2026, 9, 25))
    decisions, _ = run_coach(root, population, date(2026, 9, 25), "OPERATIONAL", live or tmp_path / "no_live.jsonl", raw_hash)
    return population, decisions


def _gate(name, manifest):
    return next(g for g in manifest["audit"]["gates"] if g["gate"] == name)


# ------------------------------------------------------------------ raw_integrity

def test_raw_integrity_passes_valid_enriched_records(tmp_path):
    raw = _root(tmp_path, MY_ONLY) / "data" / "raw"
    assert audit.gate_raw_integrity(raw, ["mycareersfuture", "hiredly"]).status == "PASS"


@pytest.mark.parametrize("hiredly, expected", [
    (None, "hiredly: no raw records"),
    ("", "hiredly: no raw records"),
    ("{not json\n", "not a raw job record"),
    (_raw("hiredly", HID, "Data Scientist", "Acme Sdn Bhd", "Kuala Lumpur", desc="   "), "empty description"),
    (_raw("hiredly", HID, "Data Scientist", "Acme Sdn Bhd", "Kuala Lumpur", job_id="hiredly_other"), "is not hiredly_<source id>"),
])
def test_raw_integrity_fails_missing_unreadable_or_unenriched_records(tmp_path, hiredly, expected):
    raw = _root(tmp_path, MY_ONLY) / "data" / "raw"
    if hiredly is None:
        (raw / "hiredly_latest.jsonl").unlink()
    else:
        (raw / "hiredly_latest.jsonl").write_text(hiredly)
    gate = audit.gate_raw_integrity(raw, ["mycareersfuture", "hiredly"])
    assert gate.status == "FAIL" and expected in gate.detail


def test_raw_integrity_flags_an_unexpected_source_id_format_for_review(tmp_path):
    raw = _root(tmp_path, {**MY_ONLY, "hiredly": _raw("hiredly", "my1", "Data Scientist", "Acme Sdn Bhd", "Kuala Lumpur")}) / "data" / "raw"
    gate = audit.gate_raw_integrity(raw, ["mycareersfuture", "hiredly"])
    assert gate.status == "INVESTIGATE" and "'my1' has an unexpected format" in gate.detail


def _row(source=None, **changes) -> str:
    """One hiredly fixture row (HID) with fields changed; `source` changes only the record's source field."""
    job = RawJob.model_validate_json(_raw("hiredly", HID, "Data Scientist", "Acme Sdn Bhd", "Kuala Lumpur"))
    return job.model_copy(update={**changes, **({"source": source} if source else {})}).model_dump_json() + "\n"


def _raw_gate(tmp_path, hiredly: str):
    raw = _root(tmp_path, MY_ONLY) / "data" / "raw"
    (raw / "hiredly_latest.jsonl").write_text(hiredly)
    return audit.gate_raw_integrity(raw, ["mycareersfuture", "hiredly"])


@pytest.mark.parametrize("sid", ["", "   ", "\t"])
def test_raw_integrity_fails_an_empty_source_id(tmp_path, sid):
    gate = _raw_gate(tmp_path, _row(source_job_id=sid, job_id=f"hiredly_{sid}"))
    assert gate.status == "FAIL" and "hiredly line 1: empty source id" in gate.detail
    assert "unexpected format" not in gate.detail                       # reported as missing, not as a format change


def test_raw_integrity_fails_a_record_from_another_source(tmp_path):
    gate = _raw_gate(tmp_path, _row() + _row(source="mycareersfuture", source_job_id=HID2, job_id=f"hiredly_{HID2}"))
    assert gate.status == "FAIL" and "hiredly line 2: record source 'mycareersfuture' in the hiredly file" in gate.detail


@pytest.mark.parametrize("changes, field", [
    ({"company": "Other Sdn Bhd"}, "company"), ({"job_title": "Sales Manager"}, "job_title"),
    ({"job_description": DESC + "<p>Different text.</p>"}, "job_description"), ({"salary_raw": "MYR 9,000"}, "salary_raw"),
])
def test_raw_integrity_fails_one_source_id_with_conflicting_posting_content(tmp_path, changes, field):
    gate = _raw_gate(tmp_path, _row() + _row(**changes))
    assert gate.status == "FAIL" and f"hiredly line 2: source id '{HID}' conflicts with line 1 ({field})" in gate.detail


def test_raw_integrity_accepts_duplicate_rows_from_overlapping_queries(tmp_path):
    rows = (_row(query_used="data scientist") + _row(query_used="machine learning")
            + _row(query_used="data scientist", scraped_at=datetime(2026, 9, 20, 0, 5, tzinfo=timezone.utc)))
    gate = _raw_gate(tmp_path, rows)
    assert gate.status == "PASS", gate.detail
    assert _raw_gate(tmp_path / "same", _row() * 3).status == "PASS"                        # byte-identical rows


def test_raw_integrity_keeps_a_non_empty_unexpected_id_format_as_investigate(tmp_path):
    gate = _raw_gate(tmp_path, _row(source_job_id="my-1", job_id="hiredly_my-1") + _row(source_job_id="my-1", job_id="hiredly_my-1"))
    assert gate.status == "INVESTIGATE" and "'my-1' has an unexpected format" in gate.detail


@pytest.mark.skipif(not list((ROOT / "data" / "raw").glob("*_2*.jsonl")), reason="real raw snapshots not present")
def test_raw_integrity_passes_every_real_raw_snapshot(tmp_path):
    """Reads the real timestamped snapshots (read only) through copies in tmp_path."""
    stamps = sorted({p.name.split("_")[1][:-6] for p in (ROOT / "data" / "raw").glob("mycareersfuture_2*.jsonl")})
    assert stamps
    for ts in stamps:
        raw = tmp_path / ts
        raw.mkdir()
        for source in ("mycareersfuture", "hiredly"):
            shutil.copy(ROOT / "data" / "raw" / f"{source}_{ts}.jsonl", raw / f"{source}_latest.jsonl")
        gate = audit.gate_raw_integrity(raw, ["mycareersfuture", "hiredly"])
        assert gate.status == "PASS", (ts, gate.detail)


# -------------------------------------------------------------- publication_state

def test_publication_state_passes_without_journal(tmp_path):
    assert audit.gate_publication_state(_root(tmp_path, MY_ONLY) / "data" / "raw").status == "PASS"


@pytest.mark.parametrize("leftover", [".publish-journal.json", ".publish-hiredly_latest.jsonl.tmp", ".publish-hiredly_latest.jsonl.bak"])
def test_publication_state_fails_on_an_interrupted_publication(tmp_path, leftover):
    raw = _root(tmp_path, MY_ONLY) / "data" / "raw"
    (raw / leftover).write_text("{}")
    gate = audit.gate_publication_state(raw)
    assert gate.status == "FAIL" and leftover in gate.detail


# ------------------------------------------------------------------ scrape_report

def test_scrape_report_passes_when_it_describes_the_current_raw_files(tmp_path):
    raw = _root(tmp_path, MY_ONLY) / "data" / "raw"
    report, _ = audit.load_scrape_report(raw)
    assert audit.gate_scrape_report(report, audit.raw_inputs(raw, ["mycareersfuture", "hiredly"])).status == "PASS"


def test_scrape_report_fails_when_the_raw_files_changed_after_it(tmp_path):
    raw = _root(tmp_path, MY_ONLY) / "data" / "raw"
    (raw / "hiredly_latest.jsonl").write_text(MY_ONLY["hiredly"] + _raw("hiredly", HID2, "ML Engineer", "Beta Sdn Bhd", "Kuala Lumpur"))
    report, _ = audit.load_scrape_report(raw)
    gate = audit.gate_scrape_report(report, audit.raw_inputs(raw, ["mycareersfuture", "hiredly"]))
    assert gate.status == "FAIL" and "hiredly" in gate.detail and "mycareersfuture" not in gate.detail


def test_scrape_report_absent_needs_review(tmp_path):
    raw = _root(tmp_path, MY_ONLY, report=False) / "data" / "raw"
    report, sha = audit.load_scrape_report(raw)
    assert report is None and sha is None
    assert audit.gate_scrape_report(None, audit.raw_inputs(raw, ["hiredly"])).status == "INVESTIGATE"


# ------------------------------------------------------------ scrape_completeness

def test_scrape_completeness_passes_when_every_query_completed(tmp_path):
    raw = _root(tmp_path, MY_ONLY) / "data" / "raw"
    assert audit.gate_scrape_completeness(audit.load_scrape_report(raw)[0]).status == "PASS"


def test_scrape_completeness_flags_failed_queries(tmp_path):
    raw = _root(tmp_path, MY_ONLY) / "data" / "raw"
    _report(raw, failed={"mycareersfuture": ["data scientist"]})
    gate = audit.gate_scrape_completeness(audit.load_scrape_report(raw)[0])
    assert gate.status == "INVESTIGATE" and "mycareersfuture: data scientist" in gate.detail


def test_scrape_completeness_is_skipped_without_a_report():
    assert audit.gate_scrape_completeness(None).status == "SKIPPED"


# ------------------------------------------------------------- posting_count_drop

def _postings(source, n, prefix="a"):
    ids = [f"{prefix}{i:031x}" if source == "mycareersfuture" else f"{i:08x}-2222-3333-4444-555555555555" for i in range(n)]
    return "".join(_raw(source, sid, "Data Scientist", f"Co {sid[:6]}", "Singapore") for sid in ids)


def _count_root(tmp_path, before: int, now: int) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "hiredly_20260924T010000Z.jsonl").write_text(_postings("hiredly", before))
    (raw / f"hiredly_{SNAP}.jsonl").write_text(_postings("hiredly", now))
    (raw / "hiredly_latest.jsonl").write_text(_postings("hiredly", now))
    return raw


@pytest.mark.parametrize("before, now", [(10, 10), (10, 7), (10, 12)])          # 30% exactly is not over the limit
def test_posting_count_drop_passes_at_or_under_30_percent(tmp_path, before, now):
    raw = _count_root(tmp_path, before, now)
    gate = audit.gate_posting_count_drop(raw, audit.raw_inputs(raw, ["hiredly"]))
    assert gate.status == "PASS" and f"hiredly {before}->{now}" in gate.detail


@pytest.mark.parametrize("before, now", [(10, 6), (100, 69)])
def test_posting_count_drop_over_30_percent_needs_review_and_never_fails(tmp_path, before, now):
    raw = _count_root(tmp_path, before, now)
    gate = audit.gate_posting_count_drop(raw, audit.raw_inputs(raw, ["hiredly"]))
    assert gate.status == "INVESTIGATE" and f"{before} -> {now}" in gate.detail


def test_posting_count_drop_counts_unique_postings_not_rows(tmp_path):
    raw = _count_root(tmp_path, 10, 10)
    duplicated = _postings("hiredly", 10) * 3                                    # same 10 postings from three queries
    (raw / "hiredly_20260924T010000Z.jsonl").write_text(duplicated)
    assert audit.gate_posting_count_drop(raw, audit.raw_inputs(raw, ["hiredly"])).status == "PASS"


def test_posting_count_drop_is_skipped_without_a_comparable_snapshot(tmp_path):
    raw = _count_root(tmp_path, 10, 1)
    (raw / "hiredly_20260924T010000Z.jsonl").unlink()
    gate = audit.gate_posting_count_drop(raw, audit.raw_inputs(raw, ["hiredly"]))
    assert gate.status == "SKIPPED" and "no previous snapshot" in gate.detail
    (raw / "hiredly_latest.jsonl").write_text(_postings("hiredly", 3))         # matches no snapshot
    gate = audit.gate_posting_count_drop(raw, audit.raw_inputs(raw, ["hiredly"]))
    assert gate.status == "SKIPPED" and "has no snapshot" in gate.detail


# ------------------------------------------------------------- decision_integrity

def test_decision_integrity_passes_one_decision_per_job(tmp_path):
    population, decisions = _evaluated(tmp_path)
    assert audit.gate_decision_integrity(decisions, population).status == "PASS"


def test_decision_integrity_fails_on_missing_duplicated_or_mixed_decisions(tmp_path):
    population, decisions = _evaluated(tmp_path)
    assert audit.gate_decision_integrity(decisions[:-1], population).status == "FAIL"
    gate = audit.gate_decision_integrity(decisions[:-1] + decisions[:1], population)
    assert gate.status == "FAIL" and "more than once" in gate.detail
    other = decisions[0].model_copy(update={"run": decisions[0].run.model_copy(update={"as_of_date": date(2026, 9, 24)})})
    gate = audit.gate_decision_integrity([other] + decisions[1:], population)
    assert gate.status == "FAIL" and "more than one run" in gate.detail


# ---------------------------------------------------------------------- code_state

def test_code_state_passes_a_clean_commit_and_flags_anything_else():
    assert audit.gate_code_state(CLEAN).status == "PASS"
    assert audit.gate_code_state({**CLEAN, "tree_clean": False, "tree_fingerprint": "f" * 64}).status == "INVESTIGATE"
    assert audit.gate_code_state({"commit": "UNKNOWN", "tree_clean": False, "tree_fingerprint": None}).status == "INVESTIGATE"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_code_identity_tracks_commit_and_uncommitted_changes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    git("init", "-q")
    (repo / "a.py").write_text("x = 1\n")
    git("add", "a.py")
    git("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "init")
    clean = audit.code_identity(repo)
    assert clean["tree_clean"] and clean["tree_fingerprint"] is None and len(clean["commit"]) == 40
    (repo / "a.py").write_text("x = 2\n")
    first = audit.code_identity(repo)
    (repo / "a.py").write_text("x = 3\n")
    second = audit.code_identity(repo)
    assert not first["tree_clean"] and first["commit"] == clean["commit"]
    assert first["tree_fingerprint"] != second["tree_fingerprint"]                  # different uncommitted code
    (repo / "a.py").write_text("x = 1\n")
    (repo / "new.py").write_text("y = 1\n")                                         # untracked files count too
    assert not audit.code_identity(repo)["tree_clean"]
    assert audit.code_identity(tmp_path / "not_a_repo") == {"commit": "UNKNOWN", "tree_clean": False, "tree_fingerprint": None}


# ------------------------------------------------------------------- live_coverage

def test_live_coverage_passes_when_every_singapore_job_has_current_evidence():
    gate = audit.gate_live_coverage({"sg_passed": 4, "covered": 4, "missing": []}, "REFRESH")
    assert gate.status == "PASS" and gate.job_warnings == {}


def test_live_coverage_wording_distinguishes_the_initial_run_from_the_refresh():
    facts = {"sg_passed": 4, "covered": 3, "missing": [{"job_id": "job_x", "reason": "no live record"}]}
    initial, refresh = audit.gate_live_coverage(facts, "INITIAL"), audit.gate_live_coverage(facts, "REFRESH")
    assert initial.status == refresh.status == "INVESTIGATE"
    assert "refresh required before the final run" in initial.detail
    assert "still lack current live evidence after the refresh" in refresh.detail
    assert initial.job_warnings == {"job_x": ["Live evidence missing for the current posting (no live record)"]}


def test_live_facts_count_only_singapore_jobs_evaluated_in_full(tmp_path):
    _, decisions = _evaluated(tmp_path, WITH_SG)
    facts = audit.live_facts(decisions)
    sg = [d for d in decisions if d.evaluation_scope == "FULL" and audit.live_evidence(d, None) is not None]
    assert len(sg) == 1 and sum(d.evaluation_scope == "FULL" for d in decisions) == 2          # the Malaysian job is not counted
    assert facts == {"sg_passed": 1, "covered": 0, "missing": [{"job_id": sg[0].job.job_id, "reason": "no live record"}]}


# ------------------------------------------------------------------- agency_markers

AGENCY_TEXT = DESC + "<p>Interested applicants please send your resume. Licence No. 12C3456 | Reg No. R1234567</p>"


def test_agency_markers_pass_without_markers(tmp_path):
    population, decisions = _evaluated(tmp_path)
    assert audit.gate_agency_markers(population, decisions).status == "PASS"


def test_agency_markers_flag_an_unprefixed_licence_on_a_job_not_classified_as_recruiter(tmp_path):
    corpus = {**MY_ONLY, "hiredly": _raw("hiredly", HID, "Data Scientist", "Acme Sdn Bhd", "Kuala Lumpur", desc=AGENCY_TEXT)}
    population, decisions = _evaluated(tmp_path, corpus)
    job = next(c.job for c in population if c.job.company == "Acme Sdn Bhd")
    assert job.employer_clarity != "RECRUITER"                    # the classifier needs the EA prefix (P0-2 scope)
    gate = audit.gate_agency_markers(population, decisions)
    assert gate.status == "INVESTIGATE" and job.job_id in gate.detail
    assert "Licence No. 12C3456" in gate.job_warnings[job.job_id][0]


def test_agency_markers_ignore_recruiters_and_jobs_not_evaluated_in_full(tmp_path):
    corpus = {**MY_ONLY, "hiredly": _raw("hiredly", HID, "Data Scientist", "Acme Sdn Bhd", "Kuala Lumpur", desc=AGENCY_TEXT)}
    population, decisions = _evaluated(tmp_path, corpus)
    recruiter = [c.model_copy(update={"job": c.job.model_copy(update={"employer_clarity": "RECRUITER"})}) for c in population]
    assert audit.gate_agency_markers(recruiter, decisions).status == "PASS"
    rejected = [d.model_copy(update={"evaluation_scope": "HARD_FILTER_REJECTED"}) for d in decisions]
    assert audit.gate_agency_markers(population, rejected).status == "PASS"


@pytest.mark.parametrize("text", ["EA Licence No.91C2918 Personnel Registration No. R26100003",      # real wordings, 10 Oct
                                  "License Number: 10C5117", "(Reg ID: R1100001)", "(EA: 94C3609 / R22100002)"])
def test_agency_marker_pattern_matches_real_licence_wordings(text):
    assert audit.AGENCY_MARKER.search(text) is not None


@pytest.mark.parametrize("text", ["Company Reg. No. 199601303W", "Registered in 2015, the team grew to 40 people in 12 cities.",
                                  "Reference ID 2024-77, apply by 30 June.",
                                  "Licence to use open data under CC BY 4.0."])
def test_agency_marker_pattern_ignores_ordinary_numbers(text):
    assert audit.AGENCY_MARKER.search(text) is None


# ------------------------------------------------------------- experience_citation

def test_experience_citation_passes_when_the_years_sentence_is_cited(tmp_path):
    _, decisions = _evaluated(tmp_path)
    assert any(d.candidate_alignment and d.candidate_alignment.experience.job_required_years for d in decisions)
    assert audit.gate_experience_citation(decisions).status == "PASS"


def test_experience_citation_flags_a_years_requirement_without_its_sentence(tmp_path):
    _, decisions = _evaluated(tmp_path)
    stripped = [d.model_copy(update={"key_evidence": [e for e in d.key_evidence if e.kind != "years_requirement"]}) for d in decisions]
    gate = audit.gate_experience_citation(stripped)
    assert gate.status == "INVESTIGATE" and "3 years required but no source sentence is cited" in str(gate.job_warnings)


# -------------------------------------------------------------------------- verdict

@pytest.mark.parametrize("statuses, expected", [
    ([], "PASS"), (["PASS", "SKIPPED"], "PASS"), (["PASS", "INVESTIGATE", "SKIPPED"], "INVESTIGATE"),
    (["INVESTIGATE", "FAIL", "PASS"], "FAIL"), (["SKIPPED"], "PASS")])
def test_verdict_is_the_most_severe_gate(statuses, expected):
    assert audit.verdict([audit.Gate(f"g{n}", s, "") for n, s in enumerate(statuses)]) == expected


# ------------------------------------------------------------ the daily run, PASS

def test_a_clean_run_passes_every_gate_and_is_archived_in_format_v2(tmp_path, capsys):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY))
    assert run_all.execute(run) == 0
    out = capsys.readouterr().out
    manifest = json.loads((run.out_dir / MANIFEST).read_text())
    assert manifest["audit"]["verdict"] == "PASS", manifest["audit"]["gates"]
    assert [g["gate"] for g in manifest["audit"]["gates"]] == [
        "raw_integrity", "publication_state", "scrape_report", "scrape_completeness", "posting_count_drop",
        "decision_integrity", "code_state", "live_coverage", "agency_markers", "experience_citation"]
    assert "DAILY RUN SUCCEEDED — audit verdict PASS" in out and "NOT CLEARED" not in out
    archive = run.archive_root / manifest["archive_id"]
    meta = verify_archive(archive)
    assert meta["archive_version"] == ARCHIVE_VERSION_V2 and meta["code"] == CLEAN == manifest["code"]
    assert (archive / MANIFEST).read_bytes() == (run.out_dir / MANIFEST).read_bytes()


def test_the_manifest_records_identity_inputs_and_output_checksums_but_never_itself(tmp_path):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY))
    assert run_all.execute(run) == 0
    m = json.loads((run.out_dir / MANIFEST).read_text())
    raw = run.root / "data" / "raw"
    assert m["schema_version"] == "run_manifest_v1" and m["run_stage"] == "REFRESH"
    assert m["run"] == run.info["run"] and m["started_utc"] <= m["finished_utc"]
    assert set(m["outputs"]) == set(OUTPUT_FILES) == {"decisions.json", "run_meta.json"}       # no self-checksum, no dashboard
    for name in OUTPUT_FILES:
        assert m["outputs"][name] == audit.sha256((run.out_dir / name).read_bytes())
    for s in ("mycareersfuture", "hiredly"):
        assert m["inputs"]["raw"][s] == {"file": f"{s}_latest.jsonl", "sha256": audit.sha256((raw / f"{s}_latest.jsonl").read_bytes()),
                                         "snapshot": f"{s}_{SNAP}.jsonl", "records": 1}
    assert m["inputs"]["scrape_report"]["sha256"] == audit.sha256((raw / "scrape_report_latest.json").read_bytes())
    assert m["scrape"]["hiredly"]["failed_queries"] == [] and m["inputs"]["live_check_records"] == 0
    assert m["live"]["sg_passed"] == 0 and m["live"]["refresh_required"] is False


def test_identical_code_and_inputs_reproduce_identical_decisions_and_audit(tmp_path):
    first, second = (_daily(tmp_path / n, _root(tmp_path / n, MY_ONLY)) for n in ("a", "b"))
    assert run_all.execute(first) == 0 and run_all.execute(second) == 0
    for name in OUTPUT_FILES:
        assert (first.out_dir / name).read_bytes() == (second.out_dir / name).read_bytes()
    m1, m2 = (json.loads((r.out_dir / MANIFEST).read_text()) for r in (first, second))
    for m in (m1, m2):
        del m["started_utc"], m["finished_utc"]
    assert m1 == m2


# ------------------------------------------------------------- INVESTIGATE / FAIL

def test_an_investigate_run_completes_but_is_not_reported_as_succeeded(tmp_path, capsys):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY, report=False))
    assert run_all.execute(run) == 0
    out = capsys.readouterr().out
    assert run.info["audit"]["audit"]["verdict"] == "INVESTIGATE"
    assert "DAILY RUN COMPLETED — audit verdict INVESTIGATE: NOT CLEARED" in out and "DAILY RUN SUCCEEDED" not in out
    assert "INVESTIGATE scrape_report: no scrape report" in out
    assert verify_archive(run.archive_root / run.info["archive"])["archive_version"] == ARCHIVE_VERSION_V2


@pytest.mark.parametrize("break_it, gate", [
    (lambda raw: (raw / "hiredly_latest.jsonl").write_text(_raw("hiredly", HID, "Data Scientist", "Acme Sdn Bhd",
                                                                 "Kuala Lumpur", desc=" ")), "scrape_report"),
    (lambda raw: (raw / ".publish-journal.json").write_text("{}"), "publication_state"),
])
def test_a_critical_audit_failure_stops_the_run_before_it_is_archived(tmp_path, capsys, break_it, gate):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY))
    break_it(run.root / "data" / "raw")
    assert run_all.execute(run) == run_all.EXIT_CODES["audit"] == 35
    out = capsys.readouterr().out
    assert "DAILY RUN FAILED at stage 'audit' (exit 35)" in out and f"{gate}:" in out
    assert "DAILY RUN SUCCEEDED" not in out and "DAILY RUN COMPLETED" not in out
    assert not run.archive_root.exists() or not any(run.archive_root.iterdir())          # not archived
    assert not any((run.out_dir / n).exists() for n in DASHBOARD_FILES)                  # no dashboard
    assert json.loads((run.out_dir / MANIFEST).read_text())["audit"]["verdict"] == "FAIL"


def test_decision_integrity_failure_stops_the_daily_run(tmp_path, capsys):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY))

    def coach_then_population_grows(r):                    # the decisions no longer cover the population
        run_all.stage_coach(r)
        r.info["population"] = r.info["population"] + r.info["population"][:1]
    stages = [(n, coach_then_population_grows if n == "coach" else f) for n, f in run_all.STAGES]
    assert run_all.execute(run, stages) == 35
    assert "decision_integrity: 2 decisions for a population of 3" in capsys.readouterr().out
    assert not run.archive_root.exists() or not any(run.archive_root.iterdir())


def test_a_stale_output_folder_with_an_unarchived_v2_run_is_never_replaced(tmp_path):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY))
    assert run_all.execute(run) == 0
    shutil.rmtree(run.archive_root)                                                      # the run's archive is gone
    assert run_all.execute(_daily(tmp_path, run.root)) == run_all.EXIT_CODES["coach"]


# ------------------------------------------------- initial run vs live-check refresh

def test_initial_run_requires_the_refresh_and_the_refresh_run_clears_it(tmp_path):
    root = _root(tmp_path, WITH_SG)
    initial = _daily(tmp_path, root, skip_scrape=False)
    assert run_all.execute(initial, _stages(initial=True)) == 0
    m = json.loads((initial.out_dir / MANIFEST).read_text())
    gate = _gate("live_coverage", m)
    sg_job = m["live"]["missing"][0]["job_id"]
    assert m["run_stage"] == "INITIAL" and m["live"]["refresh_required"] is True
    assert gate["status"] == "INVESTIGATE" and "refresh required before the final run" in gate["detail"]
    assert m["audit"]["job_warnings"][sg_job] == ["Live evidence missing for the current posting (no live record)"]

    live = tmp_path / "live_checks.jsonl"                                       # TEST FIXTURE: the refresh's record
    live.write_text(json.dumps({"record_id": f"lc-2026-09-25-{MCF2}", "job_id": sg_job, "source_job_id": MCF2,
                                "checked_on": "2026-09-25", "method": "test fixture", "page_reviewable": "YES",
                                "email_in_jd": "NO", "posting_status": "OPEN"}) + "\n")
    refresh = _daily(tmp_path, root, live=live)
    assert run_all.execute(refresh) == 0
    m = json.loads((refresh.out_dir / MANIFEST).read_text())
    assert m["run_stage"] == "REFRESH" and m["live"]["refresh_required"] is False and m["live"]["missing"] == []
    assert _gate("live_coverage", m)["status"] == "PASS" and m["audit"]["verdict"] == "PASS"
    assert m["inputs"]["live_check_records"] == 1 and m["archive_id"] != json.loads(
        (initial.archive_root / initial.info["archive"] / MANIFEST).read_text())["archive_id"]


def test_a_refresh_run_that_still_lacks_evidence_says_so(tmp_path):
    refresh = _daily(tmp_path, _root(tmp_path, WITH_SG))
    assert run_all.execute(refresh) == 0
    m = json.loads((refresh.out_dir / MANIFEST).read_text())
    assert m["run_stage"] == "REFRESH" and m["live"]["refresh_required"] is False
    assert "still lack current live evidence after the refresh" in _gate("live_coverage", m)["detail"]


# --------------------------------------------------------------- dashboard display

def test_investigate_shows_the_verdict_and_job_warnings_without_hiding_or_changing_decisions(tmp_path):
    corpus = {**WITH_SG, "hiredly": _raw("hiredly", HID, "Data Scientist", "Acme Sdn Bhd", "Kuala Lumpur", desc=AGENCY_TEXT)}
    run = _daily(tmp_path, _root(tmp_path, corpus))
    assert run_all.execute(run) == 0
    m = json.loads((run.out_dir / MANIFEST).read_text())
    assert m["audit"]["verdict"] == "INVESTIGATE" and len(m["audit"]["job_warnings"]) == 2
    decisions_before = (run.out_dir / "decisions.json").read_bytes()
    with_audit = {n: (run.out_dir / n).read_text() for n in DASHBOARD_FILES}
    counts_with = run.info["sections"]

    bare = tmp_path / "bare"                                                    # the same run without its manifest
    bare.mkdir()
    for name in OUTPUT_FILES:
        shutil.copy(run.out_dir / name, bare / name)
    counts_without = render_dashboard(bare, archive_root=run.archive_root, live_path=run.live_path)["counts"]
    assert counts_with == counts_without and (run.out_dir / "decisions.json").read_bytes() == decisions_before
    assert load_run_dir(run.out_dir)[0] == load_run_dir(bare)[0]

    md, page = with_audit["dashboard.md"], with_audit["opportunities.html"]
    assert "- Audit verdict: INVESTIGATE (NOT cleared, review the findings) · run stage REFRESH · job-level warnings: 2" in md
    assert "INVESTIGATE agency_markers:" in md and "- Audit warning: Agency registration or licence marker" in md
    assert "verdict-INVESTIGATE" in page and page.count("Audit warning") >= 2
    assert "Audit verdict: not recorded for this run" in (bare / "dashboard.md").read_text()
    headings = [line for line in md.splitlines() if line.startswith("## ")]
    assert headings == [line for line in (bare / "dashboard.md").read_text().splitlines() if line.startswith("## ")]
    verdict_block = re.sub(r"<[^>]+>", " ", page[page.index("class=\"verdict"):page.index("</div>", page.index("class=\"verdict"))])
    warnings = " ".join(w for ws in m["audit"]["job_warnings"].values() for w in ws)
    assert not re.search(CAUSAL, verdict_block + warnings + md[md.index("- Audit verdict"):md.index("- Jobs evaluated")], re.I)


# ------------------------------------------------------------------ archive format

class _Later(datetime):
    """The second run starts at a later time, so its fresh manifest differs from the archived one."""
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 25, 23, 0, tzinfo=timezone.utc)


def test_rerunning_the_same_inputs_and_code_reuses_the_archive_and_keeps_its_first_manifest(tmp_path, capsys, monkeypatch):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY))
    assert run_all.execute(run) == 0
    monkeypatch.setattr(run_all, "datetime", _Later)
    archive = run.archive_root / run.info["archive"]
    first_manifest = (archive / MANIFEST).read_bytes()
    again = _daily(tmp_path, run.root)
    assert run_all.execute(again) == 0
    assert again.info["archive"] == run.info["archive"] and again.info["archive_reused"] is True
    assert (archive / MANIFEST).read_bytes() == first_manifest == (again.out_dir / MANIFEST).read_bytes()
    assert len(list(run.archive_root.iterdir())) == 1
    assert "archive reused, its first manifest is kept" in capsys.readouterr().out


def test_different_code_on_the_same_inputs_gets_its_own_archive(tmp_path):
    root = _root(tmp_path, MY_ONLY)
    first = _daily(tmp_path, root, out="out1")
    assert run_all.execute(first) == 0
    second = _daily(tmp_path, root, code=OTHER_CODE, out="out2")
    assert run_all.execute(second) == 0
    assert first.info["archive"] != second.info["archive"]
    for name in (first.info["archive"], second.info["archive"]):
        assert verify_archive(first.archive_root / name)["archive_version"] == ARCHIVE_VERSION_V2
    r = first.info["run"]
    found = find_archive(r["as_of_date"], r["run_kind"], r["input_hashes"], first.archive_root)
    latest = max((json.loads((first.archive_root / n / MANIFEST).read_text())["started_utc"], n)
                 for n in (first.info["archive"], second.info["archive"]))[1]
    assert found.name == latest


def _writable_copy(archive: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "copies" / archive.name
    shutil.copytree(archive, copy)
    for p in copy.iterdir():
        os.chmod(p, 0o644)
    return copy


def _rewrite_meta(path: Path, **changes) -> None:
    meta = json.loads((path / "archive.json").read_text())
    meta.update(changes)
    (path / "archive.json").write_text(json.dumps(meta, indent=1, sort_keys=True))


def _rewrite_manifest(path: Path, **changes) -> None:
    """Edits the archived manifest AND its recorded hash, so only the manifest checks can catch it."""
    manifest = json.loads((path / MANIFEST).read_text())
    manifest.update(changes)
    data = json.dumps(manifest, indent=1, sort_keys=True).encode()
    (path / MANIFEST).write_bytes(data)
    meta = json.loads((path / "archive.json").read_text())
    _rewrite_meta(path, files={**meta["files"], MANIFEST: audit.sha256(data)})


@pytest.fixture
def v2_archive(tmp_path):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY))
    assert run_all.execute(run) == 0
    return _writable_copy(run.archive_root / run.info["archive"], tmp_path)


def test_v2_verification_detects_a_tampered_manifest_file(v2_archive):
    verify_archive(v2_archive)
    (v2_archive / MANIFEST).write_text((v2_archive / MANIFEST).read_text().replace('"PASS"', '"FAIL"', 1))
    with pytest.raises(ValueError, match="run_manifest.json does not match its recorded hash"):
        verify_archive(v2_archive)


@pytest.mark.parametrize("change, message", [
    ({"outputs": {"decisions.json": "0" * 64, "run_meta.json": "0" * 64}}, "output checksums do not match"),
    ({"archive_id": "2026-09-25_OPERATIONAL_0000000000000000"}, "run manifest names archive"),
    ({"code": OTHER_CODE}, "code identity differs from its manifest"),
    ({"run": {"as_of_date": "2026-09-24"}}, "describes a different run"),
])
def test_v2_verification_checks_the_manifest_against_the_archive_independently(v2_archive, change, message):
    _rewrite_manifest(v2_archive, **change)
    with pytest.raises(ValueError, match=message):
        verify_archive(v2_archive)


def test_v2_verification_rejects_a_changed_code_identity_or_a_missing_manifest(v2_archive, tmp_path):
    other = _writable_copy(v2_archive, tmp_path / "x")
    _rewrite_meta(v2_archive, code=OTHER_CODE)
    with pytest.raises(ValueError, match="archive id does not match its run identity"):
        verify_archive(v2_archive)
    meta = json.loads((other / "archive.json").read_text())
    _rewrite_meta(other, files={n: h for n, h in meta["files"].items() if n != MANIFEST})
    with pytest.raises(ValueError, match="must list exactly"):
        verify_archive(other)


def _through_audit(run):
    """Runs the stages up to and including the audit, without archiving."""
    run.config_bytes = (run_all.CONFIG_DIR / "career_coach.yaml").read_bytes()
    run.profile_bytes = (run_all.CONFIG_DIR / "profile.yaml").read_bytes()
    run.info["started_utc"] = "2026-09-25T01:00:00+00:00"
    for _, stage in run_all.STAGES[:4]:
        stage(run)


def test_archive_run_refuses_a_manifest_that_does_not_describe_the_outputs(tmp_path):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY))
    _through_audit(run)
    manifest = json.loads((run.out_dir / MANIFEST).read_text())
    manifest["outputs"]["decisions.json"] = "0" * 64
    with pytest.raises(ValueError, match="output checksums do not match"):
        archive_run(run.out_dir, run.config_bytes, run.profile_bytes, tmp_path / "a2", json.dumps(manifest).encode())
    assert not (tmp_path / "a2").exists() or not any((tmp_path / "a2").iterdir())


def test_archiving_without_a_manifest_is_still_format_v1(tmp_path):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY))
    _through_audit(run)
    path = archive_run(run.out_dir, run.config_bytes, run.profile_bytes, tmp_path / "v1")
    meta = verify_archive(path)
    r = run.info["run"]
    assert meta["archive_version"] == ARCHIVE_VERSION and "code" not in meta and not (path / MANIFEST).exists()
    assert path.name == archive_id(r["as_of_date"], r["run_kind"], r["input_hashes"])
    assert find_archive(r["as_of_date"], r["run_kind"], r["input_hashes"], tmp_path / "v1") == path


@pytest.mark.skipif(not ARCHIVE_ROOT.is_dir(), reason="real run archive not present")
def test_every_existing_real_archive_still_verifies_with_its_id_unchanged():
    archives = [p for p in sorted(ARCHIVE_ROOT.iterdir()) if not p.name.startswith(".")]
    assert archives
    for path in archives:
        meta = verify_archive(path)
        assert meta["archive_id"] == path.name
        if meta["archive_version"] == ARCHIVE_VERSION:
            assert path.name == archive_id(meta["as_of_date"], meta["run_kind"], meta["input_hashes"])


# --------------------------------------------- 10 Oct isolated replay (real corpus)

OCT10 = "20261010T080643Z"
EPS_JOB = "job_26df60ae440c865c"


@pytest.mark.skipif(not (ROOT / "data" / "raw" / f"hiredly_{OCT10}.jsonl").exists(), reason="10 Oct raw snapshot not present")
def test_the_10_october_replay_classifies_eps_as_an_agency_and_leaves_no_marker_conflict(tmp_path):
    """Reads the real 10 Oct snapshots and live-check log; writes only to tmp_path.
    Before P0-2 this replay's agency_markers gate flagged EPS ('Reg ID: R1100001' on a
    DIRECT_EMPLOYER posting); the classifier now reads that marker next to the company name."""
    raw = tmp_path / "root" / "data" / "raw"
    raw.mkdir(parents=True)
    for s in ("mycareersfuture", "hiredly"):
        shutil.copy(ROOT / "data" / "raw" / f"{s}_{OCT10}.jsonl", raw / f"{s}_latest.jsonl")
    population, raw_hash, _ = load_population(tmp_path / "root", date(2026, 10, 10))
    decisions, _ = run_coach(tmp_path / "root", population, date(2026, 10, 10), "OPERATIONAL",
                             ROOT / "data" / "live_checks.jsonl", raw_hash)
    eps = next(c.job for c in population if c.job.job_id == EPS_JOB)
    assert eps.employer_clarity == "RECRUITER"
    assert next(d for d in decisions if d.job.job_id == EPS_JOB).next_action == "RESEARCH_EMPLOYER"
    gate = audit.gate_agency_markers(population, decisions)
    assert gate.status == "PASS" and gate.job_warnings == {}
