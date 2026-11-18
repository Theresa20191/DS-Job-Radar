"""Application log (Application Strategy V1, D4): schema, snapshots and
append-only storage. No learning, no inference.

Uses the same 39 real postings as test_transition_lens.py.
"""

# Public copy: tests that need the private real-posting fixture (transition_lens_jobs.json) are not included;
# the tests below do not use it and are unchanged apart from fictional example-profile values.

import copy
import json
from pathlib import Path

import pytest

from src.config import load_profile, load_scoring, load_tracks
from src.models.processed_job import ProcessedJob
from src.models.strategy import DECLARED_EFFORTS, DECISIONS, OUTCOMES, DecisionEvent, OutcomeEvent
from src.reporting import decision_card as dc
from src.scoring.overall import score_jobs
from src.strategy.application import build_strategies, build_strategy
from src.strategy.application_log import append_event, decision_event, fold, load_events, outcome_event, snapshot
from src.transition.lens import apply_lens

ROOT = Path(__file__).resolve().parent.parent


def strategy(job):
    return build_strategy(job, load_profile())


def test_schema_enumerations_are_exactly_the_approved_ones():
    assert DECISIONS == ("APPLIED", "SKIPPED", "DEFERRED", "SAVED")
    assert DECLARED_EFFORTS == ("LOW", "MEDIUM", "HIGH", "UNKNOWN")
    assert OUTCOMES == ("NOT_STARTED", "APPLIED", "INTERVIEW", "FINAL_ROUND", "OFFER", "REJECTED", "WITHDRAWN", "CLOSED")


def test_the_log_file_stays_out_of_git():
    assert "data/applications.jsonl" in (ROOT / ".gitignore").read_text().splitlines()
