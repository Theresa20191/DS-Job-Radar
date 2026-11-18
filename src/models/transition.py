"""Transition Lens models (Career Transition V1).

The lens is ANNOTATION-ONLY (D-4): it is computed after scoring and never
feeds Overall, tier, ranking, recommended_action, PTF, compensation,
job_quality, role_type, hard filters, stability, why_apply or key_gap.
Every label, track and check carries its evidence; UNKNOWN always carries
an unknown_reason.
"""

from pydantic import BaseModel, Field, model_validator

LENS_VERSION = "transition_lens_v1"

WORK_LABELS = (
    "APPLIED_MODELLING", "EXPERIMENTATION_DECISION", "OPTIMISATION", "ANALYTICS_INSIGHT",
    "REPORTING", "ML_ENGINEERING", "ML_PLATFORM", "RESEARCH", "AI_APPLICATION_DEV", "PROJECT_DELIVERY",
)
TARGET_TIERS = ("primary", "secondary", "adjacent_ok", "non_target")
DIRECTIONS = ("TOWARD_PRIMARY", "TOWARD_SECONDARY", "ADJACENT", "NON_TARGET",
              "OFF_VOCABULARY", "UNTARGETED", "UNKNOWN")


class EvidenceRef(BaseModel):
    field: str                    # job_title | description_raw | requirements_raw | role_family | profile...
    text: str                     # the sentence or value the rule matched
    rule_id: str
    heading: str = ""             # governing section heading, if any


class WorkLabel(BaseModel):
    label: str
    evidence: list[EvidenceRef]

    @model_validator(mode="after")
    def _has_evidence(self):
        if self.label not in WORK_LABELS:
            raise ValueError(f"unknown work label {self.label}")
        if not self.evidence:
            raise ValueError("a work label needs at least one evidence sentence")
        return self


class WorkProfile(BaseModel):
    version: str = LENS_VERSION
    labels: list[WorkLabel] = Field(default_factory=list)   # multi-label, no primary, no score
    work_sentence_count: int = 0
    unknown_reason: str | None = None                        # no_task_statements | no_labelled_work

    @model_validator(mode="after")
    def _unknown_iff_no_labels(self):
        if bool(self.labels) == (self.unknown_reason is not None):
            raise ValueError("WorkProfile is UNKNOWN (with a reason) iff it has no labels")
        return self

    def has(self, label: str) -> bool:
        return any(l.label == label for l in self.labels)

    def count(self, label: str) -> int:
        return next((len(l.evidence) for l in self.labels if l.label == label), 0)


class TrackMatch(BaseModel):
    track_id: str
    target_tier: str                        # primary | secondary | adjacent_ok | non_target | UNTARGETED
    rule_id: str
    evidence: list[EvidenceRef]


class GuardFailure(BaseModel):
    """The track's context matched but its guard did not (e.g. PRODUCT_DS
    without substantive DS work evidence). Recorded, never mapped."""
    track_id: str
    failed_guard: str


class Trajectory(BaseModel):
    version: str = LENS_VERSION
    tracks: list[TrackMatch] = Field(default_factory=list)
    direction: str = "UNKNOWN"
    mixed: bool = False                     # a PRIMARY/SECONDARY and a NON_TARGET track both matched
    guard_failures: list[GuardFailure] = Field(default_factory=list)
    unknown_reason: str | None = None       # work_profile_unknown | not_configured

    @model_validator(mode="after")
    def _direction_rules(self):
        if self.direction not in DIRECTIONS:
            raise ValueError(f"unknown direction {self.direction}")
        if (self.direction == "UNKNOWN") != (self.unknown_reason is not None):
            raise ValueError("direction UNKNOWN iff an unknown_reason is given")
        return self


GAP_CLASSIFICATIONS = ("UNCLASSIFIED", "translation", "portfolio", "transferable", "not_pursuing")
FLOOR_STATUSES = ("PASS", "BELOW", "UNKNOWN")
WORK_AUTH_STATUSES = ("PASS", "UNKNOWN", "NOT_ELIGIBLE")


class GapItem(BaseModel):
    """A REQUIRED technique with no evidence in the candidate profile — an
    evidence gap, never "the candidate lacks X", never a penalty."""
    technique: str
    source_field: str
    section_heading: str = ""
    context: str = ""
    candidate_classification: str = "UNCLASSIFIED"   # declared by the candidate only
    classification_source: str | None = None         # profile path, when declared
    market_frequency: int = 0                         # jobs in this run requiring it with no profile evidence


class NiceToHave(BaseModel):
    technique: str
    importance: str                                   # preferred | unknown


class GapLedger(BaseModel):
    required_unevidenced: list[GapItem] = Field(default_factory=list)
    nice_to_have: list[NiceToHave] = Field(default_factory=list)
    settled_reason: str | None = None                 # vacuous_requirements


class FloorRule(BaseModel):
    country: str
    currency: str
    monthly_amount: float
    config_path: str


class SalaryEvidence(BaseModel):
    salary_raw: str = ""
    salary_currency: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_period: str | None = None


class CompensationFloorCheck(BaseModel):
    status: str                                       # PASS | BELOW | UNKNOWN (UNKNOWN is never negative)
    reason: str
    rule: FloorRule | None = None
    evidence: SalaryEvidence

    @model_validator(mode="after")
    def _valid(self):
        if self.status not in FLOOR_STATUSES:
            raise ValueError(f"unknown floor status {self.status}")
        return self


class WorkAuthorizationCheck(BaseModel):
    status: str                                       # PASS | UNKNOWN | NOT_ELIGIBLE — candidate-declared only
    country: str
    source: str                                       # profile path, or "not_declared"
    job_side_status: str                              # frozen HC-2 result, shown for context only
    job_side_restriction_type: str
    job_side_evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid(self):
        if self.status not in WORK_AUTH_STATUSES:
            raise ValueError(f"unknown work authorization status {self.status}")
        return self


class ConstraintChecks(BaseModel):
    compensation_floor: CompensationFloorCheck
    work_authorization: WorkAuthorizationCheck


class StrategyFlag(BaseModel):
    """A neutral descriptor for the decision card — no severity, no score."""
    flag: str
    reason: str
    evidence: list[EvidenceRef] = Field(default_factory=list)


class TransitionLens(BaseModel):
    version: str = LENS_VERSION
    work_profile: WorkProfile
    trajectory: Trajectory
    gap_ledger: GapLedger | None = None
    constraint_checks: ConstraintChecks | None = None
    strategy_flags: list[StrategyFlag] = Field(default_factory=list)
