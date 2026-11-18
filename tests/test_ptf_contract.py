"""Frozen ptf_support_v1 contract: bands, gain, overall integration, tier
derivation, decision stability, explanations, and legacy removal."""

from fractions import Fraction
from itertools import product

import pytest

from src.config import load_scoring
from src.models.ptf import DimensionResult
from src.models.scored_job import ScoredJob
from src.scoring import aggregation, ptf_scale, stability, tiers
from src.scoring.ptf_scale import BAND_VALUES, GAIN_K, PTF_LAMBDA, SCALE_VERSION, combine

WEIGHTS = load_scoring()["weights"]
L, S, C = BAND_VALUES["LIMITED"], BAND_VALUES["SUBSTANTIVE"], BAND_VALUES["CORE"]


def dim(name, band=None, reason=None, gaps=()):
    if band is None:
        return DimensionResult(name=name, known=False, unknown_reason=reason, gaps=list(gaps))
    return DimensionResult(name=name, known=True, band=band, score=BAND_VALUES[band])


# --- pinned scale snapshot ------------------------------------------------------------

def test_pinned_ptf_support_v1_snapshot():
    snapshot = (SCALE_VERSION, BAND_VALUES, GAIN_K, PTF_LAMBDA)
    assert snapshot == ("ptf_support_v1", {"LIMITED": 15.0, "SUBSTANTIVE": 45.0, "CORE": 75.0},
                        Fraction(1, 3), Fraction(2, 3)), "changing v1 values requires ptf_support_v2"


def test_band_ordering_and_spacing():
    assert 0 < L < S < C < 100
    assert S - L == C - S                       # equal known-band steps
    assert Fraction(L) / Fraction(S - L) == Fraction(1, 2)   # a = 0.5


# --- dimension invariants -------------------------------------------------------------

def test_unknown_dimension_cannot_carry_score():
    with pytest.raises(ValueError):
        DimensionResult(name="x", known=False, band="LIMITED", score=15)
    with pytest.raises(ValueError):
        DimensionResult(name="x", known=False, score=0.0)


def test_known_dimension_score_must_equal_band_value():
    with pytest.raises(ValueError):
        DimensionResult(name="x", known=True, band="LIMITED", score=16)
    with pytest.raises(ValueError):
        DimensionResult(name="x", known=True, band="WEAK", score=15)


# --- gain table -----------------------------------------------------------------------

TABLE = {("L",): 15, ("S",): 45, ("C",): 75, ("L", "L"): 20, ("L", "S"): 50, ("S", "S"): 60,
         ("L", "C"): 80, ("S", "C"): 90, ("C", "C"): 100}
V = {"L": L, "S": S, "C": C, None: None}


@pytest.mark.parametrize("pair,expected", TABLE.items())
def test_gain_table(pair, expected):
    t, d = pair[0], pair[1] if len(pair) > 1 else None
    assert combine(V[t], V[d]) == expected
    assert combine(V[d], V[t]) == expected       # symmetry


def test_gain_properties():
    assert combine(None, None) is None
    for a, b in product([L, S, C], repeat=2):
        value = combine(a, b)
        assert value >= max(a, b)                          # never below the stronger
        assert value - max(a, b) == min(a, b) / 3          # gain = k * weaker
        assert value <= 100                                # no clamping needed
    order = [None, L, S, C]
    for a in order:
        for lo, hi in zip(order, order[1:]):
            assert (combine(a, lo) or 0) <= combine(a, hi)  # monotone


# --- overall integration --------------------------------------------------------------

def test_core_is_60_40_split_and_unknown_adds_zero():
    for base in (0.0, 44.2, 66.3, 100.0):
        assert aggregation.overall_core(base, None) == pytest.approx(0.6 * base)
        for p in TABLE.values():
            assert aggregation.overall_core(base, p) == pytest.approx(0.6 * base + 0.4 * p)
            assert aggregation.overall_core(base, p) >= aggregation.overall_core(base, None)


def test_band_contributions():
    contributions = [aggregation.overall_core(0.0, v) for v in (L, S, C, 100.0)]
    assert contributions == pytest.approx([6.0, 18.0, 30.0, 40.0])


def test_overall_bounded_and_clamped():
    assert aggregation.overall_score(100.0, 100.0) == pytest.approx(100.0)
    assert aggregation.overall_score(100.0, 100.0, network_bonus=10) == 100.0
    assert aggregation.overall_score(0.0, None, job_risk_penalty=20) == 0.0


def test_base_excludes_unknown_components_and_ptf_weights():
    assert "technical_fit" not in WEIGHTS and "domain_fit" not in WEIGHTS
    comps = {"job_quality": 75.0, "interview_fit": 60.0, "compensation": None, "company_maturity": None, "location": 90.0}
    expected = (0.30 * 75 + 0.10 * 60 + 0.05 * 90) / (0.30 + 0.10 + 0.05)
    assert aggregation.base_score(comps, WEIGHTS) == pytest.approx(expected)


# --- tier thresholds --------------------------------------------------------------------

def test_tier_cutoffs_derived_exactly_from_reference_job():
    t = tiers.tier_thresholds(WEIGHTS)
    ref = tiers.reference_components()
    assert ref["interview_fit"] == pytest.approx(190 / 3)
    ref_base = (0.30 * 75 + 0.10 * (190 / 3) + 0.08 * 50 + 0.05 * 90) / 0.53
    assert t["C"] == pytest.approx(0.6 * ref_base, abs=1e-12)
    assert t["B"] == pytest.approx(0.6 * ref_base + 18, abs=1e-12)
    assert t["A"] == pytest.approx(0.6 * ref_base + 30, abs=1e-12)
    assert (round(t["C"], 5), round(t["B"], 5), round(t["A"], 5)) == (42.26415, 60.26415, 72.26415)
    assert tiers.display_thresholds(t) == "A ≥ 72.3 · B ≥ 60.3 · C ≥ 42.3"


def test_reference_job_lands_in_intended_tiers():
    t = tiers.tier_thresholds(WEIGHTS)
    ref_base = aggregation.base_score(tiers.reference_components(), WEIGHTS)
    at = lambda p: tiers.tier_for(aggregation.overall_score(ref_base, p), t)
    assert (at(None), at(L), at(S), at(C)) == ("C", "C", "B", "A")


def test_stretch_minimum_is_the_exact_c_cutoff():
    cfg = load_scoring()["opportunity_types"]
    assert "stretch_min_overall_score" not in cfg    # derived, never a separately rounded number


# --- decision stability -------------------------------------------------------------------

THRESHOLDS = tiers.tier_thresholds(WEIGHTS)
REF = tiers.reference_components()


def stab(technical, domain, comp=50.0, net=0.0, risk=0.0, **overrides):
    comps = {**REF, "compensation": comp, **overrides}
    base = aggregation.base_score(comps, WEIGHTS)
    current = tiers.tier_for(aggregation.overall_score(base, combine(technical.score, domain.score), net, risk), THRESHOLDS)
    return stability.decision_stability(comps, WEIGHTS, technical, domain, net, risk, THRESHOLDS, current)


def test_no_open_unknowns_is_stable():
    s = stab(dim("technical_alignment", "LIMITED"), dim("domain_alignment", reason="no_qualifying_overlap"))
    assert (s.stability_label, s.tier_floor, s.tier_current, s.tier_ceiling) == ("STABLE", "C", "C", "C")
    assert s.settled_unknowns == ["domain_alignment (job-domain evidence exhausted)"]


def test_vacuous_technical_alone_is_settled():
    s = stab(dim("technical_alignment", reason="vacuous"), dim("domain_alignment", reason="no_qualifying_overlap"))
    assert s.stability_label == "STABLE" and not s.open_unknowns


def test_open_technical_unknown_only_raises():
    s = stab(dim("technical_alignment", reason="unevidenced_required",
                 gaps=["No evidence in your profile for Statistics (required by this job)"]),
             dim("domain_alignment", reason="no_qualifying_overlap"))
    assert s.tier_floor == s.tier_current == "C" and s.tier_ceiling == "B" and s.stability_label == "COULD_RISE"
    assert s.open_unknowns[0].direction == "up_only"


def test_compensation_unknown_can_fall():
    # comp unknown: Base 74.07 -> core 44.4 (C); comp=0 -> 37.7 (D); comp=100 -> 46.8 (C)
    s = stab(dim("technical_alignment", reason="vacuous"), dim("domain_alignment", reason="no_qualifying_overlap"), comp=None)
    assert s.open_unknowns[0].field == "compensation" and s.open_unknowns[0].direction == "either"
    assert (s.tier_floor, s.tier_current, s.tier_ceiling, s.stability_label) == ("D", "C", "C", "COULD_FALL")


def test_compensation_unknown_can_rise():
    # interview 24: comp unknown -> 57.2 (C); comp=100 -> 60.34 (B); comp=0 -> 51.3 (C)
    s = stab(dim("technical_alignment", "SUBSTANTIVE"), dim("domain_alignment", reason="no_qualifying_overlap"),
             comp=None, interview_fit=24.0)
    assert (s.tier_floor, s.tier_current, s.tier_ceiling, s.stability_label) == ("C", "C", "B", "COULD_RISE")


def test_evidence_strength_is_not_stability():
    for band in ("LIMITED", "SUBSTANTIVE", "CORE"):
        s = stab(dim("technical_alignment", "LIMITED"), dim("domain_alignment", band))
        assert s.stability_label == "STABLE" and not s.open_unknowns


def test_company_maturity_is_system_level_only():
    s = stab(dim("technical_alignment", "LIMITED"), dim("domain_alignment", reason="no_qualifying_overlap"))
    assert "company_maturity" not in s.model_dump_json()
    assert any("company_maturity" in x for x in stability.SYSTEM_LIMITATIONS)


# --- legacy removal --------------------------------------------------------------------------

@pytest.mark.parametrize("field", [
    "technical_fit_score", "technical_fit_known", "technical_fit_reasons", "domain_fit_score",
    "domain_fit_known", "domain_fit_reasons", "confidence_score", "interview_probability_score",
    "missing_critical_skills", "required_match_pct",
])
def test_legacy_fields_removed(field):
    assert field not in ScoredJob.model_fields


def test_ptf_scale_band_helpers():
    assert ptf_scale.band_at_least("SUBSTANTIVE", "SUBSTANTIVE")
    assert ptf_scale.band_at_least("CORE", "SUBSTANTIVE")
    assert not ptf_scale.band_at_least("LIMITED", "SUBSTANTIVE")
    assert not ptf_scale.band_at_least(None, "LIMITED")
