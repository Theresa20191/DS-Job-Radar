from pydantic import Field

from src.models.processed_job import ProcessedJob
from src.models.ptf import DecisionStability, DimensionResult, PTFResult
from src.models.transition import TransitionLens


class ScoredJob(ProcessedJob):
    """A ProcessedJob plus every scoring output and its explanation.
    Every numeric score ships with reasons — no bare numbers (section 23).

    Unknown is never a number: fields that can be genuinely unknown are
    Optional and hold None, and are excluded from aggregation rather than
    substituted."""

    # --- Base components (renormalized weighted mean over the known ones) ---

    # job_quality: candidate-independent — "is this substantive DS/ML work?"
    # Numeric value comes solely from role_type's categorical base.
    job_quality_score: float = 0.0
    job_quality_known: bool = True
    job_quality_reasons: list[str] = Field(default_factory=list)

    # interview_fit: can the candidate clear THIS posting's stated bar
    # (YOE, education, role level). A heuristic fit — not a probability.
    interview_fit_score: float = 0.0
    interview_fit_known: bool = True
    interview_fit_reasons: list[str] = Field(default_factory=list)

    compensation_score: float | None = None
    compensation_known: bool = False
    compensation_reasons: list[str] = Field(default_factory=list)

    company_maturity_score: float | None = None
    company_maturity_known: bool = False  # no enrichment source yet — system-level limitation
    company_maturity_reasons: list[str] = Field(default_factory=list)

    location_score: float = 0.0
    location_known: bool = True
    location_reasons: list[str] = Field(default_factory=list)

    base_score: float = 0.0   # PTF-free weighted mean of the components above

    # --- Personal Transition Fit (ptf_support_v1) ---
    technical_alignment: DimensionResult | None = None
    domain_alignment: DimensionResult | None = None
    personal_transition_fit: PTFResult | None = None

    # --- Separate post-aggregation adjustments, NOT part of the core ---
    network_bonus: float = 0.0
    network_reasons: list[str] = Field(default_factory=list)

    job_risk_score: float = 0.0          # 0 = no risk, 100 = max risk
    job_risk_penalty: float = 0.0        # points subtracted from overall_score
    job_risk_reasons: list[str] = Field(default_factory=list)

    # --- Data completeness (job-side, independent of scoring) ---
    data_completeness_pct: float = 0.0
    unknown_fields: list[str] = Field(default_factory=list)

    # --- Final outputs ---
    overall_score: float = 0.0
    tier: str = "D"
    opportunity_types: list[str] = Field(default_factory=list)

    # Display/explanation metadata only — never feeds score, tier or action.
    decision_stability: DecisionStability | None = None

    why_apply: list[str] = Field(default_factory=list)
    why_not: list[str] = Field(default_factory=list)
    key_gap: list[str] = Field(default_factory=list)
    recommended_action: str = "SKIP"

    # Transition Lens (Career Transition V1) — annotation only, set after
    # scoring by src/transition/lens.py; never feeds any score or decision.
    transition_lens: TransitionLens | None = None

    # Hard filter outcome (kept even for filtered-out jobs so the pipeline
    # can report why something was excluded, rather than silently dropping it)
    passed_hard_filters: bool = True
    hard_filter_reasons: list[str] = Field(default_factory=list)

    # Named hard-constraint statuses (hc_substantive_ds_status,
    # hc_work_authorization_status) are inherited from ProcessedJob, where
    # hard_filters.py sets them — see processed_job.py.
