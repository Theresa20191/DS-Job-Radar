"""Tier thresholds (frozen T-1, T-2, T-3).

Each boundary is anchored to the overall score of a REFERENCE job — a
genuine DS role (TRUE_DS) with every other component at its module's
declared neutral value, network = 0, risk = 0 — at a specified PTF state:

  C cutoff = reference @ PTF UNKNOWN      (a neutral genuine DS role with
                                           unassessable transition fit stays
                                           STRETCH APPLY — unknown is not negative)
  B cutoff = reference @ PTF SUBSTANTIVE
  A cutoff = reference @ PTF CORE
  (reference @ LIMITED lands inside C)

The cutoffs are DERIVED values, not business constants: they are
recomputed from the canonical weights and neutral values and stored
exactly (C ~ 42.26415, B ~ 60.26415, A ~ 72.26415). One-decimal values
are presentation only. They were not fitted to any corpus.
"""

from src.scoring import compensation, interview_fit, job_quality, location
from src.scoring.aggregation import base_score, overall_score
from src.scoring.ptf_scale import BAND_VALUES

TIER_ORDER = "DCBA"   # low -> high


def reference_components() -> dict[str, float | None]:
    return {
        "job_quality": float(job_quality.ROLE_TYPE_BASE["TRUE_DS"]),
        "interview_fit": interview_fit.EXPERIENCE_WEIGHT * interview_fit.EXPERIENCE_UNSPECIFIED_SCORE
        + interview_fit.EDUCATION_WEIGHT * interview_fit.EDUCATION_UNSPECIFIED_SCORE,
        "compensation": compensation.NEUTRAL_PERCENTILE,
        "company_maturity": None,   # unknown for every job (system-level limitation)
        "location": location.IN_TARGET_LOCATION_SCORE,
    }


def tier_thresholds(weights: dict[str, float]) -> dict[str, float]:
    ref_base = base_score(reference_components(), weights)
    return {
        "A": overall_score(ref_base, BAND_VALUES["CORE"]),
        "B": overall_score(ref_base, BAND_VALUES["SUBSTANTIVE"]),
        "C": overall_score(ref_base, None),
        "D": 0.0,
    }


def tier_for(score: float, thresholds: dict[str, float]) -> str:
    for tier in ("A", "B", "C"):
        if score >= thresholds[tier]:
            return tier
    return "D"


def display_thresholds(thresholds: dict[str, float]) -> str:
    return " · ".join(f"{t} ≥ {thresholds[t]:.1f}" for t in ("A", "B", "C"))
