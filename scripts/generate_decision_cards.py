"""LEGACY — DO NOT RUN AGAINST THE CURRENT PRODUCTION APPLICATION LOG.
By default it reads data/applications.jsonl with the legacy
`application_log_v1` loader, which rejects current `coach_v1` decision events,
can read `coach_v1` outcome events as legacy events, and stops on
`coach_void_v1` events.

Decision Card V1 CLI: renders decision_cards.md / decision_cards.json from
the same pipeline computation generate_report.py performs. Additive output
only — the daily report and every scored value are unchanged.

    python scripts/generate_decision_cards.py [--as-of YYYY-MM-DD] [--out DIR] [--log FILE]
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_profile  # noqa: E402
from src.pipeline.orchestrator import run  # noqa: E402
from src.reporting.decision_card import build_cards, build_summary, render_json, render_markdown  # noqa: E402
from src.strategy.application import build_run_strategy, build_strategies  # noqa: E402
from src.strategy.application_log import DEFAULT_LOG, fold, load_events  # noqa: E402


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def main():
    argv = sys.argv[1:]
    as_of = _arg(argv, "--as-of")
    out_dir = Path(_arg(argv, "--out") or ROOT / "data" / "output")

    scored = run(ROOT, as_of_date=date.fromisoformat(as_of) if as_of else date.today())["scored_jobs"]
    log = Path(_arg(argv, "--log") or DEFAULT_LOG)
    strategies = build_strategies(scored, load_profile(), fold(load_events(log)))
    run_strategy = build_run_strategy(scored, strategies)
    cards, summary = build_cards(scored, strategies), build_summary(scored)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "decision_cards.md").write_text(render_markdown(cards, summary, run_strategy))
    (out_dir / "decision_cards.json").write_text(render_json(cards, summary, run_strategy, strategies))
    print(f"{len(cards)} decision cards written -> {out_dir}")


if __name__ == "__main__":
    main()
