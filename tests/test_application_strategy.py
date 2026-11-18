"""Application Strategy V1 — per-job strategy records over frozen outputs.

Uses the same 39 real postings as test_transition_lens.py, scored with the
real score_jobs() and apply_lens().
"""

# Public copy: tests that need the private real-posting fixture (transition_lens_jobs.json) are not included;
# the tests below do not use it and are unchanged apart from fictional example-profile values.

import ast
import copy
import json
from pathlib import Path

import pytest

from src.config import load_profile, load_scoring, load_tracks
from src.models.processed_job import ProcessedJob
from src.models.strategy import ApplicationStrategy, QUEUE_TIERS
from src.reporting import decision_card as dc
from src.scoring.overall import score_jobs
from src.strategy.application import build_strategies, build_strategy, validate_strategy_config
from src.transition.lens import apply_lens

ROOT = Path(__file__).resolve().parent.parent


def with_hard(value):
    profile = copy.deepcopy(load_profile())
    profile["application_strategy"] = {"hard_constraints": {"compensation_floor": {"Malaysia": value}}}
    return profile


# --- frozen isolation -------------------------------------------------------------------------



def test_strategy_is_not_imported_upstream():
    upstream = [p for d in ("scoring", "pipeline", "transition", "enrichment") for p in (ROOT / "src" / d).glob("*.py")]
    upstream += [ROOT / "src" / "reporting" / "markdown_report.py", ROOT / "src" / "reporting" / "exporters.py"]
    for path in upstream:
        tree = ast.parse(path.read_text())
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any(m.startswith("src.strategy") or m == "src.models.strategy" for m in modules), path


# --- the two axes are copied, never combined ----------------------------------------------------


def test_no_score_mode_effort_capacity_or_probability_exists():
    # every system-derived part of the record; decision_record is the candidate's own log (D4)
    import typing

    def field_names(model):
        names = []
        for name, f in model.model_fields.items():
            names.append(name)
            for arg in (f.annotation, *typing.get_args(f.annotation)):
                if hasattr(arg, "model_fields"):
                    names += field_names(arg)
        return names
    derived = [n for n in field_names(ApplicationStrategy) if n != "decision_record"]
    derived = " ".join(n for n in derived if n not in field_names(ApplicationStrategy.model_fields["decision_record"].annotation.__args__[0]))
    for word in ("score", "mode", "effort", "capacity", "probability", "confidence", "priority", "rank", "budget"):
        assert word not in derived.lower(), word


@pytest.mark.parametrize("block", [
    {"weekly_application_capacity": 5},
    {"max_high_effort_applications_per_week": 2},
    {"hard_constraints": {"effort": {"Malaysia": True}}},
])
def test_v2_inputs_are_rejected_in_v1(block):
    profile = copy.deepcopy(load_profile())
    profile["application_strategy"] = block
    with pytest.raises(ValueError):
        validate_strategy_config(profile)


# --- D2: declared blocks --------------------------------------------------------------------------

def blocks(strategy):
    return [(b.constraint, b.status) for b in strategy.declared_blocks]


def test_a_floor_cannot_be_hard_where_none_is_configured():
    profile = copy.deepcopy(load_profile())
    profile["application_strategy"] = {"hard_constraints": {"compensation_floor": {"Singapore": True}}}
    with pytest.raises(ValueError, match="no compensation floor is configured for Singapore"):
        validate_strategy_config(profile)


# --- profile actions, gaps, verify refs ---------------------------------------------------------


# --- network ---------------------------------------------------------------------------------------


# --- determinism and inputs ---------------------------------------------------------------------


# --- D1: queue view -------------------------------------------------------------------------------

from src.models.strategy import FROZEN_ACTIONS, LeverageEntry                     # noqa: E402
from src.scoring.technical_alignment import gap_text                              # noqa: E402
from src.strategy.application import build_run_strategy                           # noqa: E402
from src.transition.gap_ledger import gap_market                                  # noqa: E402


# --- profile leverage ------------------------------------------------------------------------------


# --- Decision Card integration -----------------------------------------------------------------------


def strategy_lines(card):
    return [l.text for l in card.main if l.section == dc.STRATEGY]


