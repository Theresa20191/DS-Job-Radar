"""Career Coach shadow run: evaluates the complete hard-filter population
with the Career Coach engine only — no scoring, lens, card or strategy code
is called on the Coach path. Legacy outputs are read, never written, and only
for the descriptive comparison (--compare-legacy).

    python scripts/career_coach_shadow.py [--as-of YYYY-MM-DD] [--run-kind HISTORICAL_REPLAY|OPERATIONAL]
                                          [--out DIR] [--live-checks FILE] [--compare-legacy]
                                          [--triage TRIAGE_CSV] [--archive-root DIR]

Every run is also archived immutably (D1) under data/archive/runs/ (or --archive-root).
The whole run holds the pipeline lock (src/career_coach/run_lock.py); it
refuses with exit 5, before reading any input, when another process holds it
(exit 1 if the lock cannot be set up).
"""

import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.career_coach import run_lock  # noqa: E402
from src.career_coach.operational import load_population, run_coach, write_run  # noqa: E402
from src.career_coach.run_archive import ARCHIVE_ROOT, archive_run  # noqa: E402
from src.config import CONFIG_DIR  # noqa: E402


def _arg(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def coach_run(as_of: date, run_kind: str, live_path: Path) -> tuple[list, dict]:
    population, raw_hash, _ = load_population(ROOT, as_of)
    return run_coach(ROOT, population, as_of, run_kind, live_path, raw_hash)


def legacy_comparison(decisions: list, as_of: date) -> str:
    """Descriptive only: frozen action / lens direction side by side with the
    Coach decision. Differences are listed, never labelled as bugs."""
    from src.pipeline.orchestrator import run as legacy_run   # legacy side only, imported lazily

    legacy = legacy_run(ROOT, as_of_date=as_of)
    frozen = {j.job_id: j for j in legacy["scored_jobs"]}
    lines = [f"# Career Coach shadow comparison ({as_of.isoformat()})", ""]
    rejected_legacy = {j.job_id for j, _ in legacy["rejected"]}
    coach_rejected = {d.job.job_id for d in decisions if d.evaluation_scope == "HARD_FILTER_REJECTED"}
    lines += [f"- Population: {len(decisions)} · Coach passed {len(decisions) - len(coach_rejected)} "
              f"(legacy scored {len(frozen)}) · rejected {len(coach_rejected)} (legacy {len(rejected_legacy)})",
              f"- Same passed set: {set(frozen) == {d.job.job_id for d in decisions} - coach_rejected} · "
              f"same rejected set: {coach_rejected == rejected_legacy}", ""]
    full = [d for d in decisions if d.evaluation_scope == "FULL"]

    def table(title, rows_key, cols_key):
        rows = Counter((rows_key(d), cols_key(d)) for d in full)
        rs, cs = sorted({r for r, _ in rows}), sorted({c for _, c in rows})
        out = [f"## {title}", "", "| | " + " | ".join(cs) + " |", "|---" * (len(cs) + 1) + "|"]
        out += [f"| {r} | " + " | ".join(str(rows.get((r, c), "")) for c in cs) + " |" for r in rs]
        return out + [""]

    lines += table("Coach priority × frozen action", lambda d: d.application_priority,
                   lambda d: frozen[d.job.job_id].recommended_action)
    lines += table("Coach career value × lens direction", lambda d: d.career_value.state,
                   lambda d: frozen[d.job.job_id].transition_lens.trajectory.direction)
    lines += table("Coach next action × frozen action", lambda d: d.next_action,
                   lambda d: frozen[d.job.job_id].recommended_action)

    def listing(title, pick):
        chosen = [d for d in full if pick(d, frozen[d.job.job_id])]
        out = [f"## {title} ({len(chosen)})", ""]
        for d in sorted(chosen, key=lambda d: (-frozen[d.job.job_id].overall_score, d.job.job_id)):
            j = frozen[d.job.job_id]
            out.append(f"- {d.job.company} · {d.job.job_title} — frozen {j.recommended_action} (tier {j.tier}); "
                       f"Coach {d.application_priority} / {d.next_action} [{d.primary_reason.rule_id}: {d.primary_reason.text}]")
        return out + [""]

    lines += listing("Coach DO_NOT_INVEST, frozen APPLY", lambda d, j: d.application_priority == "DO_NOT_INVEST"
                     and j.recommended_action.startswith("APPLY"))
    lines += listing("Coach HIGH/PRIORITY, frozen STRETCH APPLY or SKIP", lambda d, j:
                     d.application_priority in ("HIGH", "PRIORITY") and j.recommended_action in ("STRETCH APPLY", "SKIP"))
    return "\n".join(lines)


def main():
    run_lock.run_locked("career_coach_shadow", _main)


def _main(lock):
    argv = sys.argv[1:]
    as_of = date.fromisoformat(_arg(argv, "--as-of") or "2026-09-20")
    run_kind = _arg(argv, "--run-kind") or "HISTORICAL_REPLAY"
    out_dir = Path(_arg(argv, "--out") or ROOT / "data" / "output" / "career_coach_shadow")
    live_path = Path(_arg(argv, "--live-checks") or ROOT / "data" / "live_checks.jsonl")

    config_bytes = (CONFIG_DIR / "career_coach.yaml").read_bytes()     # the exact files this run uses
    profile_bytes = (CONFIG_DIR / "profile.yaml").read_bytes()
    decisions, meta = coach_run(as_of, run_kind, live_path)
    run_lock.require_lock(out_dir)
    write_run(out_dir, decisions, meta)
    if "--compare-legacy" in argv:
        (out_dir / "legacy_comparison.md").write_text(legacy_comparison(decisions, as_of))
    triage = _arg(argv, "--triage")
    if triage:                                          # calibration evidence only, never an engine input
        from src.calibration.human_triage import crosstab, manual_rows, render
        rows = manual_rows(Path(triage))
        table = crosstab(rows, {d.job.canonical_url: d.application_priority for d in decisions})
        (out_dir / "human_triage_calibration.md").write_text(
            f"# Human triage calibration ({run_kind}, {as_of.isoformat()})\n\nSource: {triage}\n\n"
            + render(table, "HUMAN TRIAGE × Career Coach priority"))
    archived = archive_run(out_dir, config_bytes, profile_bytes, Path(_arg(argv, "--archive-root") or ARCHIVE_ROOT))
    counts = Counter(d.application_priority for d in decisions)
    print(f"{len(decisions)} Career Coach decisions ({run_kind}, {as_of}) -> {out_dir} · {dict(sorted(counts.items()))}"
          f" · archived {archived.name}")


if __name__ == "__main__":
    main()
