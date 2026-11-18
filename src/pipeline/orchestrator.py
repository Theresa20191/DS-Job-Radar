"""Stage 2 of the CLI pipeline: raw -> normalize -> dedup -> enrich ->
hard filter -> score. Reads the `_latest.jsonl` raw files in data/raw/ (the live
collectors are not part of the public release); never mutates them.

The run date is an explicit parameter (`as_of_date`), not the wall clock:
posting age — and therefore the staleness filter and job_risk's staleness
flag — is computed against it, so replaying the same corpus with the same
as_of_date reproduces the same scores. Callers that want "today" pass it
explicitly."""

import logging
from datetime import date
from pathlib import Path

from src.config import load_profile, load_scoring, load_sources, load_tracks
from src.dedup.deduplicator import deduplicate
from src.enrichment.enrich import enrich_job
from src.models.raw_job import RawJob
from src.models.scored_job import ScoredJob
from src.pipeline.hard_filters import filter_jobs
from src.pipeline.quality_report import pipeline_summary, source_completeness
from src.scoring.network_bonus import load_contacts
from src.scoring.overall import score_jobs
from src.transition.lens import apply_lens

logger = logging.getLogger(__name__)


def load_raw_jobs(raw_dir: Path, sources: list[str]) -> dict[str, list[RawJob]]:
    by_source = {}
    for source in sources:
        path = raw_dir / f"{source}_latest.jsonl"
        if not path.exists():
            logger.warning("no raw file for source=%s at %s", source, path)
            by_source[source] = []
            continue
        with open(path) as f:
            by_source[source] = [RawJob.model_validate_json(line) for line in f if line.strip()]
    return by_source


def run(root: Path, as_of_date: date | None = None, transition_lens: bool = True) -> dict:
    as_of_date = as_of_date or date.today()
    profile = load_profile()
    scoring_config = load_scoring()
    sources_config = load_sources()

    enabled_sources = [name for name, cfg in sources_config["sources"].items() if cfg.get("enabled", True)]
    raw_by_source = load_raw_jobs(root / "data" / "raw", enabled_sources)
    all_raw = [j for jobs in raw_by_source.values() for j in jobs]

    src_completeness = source_completeness(raw_by_source)

    processed = deduplicate(all_raw, as_of_date)
    enriched = [enrich_job(j) for j in processed]

    passed, rejected = filter_jobs(enriched, scoring_config)

    contacts = load_contacts(root / "data" / "contacts.csv")
    scored = score_jobs(passed, profile, scoring_config, contacts_by_company=contacts)
    if transition_lens:
        # Annotation-only, after scoring: fills ScoredJob.transition_lens and nothing else.
        scored = apply_lens(scored, profile, load_tracks())

    stats = pipeline_summary(all_raw, len(enriched), rejected, scored, as_of_date)

    return {
        "as_of_date": as_of_date.isoformat(),
        "raw_jobs": all_raw,
        "processed_jobs": enriched,
        "rejected": rejected,
        "scored_jobs": scored,
        "pipeline_stats": stats,
        "source_completeness": src_completeness,
    }
