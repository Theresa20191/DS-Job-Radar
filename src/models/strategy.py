"""Application Strategy V1 models.

Application Strategy is a structured, additive view over frozen outputs:
where a job sits on the two independent frozen axes (frozen action and
career direction), which constraints the CANDIDATE declared as hard stops,
which profile actions are observable from frozen stability, and pointers to
the existing What-to-Verify items. It is not a score, rank, tier, mode or
effort estimate, and it never changes or overrides any frozen output.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

STRATEGY_VERSION = "application_strategy_v1"

# Queue tier = the highest career tier present, which is exactly the frozen
# trajectory direction (direction precedence primary > secondary > adjacent >
# non-target). Display grouping only — no new hierarchy among tracks.
DIRECTION_TO_QUEUE_TIER = {
    "TOWARD_PRIMARY": "PRIMARY",
    "TOWARD_SECONDARY": "SECONDARY",
    "ADJACENT": "ADJACENT",
    "NON_TARGET": "NON_TARGET",
    "UNTARGETED": "UNTARGETED",
    "OFF_VOCABULARY": "OFF_VOCABULARY",
    "UNKNOWN": "UNKNOWN",
}
QUEUE_TIERS = tuple(DIRECTION_TO_QUEUE_TIER.values())
FROZEN_ACTIONS = ("APPLY + NETWORK", "APPLY", "STRETCH APPLY", "SKIP")      # frozen tier-rule order

BLOCK_CONSTRAINTS = ("WORK_AUTH", "COMP_FLOOR")
PROFILE_ACTION_KINDS = ("ADD_EVIDENCE_OPEN_UNKNOWN",)


class OpenUnknownRef(BaseModel):
    field: str
    direction: str                     # up_only | either (frozen decision_stability)
    reason: str


class FrozenAxis(BaseModel):
    """Frozen current-fit axis — copied as-is, never adjusted."""
    recommended_action: str
    tier: str
    stability_label: str
    tier_floor: str
    tier_ceiling: str
    open_unknowns: list[OpenUnknownRef] = Field(default_factory=list)


class TrackRef(BaseModel):
    track_id: str
    target_tier: str


class CareerAxis(BaseModel):
    """Transition Lens career axis — copied as-is, tracks in upstream order."""
    tiers_present: list[str] = Field(default_factory=list)
    tracks: list[TrackRef] = Field(default_factory=list)
    direction: str
    queue_tier: str


class DeclaredBlock(BaseModel):
    """A constraint the candidate declared as a hard stop and that applies to
    this job. An Application Strategy fact — never a scoring result."""
    constraint: str                    # WORK_AUTH | COMP_FLOOR
    status: str                        # NOT_ELIGIBLE | BELOW
    source: list[str]                  # candidate declaration path(s) + the ScoredJob check path


class ProfileAction(BaseModel):
    """An observable profile action: the frozen stability shows an open
    technical unknown (up_only) — only the candidate's own profile evidence can
    resolve it. A record, never advice."""
    kind: str                          # ADD_EVIDENCE_OPEN_UNKNOWN
    technique: list[str]
    source_path: str


class GapStrategy(BaseModel):
    technique: str
    candidate_classification: str      # declared by the candidate; UNCLASSIFIED otherwise


class NetworkSignal(BaseModel):
    """Present only when the frozen network mechanism found an actual contact
    (contacts.csv -> network_bonus). Never inferred from recruiter status."""
    network_bonus: float
    reasons: list[str]
    networking_opportunity: bool       # frozen opportunity type NETWORKING_OPPORTUNITY present
    source_path: str = "network_reasons"


class ApplicationStrategy(BaseModel):
    version: str = STRATEGY_VERSION
    job_id: str
    as_of_date: str | None
    card_version: str
    frozen_axis: FrozenAxis
    career_axis: CareerAxis
    declared_blocks: list[DeclaredBlock] = Field(default_factory=list)
    profile_actions: list[ProfileAction] = Field(default_factory=list)
    gap_strategy: list[GapStrategy] = Field(default_factory=list)
    verify_refs: list[str] = Field(default_factory=list)      # existing Decision Card V1–V6 codes
    network: NetworkSignal | None = None
    decision_record: "ApplicationRecord | None" = None        # null until the candidate records a decision


# --- run level --------------------------------------------------------------------------------

LEVERAGE_KINDS = ("WORK_AUTH_DECLARATION", "COMP_FLOOR_CONFIGURATION", "OPEN_TECHNICAL_UNKNOWN", "REQUIRED_EVIDENCE_GAP")


class QueueCell(BaseModel):
    """One cell of the D1 queue view: jobs sharing (queue tier, frozen action),
    in frozen ranking order. A display grouping — not a rank or a verdict."""
    queue_tier: str
    frozen_action: str
    job_ids: list[str]


class LeverageEntry(BaseModel):
    """How many of this run's job records one profile declaration, one open
    unknown or one required evidence gap touches. A count, not advice."""
    kind: str
    subject: str
    text: str
    jobs_affected: int
    source_path: str                   # "[*]." path over the run's ScoredJobs


class RunStrategy(BaseModel):
    version: str = STRATEGY_VERSION
    as_of_date: str | None
    queue: list[QueueCell] = Field(default_factory=list)
    profile_leverage: list[LeverageEntry] = Field(default_factory=list)
    network_job_ids: list[str] = Field(default_factory=list)


# --- application log (D4): schema now, no learning --------------------------------------------------
#
# Append-only JSON Lines of candidate-recorded events. A decision stores a
# SNAPSHOT of the frozen and lens values at decision time (values, not
# references), so later rule changes never rewrite history. Outcomes are
# recorded by the candidate only — never inferred.

LOG_SCHEMA_VERSION = "application_log_v1"
DECISIONS = ("APPLIED", "SKIPPED", "DEFERRED", "SAVED")
DECLARED_EFFORTS = ("LOW", "MEDIUM", "HIGH", "UNKNOWN")        # candidate-declared; the system never estimates it
OUTCOMES = ("NOT_STARTED", "APPLIED", "INTERVIEW", "FINAL_ROUND", "OFFER", "REJECTED", "WITHDRAWN", "CLOSED")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _check_date(value: str) -> str:
    if not _DATE.match(value or ""):
        raise ValueError(f"date must be YYYY-MM-DD, got {value!r}")
    return value


class StabilitySnapshot(BaseModel):
    stability_label: str
    tier_floor: str
    tier_ceiling: str
    open_unknowns: list[OpenUnknownRef] = Field(default_factory=list)


class JobSnapshot(BaseModel):
    """Frozen + lens values copied at decision time."""
    job_id: str
    company: str
    job_title: str
    canonical_url: str
    as_of_date: str | None
    frozen_action: str
    tier: str
    overall_score: float
    decision_stability: StabilitySnapshot
    trajectory_direction: str
    matched_tracks: list[TrackRef] = Field(default_factory=list)
    queue_tier: str
    declared_blocks: list[DeclaredBlock] = Field(default_factory=list)
    transition_lens_version: str
    ptf_scale: str
    card_version: str
    strategy_version: str


class DecisionEvent(BaseModel):
    event: Literal["decision"] = "decision"
    schema_version: str = LOG_SCHEMA_VERSION
    job_id: str
    decision_date: str
    decision: str
    declared_effort: str = "UNKNOWN"
    notes: str = ""
    snapshot: JobSnapshot

    @field_validator("decision_date")
    @classmethod
    def _date(cls, v):
        return _check_date(v)

    @model_validator(mode="after")
    def _values(self):
        if self.decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        if self.declared_effort not in DECLARED_EFFORTS:
            raise ValueError(f"declared_effort must be one of {DECLARED_EFFORTS}")
        if self.snapshot.job_id != self.job_id:
            raise ValueError("snapshot belongs to a different job")
        return self


class OutcomeEvent(BaseModel):
    event: Literal["outcome"] = "outcome"
    schema_version: str = LOG_SCHEMA_VERSION
    job_id: str
    outcome: str
    outcome_date: str
    notes: str = ""

    @field_validator("outcome_date")
    @classmethod
    def _date(cls, v):
        return _check_date(v)

    @model_validator(mode="after")
    def _values(self):
        if self.outcome not in OUTCOMES or self.outcome == "NOT_STARTED":
            raise ValueError(f"outcome must be one of {OUTCOMES[1:]} (NOT_STARTED is the state before any outcome)")
        return self


class ApplicationRecord(BaseModel):
    """The candidate's log for one job, folded from its events in file order.
    Every decision snapshot is kept; the latest decision is current."""
    job_id: str
    decisions: list[DecisionEvent] = Field(default_factory=list)
    outcome: str = "NOT_STARTED"
    outcome_dates: dict[str, str] = Field(default_factory=dict)
    outcome_events: list[OutcomeEvent] = Field(default_factory=list)

    @property
    def current(self) -> DecisionEvent | None:
        return self.decisions[-1] if self.decisions else None


ApplicationStrategy.model_rebuild()
