"""decision_stability (frozen C-1, C-2, C-3): how stable the job's current
tier and recommended action are against unresolved information the system
does not have but that could actually change the decision.

OPEN unknowns (counted):
  - technical_alignment UNKNOWN because REQUIRED techniques are not
    evidenced in the profile — resolvable by the candidate's own input;
    feasible outcomes {UNKNOWN, LIMITED, SUBSTANTIVE} (technical CORE is
    unreachable in v1), so it can only raise the tier
  - compensation UNKNOWN — feasible percentile 0-100, either direction
SETTLED unknowns (never counted): domain UNKNOWN after the posting's domain
evidence is exhausted; vacuous technical UNKNOWN (zero REQUIRED techniques).
SYSTEM-WIDE limitation (never per job): company maturity.

Evidence strength is NOT confidence: an assessed dimension is not less
stable because its band is LIMITED. This is display metadata only — it
never feeds score, PTF, tier, action, ranking or opportunity types.
"""

from src.models.ptf import DecisionStability, DimensionResult, OpenUnknown
from src.scoring.aggregation import base_score, overall_score
from src.scoring.ptf_scale import BAND_VALUES, combine
from src.scoring.tiers import TIER_ORDER, tier_for

COMPENSATION_FEASIBLE = (0.0, 100.0)
TECHNICAL_FEASIBLE_WHEN_OPEN = (None, BAND_VALUES["LIMITED"], BAND_VALUES["SUBSTANTIVE"])
SYSTEM_LIMITATIONS = ["company_maturity: no data source — unknown for every job (system-level, not a per-job uncertainty)"]


def _assessed(technical: DimensionResult, domain: DimensionResult) -> str:
    parts = [n for n, d in (("technical", technical), ("domain", domain)) if d.known]
    return "+".join(parts) or "none"


def decision_stability(
    components: dict[str, float | None],
    weights: dict[str, float],
    technical: DimensionResult,
    domain: DimensionResult,
    network_bonus: float,
    job_risk_penalty: float,
    thresholds: dict[str, float],
    current_tier: str,
) -> DecisionStability:
    open_unknowns: list[OpenUnknown] = []
    settled: list[str] = []
    technical_options = [technical.score]
    compensation_options = [components["compensation"]]

    if not technical.known and technical.unknown_reason == "unevidenced_required":
        technical_options = list(TECHNICAL_FEASIBLE_WHEN_OPEN)
        open_unknowns.append(OpenUnknown(field="technical_alignment", direction="up_only",
                                         reason="; ".join(technical.gaps)))
    elif not technical.known and technical.unknown_reason == "vacuous":
        settled.append("technical_alignment (vacuous: zero REQUIRED techniques)")
    if not domain.known:
        settled.append("domain_alignment (job-domain evidence exhausted)")
    if components["compensation"] is None:
        compensation_options = [None, *COMPENSATION_FEASIBLE]
        open_unknowns.append(OpenUnknown(field="compensation", direction="either",
                                         reason="Salary not disclosed — feasible percentile 0-100"))

    tiers = []
    for tech_value in technical_options:
        for comp_value in compensation_options:
            base = base_score({**components, "compensation": comp_value}, weights)
            ptf = combine(tech_value, domain.score)
            tiers.append(tier_for(overall_score(base, ptf, network_bonus, job_risk_penalty), thresholds))
    floor = min(tiers, key=TIER_ORDER.index)
    ceiling = max(tiers, key=TIER_ORDER.index)
    up = TIER_ORDER.index(ceiling) > TIER_ORDER.index(current_tier)
    down = TIER_ORDER.index(floor) < TIER_ORDER.index(current_tier)
    label = "COULD_MOVE" if up and down else "COULD_RISE" if up else "COULD_FALL" if down else "STABLE"
    return DecisionStability(
        tier_floor=floor, tier_current=current_tier, tier_ceiling=ceiling, stability_label=label,
        open_unknowns=open_unknowns, settled_unknowns=settled,
        ptf_dimensions_assessed=_assessed(technical, domain),
    )
