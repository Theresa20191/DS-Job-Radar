"""Career Coach decision rules V1.2: viability (V-*), alignment (A-T, A-D,
EX-*, SN-*, ED-1), readiness (R-*), practical constraint (P-*), effort (E-*),
priority (PR-*), investment (I-*) and next action (NA-*).

Every function returns its state plus the rule id that decided it. No
scores, no weights, no probabilities; UNKNOWN is never a negative signal."""

import re
from dataclasses import dataclass

from src.enrichment.requirement_extractor import years_experience_candidates
from src.models.career_coach import (
    JUSTIFIED_BY_PRIORITY,
    CoachConfig,
    CoachDeclarations,
    JobProjection,
    LiveCheckRecord,
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
ML_ORIENTED_TRACKS = ("ML_ENGINEER", "ML_SCIENTIST", "APPLIED_SCIENTIST")      # EX-1
# E-S2: activities a title family is expected to centre on. A family not
# listed here (e.g. "Other") sets no expectation, so it never "differs".
TITLE_FAMILY_WORK = {
    "Data Scientist": {"MODELLING", "EXPERIMENTATION", "OPTIMISATION", "ANALYTICS"},
    "Senior Data Scientist": {"MODELLING", "EXPERIMENTATION", "OPTIMISATION", "ANALYTICS"},
    "Risk Data Scientist": {"MODELLING", "ANALYTICS"},
    "Fraud Data Scientist": {"MODELLING", "ANALYTICS"},
    "Product Data Scientist": {"EXPERIMENTATION", "ANALYTICS", "MODELLING"},
    "Decision Scientist": {"OPTIMISATION", "MODELLING", "EXPERIMENTATION"},
    "Analytics Scientist": {"ANALYTICS", "MODELLING", "EXPERIMENTATION"},
    "ML Scientist": {"RESEARCH", "MODELLING"},
    "Applied Scientist": {"RESEARCH", "MODELLING"},
    "ML Engineer": {"DEPLOYMENT", "MODELLING", "MLOPS_PLATFORM", "GENAI_APPS"},
    "Data Analyst": {"ANALYTICS", "DASHBOARDS_MIS"},
}


@dataclass
class Factor:
    rule_id: str
    factor: str
    result: str                     # PASS | FAIL | UNKNOWN
    detail: str
    live_record_id: str | None = None
    material: bool = True           # every UNKNOWN factor produced here is material by construction


# ------------------------------------------------------------- viability

def viability_factors(job: JobProjection, declarations: CoachDeclarations,
                      live: LiveCheckRecord | None) -> list[Factor]:
    factors: list[Factor] = []
    country = job.country

    auth = declarations.work_authorization.get(country)                       # V-2 / V-2c
    if auth == "NOT_ELIGIBLE":
        factors.append(Factor("V-2", "work_authorization", "FAIL", f"You declared NOT_ELIGIBLE for {country}"))
    elif auth == "PASS":
        factors.append(Factor("V-2", "work_authorization", "PASS", f"You declared PASS for {country}"))

    floor = declarations.compensation_floor.get(country)                      # V-3 / V-3b
    if floor is not None and floor.hard:
        status = compare_floor(job, floor.currency, floor.monthly_amount)
        amount = f"{floor.currency} {floor.monthly_amount:g}/month"
        if status == "BELOW":
            factors.append(Factor("V-3", "compensation_floor", "FAIL",
                                  f"Posted maximum {job.salary_currency} {job.salary_max:g}/month is below your hard floor {amount}"))
        elif status == "MEETS":
            factors.append(Factor("V-3", "compensation_floor", "PASS", f"Posted range meets your hard floor {amount}"))
        else:
            detail = "salary not disclosed" if status == "UNDISCLOSED" else "posted range straddles"
            factors.append(Factor("V-3b", "compensation_floor", "UNKNOWN", f"Hard floor {amount}: {detail}"))

    if country == "Singapore" and "mycareersfuture" in job.sources:            # V-4 (M5)
        if live is not None:
            if live.page_reviewable == "NO" or live.email_in_jd == "NO":
                factors.append(Factor("V-4a", "sg_application_channel", "FAIL",
                                      "SG MyCareersFuture JD has no email address" if live.email_in_jd == "NO"
                                      else "SG MyCareersFuture JD page is not reviewable",
                                      live.record_id))
            else:
                factors.append(Factor("V-4c", "sg_application_channel", "PASS",
                                      "Live MyCareersFuture JD is reviewable and lists an email address", live.record_id))
        elif not EMAIL_RE.search(job.description_raw):
            factors.append(Factor("V-4a′", "sg_application_channel", "FAIL", "SG MyCareersFuture JD has no email address"))
        else:
            factors.append(Factor("V-4b", "sg_application_channel", "UNKNOWN",
                                  "Stored JD lists an email; no sufficiently current live check confirms the page"))

    if job.work_authorization_restriction_type == "OTHER_EXPLICIT_RESTRICTION" and auth != "PASS":   # V-5
        factors.append(Factor("V-5", "job_side_restriction", "UNKNOWN",
                              f"Posting states a work-authorization condition; your {country} status is {auth or 'undeclared'}"))

    if live is not None and live.posting_status == "CLOSED":                  # V-6 (M6)
        factors.append(Factor("V-6", "posting_open", "UNKNOWN",
                              f"Live check on {live.checked_on} recorded the posting as Closed", live.record_id))
    return factors


def compare_floor(job: JobProjection, currency: str, amount: float) -> str:
    """MEETS | BELOW | STRADDLES | UNDISCLOSED — same currency, monthly only."""
    if job.salary_unknown or job.salary_max is None or job.salary_currency != currency \
            or (job.salary_period or "").lower() != "monthly":
        return "UNDISCLOSED"
    if job.salary_max < amount:
        return "BELOW"
    if job.salary_min is not None and job.salary_min >= amount:
        return "MEETS"
    return "STRADDLES"


def viability_state(factors: list[Factor]) -> str:                            # V-AGG
    if any(f.result == "FAIL" for f in factors):
        return "BLOCKED"
    if any(f.result == "UNKNOWN" for f in factors):
        return "UNKNOWN"
    return "VIABLE"


# ------------------------------------------------------------- alignment

def technical(job: JobProjection, profile: dict) -> tuple[str, list[str], list[str], dict[str, str]]:
    """A-T: (state, required, evidenced, {gap name: kind})."""
    candidate = {s.lower() for level in ("strong", "moderate")
                 for s in (profile.get("skills", {}).get(level, []) + profile.get("techniques", {}).get(level, []))}
    required: dict[str, str] = {}
    for e in job.skill_evidence:
        if e.importance == "required":
            required.setdefault(e.name, e.kind)
    names = sorted(required)
    evidenced = [n for n in names if n.lower() in candidate]
    gaps = {n: required[n] for n in names if n.lower() not in candidate}
    if not names:
        state = "NO_REQUIREMENTS_STATED"
    elif not gaps:
        state = "FULL"
    elif not evidenced:
        state = "NONE_EVIDENCED"
    else:
        state = "PARTIAL"
    return state, names, evidenced, gaps


def domain(placements: dict[str, set[str]], profile: dict) -> tuple[str, list[str]]:
    """A-D over OWN_WORK / DEFINING terms only."""
    terms = sorted(t for t, p in placements.items() if p & {"OWN_WORK", "DEFINING"})
    if not terms:
        return "NO_DOMAIN_STATED", []
    return ("MATCH" if set(terms) & set(profile.get("domain", [])) else "NO_OVERLAP"), terms


def experience(job: JobProjection, declarations: CoachDeclarations, deciding_track: str | None,
               k: float) -> tuple[str, str, object]:
    """EX-0..EX-4: (state, candidate measure, candidate value)."""
    measure = "relevant_ml_years" if deciding_track in ML_ORIENTED_TRACKS else "relevant_ds_years"
    value = getattr(declarations.experience, measure)
    required = job.years_experience_required
    if required is None:
        return "NOT_STATED_BY_JOB", measure, value
    if value == "UNKNOWN":
        return "UNASSESSED", measure, value
    shortfall = required - value
    if shortfall <= 0:
        return "MEETS", measure, value
    return ("WITHIN_STRETCH" if shortfall <= k else "MATERIAL_GAP"), measure, value


def years_sentence(job: JobProjection) -> str | None:
    """The sentence stating the extracted years requirement, if findable: the
    first sentence in which the requirement extractor itself reads that value
    (written numbers, ranges by their lower bound, "N+" / "N or more")."""
    if job.years_experience_required is None:
        return None
    for text in (job.requirements_section_raw, job.description_raw):
        for line in re.split(r"(?<=[.;!?])\s+|\n", text or ""):
            if job.years_experience_required in years_experience_candidates(line):
                return line.strip()
    return None


def seniority(job: JobProjection, declarations: CoachDeclarations, config: CoachConfig
              ) -> tuple[str, str | None, str]:
    """SN-1..SN-4: (state, direction, rule)."""
    aligned, stretch = declarations.seniority.aligned_levels, declarations.seniority.stretch_levels
    if not aligned and not stretch:
        return "UNKNOWN", None, "SN-2"
    level = job.role_level
    if level == "Mid/Unspecified":
        return "UNKNOWN", None, "SN-3"
    if level in aligned:
        return "ALIGNED", None, "SN-4"
    if level in stretch:
        return "STRETCH", None, "SN-4"
    rung, declared = config.level_rungs[level], [config.level_rungs[x] for x in aligned + stretch]
    direction = "ABOVE" if rung > max(declared) else "BELOW" if rung < min(declared) else "OTHER"
    return "MATERIAL_GAP", direction, "SN-4"


def education(job: JobProjection, profile: dict, config: CoachConfig) -> str:           # ED-1
    rank = config.education_ranks.get(job.education_required or "")
    if rank is None:
        return "NOT_STATED"
    return "BELOW" if rank > profile.get("education_level_rank", 0) else "MEETS"


def readiness(exp: str, sen: str, sen_direction: str | None, edu: str) -> tuple[str, str]:  # R-1..R-7
    if edu == "BELOW":
        return "MATERIAL_GAP", "R-1"
    if exp == "MATERIAL_GAP":
        return "MATERIAL_GAP", "R-2"
    if sen == "MATERIAL_GAP" and sen_direction == "ABOVE" and exp == "MEETS":
        return "LEVEL_CONFLICT", "R-3"
    if sen == "MATERIAL_GAP":
        return "MATERIAL_GAP", "R-4"
    if exp == "WITHIN_STRETCH" or sen == "STRETCH":
        return "STRETCH", "R-5"
    if exp == "MEETS" or sen == "ALIGNED" or edu == "MEETS":
        return "NO_GAP", "R-6"
    return "UNASSESSED", "R-7"


# ------------------------------------------------------ practical (P-*)

def practical(job: JobProjection, declarations: CoachDeclarations, profile: dict,
              contacts: set[str]) -> tuple[str, dict[str, str], str]:
    """(state, components, deciding rule)."""
    components: dict[str, str] = {}
    state, rule = "CLEAR", "P-1c"
    floor = declarations.compensation_floor.get(job.country)
    if floor is not None and not floor.hard:
        status = compare_floor(job, floor.currency, floor.monthly_amount)
        if status == "BELOW":
            state, rule = "MATERIAL", "P-1"
        elif status in ("STRADDLES", "UNDISCLOSED"):
            state, rule = "OPEN", "P-1b"
        components["compensation"] = {"BELOW": "BELOW_SOFT_FLOOR", "MEETS": "MEETS_FLOOR"}.get(status, status)
    else:
        components["compensation"] = "HARD_FLOOR_SEE_VIABILITY" if floor is not None else "NO_FLOOR"
    targets = {t.lower() for t in profile.get("target_locations", [])}
    located = {job.country.lower(), job.city.lower(), job.location_normalized.lower()} & targets
    components["location"] = "IN_TARGET" if located else "OUTSIDE_TARGET"                     # P-2
    components["arrangement"] = job.remote_type                                                 # P-3
    recruiter = job.employer_clarity == "RECRUITER"
    components["network"] = "CONTACT_AVAILABLE" if (not recruiter and job.company_normalized in contacts) else "NONE"   # P-4
    components["employer"] = "RECRUITER_POSTING" if recruiter else "DIRECT"                   # P-5
    return state, components, rule


# ---------------------------------------------------------- effort (E-*)

def resume_effort(tech_state: str, gap_count: int, readiness_state: str, positioning: str | None,
                  declarations: CoachDeclarations, heavy_gap_min: int) -> tuple[str, str]:
    if tech_state == "NONE_EVIDENCED" or gap_count >= heavy_gap_min:
        return "HEAVY", "E-R1"
    ready = declarations.resume_variants_ready or []
    if tech_state == "PARTIAL" or readiness_state == "STRETCH" or positioning not in ready:
        return "TARGETED", "E-R2"
    return "LIGHT", "E-R3"


def research_effort(job: JobProjection, substance_status: str, domain_state: str, cv_state: str,
                    central_work: str | None, declarations: CoachDeclarations) -> tuple[str, str]:
    if job.employer_clarity == "RECRUITER" or substance_status == "PARTIAL" or domain_state == "NO_OVERLAP":
        return "HIGH", "E-S1"
    expected = TITLE_FAMILY_WORK.get(job.role_family)
    family_differs = expected is not None and central_work is not None and central_work not in expected
    undisclosed_no_floor = job.salary_unknown and declarations.compensation_floor.get(job.country) is None
    if cv_state == "OFF_VOCABULARY" or family_differs or undisclosed_no_floor:
        return "MEDIUM", "E-S2"
    return "LOW", "E-S3"


# ------------------------------------------------ priority / investment / action

def priority(viability: str, cv: str, ready: str, constraint: str) -> str:            # PR-1..PR-12
    if viability == "BLOCKED":
        return "PR-1"
    if cv == "NON_TARGET":
        return "PR-2"
    if viability == "UNKNOWN":
        return "PR-3"
    if cv == "UNKNOWN":
        return "PR-4"
    if constraint == "MATERIAL":
        return "PR-5"
    if ready == "MATERIAL_GAP":
        return "PR-6"
    if cv in ("ADJACENT", "OFF_VOCABULARY"):
        return "PR-7"
    if ready == "LEVEL_CONFLICT":
        return "PR-8"
    open_ok = ready in ("NO_GAP", "UNASSESSED") and constraint == "CLEAR"
    if cv == "PRIMARY":
        return "PR-9" if open_ok else "PR-10"
    return "PR-11" if open_ok else "PR-12"                      # SECONDARY


PRIORITY_OF_RULE = {"PR-1": "DO_NOT_INVEST", "PR-2": "DO_NOT_INVEST", "PR-3": "VERIFY_FIRST",
                    "PR-4": "VERIFY_FIRST", "PR-5": "SELECTIVE", "PR-6": "SELECTIVE", "PR-7": "SELECTIVE",
                    "PR-8": "VERIFY_FIRST", "PR-9": "HIGH", "PR-10": "PRIORITY", "PR-11": "PRIORITY",
                    "PR-12": "SELECTIVE"}


def investment(prio: str, resume: str | None, research: str | None) -> tuple[str, str | None, str]:   # I-1..I-3
    justified = JUSTIFIED_BY_PRIORITY[prio]
    if resume is None or research is None:
        return justified, None, "NOT_APPLICABLE"
    required = "HIGH" if (resume == "HEAVY" or research == "HIGH") else \
        "LOW" if (resume == "LIGHT" and research == "LOW") else "MODERATE"
    if justified in ("VERIFY_ONLY", "NONE"):
        fit = "NOT_APPLICABLE"
    elif justified == "FULL":
        fit = "WITHIN_BUDGET"
    elif justified == "STANDARD":
        fit = "EXCEEDS_BUDGET" if (resume == "HEAVY" and research == "HIGH") else "WITHIN_BUDGET"
    else:                                                        # LIGHT_TOUCH
        fit = "EXCEEDS_BUDGET" if (resume == "HEAVY" or research == "HIGH") else "WITHIN_BUDGET"
    return justified, required, fit


VERIFY_ORDER = (("V-5", "VERIFY_ELIGIBILITY"), ("V-3b", "VERIFY_COMPENSATION"),
                ("V-6", "VERIFY_POSTING_OPEN"), ("V-4b", "VERIFY_APPLICATION_CHANNEL"))


def next_action(prio_rule: str, unknown_rules: list[str], fit: str, contact: bool, research: str | None,
                resume: str | None) -> tuple[str, str]:
    prio = PRIORITY_OF_RULE[prio_rule]
    if prio == "DO_NOT_INVEST":
        return "DO_NOT_INVEST", "NA-1"
    if prio_rule == "PR-3":
        return next(a for r, a in VERIFY_ORDER if r in unknown_rules), "NA-2"
    if prio_rule == "PR-4":
        return "VERIFY_JOB_SCOPE", "NA-3"
    if prio_rule == "PR-8":
        return "VERIFY_ROLE_LEVEL", "NA-4"
    if fit == "EXCEEDS_BUDGET":
        return "HOLD", "NA-5"
    if contact and prio in ("HIGH", "PRIORITY"):
        return "NETWORK", "NA-6"
    if research == "HIGH" and prio in ("HIGH", "PRIORITY"):
        return "RESEARCH_EMPLOYER", "NA-7"
    if resume in ("TARGETED", "HEAVY"):
        return "TAILOR_RESUME", "NA-8"
    return "APPLY", "NA-9"
