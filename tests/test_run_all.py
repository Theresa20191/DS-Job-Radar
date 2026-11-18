"""Daily CLI gate: `scripts/run_all.py` runs scrape -> pipeline -> Career
Coach (OPERATIONAL) -> archive -> dashboard in order, stops loudly at the
first failure, never renders from stale output, writes no legacy artefact
and never touches the application log. Raw corpora here are SYNTHETIC TEST
FIXTURES in tmp_path unless a test says it uses the real corpus."""

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.career_coach import application_log
from src.career_coach.inputs import load_live_checks
from src.career_coach.operational import DASHBOARD_FILES, RUN_FILES, render_dashboard
from src.career_coach.run_archive import load_archived_decisions, verify_archive
from src.models.raw_job import RawJob

ROOT = Path(__file__).resolve().parent.parent
DESC = ("<p>Responsibilities:</p><ul><li>Build credit risk models to support lending decisions across the portfolio.</li>"
        "<li>Develop scorecards and validate machine learning models with the model risk team.</li></ul>"
        "<p>Requirements:</p><ul><li>3 years of experience with Python and SQL.</li></ul>")


def _load_run_all():
    spec = importlib.util.spec_from_file_location("run_all", ROOT / "scripts" / "run_all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_all = _load_run_all()


def _raw(source, sid, title, company, loc) -> str:
    return RawJob(job_id=f"{source}_{sid}", source=source, source_job_id=sid, source_url=f"https://example.test/{source}/{sid}",
                  company=company, job_title=title, job_description=DESC, location_raw=loc, posted_date_raw="2026-09-15",
                  scraped_at=datetime(2026, 9, 20, tzinfo=timezone.utc)).model_dump_json() + "\n"


@pytest.fixture
def daily(tmp_path):
    """TEST FIXTURE: a tmp root with a small synthetic raw corpus."""
    raw = tmp_path / "root" / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "mycareersfuture_latest.jsonl").write_text(_raw("mycareersfuture", "sg1", "Data Scientist", "Acme Pte Ltd", "Singapore"))
    (raw / "hiredly_latest.jsonl").write_text(_raw("hiredly", "my1", "Data Scientist", "Acme Sdn Bhd", "Kuala Lumpur")
                                              + _raw("hiredly", "my2", "Software Engineer", "Other Sdn Bhd", "Kuala Lumpur"))
    return run_all.DailyRun(root=tmp_path / "root", as_of=date(2026, 9, 25), out_dir=tmp_path / "out",
                            archive_root=tmp_path / "archive", live_path=tmp_path / "no_live.jsonl", skip_scrape=True)


def _recording(calls: list, failing: str | None = None, error=RuntimeError("boom")):
    stages = []
    for name, fn in run_all.STAGES:
        def wrapped(run, name=name, fn=fn):
            calls.append(name)
            if name == failing:
                raise error
            fn(run)
        stages.append((name, wrapped))
    return stages


# ------------------------------------------------------------- success path

def test_stages_run_in_order_and_publish_one_consistent_run(daily, capsys):
    calls = []
    assert run_all.execute(daily, _recording(calls)) == 0
    out = capsys.readouterr().out
    assert calls == ["scrape", "pipeline", "coach", "audit", "archive", "dashboard"]
    assert "run_kind: OPERATIONAL" in out and "as_of_date: 2026-09-25" in out
    # P0-1 (intentional change): this fixture has no scrape report and a Singapore job without live
    # evidence, so its audit verdict is INVESTIGATE and the run is completed but NOT reported as succeeded.
    assert daily.info["audit"]["audit"]["verdict"] == "INVESTIGATE"
    assert "DAILY RUN COMPLETED — audit verdict INVESTIGATE: NOT CLEARED" in out and "DAILY RUN SUCCEEDED" not in out
    assert sorted(p.name for p in daily.out_dir.iterdir()) == sorted(RUN_FILES + DASHBOARD_FILES)
    archive = daily.archive_root / daily.info["archive"]
    assert verify_archive(archive)["run_kind"] == "OPERATIONAL"
    assert (archive / "decisions.json").read_bytes() == (daily.out_dir / "decisions.json").read_bytes()
    assert set(load_archived_decisions(archive)) == {d["job"]["job_id"] for d in
                                                     json.loads((daily.out_dir / "decisions.json").read_text())}
    assert daily.info["meta"]["passed"] == 2 and daily.info["meta"]["rejected"] == 1
    assert "scrape: SKIPPED" in out


def test_rerunning_the_same_day_replaces_outputs_and_reuses_the_archive(daily):
    assert run_all.execute(daily) == 0
    first = {n: (daily.out_dir / n).read_bytes() for n in RUN_FILES + DASHBOARD_FILES}
    assert run_all.execute(daily) == 0
    assert {n: (daily.out_dir / n).read_bytes() for n in RUN_FILES + DASHBOARD_FILES} == first
    assert len(list(daily.archive_root.iterdir())) == 1


# ------------------------------------------------------------- failures

@pytest.mark.parametrize("stage", ["scrape", "pipeline", "coach", "audit", "archive", "dashboard"])
def test_a_failed_stage_stops_the_run_loudly(daily, capsys, stage):
    calls = []
    code = run_all.execute(daily, _recording(calls, failing=stage))
    out = capsys.readouterr().out
    order = ["scrape", "pipeline", "coach", "audit", "archive", "dashboard"]
    assert code == run_all.EXIT_CODES[stage] != 0
    assert calls == order[:order.index(stage) + 1]                     # nothing downstream ran
    assert f"STAGE {stage} FAILED" in out and f"DAILY RUN FAILED at stage '{stage}'" in out
    assert "DAILY RUN SUCCEEDED" not in out and "DAILY RUN COMPLETED" not in out and "DAILY RUN SUMMARY" not in out
    assert not any((daily.out_dir / n).exists() for n in DASHBOARD_FILES)    # no dashboard for a failed run


def test_scraper_non_zero_exit_fails_the_scrape(daily, monkeypatch):
    daily.skip_scrape = False
    monkeypatch.setattr(run_all.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 3))
    with pytest.raises(RuntimeError, match="exited 3"):
        run_all.stage_scrape(daily)


def test_scraper_exit_zero_without_fresh_raw_fails_the_scrape(daily, monkeypatch):
    daily.skip_scrape = False
    monkeypatch.setattr(run_all.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    with pytest.raises(RuntimeError, match="no fresh raw file"):
        run_all.stage_scrape(daily)


def test_scraper_writing_an_empty_raw_file_fails_the_scrape(daily, monkeypatch):
    daily.skip_scrape = False

    def empty_scrape(*a, **k):
        for s in ("mycareersfuture", "hiredly"):
            (daily.root / "data" / "raw" / f"{s}_latest.jsonl").write_text("")
        return subprocess.CompletedProcess(a, 0)

    monkeypatch.setattr(run_all.subprocess, "run", empty_scrape)
    with pytest.raises(RuntimeError, match="empty raw file"):
        run_all.stage_scrape(daily)


def test_missing_raw_data_fails_the_pipeline(daily, capsys):
    for p in (daily.root / "data" / "raw").iterdir():
        p.unlink()
    assert run_all.execute(daily) == run_all.EXIT_CODES["pipeline"]
    assert "no raw job records" in capsys.readouterr().out and not daily.out_dir.exists()


def test_an_unarchived_previous_run_is_never_discarded(daily, capsys):
    assert run_all.execute(daily) == 0
    for p in daily.archive_root.iterdir():                              # lose the archive of the published run
        for f in p.iterdir():
            f.chmod(0o644)
            f.unlink()
        p.rmdir()
    before = (daily.out_dir / "decisions.json").read_bytes()
    assert run_all.execute(daily) == run_all.EXIT_CODES["coach"]
    assert "unarchived run" in capsys.readouterr().out
    assert (daily.out_dir / "decisions.json").read_bytes() == before


def test_archive_failure_leaves_no_dashboard_beside_new_decisions(daily, monkeypatch):
    assert run_all.execute(daily) == 0                                  # a previous dashboard exists
    monkeypatch.setattr(run_all, "archive_run", lambda *a, **k: (_ for _ in ()).throw(ValueError("disk full")))
    assert run_all.execute(daily) == run_all.EXIT_CODES["archive"]
    assert (daily.out_dir / "decisions.json").exists()
    assert not any((daily.out_dir / n).exists() for n in DASHBOARD_FILES)


def test_dashboard_renders_only_from_the_expected_run(daily):
    assert run_all.execute(daily) == 0
    other = dict(daily.info["run"], as_of_date="2026-09-20")
    with pytest.raises(ValueError, match="does not hold the expected run"):
        render_dashboard(daily.out_dir, expected_run=other)


# ------------------------------------------------------ legacy boundary

LEGACY = ("src.scoring", "src.transition", "src.reporting", "src.strategy", "src.models.ptf", "src.models.transition",
          "src.models.scored_job", "src.models.strategy", "src.pipeline.orchestrator", "src.pipeline.quality_report",
          "src.calibration", "src.learning")


@pytest.mark.parametrize("path", ["scripts/run_all.py", "src/career_coach/operational.py"])
def test_daily_path_imports_no_legacy_code(path):
    tree = ast.parse((ROOT / path).read_text())
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
              {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not {m for m in modules if m.startswith(LEGACY)}
    text = (ROOT / path).read_text()
    for legacy_command in ("run_pipeline.py", "generate_report.py", "log_application.py"):
        assert f'"{legacy_command}"' not in text


def test_a_daily_run_loads_no_legacy_module_at_runtime(daily):
    code = (f"import sys, importlib.util; from datetime import date; from pathlib import Path\n"
            f"spec = importlib.util.spec_from_file_location('run_all', {str(ROOT / 'scripts' / 'run_all.py')!r})\n"
            f"m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            f"run = m.DailyRun(root=Path({str(daily.root)!r}), as_of=date(2026, 9, 25), out_dir=Path({str(daily.out_dir)!r}),"
            f" archive_root=Path({str(daily.archive_root)!r}), live_path=Path({str(daily.live_path)!r}), skip_scrape=True)\n"
            f"assert m.execute(run) == 0\n"
            f"print(sorted(x for x in sys.modules if x.startswith({LEGACY!r})))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert out.strip().splitlines()[-1] == "[]"


# ------------------------------------------------------ real corpus regression

VERIFIED_RAW = "9c75bb6dc7c294d17cf823e3df61f72dedf34c4cd7b5df3cf26e3ea8818c170e"
# The live-check evidence the verified 25 Sep run actually used (run.input_hashes.eligible_live_checks).
# The append-only live-check file itself is expected to grow, so its whole-file hash is never pinned.
VERIFIED_ELIGIBLE_LIVE = "d9ee3d75e3090c411e2c7a867788df33acc2be3d8afa4dcf4fbdf1fedce82388"
VERIFIED_OPERATIONAL = "fc0314ddbe25a00b57bc4d5d78e7d22276e2ec6b7eefdce6290425644a32a28a"
LEGACY_OUTPUTS = ("jobs_scored.parquet", "daily_top_jobs.csv", "daily_top_jobs.parquet", "daily_job_radar.md")


def _tree_hash(*paths: Path) -> str:
    digest = hashlib.sha256()
    for p in sorted(f for path in paths if path.exists() for f in ([path] if path.is_file() else path.rglob("*")) if f.is_file()):
        digest.update(str(p).encode() + p.read_bytes())
    return digest.hexdigest()


@pytest.mark.skipif(not (ROOT / "data" / "live_checks.jsonl").exists(), reason="real live-check history not present")
def test_real_daily_run_reproduces_the_verified_operational_run(tmp_path, verified_corpus_root):
    """Change detector: the 25 Sep operational run of the CURRENT pipeline over
    fixed inputs (the 24 Aug raw snapshots in a scratch root, never the
    production raw data, and the real append-only live-check history) is
    deterministic, and its decision bytes are pinned. Any change to derived
    output fails here and must be re-pinned deliberately. The archived bytes
    of the original 25 Sep run are protected separately, by the immutable
    archive checks in test_career_coach_dashboard.py."""
    live = ROOT / "data" / "live_checks.jsonl"
    live_before = live.read_bytes()
    sources = _tree_hash(ROOT / "data" / "raw", ROOT / "data" / "processed")
    legacy = {n: _tree_hash(ROOT / "data" / "output" / n) for n in LEGACY_OUTPUTS}
    log_existed = application_log.DEFAULT_LOG.exists()
    as_of = date(2026, 9, 25)

    run = run_all.DailyRun(root=verified_corpus_root, as_of=as_of, out_dir=tmp_path / "out",
                           archive_root=tmp_path / "archive", live_path=live, skip_scrape=True)
    assert run_all.execute(run) == 0
    decisions = (run.out_dir / "decisions.json").read_bytes()
    meta = run.info["meta"]
    assert run.info["run"]["input_hashes"]["raw_corpus"] == VERIFIED_RAW

    # The run consumed exactly the verified eligible evidence ...
    assert run.info["run"]["input_hashes"]["eligible_live_checks"] == VERIFIED_ELIGIBLE_LIVE
    used = [r for r in load_live_checks(live) if r.record_id in set(meta["live_checks_used"])]
    assert len(used) == 56 and all(r.checked_on <= as_of for r in used)
    # ... every other record in the append-only history was ignored as out of scope for this date ...
    assert all(reason in ("AFTER_AS_OF", "OUTSIDE_WINDOW") for _, reason in meta["live_checks_ignored"])
    assert len(meta["live_checks_used"]) + len(meta["live_checks_ignored"]) == len(load_live_checks(live))
    # ... and the decisions are the verified operational bytes.
    assert hashlib.sha256(decisions).hexdigest() == VERIFIED_OPERATIONAL

    # The same run from only the used records is byte-identical: the rest of the history changes nothing.
    only_used = tmp_path / "only_used.jsonl"
    only_used.write_text("".join(line + "\n" for line in live.read_text().splitlines()
                                 if line.strip() and json.loads(line)["record_id"] in set(meta["live_checks_used"])))
    narrow = run_all.DailyRun(root=verified_corpus_root, as_of=as_of, out_dir=tmp_path / "out2",
                              archive_root=tmp_path / "archive2", live_path=only_used, skip_scrape=True)
    assert run_all.execute(narrow) == 0
    assert (narrow.out_dir / "decisions.json").read_bytes() == decisions

    assert verify_archive(tmp_path / "archive" / run.info["archive"])["decision_count"] == 118
    assert not any((run.out_dir / n).exists() for n in LEGACY_OUTPUTS)             # no legacy artefacts
    assert {n: _tree_hash(ROOT / "data" / "output" / n) for n in LEGACY_OUTPUTS} == legacy
    assert _tree_hash(ROOT / "data" / "raw", ROOT / "data" / "processed") == sources
    assert live.read_bytes() == live_before                                         # evidence never rewritten
    assert application_log.DEFAULT_LOG.exists() == log_existed
