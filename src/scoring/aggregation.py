"""Overall-score arithmetic (frozen H-1, L-1, L-2).

  Base         = weighted mean over KNOWN non-PTF components (job_quality,
                 interview_fit, compensation, company_maturity, location),
                 renormalized over the known ones. technical/domain evidence
                 never enters Base — it reaches Overall only through PTF.
  Overall_core = (Base + lambda * PTF) / (1 + lambda),  lambda = 2/3
               = 0.60 * Base + 0.40 * PTF; an UNKNOWN PTF contributes exactly
                 0 (no substituted value).
  Overall      = clamp_0_100(Overall_core + network_bonus - job_risk_penalty)

The uniform rescale keeps the core within 0-100 and preserves the ordering
of Base + lambda*PTF. Network bonus and risk penalty are separate
post-aggregation adjustments, so the final ranking can differ from the
core ordering.
"""

from src.scoring.ptf_scale import PTF_LAMBDA

BASE_COMPONENTS = ("job_quality", "interview_fit", "compensation", "company_maturity", "location")
_LAMBDA = float(PTF_LAMBDA)


def base_score(components: dict[str, float | None], weights: dict[str, float]) -> float:
    """components: {name: score, or None when UNKNOWN}. Unknown components
    are excluded and the remaining weights renormalized."""
    known = {k: v for k, v in components.items() if v is not None}
    total = sum(weights[k] for k in known)
    if total == 0:
        raise ValueError("Base needs at least one known component (job_quality is always known)")
    return sum(weights[k] * v for k, v in known.items()) / total


def overall_core(base: float, ptf: float | None) -> float:
    return (base + (_LAMBDA * ptf if ptf is not None else 0.0)) / (1 + _LAMBDA)


def overall_score(base: float, ptf: float | None, network_bonus: float = 0.0, job_risk_penalty: float = 0.0) -> float:
    return max(0.0, min(100.0, overall_core(base, ptf) + network_bonus - job_risk_penalty))
