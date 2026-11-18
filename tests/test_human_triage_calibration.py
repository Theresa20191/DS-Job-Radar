"""The human-triage calibration cross-tab reconciles every subtotal against
the triage rows it was built from, and stays outside the Career Coach engine."""

import ast
import csv
from pathlib import Path

import pytest

from src.calibration.human_triage import CrossTab, crosstab, manual_rows, reconcile, render

ROOT = Path(__file__).resolve().parent.parent


def _row(rank, human, url, screen="ELIGIBLE FOR HUMAN TRIAGE"):
    return {"Rank (frozen)": str(rank), "HUMAN TRIAGE (APPLY / INVESTIGATE · MAYBE · PASS)": human,
            "JD URL": url, "SG Screen Result": screen}


ROWS = [_row(1, "APPLY", "u1"), _row(2, "APPLY", "u2"), _row(3, "PASS", "u3"), _row(4, "APPLY", "u4"),
        _row(5, "PASS", "u5")]
PRIORITIES = {"u1": "VERIFY_FIRST", "u2": "VERIFY_FIRST", "u3": "DO_NOT_INVEST", "u4": "SELECTIVE",
              "u5": "VERIFY_FIRST"}


def test_row_totals_equal_human_counts_and_grand_total_equals_rows():
    table = crosstab(ROWS, PRIORITIES)
    assert table.row_totals == table.human_counts == {"APPLY": 3, "PASS": 2}
    assert table.total == len(ROWS) == 5
    assert table.column_totals == {"VERIFY_FIRST": 3, "DO_NOT_INVEST": 1, "SELECTIVE": 1}
    assert table.cells[("APPLY", "VERIFY_FIRST")] == 2


def test_rendered_totals_are_the_computed_totals():
    text = render(crosstab(ROWS, PRIORITIES), "t")
    assert "| APPLY | 0 | 1 | 2 | 3 |" in text and "| PASS | 1 | 0 | 1 | 2 |" in text
    assert "| Total | 1 | 1 | 3 | 5 |" in text


def test_inconsistent_totals_are_refused():
    bad = CrossTab(cells={("APPLY", "VERIFY_FIRST"): 10, ("APPLY", "SELECTIVE"): 1},
                   row_totals={"APPLY": 14}, column_totals={"VERIFY_FIRST": 10, "SELECTIVE": 1},
                   total=11, human_counts={"APPLY": 11})
    with pytest.raises(ValueError, match="does not reconcile"):
        reconcile(bad, 11)
    with pytest.raises(ValueError, match="grand total"):
        reconcile(crosstab(ROWS, PRIORITIES), 19)


def test_blank_or_unmatched_rows_are_refused():
    with pytest.raises(ValueError, match="blank"):
        crosstab(ROWS + [_row(6, "", "u6")], {**PRIORITIES, "u6": "HIGH"})
    with pytest.raises(ValueError, match="no Career Coach decision"):
        crosstab(ROWS + [_row(6, "APPLY", "u6")], PRIORITIES)


def test_manual_rows_exclude_the_automatic_sg_screen(tmp_path):
    path = tmp_path / "triage.csv"
    rows = ROWS + [_row(7, "PASS", "u7", screen="PASS")]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    assert [r["Rank (frozen)"] for r in manual_rows(path)] == ["1", "2", "3", "4", "5"]


def test_career_coach_engine_never_imports_calibration():
    for path in sorted((ROOT / "src" / "career_coach").glob("*.py")) + [ROOT / "src" / "models" / "career_coach.py"]:
        for node in ast.walk(ast.parse(path.read_text())):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            assert not any(m.startswith("src.calibration") for m in modules), path.name
