"""Human-triage calibration report (descriptive only).

Compares the candidate's manual HUMAN TRIAGE decisions with Career Coach
priorities. It is calibration evidence, never a production input: the Career
Coach engine does not import this module or read the triage file, and no rule
is derived from it. Every subtotal is computed from the data and reconciled
before anything is rendered — the report refuses to print inconsistent totals.
"""

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

AUTOMATIC_SCREEN = "PASS"          # SG Screen Result of the automatic SG MyCareersFuture PASS rows


@dataclass(frozen=True)
class CrossTab:
    cells: dict[tuple[str, str], int]       # (human decision, coach priority) -> count
    row_totals: dict[str, int]
    column_totals: dict[str, int]
    total: int
    human_counts: dict[str, int]            # counted directly from the triage rows


def manual_rows(path: Path) -> list[dict]:
    """The manually triaged rows: every row not decided by the automatic SG screen."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r.get("SG Screen Result", "") != AUTOMATIC_SCREEN]


def human_decision(row: dict) -> str:
    key = next(k for k in row if k.startswith("HUMAN TRIAGE"))
    return row[key].strip()


def crosstab(rows: list[dict], priority_by_url: dict[str, str]) -> CrossTab:
    """HUMAN TRIAGE x Career Coach priority, joined on the JD URL (the job's
    canonical URL). Raises if any row is unfilled, unmatched, or if any total
    fails to reconcile."""
    unfilled = [r.get("Rank (frozen)") for r in rows if not human_decision(r)]
    if unfilled:
        raise ValueError(f"HUMAN TRIAGE is blank for ranks {unfilled}")
    unmatched = [r.get("Rank (frozen)") for r in rows if r["JD URL"] not in priority_by_url]
    if unmatched:
        raise ValueError(f"no Career Coach decision for ranks {unmatched}")

    human_counts = Counter(human_decision(r) for r in rows)
    cells = Counter((human_decision(r), priority_by_url[r["JD URL"]]) for r in rows)
    row_totals: Counter = Counter()
    column_totals: Counter = Counter()
    for (human, coach), n in cells.items():
        row_totals[human] += n
        column_totals[coach] += n
    table = CrossTab(dict(cells), dict(row_totals), dict(column_totals), sum(cells.values()), dict(human_counts))
    reconcile(table, len(rows))
    return table


def reconcile(table: CrossTab, expected_rows: int) -> None:
    """Every subtotal must equal what was counted from the data."""
    problems = []
    if table.total != expected_rows:
        problems.append(f"grand total {table.total} != {expected_rows} triage rows")
    if table.row_totals != table.human_counts:
        problems.append(f"row totals {table.row_totals} != human decision counts {table.human_counts}")
    if sum(table.row_totals.values()) != table.total or sum(table.column_totals.values()) != table.total:
        problems.append("row or column totals do not sum to the grand total")
    if problems:
        raise ValueError("calibration cross-tab does not reconcile: " + "; ".join(problems))


def render(table: CrossTab, title: str) -> str:
    humans = sorted(table.row_totals)
    coaches = sorted(table.column_totals)
    lines = [f"## {title}", "",
             "| HUMAN TRIAGE \\ Coach priority | " + " | ".join(coaches) + " | Total |",
             "|---" * (len(coaches) + 2) + "|"]
    for h in humans:
        lines.append(f"| {h} | " + " | ".join(str(table.cells.get((h, c), 0)) for c in coaches)
                     + f" | {table.row_totals[h]} |")
    lines.append("| Total | " + " | ".join(str(table.column_totals[c]) for c in coaches) + f" | {table.total} |")
    return "\n".join(lines + [""])
