"""Transition Lens V1 — Gap Ledger, constraint checks (D-2, D-3), strategy
flags, report block, and the remaining invariants (I-8, I-9).

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
from src.reporting.markdown_report import generate_report
from src.scoring.overall import score_jobs
from src.transition.lens import apply_lens



def profile(transition_overrides=None):
    p = copy.deepcopy(load_profile())
    if transition_overrides is not None:
        p["transition"].update(transition_overrides)
    return p


def flags(job):
    return {f.flag for f in job.transition_lens.strategy_flags}


def floor(job):
    check = job.transition_lens.constraint_checks.compensation_floor
    return check.status, check.reason


# --- D-2 compensation floor on real postings ------------------------------------------


def _synthetic(country="Malaysia", currency="MYR", lo=None, hi=None, period="Monthly", prof=None):
    job = ProcessedJob(job_id="s", job_title="Data Scientist", role_family="Data Scientist", role_type="TRUE_DS",
                       company="Acme", company_normalized="acme", country=country, location_normalized="Kuala Lumpur",
                       description_raw="Responsibilities\nBuild predictive models for credit decisions.",
                       salary_min=lo, salary_max=hi, salary_currency=currency if lo or hi else None,
                       salary_period=period if lo or hi else None, salary_unknown=not (lo or hi))
    prof = prof or profile()
    return apply_lens(score_jobs([job], prof, load_scoring()), prof, load_tracks())[0]


@pytest.mark.parametrize("lo, hi, expected", [
    (7500, 9000, ("PASS", "min_meets_floor")),
    (7500, 7500, ("PASS", "min_meets_floor")),              # exactly the floor
    (6000, 7499, ("BELOW", "max_below_floor")),
    (6000, 10000, ("UNKNOWN", "range_straddles_floor")),    # D-2: straddle is UNKNOWN
    (7000, None, ("BELOW", "max_below_floor")),             # one-sided figure used for both ends
])
def test_floor_range_semantics(lo, hi, expected):
    assert floor(_synthetic(lo=lo, hi=hi)) == expected


def test_no_fx_conversion_and_other_unknowns():
    assert floor(_synthetic(currency="USD", lo=5000, hi=6000)) == ("UNKNOWN", "currency_mismatch")
    assert floor(_synthetic(lo=96000, hi=120000, period="Annual")) == ("UNKNOWN", "period_not_monthly")
    # A Singapore floor, once decided, is applied in SGD — still no FX conversion.
    sg = profile({"compensation_floor": {"Singapore": {"currency": "SGD", "monthly_amount": 6000}}})
    assert floor(_synthetic(country="Singapore", currency="SGD", lo=7000, hi=9000, prof=sg)) == ("PASS", "min_meets_floor")
    assert floor(_synthetic(country="Singapore", currency="SGD", lo=4000, hi=5000, prof=sg)) == ("BELOW", "max_below_floor")
    no_block = profile()
    no_block["transition"].pop("compensation_floor")
    assert floor(_synthetic(lo=9000, hi=9500, prof=no_block)) == ("UNKNOWN", "not_configured")


def test_real_profile_floor_is_exactly_d2():
    assert load_profile()["transition"]["compensation_floor"] == {
        "Malaysia": {"currency": "MYR", "monthly_amount": 7500}, "Singapore": None}


@pytest.mark.parametrize("bad", [
    {"Malaysia": {"currency": "MYR"}},
    {"Malaysia": {"currency": "MYR", "monthly_amount": -1}},
])
def test_malformed_floor_fails_loudly(bad):
    with pytest.raises(ValueError):
        _synthetic(lo=9000, hi=9500, prof=profile({"compensation_floor": bad}))


# --- D-3 work authorization -----------------------------------------------------------------


# --- Gap Ledger ------------------------------------------------------------------------------


# --- strategy flags ----------------------------------------------------------------------------


# --- report --------------------------------------------------------------------------------------


# --- D-4 across the full lens (ledger, checks, flags included) ------------------------------------


