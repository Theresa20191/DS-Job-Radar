"""Daily operational run: scrape -> pipeline -> Career Coach (OPERATIONAL)
-> audit -> immutable archive -> dashboard -> summary.

    python scripts/run_all.py [--as-of YYYY-MM-DD] [--skip-scrape] [--out DIR]
                              [--archive-root DIR] [--live-checks FILE]

  --as-of        run date (default: today's date, shown in the summary)
  --skip-scrape  evaluate the raw data already on disk (reported as SKIPPED)
  --out          operational output folder (default data/output/career_coach_operational)

The pipeline stage only loads, deduplicates and enriches raw jobs and applies
the existing hard filter; nothing is scored or tiered and no legacy report is
written. Stages run in order and stop at the first failure with a non-zero
exit code; no later stage runs on missing or stale input. Nothing here reads
or writes the application log.

The audit stage (src/career_coach/run_audit.py) writes run_manifest.json and
grades the run PASS, INVESTIGATE or FAIL. FAIL stops the run before it is
archived (exit 35). INVESTIGATE completes the run, but the summary reports it
as not cleared; only a PASS run ends with "DAILY RUN SUCCEEDED". A run that
scraped is the INITIAL run of its day; a --skip-scrape run is a REFRESH run
(normally after recording live checks). Operational runs are archived in
format v2, whose id includes the code identity recorded in the manifest.

The whole run holds the pipeline lock (src/career_coach/run_lock.py), taken
before anything else: a second pipeline process (another run_all, a
standalone scraper, the live-check producer or recorder, a shadow run, a
dashboard render or smoke_test.py) is refused with exit 5, and this run is refused with exit 5,
changing nothing, while another one holds it. A lock that cannot be set up
(the lock file cannot be created or written, or an unsafe lock folder) stops
the run with exit 1 before any stage. The scraper child inherits the
lock, so it stays held until the scraper exits even if run_all is killed.

Legacy tools (run_pipeline.py, generate_report.py) remain available separately.
"""

import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.career_coach import run_lock  # noqa: E402
from src.career_coach.operational import (  # noqa: E402
    DASHBOARD_FILES,
    RUN_FILES,
    enabled_sources,
    load_population,
    load_run_dir,
    render_dashboard,
    run_coach,
    write_run,
)
from src.career_coach.run_archive import ARCHIVE_ROOT, MANIFEST, archive_run, find_archive, verify_archive  # noqa: E402
from src.career_coach.run_audit import build_manifest, code_identity, load_manifest, write_manifest  # noqa: E402
from src.config import CONFIG_DIR  # noqa: E402

RUN_KIND = "OPERATIONAL"
EXIT_CODES = {"lock_setup": run_lock.LOCK_SETUP_EXIT, "lock": run_lock.LOCK_EXIT, "scrape": 10, "pipeline": 20, "coach": 30, "audit": 35, "archive": 40, "dashboard": 50}


@dataclass
class DailyRun:
    root: Path
    as_of: date
    out_dir: Path
    archive_root: Path
    live_path: Path
    skip_scrape: bool = False
    config_bytes: bytes = b""
    profile_bytes: bytes = b""
    code: dict | None = None             # code identity; None: read from this repository's git state
    lock: run_lock.PipelineLock | None = None      # held for the whole run by execute()
    info: dict = field(default_factory=dict)


# ---------------------------------------------------------------- stages

def stage_scrape(run: DailyRun) -> None:
    if run.skip_scrape:
        run.info["scrape"] = "SKIPPED (--skip-scrape): evaluating the raw data already on disk"
        return
    raw_dir = run.root / "data" / "raw"
    before = {s: (raw_dir / f"{s}_latest.jsonl").stat().st_mtime_ns if (raw_dir / f"{s}_latest.jsonl").exists() else None
              for s in enabled_sources()}
    passed = run_lock.child_process_args(run.lock) if run.lock else {}     # the scraper inherits this run's lock
    result = subprocess.run([sys.executable, str(run.root / "scripts" / "run_scraper.py")], **passed)
    if result.returncode != 0:
        raise RuntimeError(f"run_scraper.py exited {result.returncode}")
    for source, mtime in before.items():
        path = raw_dir / f"{source}_latest.jsonl"
        if not path.exists() or path.stat().st_mtime_ns == mtime:
            raise RuntimeError(f"scrape wrote no fresh raw file for {source}")
        if not any(line.strip() for line in path.read_text().splitlines()):
            raise RuntimeError(f"scrape wrote an empty raw file for {source}")
    run.info["scrape"] = "OK"


def stage_pipeline(run: DailyRun) -> None:
    run.info["population"], run.info["raw_hash"], run.info["raw_counts"] = load_population(run.root, run.as_of)


def _archived(out_dir: Path, archive_root: Path) -> bool:
    """Is the run in out_dir archived? A run with a manifest names its v2
    archive; an older run is found by its v1 identity."""
    manifest = load_manifest(out_dir)
    if manifest is not None:
        path = archive_root / manifest["archive_id"]
        return path.exists() and bool(verify_archive(path))
    prev = load_run_dir(out_dir)[0][0].run
    return find_archive(prev.as_of_date.isoformat(), prev.run_kind, prev.input_hashes.model_dump(), archive_root) is not None


def stage_coach(run: DailyRun) -> None:
    if (run.out_dir / "decisions.json").exists():
        prev = load_run_dir(run.out_dir)[0][0].run
        if not _archived(run.out_dir, run.archive_root):
            raise RuntimeError(f"{run.out_dir} holds an unarchived run ({prev.as_of_date}, {prev.run_kind}); "
                               "archive or move it before replacing it")
    run_lock.require_lock(run.out_dir)
    for name in RUN_FILES + DASHBOARD_FILES:            # the previous run is safe in the archive
        (run.out_dir / name).unlink(missing_ok=True)
    decisions, meta = run_coach(run.root, run.info["population"], run.as_of, RUN_KIND, run.live_path, run.info["raw_hash"])
    write_run(run.out_dir, decisions, meta)
    run.info["run"] = decisions[0].run.model_dump(mode="json")
    run.info["meta"] = meta
    run.info["priorities"] = dict(sorted(Counter(d.application_priority for d in decisions).items()))


def stage_audit(run: DailyRun) -> None:
    decisions, _ = load_run_dir(run.out_dir)
    manifest = build_manifest(root=run.root, out_dir=run.out_dir, sources=enabled_sources(), decisions=decisions,
                              population=run.info["population"], run_stage="REFRESH" if run.skip_scrape else "INITIAL",
                              code=run.code, started_utc=run.info["started_utc"],
                              finished_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              live_records=sum(1 for line in run.live_path.read_text().splitlines() if line.strip())
                              if run.live_path.exists() else 0)
    write_manifest(run.out_dir, manifest)
    run.info["audit"] = manifest
    failed = [g for g in manifest["audit"]["gates"] if g["status"] == "FAIL"]
    if failed:
        raise RuntimeError("audit FAIL, the run is not archived: " + "; ".join(f"{g['gate']}: {g['detail']}" for g in failed))


def stage_archive(run: DailyRun) -> None:
    manifest_path = run.out_dir / MANIFEST
    path = archive_run(run.out_dir, run.config_bytes, run.profile_bytes, run.archive_root, manifest_bytes=manifest_path.read_bytes())
    run.info["archive"] = verify_archive(path)["archive_id"]
    archived = (path / MANIFEST).read_bytes()
    if archived != manifest_path.read_bytes():           # same inputs and code: the archive keeps its first manifest
        run_lock.require_lock(run.out_dir)
        manifest_path.write_bytes(archived)
        run.info["audit"] = json.loads(archived)
        run.info["archive_reused"] = True


def stage_dashboard(run: DailyRun) -> None:
    run.info["sections"] = render_dashboard(run.out_dir, expected_run=run.info["run"], archive_root=run.archive_root,
                                            live_path=run.live_path)["counts"]


STAGES = [("scrape", stage_scrape), ("pipeline", stage_pipeline), ("coach", stage_coach), ("audit", stage_audit),
          ("archive", stage_archive), ("dashboard", stage_dashboard)]


# ---------------------------------------------------------------- driver

def summary(run: DailyRun) -> list[str]:
    r, meta = run.info["run"], run.info["meta"]
    return [
        "=" * 70, "DAILY RUN SUMMARY",
        f"as_of_date: {r['as_of_date']} · run_kind: {r['run_kind']} · coach_version: {r['coach_version']}",
        f"archive: {run.info['archive']} (in {run.archive_root})",
        "input hashes: " + ", ".join(f"{k} {v[:12]}" for k, v in sorted(r["input_hashes"].items())),
        f"scrape: {run.info['scrape']}",
        "raw records: " + ", ".join(f"{s} {n}" for s, n in sorted(run.info["raw_counts"].items())),
        f"population: {meta['population']} (passed {meta['passed']}, rejected by the hard filter {meta['rejected']})",
        f"live checks: used {len(meta['live_checks_used'])}, ignored {len(meta['live_checks_ignored'])} "
        f"(window {meta['live_check_window_days']} days)",
        f"priorities: {run.info['priorities']}",
        f"dashboard placements: {run.info['sections']}",
        f"outputs: {run.out_dir}/ ({', '.join(RUN_FILES + DASHBOARD_FILES)})",
        "application log: not read or written by this run",
    ] + audit_lines(run.info["audit"], run.info.get("archive_reused", False))


def audit_lines(manifest: dict, reused: bool) -> list[str]:
    audit, code = manifest["audit"], manifest["code"]
    lines = [f"run stage: {manifest['run_stage']} · code {code['commit'][:12]}{'' if code['tree_clean'] else ' (uncommitted changes)'}"
             + (" · archive reused, its first manifest is kept" if reused else ""),
             f"audit verdict: {audit['verdict']}"]
    lines += [f"  {g['status']:<11} {g['gate']}: {g['detail']}" for g in audit["gates"] if g["status"] != "PASS"]
    if audit["job_warnings"]:
        lines.append(f"  job-level warnings: {len(audit['job_warnings'])} job(s) (see run_manifest.json and the dashboard)")
    return lines


def execute(run: DailyRun, stages=STAGES) -> int:
    try:
        run.lock = run_lock.acquire(run.root / "data", purpose="run_all")
    except run_lock.LockHeld as e:
        print(f"DAILY RUN REFUSED (exit {EXIT_CODES['lock']}): {e}")
        return EXIT_CODES["lock"]
    except run_lock.LockSetupError as e:
        print(f"DAILY RUN FAILED before any stage (exit {EXIT_CODES['lock_setup']}): pipeline lock setup failed: {e}")
        return EXIT_CODES["lock_setup"]
    try:
        return _execute(run, stages)
    finally:                                                               # also on KeyboardInterrupt
        run_lock.release(run.lock)
        run.lock = None


def _execute(run: DailyRun, stages) -> int:
    run.config_bytes = (CONFIG_DIR / "career_coach.yaml").read_bytes()     # the exact files this run uses
    run.profile_bytes = (CONFIG_DIR / "profile.yaml").read_bytes()
    run.code = run.code or code_identity(ROOT)
    run.info["started_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Daily run as_of {run.as_of.isoformat()} ({RUN_KIND}) -> {run.out_dir}")
    for name, stage in stages:
        print(f"=== {name} ===", flush=True)
        try:
            stage(run)
        except Exception as e:                                             # every failure stops the run
            print(f"STAGE {name} FAILED: {type(e).__name__}: {e}")
            print(f"DAILY RUN FAILED at stage '{name}' (exit {EXIT_CODES[name]}); later stages were not run.")
            return EXIT_CODES[name]
    print("\n".join(summary(run)))
    if run.info["audit"]["audit"]["verdict"] == "PASS":
        print("DAILY RUN SUCCEEDED — audit verdict PASS")
    else:
        print("DAILY RUN COMPLETED — audit verdict INVESTIGATE: NOT CLEARED, review the findings above")
    return 0


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def main():
    argv = sys.argv[1:]
    run = DailyRun(root=ROOT, as_of=date.fromisoformat(_arg(argv, "--as-of")) if "--as-of" in argv else date.today(),
                   out_dir=Path(_arg(argv, "--out") or ROOT / "data" / "output" / "career_coach_operational"),
                   archive_root=Path(_arg(argv, "--archive-root") or ARCHIVE_ROOT),
                   live_path=Path(_arg(argv, "--live-checks") or ROOT / "data" / "live_checks.jsonl"),
                   skip_scrape="--skip-scrape" in argv)
    sys.exit(execute(run))


if __name__ == "__main__":
    main()
