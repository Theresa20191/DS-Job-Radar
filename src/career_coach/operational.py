"""Career Coach operational stages, shared by `scripts/run_all.py`,
`scripts/career_coach_shadow.py` and `scripts/career_coach_dashboard.py` so
every entry point produces byte-identical outputs.

Upstream of the Coach this path only loads raw jobs, deduplicates and
enriches them, and consumes the existing hard filter (IN-1). It never scores,
tiers, applies the lens, builds strategies or renders cards.
"""

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

from src.career_coach.dashboard import build as build_dashboard
from src.career_coach.dashboard import render_csv, render_html, render_markdown
from src.career_coach.engine import Coach
from src.career_coach.inputs import build_population, load_contact_companies, load_live_checks, load_raw_jobs
from src.career_coach.run_archive import MANIFEST, find_archive, load_archived_decisions, verify_archive
from src.career_coach.run_lock import case_insensitive_dir, path_identity, protected_roots, require_lock
from src.config import load_career_coach, load_profile, load_scoring, load_sources
from src.dedup.deduplicator import deduplicate
from src.enrichment.enrich import enrich_job
from src.models.career_coach import CareerCoachDecision, CoachInput

ACTIVE_OUTPUT = "career_coach_operational"        # run_all's default output folder name under <data>/output
RUN_FILES = ("decisions.json", "run_meta.json", "run_manifest.json")     # the manifest: operational runs only
DASHBOARD_FILES = ("dashboard.md", "opportunities.csv", "opportunities.html")


def _active_name(name: str, directory: Path) -> bool:
    """Is name the active folder's name or a dot-sibling's, as directory
    looks names up (case folded only on a case-insensitive filesystem)?"""
    if case_insensitive_dir(directory):
        name, active = name.casefold(), ACTIVE_OUTPUT.casefold()
    else:
        active = ACTIVE_OUTPUT
    return name == active or name.startswith(f".{active}")


def is_active_output(path: Path) -> bool:
    """Is path the active operational output folder, or one of its
    dot-siblings (existing or not), in a protected data folder? Decided by
    file identity, not path text: a case variant, a symlinked parent or a
    symlink to the folder is caught. Only run_all writes there."""
    target = Path(path) if Path(path).is_absolute() else Path.cwd() / path
    resolved = Path(os.path.realpath(target))                            # follows dangling links and chains too
    for root in protected_roots():
        output = root / "output"
        output_id = path_identity(output)
        if output_id is None:
            continue
        if any(path_identity(t.parent) == output_id and _active_name(t.name, output) for t in (target, resolved)):
            return True
        if path_identity(target) is not None and any(                    # e.g. a symlink placed elsewhere
                _active_name(entry, output) and os.path.samefile(output / entry, target) for entry in os.listdir(output)):
            return True
    return False


def enabled_sources() -> list[str]:
    return [n for n, cfg in load_sources()["sources"].items() if cfg.get("enabled", True)]


def load_population(root: Path, as_of: date) -> tuple[list[CoachInput], str, dict]:
    """raw -> dedup -> enrich -> existing hard filter. Returns the complete
    evaluated population, the raw-corpus hash and raw record counts."""
    sources = enabled_sources()
    raw_dir = root / "data" / "raw"
    raw = load_raw_jobs(raw_dir, sources)
    digest = hashlib.sha256()
    counts = {}
    for source in sources:
        path = raw_dir / f"{source}_latest.jsonl"
        if path.exists():
            data = path.read_bytes()
            digest.update(source.encode() + b"\0" + data)
            counts[source] = sum(1 for line in data.splitlines() if line.strip())
    if not raw:
        raise ValueError(f"no raw job records found in {raw_dir} for sources {sources}")
    enriched = [enrich_job(j) for j in deduplicate(raw, as_of)]
    population = build_population(enriched, load_scoring())
    if not population:
        raise ValueError("the evaluated population is empty")
    return population, digest.hexdigest(), counts


def run_coach(root: Path, population: list[CoachInput], as_of: date, run_kind: str, live_path: Path,
              raw_hash: str) -> tuple[list[CareerCoachDecision], dict]:
    coach = Coach(load_profile(), load_career_coach(), load_contact_companies(root / "data" / "contacts.csv"))
    decisions, meta = coach.run(population, as_of, run_kind, load_live_checks(live_path), raw_hash)
    meta.update({"population": len(population), "passed": sum(c.hard_filter.passed for c in population),
                 "rejected": sum(not c.hard_filter.passed for c in population)})
    return decisions, meta


def write_run(out_dir: Path, decisions: list[CareerCoachDecision], meta: dict) -> None:
    require_lock(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "decisions.json").write_text(json.dumps(
        [d.model_dump(mode="json") for d in decisions], indent=1, ensure_ascii=False, sort_keys=True))
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True))


def load_run_dir(run_dir: Path) -> tuple[list[CareerCoachDecision], dict]:
    decisions = [CareerCoachDecision.model_validate(d) for d in json.loads((run_dir / "decisions.json").read_text())]
    return decisions, json.loads((run_dir / "run_meta.json").read_text())


def previous_run(current: dict, archive_root: Path | None) -> dict:
    """The previous comparable operational run for the dashboard's daily delta:
    the most recently archived OPERATIONAL run of the latest as-of date before
    `current` (the run identity dict). It is compared only when it verifies and
    shares the coach version and config hash; otherwise it is excluded with the
    reason. Historical replays and scratch outputs are never candidates."""
    if archive_root is None or not archive_root.is_dir():
        return {"status": "UNAVAILABLE", "reason": "No run archive was given to the dashboard."}
    candidates, earlier = [], set()
    for path in sorted(archive_root.iterdir()):
        meta_file = path / "archive.json"
        if path.name.startswith(".") or not meta_file.exists():
            continue
        meta = json.loads(meta_file.read_text())
        if meta["run_kind"] == "OPERATIONAL" and meta["as_of_date"] < current["as_of_date"]:
            candidates.append((meta["as_of_date"], meta_file.stat().st_mtime_ns, meta["archive_id"], path, meta))
    if not candidates:
        return {"status": "UNAVAILABLE", "reason": "No earlier operational run is archived."}
    latest = max(c[0] for c in candidates)
    _, _, archive_id, path, meta = max(c for c in candidates if c[0] == latest)
    if meta["coach_version"] != current["coach_version"]:
        return {"status": "UNAVAILABLE", "reason": f"{archive_id} excluded: coach version {meta['coach_version']} "
                                                   f"differs from {current['coach_version']}."}
    if meta["input_hashes"]["config"] != current["input_hashes"]["config"]:
        return {"status": "UNAVAILABLE", "reason": f"{archive_id} excluded: its Coach config hash differs from this run's."}
    try:
        decisions = load_archived_decisions(path)                 # verifies the archive first
    except (ValueError, KeyError, OSError) as e:
        return {"status": "UNAVAILABLE", "reason": f"{archive_id} excluded: archive verification failed ({e})."}
    # "Reappeared" evidence: job ids of every OTHER earlier comparable operational
    # archive (same coach version and config, as-of before this run).
    for _, _, other_id, other, other_meta in candidates:
        if other_id != archive_id and other_meta["coach_version"] == current["coach_version"] \
                and other_meta["input_hashes"]["config"] == current["input_hashes"]["config"]:
            earlier |= {d["job"]["job_id"] for d in json.loads((other / "decisions.json").read_text())}
    notes = ["The candidate profile differs from that run's."] \
        if meta["input_hashes"]["profile"] != current["input_hashes"]["profile"] else []
    return {"status": "COMPARED", "archive_id": archive_id, "as_of": meta["as_of_date"], "decisions": decisions,
            "earlier_job_ids": earlier, "notes": notes}


_LIVE_RECORD_DATE = re.compile(r"^lc-(\d{4}-\d{2}-\d{2})-")


def _same_day_key(archive_id: str, meta: dict) -> tuple:
    """Orders archives of ONE as-of date from run metadata only (never file
    times): the latest check date among the live records the run used, then
    how many used records carry that date, then the archive id. A later
    same-day run is the one that consumed the newer live evidence."""
    dates = [m.group(1) for r in meta.get("live_checks_used", []) if (m := _LIVE_RECORD_DATE.match(r))]
    latest = max(dates, default="")
    return latest, dates.count(latest), archive_id


def history_runs(current: dict, archive_root: Path | None) -> dict:
    """Display-only job history: one archived OPERATIONAL run per as-of date
    before `current`, sharing its coach version and config hash (the same
    comparability rule as previous_run). Every candidate archive is verified
    BEFORE its run metadata is read, and only verified archives take part in
    the choice: within an as-of date the run chosen is the latest by
    _same_day_key. Archives that fail verification are left out and named in
    `notes`, as are dates whose top two runs tie on everything but the archive
    id. With no verified earlier run the history is UNAVAILABLE."""
    if archive_root is None or not archive_root.is_dir():
        return {"status": "UNAVAILABLE", "reason": "No run archive was given to the dashboard.", "runs": [], "notes": []}
    by_date: dict[str, list] = {}
    notes: list[str] = []
    for path in sorted(archive_root.iterdir()):
        if path.name.startswith(".") or not (path / "archive.json").exists():
            continue
        try:
            listed = json.loads((path / "archive.json").read_text())          # discovery only
            if listed["run_kind"] != "OPERATIONAL" or listed["as_of_date"] >= current["as_of_date"]:
                continue
            meta = verify_archive(path)                                        # before any metadata is used
            if meta["coach_version"] != current["coach_version"] \
                    or meta["input_hashes"]["config"] != current["input_hashes"]["config"]:
                continue
            run_meta = json.loads((path / "run_meta.json").read_text())       # hash-checked by verify_archive
        except (ValueError, KeyError, OSError) as e:
            notes.append(f"{path.name} excluded: archive verification failed ({e}).")
            continue
        by_date.setdefault(meta["as_of_date"], []).append((_same_day_key(meta["archive_id"], run_meta), path))
    runs = []
    for as_of in sorted(by_date):
        ranked = sorted(by_date[as_of], reverse=True)
        if len(ranked) > 1 and ranked[0][0][:2] == ranked[1][0][:2]:
            notes.append(f"{as_of}: same-day runs not ordered by their live evidence; archive id order used.")
        path = ranked[0][1]
        runs.append({"as_of": as_of, "archive_id": path.name, "decisions": list(load_archived_decisions(path).values())})
    if not runs:
        return {"status": "UNAVAILABLE", "reason": "No verified earlier comparable operational run is archived.",
                "runs": [], "notes": notes}
    return {"status": "AVAILABLE", "runs": runs, "notes": notes}


def dashboard_context(decisions: list[CareerCoachDecision], archive_root: Path | None, live_path: Path | None,
                      manifest: dict | None = None) -> dict:
    """Display-only context: the live records the decisions cite, the previous
    comparable run, the earlier comparable runs (job browser history), this
    run's archive status and, when the run has a manifest, its audit result.
    Nothing here is a decision input."""
    if not decisions:
        return {}
    run = decisions[0].run.model_dump(mode="json")
    cited = {f.live_record_id for d in decisions for f in d.application_viability.factors if f.live_record_id}
    records = None
    if live_path is not None and live_path.exists():
        records = {r.record_id: r for r in load_live_checks(live_path) if r.record_id in cited}
    if archive_root is None:
        archive = {"status": "NOT CHECKED", "reason": "no archive root was given"}
    else:
        try:
            if manifest is not None:                                   # a v2 run names its archive
                path = archive_root / manifest["archive_id"]
                found = path if path.exists() and verify_archive(path) else None
            else:
                found = find_archive(run["as_of_date"], run["run_kind"], run["input_hashes"], archive_root)
            archive = {"status": "VERIFIED", "archive_id": found.name} if found else {"status": "NOT ARCHIVED"}
        except (ValueError, KeyError, OSError) as e:
            archive = {"status": "VERIFICATION FAILED", "reason": str(e)}
    return {"live_records": records, "previous": previous_run(run, archive_root), "archive": archive,
            "history": history_runs(run, archive_root), "audit": manifest}


def render_dashboard(run_dir: Path, expected_run: dict | None = None, archive_root: Path | None = None,
                     live_path: Path | None = None) -> dict:
    """Renders the dashboard from run_dir's own decisions. With expected_run,
    refuses to render unless run_dir holds exactly that run. archive_root and
    live_path only feed display-only context (daily delta, archive status,
    observed live-check values); they never change a decision."""
    require_lock(run_dir)
    decisions, meta = load_run_dir(run_dir)
    if expected_run is not None:
        found = {json.dumps(d.run.model_dump(mode="json"), sort_keys=True) for d in decisions}
        if found != {json.dumps(expected_run, sort_keys=True)}:
            raise ValueError(f"{run_dir} does not hold the expected run; refusing to render a dashboard from it")
    manifest = json.loads((run_dir / MANIFEST).read_text()) if (run_dir / MANIFEST).exists() else None
    dashboard = build_dashboard(decisions, meta, dashboard_context(decisions, archive_root, live_path, manifest))
    (run_dir / "dashboard.md").write_text(render_markdown(dashboard))
    (run_dir / "opportunities.csv").write_text(render_csv(dashboard))
    (run_dir / "opportunities.html").write_text(render_html(dashboard))
    return dashboard
