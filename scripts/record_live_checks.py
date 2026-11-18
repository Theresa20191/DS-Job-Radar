"""Appends dated live-check observations to data/live_checks.jsonl (LC-1, M15).

The engine never performs live checks; this command only records
observations someone already made. The file is append-only: a record that
already exists is skipped, and a conflicting record with the same id is
refused.

    python scripts/record_live_checks.py OBSERVATIONS_CSV [--out FILE]

OBSERVATIONS_CSV columns: source, source_job_id, checked_on (YYYY-MM-DD),
method, page_reviewable (YES|NO), email_in_jd (YES|NO),
posting_status (OPEN|CLOSED|UNKNOWN).

It holds the pipeline lock (src/career_coach/run_lock.py) while it reads and
appends, so it never appends during a pipeline run: launched by a parent that
holds the lock it inherits that lock, otherwise it takes the lock itself, and refuses with exit 5 when another process holds it
(exit 1 if the lock cannot be set up).
"""

import csv
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.career_coach import run_lock  # noqa: E402
from src.career_coach.inputs import load_live_checks, load_raw_jobs  # noqa: E402
from src.dedup.deduplicator import deduplicate  # noqa: E402
from src.models.career_coach import LiveCheckRecord  # noqa: E402


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def job_ids_by_source_id(sources: list[str], as_of: date) -> dict[str, str]:
    """source_job_id -> canonical job_id, from the stored raw data (no scraping)."""
    processed = deduplicate(load_raw_jobs(ROOT / "data" / "raw", sources), as_of)
    return {sid: job.job_id for job in processed for sid in job.source_job_ids}


def build_records(observations: list[dict], ids: dict[str, str]) -> list[LiveCheckRecord]:
    records = []
    for row in observations:
        sid = row["source_job_id"]
        if sid not in ids:
            raise ValueError(f"source_job_id {sid} is not in the stored raw data")
        records.append(LiveCheckRecord(
            record_id=f"lc-{row['checked_on']}-{sid}", job_id=ids[sid], source_job_id=sid,
            checked_on=row["checked_on"], method=row["method"], page_reviewable=row["page_reviewable"],
            email_in_jd=row["email_in_jd"], posting_status=row["posting_status"]))
    return records


def append(records: list[LiveCheckRecord], path: Path) -> tuple[int, int]:
    """(appended, already present). Existing lines are never rewritten.
    Writing under a protected data folder requires the pipeline lock."""
    run_lock.require_lock(path)
    existing = {r.record_id: r for r in load_live_checks(path)}
    new = []
    for record in records:
        if record.record_id in existing:
            if existing[record.record_id] != record:
                raise ValueError(f"record {record.record_id} already exists with different content")
            continue
        new.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        for record in new:
            f.write(json.dumps(record.model_dump(mode="json"), sort_keys=True) + "\n")
    return len(new), len(records) - len(new)


def main():
    run_lock.run_locked("record_live_checks", _main, inherit_ok=True)


def _main(lock):
    argv = sys.argv[1:]
    with open(argv[0], newline="", encoding="utf-8-sig") as f:
        observations = list(csv.DictReader(f))
    out = Path(_arg(argv, "--out") or ROOT / "data" / "live_checks.jsonl")
    sources = sorted({row["source"] for row in observations})
    as_of = max(date.fromisoformat(row["checked_on"]) for row in observations)
    appended, present = append(build_records(observations, job_ids_by_source_id(sources, as_of)), out)
    print(f"{appended} live-check records appended, {present} already present -> {out}")


if __name__ == "__main__":
    main()
