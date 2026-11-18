from datetime import datetime, timezone

from pydantic import BaseModel, Field

from src.models.skill_evidence import SkillEvidence
from src.models.work_authorization_evidence import WorkAuthorizationEvidence


class ProcessedJob(BaseModel):
    """Normalized, deduplicated job. One row per real-world job posting,
    even if it was seen under multiple queries or (in future) multiple
    sources. Provenance from collection is preserved, never discarded."""

    # Identity
    job_id: str  # canonical internal ID (stable hash of company+title+location)
    sources: list[str] = Field(default_factory=list)
    source_job_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    matched_queries: list[str] = Field(default_factory=list)
    canonical_url: str = ""
    duplicate_companies: list[str] = Field(default_factory=list)  # companies merged in via content-similarity dedup (recruiter reposts)

    # Company
    company: str = ""
    company_normalized: str = ""
    industry: str = "unknown"
    company_size: str = "unknown"
    company_stage: str = "unknown"

    # Role
    job_title: str = ""
    job_title_normalized: str = ""
    role_family: str = "unknown"
    role_level: str = "unknown"
    employment_type: str = "unknown"

    # Location
    country: str = "unknown"
    city: str = "unknown"
    location_normalized: str = ""
    remote_type: str = "unknown"
    hybrid_days: str = "unknown"
    office_location: str = ""

    # Compensation
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_raw: str = ""
    salary_unknown: bool = True
    salary_min_myr: float | None = None  # cross-currency comparison only, approximate FX rate
    salary_max_myr: float | None = None

    # Timing
    posted_date: str | None = None
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    job_age_days: int | None = None   # calendar days between posted_date and as_of_date
    as_of_date: str | None = None     # scoring-run date this job was aged against (YYYY-MM-DD)
    status: str = "NEW"

    # Description (cleaned of HTML, but not otherwise rewritten)
    description_raw: str = ""
    requirements_raw: str = ""
    responsibilities_raw: str = ""

    # Extracted requirements
    # LEGACY skill lists. Legacy taxonomy only, and description-only
    # mentions are lumped into preferred_skills (unknown importance
    # conflated with preferred). No score reads them any more — scoring
    # uses skill_evidence below (technical_alignment); they remain only for
    # data_completeness's `skills_extracted` check.
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    years_experience_required: float | None = None
    education_required: str | None = None

    # Evidence layer (technical_alignment reads skill_evidence). Concept names,
    # split by kind, plus per-concept provenance with importance
    # required | preferred | unknown.
    tools: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    skill_evidence: list[SkillEvidence] = Field(default_factory=list)

    # Requirements-section detection. requirements_raw above is left
    # untouched (interview_fit/YOE extraction read it); detected section
    # text is stored separately. UNKNOWN = no section confidently found.
    requirements_section_status: str = "UNKNOWN"   # DETECTED | UNKNOWN
    requirements_section_source: str = "none"      # source_field | description_heading | both | none
    requirements_section_raw: str = ""
    preferred_section_raw: str = ""
    section_headings: list[str] = Field(default_factory=list)  # e.g. "required: Requirements"

    # ML / DS characteristics
    machine_learning: bool = False
    deep_learning: bool = False
    statistics: bool = False
    experimentation: bool = False
    ab_testing: bool = False
    forecasting: bool = False
    nlp: bool = False
    computer_vision: bool = False
    recommendation: bool = False
    fraud: bool = False
    risk: bool = False
    fintech: bool = False

    # Role classification (section 11)
    role_type: str = "UNKNOWN"          # e.g. TRUE_DS, DS_TITLE_BUT_ANALYTICS_HEAVY, ML_ENGINEER, ...
    role_type_confidence: float = 0.0

    # Employer clarity (P1-2) — is the posting company likely the actual
    # employer, or a staffing/recruitment agency? Name-pattern heuristic
    # only; never guesses who the real underlying employer is.
    employer_clarity: str = "UNKNOWN"   # DIRECT_EMPLOYER, RECRUITER, UNKNOWN
    employer_clarity_reason: str = ""

    # HC-2 work authorization (hard constraint, see hard_filters.py and
    # enrichment/work_authorization.py). Purely JOB-side: FAIL means the
    # posting explicitly denies eligibility to foreign applicants. No
    # candidate citizenship data exists or is inferred.
    # Values: PASS (explicit openness to foreign applicants), FAIL
    # (explicit foreigner/nationality exclusion, uncontradicted), UNKNOWN
    # (everything else, the default — always eligible).
    work_authorization_status: str = "UNKNOWN"
    work_authorization_reason: str = ""
    # FOREIGNERS_NOT_ACCEPTED | NATIONALITY_SPECIFIC | OTHER_EXPLICIT_RESTRICTION | NONE | UNKNOWN
    work_authorization_restriction_type: str = "UNKNOWN"
    work_authorization_evidence: list[WorkAuthorizationEvidence] = Field(default_factory=list)

    # Named hard-constraint statuses, set by pipeline/hard_filters.py.
    # They live here (not only on ScoredJob) so values set during
    # filtering survive the ProcessedJob -> ScoredJob conversion, which
    # copies declared fields only.
    hc_substantive_ds_status: str = "PASS"         # PASS or FAIL
    hc_work_authorization_status: str = "UNKNOWN"  # PASS, FAIL, or UNKNOWN
