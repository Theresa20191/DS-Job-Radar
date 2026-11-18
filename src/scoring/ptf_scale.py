"""Frozen `ptf_support_v1` contract. Every value here is a versioned
business decision — changing any of them requires a new SCALE_VERSION
(ptf_support_v2), never a silent edit of v1.

  bands (J-1, H-2a/b)   UNKNOWN=None, LIMITED=15, SUBSTANTIVE=45, CORE=75
                        equal known-band steps; unknown->LIMITED step is half
                        a band step (a=0.5, a deliberate calibration choice)
  gain (G-1, G-2)       PTF = max(T, D) + k * min(T, D), k = 1/3 — k is a
                        business choice within 0 < k <= 1/3 that uses the
                        25 points reserved above CORE at {CORE, CORE}
  weight (L-2)          lambda = 2/3, i.e. core = 0.60 * Base + 0.40 * PTF
"""

from fractions import Fraction

SCALE_VERSION = "ptf_support_v1"

BAND_VALUES: dict[str, float] = {"LIMITED": 15.0, "SUBSTANTIVE": 45.0, "CORE": 75.0}
BAND_ORDER = ("LIMITED", "SUBSTANTIVE", "CORE")

GAIN_K = Fraction(1, 3)
PTF_LAMBDA = Fraction(2, 3)


def band_rank(band: str | None) -> int:
    """0 for UNKNOWN, then 1..3 in band order."""
    return 0 if band is None else BAND_ORDER.index(band) + 1


def band_at_least(band: str | None, minimum: str) -> bool:
    return band is not None and band_rank(band) >= band_rank(minimum)


def combine(technical_score: float | None, domain_score: float | None) -> float | None:
    """Frozen gain function. Both unknown -> None; one known -> its own
    value; both known -> max + k*min (symmetric, never needs clamping)."""
    known = [s for s in (technical_score, domain_score) if s is not None]
    if not known:
        return None
    if len(known) == 1:
        return float(known[0])
    hi, lo = max(known), min(known)
    return float(Fraction(hi) + GAIN_K * Fraction(lo))
