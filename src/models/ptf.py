"""Result models for Personal Transition Fit (PTF) and decision stability.

Frozen contract `ptf_support_v1` (see src/scoring/ptf_scale.py):
  - a dimension is either UNKNOWN (known=False, band=None, score=None) or
    known with a positive band in {LIMITED, SUBSTANTIVE, CORE} whose score
    is exactly that band's pinned value. UNKNOWN is never 0.
  - decision stability is display/explanation metadata only; nothing in
    scoring reads it back.
"""

from pydantic import BaseModel, Field, model_validator

from src.scoring.ptf_scale import BAND_VALUES, SCALE_VERSION


class DomainEvidence(BaseModel):
    """One domain-term mention and how D2 placed it (qualifying or not)."""

    term: str
    field: str                    # job_title | company_normalized | description_raw | requirements_raw
    sentence: str
    placement: str                # defining | own_work | peripheral | non_evidence
    classification: str           # generic | specific
    rule: str                     # the rule that fired
    risk_rule: str | None = None  # D2-b interpretation, for the term "Risk" only


class DimensionResult(BaseModel):
    name: str                     # technical_alignment | domain_alignment
    known: bool
    band: str | None = None
    score: float | None = None
    scale: str = SCALE_VERSION
    unknown_reason: str | None = None   # unevidenced_required | vacuous | no_qualifying_overlap
    reasons: list[str] = Field(default_factory=list)
    provenance: list[dict] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _frozen_invariants(self):
        if not self.known:
            if self.band is not None or self.score is not None:
                raise ValueError("UNKNOWN dimension must have band=None and score=None")
        else:
            if self.band not in BAND_VALUES:
                raise ValueError(f"known dimension must have a band in {sorted(BAND_VALUES)}")
            if self.score != BAND_VALUES[self.band]:
                raise ValueError("score must equal the pinned value of its band")
            if self.unknown_reason is not None:
                raise ValueError("known dimension cannot carry an unknown_reason")
        return self


class PTFResult(BaseModel):
    known: bool
    score: float | None = None
    scale: str = SCALE_VERSION
    assessed: list[str] = Field(default_factory=list)   # which dimensions were known
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unknown_is_none(self):
        if self.known != (self.score is not None):
            raise ValueError("PTF known iff score is not None")
        return self


class OpenUnknown(BaseModel):
    field: str                    # technical_alignment | compensation
    direction: str                # up_only | either
    reason: str


class DecisionStability(BaseModel):
    tier_floor: str
    tier_current: str
    tier_ceiling: str
    stability_label: str          # STABLE | COULD_RISE | COULD_FALL | COULD_MOVE
    open_unknowns: list[OpenUnknown] = Field(default_factory=list)
    settled_unknowns: list[str] = Field(default_factory=list)
    ptf_dimensions_assessed: str = "none"   # none | technical | domain | technical+domain
