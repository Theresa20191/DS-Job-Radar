"""V1 descriptive outcome report (Gate 5, layer E).

Every figure is a count over a named population with its date range. Rates
are shown only as "k of n" with an exact (Clopper-Pearson) 95% binomial
interval; nothing is suppressed (D7), nothing is ranked or labelled, and
groups are listed alphabetically. Schema versions are never pooled (D9).
OPEN applications are reported separately (D4). Sequence-dependent metrics
exclude histories flagged OUT_OF_ORDER or OUTCOME_WITHOUT_SUBMISSION (D10).
"""

import math
import statistics
from collections import Counter
from datetime import date

from src.learning.outcome_state import MILESTONES, SEQUENCE_ANOMALIES, ANOMALIES

STAGES_AFTER_SUBMISSION = ("INTERVIEW", "FINAL_ROUND", "OFFER")
COACH_ATTRIBUTES = ("ctx_career_value", "ctx_declared_tier", "ctx_deciding_track", "ctx_readiness", "ctx_seniority",
                    "ctx_country", "ctx_priority", "ctx_next_action")
ARCHIVE_ATTRIBUTES = ("ctx_employer", "ctx_network", "ctx_compensation_state")
LEGACY_ATTRIBUTES = ("ctx_legacy_frozen_action", "ctx_legacy_tier", "ctx_legacy_trajectory_direction")
LATENCIES = (("APPLIED", "INTERVIEW"), ("INTERVIEW", "FINAL_ROUND"), ("FINAL_ROUND", "OFFER"))


def _binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def exact_interval(k: int, n: int, level: float = 0.95) -> tuple[float, float] | None:
    """Clopper-Pearson interval for k of n (None when n == 0), by bisection
    on the binomial tails: P(X >= k | lower) = P(X <= k | upper) = (1 - level) / 2."""
    if n == 0:
        return None
    tail = (1 - level) / 2
    lower = upper = None
    if k == 0:
        lower = 0.0
    else:
        lo, hi = 0.0, 1.0
        for _ in range(100):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if 1 - _binom_cdf(k - 1, n, mid) < tail else (lo, mid)
        lower = (lo + hi) / 2
    if k == n:
        upper = 1.0
    else:
        lo, hi = 0.0, 1.0
        for _ in range(100):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if _binom_cdf(k, n, mid) > tail else (lo, mid)
        upper = (lo + hi) / 2
    return lower, upper


def k_of_n(k: int, n: int) -> str:
    interval = exact_interval(k, n)
    return f"{k} of {n}" + (f" [{interval[0]:.2f}–{interval[1]:.2f}]" if interval else "")


def _dates(rows: list[dict]) -> str:
    dates = sorted(d for r in rows for d in (r["out_last_event_date"], r["ctx_decision_date"],
                                               *(r[f"out_first_{m.lower()}_date"] for m in MILESTONES)) if d)
    return f"{dates[0]} to {dates[-1]}" if dates else "no dated events"


def _label(value) -> str:
    return "not recorded" if value in (None, "", []) else str(value)


def _milestone_table(rows: list[dict], key, title: str, population: str) -> list[str]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        for value in (key(r) if callable(key) else [r[key]]):
            groups.setdefault(_label(value), []).append(r)
    out = [f"#### {title}", "", f"Population: {population}.", "",
           "| Value | Submitted | OPEN | Recorded INTERVIEW | Recorded FINAL_ROUND | Recorded OFFER | Event dates |",
           "|---|---|---|---|---|---|---|"]
    for value in sorted(groups):
        g = groups[value]
        n = len(g)
        out.append(f"| {value} | {n} | {sum(r['out_resolution'] == 'OPEN' for r in g)} | "
                   + " | ".join(k_of_n(sum(r[f'out_reached_{m.lower()}'] for r in g), n) for m in STAGES_AFTER_SUBMISSION)
                   + f" | {_dates(g)} |")
    if not groups:
        out.append("| — | 0 | 0 | — | — | — | — |")
    return out + [""]


def render(rows: list[dict], meta: dict) -> str:
    submitted = [r for r in rows if r["out_submitted"]]
    lines = ["# Application outcomes — descriptive record (V1)", "",
             f"Report date: {meta['report_date']} · log: `{meta['log']}` · archive: `{meta['archive_root']}`", "",
             "Every figure below is a count of recorded events for the stated population and dates. "
             "Intervals are exact 95% binomial intervals for k of n. Nothing here is a forecast, a comparison of "
             "quality, or an explanation of why an outcome happened.", "",
             "## 1. Sources", "",
             f"- Events read: {meta['event_count']} "
             f"({', '.join(f'{v}: {c}' for v, c in sorted(meta['events_by_schema'].items())) or 'none'})",
             f"- Jobs with any event: {len(rows)} · event dates: {_dates(rows)}",
             f"- Run archives available: {meta['archive_count']} · jobs whose decision context matched an archive: "
             f"{sum(r['src_archive_status'] == 'MATCHED' for r in rows)}"]
    voided = meta.get("voided_events", [])
    lines.append(f"- Voided events (entered in error; still in the log, excluded from every figure below): {len(voided)}")
    lines += [f"  - `{v['job_id']}` — event {v['event_id']} ({v['schema_version']} {v['kind']} {v['value']}, "
              f"{v['date']}), voided by event {v['void_event_id']} on {v['void_date']}" for v in voided]
    lines.append("")
    if not rows:
        lines += [("Every recorded event has been voided" if voided else "No application events are recorded yet")
                  + ", so every population below is empty.", ""]

    lines += ["## 2. Submitted applications (outcome APPLIED recorded)", ""]
    for stratum in sorted({r["src_event_schema_versions"] for r in submitted}) or ["—"]:
        g = [r for r in submitted if r["src_event_schema_versions"] == stratum]
        n = len(g)
        lines += [f"### Event schema: {stratum}", "", f"Population: {n} jobs with a recorded APPLIED outcome; "
                  f"event dates {_dates(g)}.", ""]
        for m in STAGES_AFTER_SUBMISSION:
            lines.append(f"- Recorded {m} (in any order): {k_of_n(sum(r[f'out_reached_{m.lower()}'] for r in g), n)}")
        terminal = Counter(r["out_terminal_outcome"] for r in g if r["out_resolution"] == "TERMINAL")
        lines += [f"- Terminal outcome recorded: {sum(terminal.values())} of {n} "
                  f"({', '.join(f'{k}: {v}' for k, v in sorted(terminal.items())) or 'none'})",
                  f"- OPEN (no terminal outcome recorded): {sum(r['out_resolution'] == 'OPEN' for r in g)} of {n}", ""]
        for a, b in LATENCIES[:2]:
            base = [r for r in g if r[f"out_reached_{a.lower()}"]]
            lines.append(f"- Among {len(base)} with {a} recorded, {b} recorded: "
                         f"{k_of_n(sum(r[f'out_reached_{b.lower()}'] for r in base), len(base))}")
        base = [r for r in g if r["out_reached_final_round"]]
        lines += [f"- Among {len(base)} with FINAL_ROUND recorded, OFFER recorded: "
                  f"{k_of_n(sum(r['out_reached_offer'] for r in base), len(base))}", ""]

    open_rows = [r for r in submitted if r["out_resolution"] == "OPEN"]
    lines += [f"## 3. OPEN applications ({len(open_rows)})", "",
              "No terminal outcome is recorded; no timeout applies and nothing is assumed about them.", ""]
    lines += [f"- `{r['job_id']}` — latest recorded outcome {r['out_current_status']}, last event "
              f"{r['out_last_event_date']}, {r['out_days_since_last_event']} days before the report date"
              for r in open_rows] + [""]

    decided = [r for r in rows if r["out_decided_applied_without_submission"]]
    lines += [f"## 4. Decided to apply, submission not recorded ({len(decided)})", "",
              "A decision APPLIED without a recorded APPLIED outcome is not counted as a submitted application.", ""]
    lines += [f"- `{r['job_id']}` — decisions {r['out_decisions']}" for r in decided] + [""]

    none = [r for r in rows if not r["out_submitted"] and not r["out_decided_applied_without_submission"]]
    lines += [f"## 5. Other histories without a submission ({len(none)})", "",
              "Jobs that were not applied to are not counted as unsuccessful applications.", ""]
    lines += [f"- `{r['job_id']}` — decisions {r['out_decisions']}, outcomes recorded: "
              f"{r['out_current_status'] if r['out_current_status'] != 'NOT_STARTED' else 'none'}" for r in none] + [""]

    lines += ["## 6. Decision conversions", ""]
    for d in ("SAVED", "DEFERRED"):
        base = [r for r in rows if d in r["out_decisions"]]
        lines.append(f"- Jobs with a {d} decision: {len(base)}; of these, submitted: "
                     f"{k_of_n(sum(r['out_submitted'] for r in base), len(base))} · event dates {_dates(base)}")
    lines.append("")

    for version in sorted({r["ctx_schema_version"] for r in rows if r["ctx_schema_version"]}) or ["—"]:
        decided_rows = [r for r in rows if r["ctx_schema_version"] == version]
        lines += [f"## 7. Decision-time context — {version}", ""]
        if version == "coach_v1":
            for attr in ("ctx_priority", "ctx_next_action", "ctx_career_value"):
                counts = Counter(_label(r[attr]) for r in decided_rows)
                lines.append(f"- Decisions by {attr[4:]} at decision time: "
                             + (", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "none"))
            lines.append("")
        stratified = [r for r in submitted if r["ctx_schema_version"] == version]
        excluded = sum(1 for r in submitted if r["src_decision_context"] == "NONE")
        lines += [f"Submitted applications with {version} decision-time context: {len(stratified)} "
                  f"(submitted applications without any decision-time context, excluded here: {excluded}).", ""]
        attributes = COACH_ATTRIBUTES if version == "coach_v1" else LEGACY_ATTRIBUTES
        for attr in attributes:
            lines += _milestone_table(stratified, attr, f"By {attr[4:]} at decision time",
                                      f"{len(stratified)} submitted applications, {version} context")
        if version == "coach_v1":
            matched = [r for r in stratified if r["src_archive_status"] == "MATCHED"]
            lines += [f"Archive-dependent context (only the {len(matched)} applications whose run archive matched; "
                      f"{len(stratified) - len(matched)} without a matching archive are not shown here).", ""]
            for attr in ARCHIVE_ATTRIBUTES:
                lines += _milestone_table(matched, attr, f"By {attr[4:]} at decision time",
                                          f"{len(matched)} submitted applications with a matched archive")
            lines += _milestone_table(matched, lambda r: r["ctx_evidence_gaps"] or [None],
                                      "By evidence gap present at decision time (an application can appear under "
                                      "several gaps)", f"{len(matched)} submitted applications with a matched archive")
            lines += ["#### Declared effort (candidate) and Coach-estimated resume effort", "",
                      f"Population: {len(stratified)} submitted applications, coach_v1 context. Shown side by side; "
                      "neither value is derived from the other.", "",
                      "| Declared effort | Coach resume effort | Submitted | Recorded INTERVIEW |", "|---|---|---|---|"]
            pairs = Counter((_label(r["ctx_declared_effort"]), _label(r["ctx_coach_resume_effort"])) for r in stratified)
            for (declared, coach), n in sorted(pairs.items()):
                g = [r for r in stratified if (_label(r["ctx_declared_effort"]), _label(r["ctx_coach_resume_effort"]))
                     == (declared, coach)]
                lines.append(f"| {declared} | {coach} | {n} | {k_of_n(sum(r['out_reached_interview'] for r in g), n)} |")
            lines.append("")

    lines += ["## 8. Days between recorded stages", "",
              f"Histories flagged {' or '.join(SEQUENCE_ANOMALIES)} are excluded from this section only.", ""]
    for a, b in LATENCIES:
        pairs = [r for r in submitted if r[f"out_first_{a.lower()}_date"] and r[f"out_first_{b.lower()}_date"]]
        valid = [r for r in pairs if not set(r["out_anomalies"]) & set(SEQUENCE_ANOMALIES)]
        days = sorted(_days(r[f"out_first_{a.lower()}_date"], r[f"out_first_{b.lower()}_date"]) for r in valid)
        summary = f"min {days[0]}, median {statistics.median(days):g}, max {days[-1]}" if days else "no pairs"
        lines.append(f"- {a} → {b}: {len(valid)} histories ({len(pairs) - len(valid)} excluded) · {summary}")
    lines += ["", "## 9. Data-quality flags (derived; the log is unchanged)", ""]
    for flag in ANOMALIES:
        flagged = [r["job_id"] for r in rows if flag in r["out_anomalies"]]
        lines.append(f"- {flag}: {len(flagged)}" + (f" — {', '.join(f'`{j}`' for j in flagged)}" if flagged else ""))
    lines += ["", "## 10. Not available in V1", "",
              "- Surfaced → reviewed: review is not recorded (D8), so this population is unavailable.",
              "- Salary values: salary values unavailable because they were not preserved in the approved archived "
              "artefacts (only the compensation state is available).",
              "- Career outcomes (acceptance, joining, retention): a separate future event family.", ""]
    return "\n".join(lines)


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days
