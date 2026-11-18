"""Career Coach V1.2 models — the frozen Gate 1 contract.

Career Coach derives its own decision state from upstream job data, the
hard-filter evaluation, the candidate's explicit declarations and its own
config (config/career_coach.yaml). It never reads Frozen Scoring, PTF,
Transition Lens, Decision Card or Application Strategy outputs: a job enters
only as a JobProjection built from an exact ProcessedJob (never a ScoredJob),
paired with its HardFilterEvaluation (IN-1).

This module holds schemas and design invariants only — no decision rules.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.models.processed_job import ProcessedJob
from src.models.skill_evidence import SkillEvidence
from src.models.work_authorization_evidence import WorkAuthorizationEvidence

COACH_VERSION = "career_coach_v1.2"

# Exactly the values enrichment/role_classifier.classify_role_level returns.
CLASSIFIER_LEVELS = ("Intern", "Junior", "Principal", "Lead", "Staff", "Manager", "Senior", "Mid/Unspecified")
# `Mid/Unspecified` means "no level word in the title" — not declarable (SN-0).
DECLARABLE_LEVELS = tuple(level for level in CLASSIFIER_LEVELS if level != "Mid/Unspecified")

ACTIVITIES = ("MODELLING", "DEPLOYMENT", "EXPERIMENTATION", "OPTIMISATION", "ANALYTICS", "RESEARCH",
              "MLOPS_PLATFORM", "GENAI_APPS", "DASHBOARDS_MIS", "PROJECT_DELIVERY")
TRACK_TIERS = ("primary", "secondary", "adjacent_ok", "non_target")
SPECIFICITIES = ("DOMAIN", "FUNCTION", "GENERAL")        # RT-2 order
PLACEMENTS = ("OWN_WORK", "DEFINING", "CONTEXT")          # D-1
YEARS_UNKNOWN = "UNKNOWN"

Activity = Literal["MODELLING", "DEPLOYMENT", "EXPERIMENTATION", "OPTIMISATION", "ANALYTICS", "RESEARCH",
                   "MLOPS_PLATFORM", "GENAI_APPS", "DASHBOARDS_MIS", "PROJECT_DELIVERY"]
ClassifierLevel = Literal["Intern", "Junior", "Principal", "Lead", "Staff", "Manager", "Senior", "Mid/Unspecified"]
DeclarableLevel = Literal["Intern", "Junior", "Principal", "Lead", "Staff", "Manager", "Senior"]
Placement = Literal["OWN_WORK", "DEFINING", "CONTEXT"]
Specificity = Literal["DOMAIN", "FUNCTION", "GENERAL"]
Years = float | Literal["UNKNOWN"]

RunKind = Literal["HISTORICAL_REPLAY", "OPERATIONAL"]
EvaluationScope = Literal["FULL", "HARD_FILTER_REJECTED"]
ViabilityState = Literal["BLOCKED", "VIABLE", "UNKNOWN"]
FactorResult = Literal["PASS", "FAIL", "UNKNOWN"]
SubstanceStatus = Literal["ESTABLISHED", "PARTIAL", "UNKNOWN"]
SubstanceBasis = Literal["RESPONSIBILITY_SECTION", "DESCRIPTION_SENTENCES", "NONE"]
CareerValueState = Literal["PRIMARY", "SECONDARY", "ADJACENT", "NON_TARGET", "OFF_VOCABULARY", "UNKNOWN"]
TechnicalState = Literal["FULL", "PARTIAL", "NONE_EVIDENCED", "NO_REQUIREMENTS_STATED"]
DomainState = Literal["MATCH", "NO_OVERLAP", "NO_DOMAIN_STATED"]
ExperienceState = Literal["MEETS", "WITHIN_STRETCH", "MATERIAL_GAP", "NOT_STATED_BY_JOB", "UNASSESSED"]
ExperienceMeasure = Literal["relevant_ds_years", "relevant_ml_years"]
SeniorityState = Literal["ALIGNED", "STRETCH", "MATERIAL_GAP", "UNKNOWN"]
SeniorityDirection = Literal["ABOVE", "BELOW", "OTHER"]
EducationState = Literal["MEETS", "BELOW", "NOT_STATED"]
ReadinessState = Literal["NO_GAP", "STRETCH", "LEVEL_CONFLICT", "MATERIAL_GAP", "UNASSESSED"]
ConstraintState = Literal["CLEAR", "OPEN", "MATERIAL"]
ResumeEffort = Literal["LIGHT", "TARGETED", "HEAVY"]
ResearchEffort = Literal["LOW", "MEDIUM", "HIGH"]
Priority = Literal["HIGH", "PRIORITY", "SELECTIVE", "VERIFY_FIRST", "DO_NOT_INVEST"]
JustifiedInvestment = Literal["FULL", "STANDARD", "LIGHT_TOUCH", "VERIFY_ONLY", "NONE"]
RequiredInvestment = Literal["LOW", "MODERATE", "HIGH"]
InvestmentFit = Literal["WITHIN_BUDGET", "EXCEEDS_BUDGET", "NOT_APPLICABLE"]
NextAction = Literal["APPLY", "TAILOR_RESUME", "NETWORK", "RESEARCH_EMPLOYER", "HOLD",
                     "VERIFY_ELIGIBILITY", "VERIFY_COMPENSATION", "VERIFY_POSTING_OPEN",
                     "VERIFY_APPLICATION_CHANNEL", "VERIFY_JOB_SCOPE", "VERIFY_ROLE_LEVEL", "DO_NOT_INVEST"]
VerificationCode = Literal["VQ-ELIGIBILITY", "VQ-COMPENSATION", "VQ-POSTING_OPEN", "VQ-APPLICATION_CHANNEL",
                           "VQ-JOB_SCOPE", "VQ-ROLE_LEVEL"]
ProfileItem = Literal["PD-WORK_AUTH", "PD-RELEVANT_YEARS", "PD-TARGET_LEVELS", "PD-RESUME_VARIANTS"]
UncertaintyState = Literal["RESOLVED", "JOB_SIDE_UNKNOWN", "PROFILE_DECLARATION_UNKNOWN", "MIXED"]

VERIFY_ACTIONS = ("VERIFY_ELIGIBILITY", "VERIFY_COMPENSATION", "VERIFY_POSTING_OPEN",
                  "VERIFY_APPLICATION_CHANNEL", "VERIFY_JOB_SCOPE", "VERIFY_ROLE_LEVEL")
JUSTIFIED_BY_PRIORITY = {"HIGH": "FULL", "PRIORITY": "STANDARD", "SELECTIVE": "LIGHT_TOUCH",      # I-1
                         "VERIFY_FIRST": "VERIFY_ONLY", "DO_NOT_INVEST": "NONE"}
PRIORITY_RULES = tuple(f"PR-{n}" for n in range(1, 13))
# Decision fields that are NOT_EVALUATED (None) for a hard-filter-rejected job (IN-4).
LAYER_FIELDS = ("work_substance", "career_value", "resume_track", "candidate_alignment", "readiness",
                "practical_constraint", "resume_effort", "research_effort")
# Every decision field below must carry provenance (O.1).
PROVENANCE_FIELDS = ("evaluation_scope", "application_viability", "blocking_reason", *LAYER_FIELDS,
                     "application_priority", "application_investment", "next_action", "primary_reason",
                     "verification_queue", "profile_items_affecting_job", "uncertainty_state",
                     "key_evidence", "evidence_gaps")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- inputs (C)

class JobProjection(_Strict):
    """IN-2 whitelist: the only job fields Career Coach can read. Every field
    is used by a named rule; nothing else on ProcessedJob is reachable."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    company: str
    company_normalized: str
    job_title: str
    country: str
    canonical_url: str
    posted_date: str | None
    source_job_ids: list[str]
    sources: list[str]
    description_raw: str
    requirements_section_raw: str
    preferred_section_raw: str
    requirements_section_status: str
    section_headings: list[str]
    role_family: str
    role_level: ClassifierLevel
    skill_evidence: list[SkillEvidence]
    years_experience_required: float | None
    education_required: str | None
    city: str
    location_normalized: str
    remote_type: str
    hybrid_days: str
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    salary_unknown: bool
    employer_clarity: str
    work_authorization_status: str
    work_authorization_restriction_type: str
    work_authorization_evidence: list[WorkAuthorizationEvidence]

    @classmethod
    def from_processed(cls, job: ProcessedJob) -> "JobProjection":
        """IN-3: only an exact ProcessedJob is accepted — any subclass (such as
        a scored job carrying legacy fields) is refused, not silently narrowed."""
        if type(job) is not ProcessedJob:
            raise TypeError(f"Career Coach accepts an exact ProcessedJob, not {type(job).__name__} (IN-3)")
        return cls(**{name: getattr(job, name) for name in cls.model_fields})


class HardFilterEvaluation(_Strict):
    """The existing filter_jobs result for one job, consumed as-is (IN-1)."""
    passed: bool
    reasons: list[str] = Field(default_factory=list)
    hc_substantive_ds_status: str
    hc_work_authorization_status: str

    @model_validator(mode="after")
    def _passed_iff_no_reasons(self):
        if self.passed == bool(self.reasons):
            raise ValueError("hard-filter evaluation: passed must be True exactly when there are no reasons")
        return self


class CoachInput(_Strict):
    job: JobProjection
    hard_filter: HardFilterEvaluation


class LiveCheckRecord(_Strict):
    """LC-1: one dated, append-only live observation. The engine never makes
    live checks itself; it only reads records like this (M15)."""
    record_id: str
    job_id: str
    source_job_id: str
    checked_on: date
    method: str
    page_reviewable: Literal["YES", "NO"]
    email_in_jd: Literal["YES", "NO"]
    posting_status: Literal["OPEN", "CLOSED", "UNKNOWN"]


# ------------------------------------------- candidate declarations (profile)

class TargetTracks(_Strict):
    primary: list[str]
    secondary: list[str]
    adjacent_ok: list[str]
    non_target: list[str]


class CompensationFloor(_Strict):
    currency: str
    monthly_amount: float = Field(gt=0)
    hard: bool


class ExperienceDeclaration(_Strict):
    """EX-0..EX-5. Only the candidate supplies values; UNKNOWN stays UNKNOWN.
    current_role_years is tenure and is never compared with a job (EX-0)."""
    current_role_years: Years
    relevant_ds_years: Years
    relevant_ml_years: Years
    relevant_domain_years: dict[str, Years] = Field(default_factory=dict)   # recorded, not compared (EX-5)

    @field_validator("current_role_years", "relevant_ds_years", "relevant_ml_years")
    @classmethod
    def _non_negative(cls, v):
        if v != YEARS_UNKNOWN and v < 0:
            raise ValueError("years must be >= 0 or UNKNOWN")
        return v

    @field_validator("relevant_domain_years")
    @classmethod
    def _domain_non_negative(cls, v):
        if any(x != YEARS_UNKNOWN and x < 0 for x in v.values()):
            raise ValueError("years must be >= 0 or UNKNOWN")
        return v


class SeniorityDeclaration(_Strict):
    """SN-0: levels drawn from the role classifier's declarable levels only."""
    aligned_levels: list[DeclarableLevel] = Field(default_factory=list)
    stretch_levels: list[DeclarableLevel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _disjoint_unique(self):
        for name, levels in (("aligned_levels", self.aligned_levels), ("stretch_levels", self.stretch_levels)):
            if len(levels) != len(set(levels)):
                raise ValueError(f"seniority.{name} has duplicate levels")
        overlap = set(self.aligned_levels) & set(self.stretch_levels)
        if overlap:
            raise ValueError(f"seniority levels declared both aligned and stretch: {sorted(overlap)}")
        return self


class CoachDeclarations(_Strict):
    """profile.yaml `career_coach:` — the candidate's own declarations (M2)."""
    target_tracks: TargetTracks
    compensation_floor: dict[str, CompensationFloor | None]
    work_authorization: dict[str, Literal["PASS", "UNKNOWN", "NOT_ELIGIBLE"]]
    experience: ExperienceDeclaration
    seniority: SeniorityDeclaration
    resume_variants_ready: list[str] | None      # None = not declared (Q-2 PD-RESUME_VARIANTS)


# ---------------------------------------------- coach config (career_coach.yaml)

class ActivityPattern(_Strict):
    id: str
    verbs: str
    objects: str


class ActivitySpec(_Strict):
    patterns: list[ActivityPattern] = Field(min_length=1)
    exclusions: list[str] = Field(default_factory=list)


class DomainCondition(_Strict):
    terms: list[str] = Field(min_length=1)
    placements: list[Placement] = Field(min_length=1)


class ActivityEvidenceCondition(_Strict):
    activities: list[Activity] = Field(min_length=1)
    matching: str | None = None
    not_matching: str | None = None


class TrackCondition(_Strict):
    """All keys present in one condition must hold (AND); any_of / all_of nest."""
    activity_present: list[Activity] | None = None
    activity_absent: list[Activity] | None = None
    central_work_in: list[Activity] | None = None
    domain: DomainCondition | None = None
    title_regex: str | None = None
    work_sentence_regex: str | None = None
    role_family_in: list[str] | None = None
    activity_evidence: ActivityEvidenceCondition | None = None
    any_of: list["TrackCondition"] | None = None
    all_of: list["TrackCondition"] | None = None

    @model_validator(mode="after")
    def _not_empty(self):
        if not any(getattr(self, name) is not None for name in type(self).model_fields):
            raise ValueError("a track condition needs at least one key")
        return self


class TrackSpec(_Strict):
    specificity: Specificity | None = None
    defining: bool = False                         # CV-2: a central-work-defined non-target track
    match: TrackCondition


class Positioning(_Strict):
    label: str
    tracks: list[str] = Field(min_length=1)


class WorkTextConfig(_Strict):
    responsibility_headings: list[str] = Field(min_length=1)
    boilerplate_headings: list[str] = Field(min_length=1)


class Thresholds(_Strict):
    heavy_gap_min: int = Field(ge=1)              # E-R1
    k_stretch_years: float = Field(ge=0)          # EX-4 (O-2)
    live_check_window_days: int = Field(ge=0)     # LC-2 (O-11)


class CoachConfig(_Strict):
    version: Literal["career_coach_v1.2"]
    thresholds: Thresholds
    level_rungs: dict[DeclarableLevel, int]       # SN-4 direction label only
    education_ranks: dict[str, int]               # ED-1
    work_text: WorkTextConfig
    activity_precedence: list[Activity]           # S-4 tie-break
    activities: dict[Activity, ActivitySpec]
    domains: dict[str, list[str]]                 # D-1 vocabulary
    tracks: dict[str, TrackSpec]                  # insertion order = RT-4 config order
    positionings: dict[str, Positioning]          # RT-5 (O-1)


# --------------------------------------------------------------- outputs (N)

class JobRef(_Strict):
    job_id: str
    company: str
    job_title: str
    country: str
    canonical_url: str
    posted_date: str | None


class InputHashes(_Strict):
    raw_corpus: str
    config: str
    profile: str
    eligible_live_checks: str


class RunInfo(_Strict):
    as_of_date: date
    run_kind: RunKind
    coach_version: Literal["career_coach_v1.2"]
    input_hashes: InputHashes


class ViabilityFactor(_Strict):
    rule_id: str
    factor: str
    result: FactorResult
    detail: str
    live_record_id: str | None = None


class ApplicationViability(_Strict):
    state: ViabilityState
    factors: list[ViabilityFactor] = Field(default_factory=list)


class Reason(_Strict):
    rule_id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class WorkSubstance(_Strict):
    status: SubstanceStatus
    basis: SubstanceBasis
    central_work: Activity | None
    activities: dict[Activity, int] = Field(default_factory=dict)
    title_family: str
    evidence_ids: list[str] = Field(default_factory=list)


class MatchedTrack(_Strict):
    track: str
    declared_tier: Literal["primary", "secondary", "adjacent_ok", "non_target"]
    specificity: Specificity | None
    defining: bool
    clause: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_count: int = Field(ge=0)


class CareerValue(_Strict):
    state: CareerValueState
    deciding_rule: Literal["CV-1", "CV-2", "CV-3", "CV-4", "CV-5", "CV-6", "CV-7"]
    deciding_track: str | None
    matched_tracks: list[MatchedTrack] = Field(default_factory=list)
    explanation: str


class ResumeTrack(_Strict):
    positioning: str
    source_track: str
    declared_tier: Literal["primary", "secondary", "adjacent_ok"]
    runner_up: str | None
    deciding_key: Literal["RT-1", "RT-2", "RT-3", "RT-4"]


class TechnicalAlignment(_Strict):
    state: TechnicalState
    required: list[str] = Field(default_factory=list)
    evidenced: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class DomainAlignment(_Strict):
    state: DomainState
    terms: list[str] = Field(default_factory=list)


class ExperienceAlignment(_Strict):
    state: ExperienceState
    job_required_years: float | None
    job_evidence_id: str | None
    candidate_measure: ExperienceMeasure            # EX-1: always named
    candidate_value: Years


class SeniorityAlignment(_Strict):
    state: SeniorityState
    job_level: ClassifierLevel
    candidate_aligned_levels: list[DeclarableLevel] = Field(default_factory=list)
    candidate_stretch_levels: list[DeclarableLevel] = Field(default_factory=list)
    direction: SeniorityDirection | None            # only for MATERIAL_GAP


class EducationAlignment(_Strict):
    state: EducationState


class CandidateAlignment(_Strict):
    technical: TechnicalAlignment
    domain: DomainAlignment
    experience: ExperienceAlignment
    seniority: SeniorityAlignment
    education: EducationAlignment


class Readiness(_Strict):
    state: ReadinessState
    deciding_rule: Literal["R-1", "R-2", "R-3", "R-4", "R-5", "R-6", "R-7"]
    components: dict[str, str] = Field(default_factory=dict)


class PracticalConstraint(_Strict):
    state: ConstraintState
    components: dict[str, str] = Field(default_factory=dict)


class ApplicationInvestment(_Strict):
    justified: JustifiedInvestment
    required: RequiredInvestment | None             # None when effort is NOT_EVALUATED (IN-4)
    fit: InvestmentFit


class VerificationItem(_Strict):
    code: VerificationCode
    question: str
    source: Literal["JOB"]
    if_resolved_favourably: str
    if_resolved_unfavourably: str


class Evidence(_Strict):
    id: str
    kind: str
    text: str
    source_field: str
    section: str
    pattern_id: str | None = None


class EvidenceGap(_Strict):
    kind: Literal["TECHNIQUE", "TOOL", "DOMAIN"]
    name: str
    text: str


class FieldProvenance(_Strict):
    rules: list[str] = Field(min_length=1)
    inputs: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class CareerCoachDecision(_Strict):
    job: JobRef
    run: RunInfo
    evaluation_scope: EvaluationScope
    application_viability: ApplicationViability
    blocking_reason: Reason | None
    work_substance: WorkSubstance | None
    career_value: CareerValue | None
    resume_track: ResumeTrack | Literal["NONE"] | None
    candidate_alignment: CandidateAlignment | None
    readiness: Readiness | None
    practical_constraint: PracticalConstraint | None
    resume_effort: ResumeEffort | None
    research_effort: ResearchEffort | None
    application_priority: Priority
    application_investment: ApplicationInvestment
    next_action: NextAction
    primary_reason: Reason
    verification_queue: list[VerificationItem] = Field(default_factory=list)
    profile_items_affecting_job: list[ProfileItem] = Field(default_factory=list)
    uncertainty_state: UncertaintyState
    key_evidence: list[Evidence] = Field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list)
    provenance: dict[str, FieldProvenance]

    @model_validator(mode="after")
    def _design_invariants(self):
        errors: list[str] = []
        priority, action, scope = self.application_priority, self.next_action, self.evaluation_scope
        blocked = self.application_viability.state == "BLOCKED"

        if scope == "HARD_FILTER_REJECTED":                                     # IN-4
            if not blocked or self.blocking_reason is None or self.blocking_reason.rule_id != "V-1":
                errors.append("IN-4: a hard-filter-rejected job is BLOCKED by V-1")
            if any(getattr(self, name) is not None for name in LAYER_FIELDS):
                errors.append("IN-4: layers after viability are NOT_EVALUATED (None) for rejected jobs")
        elif any(getattr(self, name) is None for name in LAYER_FIELDS):
            errors.append("FULL scope: every layer field must be evaluated")

        if blocked != (self.blocking_reason is not None):
            errors.append("V-AGG: blocking_reason is present exactly when viability is BLOCKED")
        if blocked and priority != "DO_NOT_INVEST":
            errors.append("PR-1: BLOCKED can only be DO_NOT_INVEST")
        if (priority == "DO_NOT_INVEST") != (action == "DO_NOT_INVEST"):
            errors.append("NA-10: DO_NOT_INVEST priority and action go together")
        if (priority == "VERIFY_FIRST") != (action in VERIFY_ACTIONS):
            errors.append("NA-10: VERIFY_FIRST maps to exactly one VERIFY_* action")
        if priority in ("HIGH", "PRIORITY", "SELECTIVE") and \
                (action == "HOLD") != (self.application_investment.fit == "EXCEEDS_BUDGET"):
            errors.append("NA-5: HOLD exactly when effort exceeds the justified investment")
        if self.application_investment.justified != JUSTIFIED_BY_PRIORITY[priority]:
            errors.append("I-1: justified investment follows priority")
        if self.primary_reason.rule_id not in PRIORITY_RULES:
            errors.append("primary_reason must be the PR rule that fired")

        cv = self.career_value.state if self.career_value else None
        if cv == "SECONDARY" and priority == "HIGH":
            errors.append("PR-11: SECONDARY never reaches HIGH")
        if cv in ("ADJACENT", "OFF_VOCABULARY") and priority in ("HIGH", "PRIORITY"):
            errors.append("PR-7: ADJACENT / OFF_VOCABULARY never exceed SELECTIVE")
        if cv == "NON_TARGET" and priority != "DO_NOT_INVEST":
            errors.append("PR-2: NON_TARGET is DO_NOT_INVEST")
        if self.career_value is not None:
            if cv in ("PRIMARY", "SECONDARY", "ADJACENT"):
                if not isinstance(self.resume_track, ResumeTrack) or \
                        self.resume_track.source_track != self.career_value.deciding_track:
                    errors.append("RT-5: resume_track comes from the career-value deciding track")
            elif self.resume_track != "NONE":
                errors.append("RT-6: no resume positioning without a targeted career value")

        missing = [name for name in PROVENANCE_FIELDS if name not in self.provenance]
        if missing:
            errors.append(f"O.1: provenance missing for {missing}")
        if errors:
            raise ValueError("; ".join(errors))
        return self
