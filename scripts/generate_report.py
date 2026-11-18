"""Stage 3 CLI entrypoint: generates the human-readable daily_job_radar.md
from the same pipeline computation run_pipeline.py performs (cheap, local,
no network calls — safe to re-run)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from datetime import date  # noqa: E402

from src.pipeline.orchestrator import run  # noqa: E402
from src.reporting.markdown_report import generate_report  # noqa: E402


def _as_of_date(argv: list[str]) -> date:
    """Explicit scoring-run date. `--as-of YYYY-MM-DD` replays a historical
    run reproducibly; with no argument the run uses today's date, passed
    explicitly rather than read inside the scoring logic."""
    if "--as-of" in argv:
        return date.fromisoformat(argv[argv.index("--as-of") + 1])
    return date.today()


def main():
    result = run(ROOT, as_of_date=_as_of_date(sys.argv[1:]))
    report_md = generate_report(result["scored_jobs"], result["pipeline_stats"], result["source_completeness"], result["rejected"])

    out_path = ROOT / "data" / "output" / "daily_job_radar.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_md)
    print(f"Report written -> {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
