"""Stage 2 CLI entrypoint: normalize -> dedup -> enrich -> hard filter ->
score. Reads the raw files in data/raw/, writes jobs_processed.parquet
and jobs_scored.parquet to data/processed/ and data/output/."""

import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.pipeline.orchestrator import run  # noqa: E402
from src.reporting.exporters import export_processed, export_scored, export_top_jobs  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_pipeline")


def _as_of_date(argv: list[str]) -> date:
    """Explicit scoring-run date. `--as-of YYYY-MM-DD` replays a historical
    run reproducibly; with no argument the run uses today's date, passed
    explicitly rather than read inside the scoring logic."""
    if "--as-of" in argv:
        return date.fromisoformat(argv[argv.index("--as-of") + 1])
    return date.today()


def main():
    as_of = _as_of_date(sys.argv[1:])
    logger.info("scoring as_of_date=%s", as_of.isoformat())
    result = run(ROOT, as_of_date=as_of)

    export_processed(result["processed_jobs"], ROOT / "data" / "processed")
    export_scored(result["scored_jobs"], ROOT / "data" / "output")
    export_top_jobs(result["scored_jobs"], ROOT / "data" / "output", top_n=10)

    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    for k, v in result["pipeline_stats"].items():
        logger.info("  %s: %s", k, v)
    logger.info("=" * 60)
    for source, metrics in result["source_completeness"].items():
        logger.info("source=%s: %s", source, metrics)


if __name__ == "__main__":
    main()
