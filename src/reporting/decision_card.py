"""Decision Card V1 — a job-level presentation of frozen outputs.

A RENDERER, not a second decision engine. It reads only `ScoredJob` values
from one run (after apply_lens) and derives nothing that is not already on
them: no score, weight, threshold, rank, label or action. It never reads the
profile, config, tracks.yaml or raw job descriptions, and nothing upstream
imports it.

Two views (presentation amendment A1–A11):
  - main card: the fast decision interface — Header, Frozen Ranking, Career
    Direction, Work, Key Evidence, Constraints & Readiness, Evidence Gaps,
    What to Verify — in readable wording from fixed display dictionaries;
  - drill-down: the full audit interface — the complete V1 field-level view
    ("Audit › ..."), machine codes, every evidence sentence, per-item gap
    details, compensation detail and every flag with its reason.
Facts identical across the run, or sourced from a profile declaration, sit
in the run context and appear on a card only when job-specific.

Every factual CardLine carries resolvable `source_paths` into the ScoredJob;
evidence lines keep the upstream field / heading / rule_id. Each main-routed
flag is attached, exactly once, to the line stating its fact. Identical
evidence sentences are rendered once per card; later references point to
them. Frozen Ranking and Career Direction are separate and never reconciled;
tracks keep their configured tier and order — no track hierarchy.

UNKNOWN is never a negative: every UNKNOWN keeps one category — Job-side,
Profile-declaration, Settled, System-level — and a required technique with
no profile evidence is its own category, NO EVIDENCE IN PROFILE, worded
byte-for-byte as the frozen text.
"""

import json
import re
from collections import Counter

from pydantic import BaseModel, Field, model_validator

from src.models.scored_job import ScoredJob
from src.models.strategy import ApplicationStrategy, RunStrategy
from src.models.transition import DIRECTIONS, TARGET_TIERS, EvidenceRef
from src.scoring.technical_alignment import gap_text
from src.transition.gap_ledger import gap_market

CARD_VERSION = "decision_card_v1"

# --- UNKNOWN categories -------------------------------------------------------------------

JOB_SIDE = "Job-side"
PROFILE_DECLARATION = "Profile-declaration"
SETTLED = "Settled"
SYSTEM_LEVEL = "System-level"
NO_EVIDENCE = "NO EVIDENCE IN PROFILE"

FLOOR_REASON_CATEGORY = {
    "salary_undisclosed": JOB_SIDE, "range_straddles_floor": JOB_SIDE,
    "currency_mismatch": JOB_SIDE, "period_not_monthly": JOB_SIDE,
    "no_floor_for_country": PROFILE_DECLARATION, "not_configured": PROFILE_DECLARATION,
}
TECHNICAL_UNKNOWN_CATEGORY = {"unevidenced_required": NO_EVIDENCE, "vacuous": SETTLED}
DOMAIN_UNKNOWN_CATEGORY = {"no_qualifying_overlap": SETTLED}
WORK_UNKNOWN_CATEGORY = {"no_task_statements": JOB_SIDE, "no_labelled_work": JOB_SIDE}
TRAJECTORY_UNKNOWN_CATEGORY = {"work_profile_unknown": JOB_SIDE, "not_configured": PROFILE_DECLARATION}
GAP_SETTLED_CATEGORY = {"vacuous_requirements": SETTLED}

# --- main-card sections (A1) --------------------------------------------------------------

HEADER = "Header"
RANKING = "Frozen Ranking"
STRATEGY = "Application Strategy"
CAREER = "Career Direction"
WORK = "Work"
EVIDENCE = "Key Evidence"
CONSTRAINTS = "Constraints & Readiness"
GAPS = "Evidence Gaps"
VERIFY = "What to Verify"
MAIN_SECTIONS = (HEADER, RANKING, STRATEGY, CAREER, WORK, EVIDENCE, CONSTRAINTS, GAPS, VERIFY)

# --- drill-down sections: the full V1 audit view, then the detail blocks ------------------

A_HEADER = "Audit › Header"
A_WORK = "Audit › Work"
A_CAREER = "Audit › Career Direction"
A_EVIDENCE = "Audit › Evidence"
A_GAPS = "Audit › Evidence Gaps"
A_CONSTRAINTS = "Audit › Constraints"
A_READINESS = "Audit › Readiness"
A_RANKING = "Audit › Frozen Ranking"
A_STAMP = "Audit › Stamp"
DRILL_COMPENSATION = "Compensation — FX-approximate, run-relative"
DRILL_ANNOTATIONS = "Frozen annotations"
DRILL_COMPONENTS = "Component scores"
DRILL_WORK = "Work detail"
DRILL_FLAGS = "Flags (full list)"
DRILL_DATA = "Data quality"
DRILL_STRATEGY = "Application Strategy record"

RUN_CONTEXT = "Run context"
RUN_SUMMARY = "Run summary"

ACTION_LABEL = "Existing frozen action — tier rule"
ONSITE_DERIVATION = "Onsite — parser default when a location is given and no remote/hybrid term appears"

# --- display dictionaries (A5): readable names; machine codes stay in the drill-down ------

WORK_LABEL_DISPLAY = {
    "APPLIED_MODELLING": "Builds or trains models",
    "EXPERIMENTATION_DECISION": "Designs / analyses experiments or causal studies",
    "OPTIMISATION": "Optimisation / operations research",
    "ANALYTICS_INSIGHT": "Analysis and insights",
    "REPORTING": "Dashboards / BI-MIS reporting",
    "ML_ENGINEERING": "Deploys models to production",
    "ML_PLATFORM": "ML platform / MLOps",
    "RESEARCH": "Research",
    "AI_APPLICATION_DEV": "Builds LLM / GenAI / agent applications",
    "PROJECT_DELIVERY": "Project delivery management",
}
TRACK_DISPLAY = {
    "RISK_DS": "Risk DS",
    "FRAUD_FINCRIME_DS": "Fraud / Financial Crime DS",
    "PRODUCT_DS": "Product DS",
    "APPLIED_DS": "Applied DS",
    "DECISION_SCIENTIST": "Decision Science",
    "CREDIT_RISK_DS": "Credit Risk DS",
    "EXPERIMENTATION_CAUSAL": "Experimentation / Causal",
    "PRICING_FORECASTING": "Pricing / Forecasting",
    "APPLIED_SCIENTIST": "Applied Scientist",
    "ML_SCIENTIST": "ML Scientist",
    "ML_ENGINEER": "ML Engineer",
    "MLOPS_PLATFORM": "MLOps / Platform",
    "ANALYTICS_HEAVY": "Analytics-heavy DS",
    "MIS_REPORTING": "MIS Reporting",
    "DATA_ANALYST": "Data Analyst",
    "BI_ANALYST": "BI Analyst",
}
TIER_DISPLAY = {"primary": "Primary", "secondary": "Secondary", "adjacent_ok": "Adjacent",
                "non_target": "Non-target", "UNTARGETED": "Untargeted"}
# Shown only when no primary track matched (A4); each follows the frozen
# direction precedence primary > secondary > adjacent > non-target.
DIRECTION_DISPLAY = {
    "TOWARD_SECONDARY": "toward a secondary track (no primary track matched)",
    "ADJACENT": "adjacent (no primary or secondary track matched)",
    "NON_TARGET": "non-target (no primary, secondary or adjacent track matched)",
    "UNTARGETED": "untargeted (no matched track is in your configured tiers)",
    "OFF_VOCABULARY": "outside your track vocabulary — work evidenced, no track matched",
}
TRAJECTORY_UNKNOWN_DISPLAY = {
    "work_profile_unknown": "not established from the posting (the work profile is unknown)",
    "not_configured": "not established — target tracks not declared in your profile",
}
WORK_UNKNOWN_DISPLAY = {
    "no_task_statements": "no task statements found",
    "no_labelled_work": "task statements found, none matched the work vocabulary",
}
ASSESSED_DISPLAY = {"technical_alignment": "technical", "domain_alignment": "domain"}
IMPORTANCE_DISPLAY = {"preferred": "preferred", "unknown": "unknown importance"}
SETTLED_SUFFIX = " — settled (the posting was read in full)"
GUARD_DISPLAY = "not mapped: context present, but no substantive DS work evidence"   # frozen report wording
MIXED_DISPLAY = "Both a target track and a non-target track match this work"         # frozen flag reason

# --- flag routing (A3): the main-card section whose line states the flag's fact, or None
# for drill-down only (I-4). COMP_UNDETERMINED depends on its floor reason.
FLAG_ROUTES = {
    "RECRUITER_POSTED": HEADER,
    "SENIORITY_REACH": CONSTRAINTS,
    "TRAJECTORY_NON_TARGET": CAREER,
    "TRAJECTORY_OFF_VOCABULARY": CAREER,
    "TRAJECTORY_UNTARGETED": CAREER,
    "TRAJECTORY_UNKNOWN": CAREER,
    "TRAJECTORY_MIXED": CAREER,
    "COMP_BELOW_FLOOR": CONSTRAINTS,
    "COMP_UNDETERMINED": CONSTRAINTS,
    "WORK_AUTH_NOT_ELIGIBLE": CONSTRAINTS,
    "WORK_AUTH_UNDECLARED": None,
    "EVIDENCE_GAPS": GAPS,
}

VERIFY_JOB_SIDE_FLOOR_REASONS = ("salary_undisclosed", "range_straddles_floor", "currency_mismatch", "period_not_monthly")


class CardLine(BaseModel):
    section: str
    text: str
    source_path: str                     # primary provenance path (or "[*]." over the run for the run views)
    source_paths: list[str] = Field(default_factory=list)   # every path a multi-fact line draws on (A11)
    field: str | None = None             # upstream evidence field, where it exists
    heading: str | None = None
    rule_id: str | None = None
    category: str | None = None          # UNKNOWN category, when the line states an UNKNOWN
    evidence_id: str | None = None       # E1, E2 ... for evidence sentences
    flag: str | None = None
    verify: str | None = None            # V1 ... V6

    @model_validator(mode="after")
    def _primary_path_listed(self):
        if not self.source_paths:
            self.source_paths = [self.source_path]
        elif self.source_paths[0] != self.source_path:
            raise ValueError("source_paths must start with source_path")
        return self


class DecisionCard(BaseModel):
    version: str = CARD_VERSION
    job_id: str
    main: list[CardLine] = Field(default_factory=list)
    drill: list[CardLine] = Field(default_factory=list)


class RunSummary(BaseModel):
    version: str = CARD_VERSION
    as_of_date: str | None
    lines: list[CardLine] = Field(default_factory=list)


# --- helpers ------------------------------------------------------------------------------

def _num(value) -> str:
    """Upstream number as written: integral floats without a trailing .0."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def resolve_path(obj, path: str):
    """Resolve 'a.b[2].c' against a ScoredJob (attributes, dict keys, list indices)."""
    for part in path.split("."):
        name, *indices = re.split(r"\[", part)
        if name:
            obj = obj[name] if isinstance(obj, dict) else getattr(obj, name)
        for index in indices:
            obj = obj[int(index.rstrip("]"))]
    return obj


def flag_route(job: ScoredJob, flag: str) -> str | None:
    if flag not in FLAG_ROUTES:
        raise ValueError(f"flag {flag} has no Decision Card route — the frozen contract does not cover it")
    if flag == "COMP_UNDETERMINED":
        reason = job.transition_lens.constraint_checks.compensation_floor.reason
        if FLOOR_REASON_CATEGORY.get(reason) == PROFILE_DECLARATION:
            return None
    return FLAG_ROUTES[flag]


def _category(mapping: dict, reason: str, what: str) -> str:
    if reason not in mapping:
        raise ValueError(f"{what}: reason '{reason}' has no UNKNOWN category in the frozen contract")
    return mapping[reason]


def _display(mapping: dict, code: str) -> str:
    return mapping.get(code, code)


def _auth_display(auth) -> str:
    """Work-authorization declaration wording from the frozen check: its
    source is "not_declared" when the profile has no key for the country,
    otherwise the profile path of the declared value (PASS | UNKNOWN |
    NOT_ELIGIBLE)."""
    return "not declared in your profile" if auth.source == "not_declared" else f"declared {auth.status} in your profile"


def _flag_index(job: ScoredJob, names) -> tuple[int, str] | None:
    return next(((k, f.flag) for k, f in enumerate(job.transition_lens.strategy_flags) if f.flag in names), None)


def _seniority_flag(job: ScoredJob):
    return next(((i, f) for i, f in enumerate(job.transition_lens.strategy_flags) if f.flag == "SENIORITY_REACH"), None)


class _Builder:
    def __init__(self, job: ScoredJob):
        self.job = job
        self.card = DecisionCard(job_id=job.job_id)
        self.evidence_ids: dict[tuple, str] = {}

    def add(self, section: str, text: str, paths, drill: bool = False, **kw):
        paths = [paths] if isinstance(paths, str) else list(paths)
        line = CardLine(section=section, text=text, source_path=paths[0], source_paths=paths, **kw)
        (self.card.drill if drill else self.card.main).append(line)

    def evidence(self, section: str, ref: EvidenceRef, path: str, prefix: str = "", suffix: str = "",
                 drill: bool = False, extra_paths=(), show_rule: bool = True, **kw):
        """An evidence sentence is rendered once per card; later references point to it."""
        key = (ref.field, ref.heading, ref.text)
        where = ref.field + (f" / {ref.heading}" if ref.heading else "")
        rule = f"; rule_id: {ref.rule_id}" if show_rule else ""
        if key in self.evidence_ids:
            eid = self.evidence_ids[key]
            text = f"{prefix}[{eid}] (same evidence as above{rule}){suffix}"
        else:
            eid = self.evidence_ids[key] = f"E{len(self.evidence_ids) + 1}"
            text = f'{prefix}[{eid}] "{ref.text}" ({where}{rule}){suffix}'
        self.add(section, text, [path, *extra_paths], drill=drill, field=ref.field, heading=ref.heading or None,
                 rule_id=ref.rule_id, evidence_id=eid, **kw)


class _Audit:
    """Routes the V1 field-level builders into the drill-down."""

    def __init__(self, b: _Builder):
        self.job = b.job
        self._b = b

    def add(self, section, text, paths, **kw):
        self._b.add(section, text, paths, drill=True, **kw)

    def evidence(self, section, ref, path, prefix="", **kw):
        self._b.evidence(section, ref, path, prefix=prefix, drill=True, **kw)


# --- main card ----------------------------------------------------------------------------

def _m_header(b: _Builder):
    j = b.job
    b.add(HEADER, j.job_title, "job_title")
    b.add(HEADER, j.company, "company")
    unknown = [not j.location_normalized, j.employment_type == "unknown", j.role_level == "Mid/Unspecified",
               j.job_age_days is None]
    parts = [
        j.country,
        j.location_normalized or "location not stated in the posting",
        "employment type not stated in the posting" if unknown[1] else j.employment_type,
        "level not stated in title" if unknown[2] else j.role_level,
        "posting age not stated" if unknown[3] else f"posted {j.job_age_days} days before the run",
    ]
    b.add(HEADER, " · ".join(parts), ["country", "location_normalized", "employment_type", "role_level", "job_age_days"],
          category=JOB_SIDE if any(unknown) else None)
    b.add(HEADER, f"Frozen classifier: {j.role_type} ({j.role_type_confidence:.2f}) · {j.canonical_url}",
          ["role_type", "role_type_confidence", "canonical_url"])
    found = _flag_index(j, ("RECRUITER_POSTED",))
    if found:
        k, _ = found
        b.add(HEADER, f"Posted by a recruiter: {j.transition_lens.strategy_flags[k].reason}",
              f"transition_lens.strategy_flags[{k}]", flag="RECRUITER_POSTED")


def _m_ranking(b: _Builder):
    j = b.job
    s = j.decision_stability
    b.add(RANKING, f"{ACTION_LABEL}: {j.recommended_action} · Tier {j.tier} · overall {_num(j.overall_score)} · "
                   f"{s.stability_label} ({s.tier_floor}..{s.tier_ceiling})",
          ["recommended_action", "tier", "overall_score", "decision_stability"])
    if s.open_unknowns:
        b.add(RANKING, "Could change with: " + ", ".join(f"{u.field} ({u.direction})" for u in s.open_unknowns),
              [f"decision_stability.open_unknowns[{i}]" for i in range(len(s.open_unknowns))])


def _tier_groups(tracks) -> list[tuple[str, list[int]]]:
    tiers = list(TARGET_TIERS) + [t for t in dict.fromkeys(m.target_tier for m in tracks) if t not in TARGET_TIERS]
    return [(tier, idx) for tier in tiers if (idx := [i for i, m in enumerate(tracks) if m.target_tier == tier])]


def _m_career(b: _Builder):
    j = b.job
    tr = j.transition_lens.trajectory
    for tier, idx in _tier_groups(tr.tracks):
        b.add(CAREER, f"{_display(TIER_DISPLAY, tier)} → " + " · ".join(_display(TRACK_DISPLAY, tr.tracks[i].track_id) for i in idx),
              [f"transition_lens.trajectory.tracks[{i}]" for i in idx])
    if not any(m.target_tier == "primary" for m in tr.tracks):
        found = _flag_index(j, ("TRAJECTORY_NON_TARGET", "TRAJECTORY_OFF_VOCABULARY", "TRAJECTORY_UNTARGETED", "TRAJECTORY_UNKNOWN"))
        flag = found[1] if found else None
        if tr.unknown_reason:
            category = _category(TRAJECTORY_UNKNOWN_CATEGORY, tr.unknown_reason, "trajectory")
            b.add(CAREER, f"Direction: {_display(TRAJECTORY_UNKNOWN_DISPLAY, tr.unknown_reason)}",
                  ["transition_lens.trajectory.direction", "transition_lens.trajectory.unknown_reason"],
                  category=category, flag=flag)
        else:
            b.add(CAREER, f"Direction: {_display(DIRECTION_DISPLAY, tr.direction)}", "transition_lens.trajectory.direction",
                  flag=flag)
    for i, g in enumerate(tr.guard_failures):
        b.add(CAREER, f"{_display(TRACK_DISPLAY, g.track_id)} {GUARD_DISPLAY}",
              f"transition_lens.trajectory.guard_failures[{i}].track_id")
    if tr.mixed:
        found = _flag_index(j, ("TRAJECTORY_MIXED",))
        b.add(CAREER, MIXED_DISPLAY, "transition_lens.trajectory.mixed", flag=found[1] if found else None)


def _m_work(b: _Builder):
    wp = b.job.transition_lens.work_profile
    if wp.unknown_reason:
        category = _category(WORK_UNKNOWN_CATEGORY, wp.unknown_reason, "work_profile")
        b.add(WORK, f"Work not established from the posting — {_display(WORK_UNKNOWN_DISPLAY, wp.unknown_reason)}",
              "transition_lens.work_profile.unknown_reason", category=category)
    if wp.labels:
        b.add(WORK, " · ".join(f"{_display(WORK_LABEL_DISPLAY, l.label)} ×{len(l.evidence)}" for l in wp.labels),
              [f"transition_lens.work_profile.labels[{i}]" for i in range(len(wp.labels))])


def _m_evidence(b: _Builder):
    j = b.job
    tracks = j.transition_lens.trajectory.tracks
    groups: dict[tuple, list[int]] = {}
    for _, idx in _tier_groups(tracks):
        for i in idx:
            ref = tracks[i].evidence[0]
            groups.setdefault((ref.field, ref.heading, ref.text), []).append(i)
    for idx in groups.values():
        names = " · ".join(_display(TRACK_DISPLAY, tracks[i].track_id) for i in idx)
        paths = [f"transition_lens.trajectory.tracks[{i}].evidence[0]" for i in idx]
        b.evidence(EVIDENCE, tracks[idx[0]].evidence[0], paths[0], suffix=f" — for {names}",
                   extra_paths=paths[1:], show_rule=False)

    t = j.technical_alignment
    if t.known:
        b.add(EVIDENCE, t.reasons[0], ["technical_alignment.reasons[0]", "technical_alignment.band"])
    else:
        category = _category(TECHNICAL_UNKNOWN_CATEGORY, t.unknown_reason, "technical_alignment")
        b.add(EVIDENCE, t.reasons[0] + (SETTLED_SUFFIX if category == SETTLED else ""),
              ["technical_alignment.reasons[0]", "technical_alignment.unknown_reason"], category=category)

    d = j.domain_alignment
    if d.known:
        b.add(EVIDENCE, d.reasons[0], ["domain_alignment.reasons[0]", "domain_alignment.band"])
    else:
        category = _category(DOMAIN_UNKNOWN_CATEGORY, d.unknown_reason, "domain_alignment")
        b.add(EVIDENCE, d.reasons[0] + (SETTLED_SUFFIX if category == SETTLED else ""),
              ["domain_alignment.reasons[0]", "domain_alignment.unknown_reason"], category=category)

    p = j.personal_transition_fit
    if p.score is None:
        b.add(EVIDENCE, "Personal transition fit: not assessed", "personal_transition_fit.score")
    else:
        b.add(EVIDENCE, f"Personal transition fit: {_num(p.score)} ({' + '.join(_display(ASSESSED_DISPLAY, a) for a in p.assessed)})",
              "personal_transition_fit")


def _floor_display(floor, country: str) -> str:
    rule = floor.rule
    amount = f"{rule.currency} {_num(rule.monthly_amount)}/month" if rule else ""
    displays = {
        "min_meets_floor": f"posted minimum meets your {amount} floor",
        "max_below_floor": f"posted maximum is below your {amount} floor",
        "range_straddles_floor": f"posted range straddles your {amount} floor",
        "salary_undisclosed": "salary not disclosed",
        "currency_mismatch": f"posted currency differs from your {amount} floor",
        "period_not_monthly": "posted period is not monthly",
        "no_floor_for_country": f"no floor configured for {country}",
        "not_configured": "no compensation floor configured",
    }
    if floor.reason not in displays:
        raise ValueError(f"compensation_floor: reason '{floor.reason}' has no display wording in the contract")
    return displays[floor.reason]


def _m_constraints(b: _Builder):
    j = b.job
    checks = j.transition_lens.constraint_checks
    floor = checks.compensation_floor
    floor_path = "transition_lens.constraint_checks.compensation_floor"

    # A10: one salary line — posted salary in its own currency / period, plus the floor check.
    if j.salary_unknown:
        salary, paths, category = "Salary: not disclosed in the posting", ["salary_unknown"], JOB_SIDE
    else:
        low = "not stated" if j.salary_min is None else _num(j.salary_min)
        high = "not stated" if j.salary_max is None else _num(j.salary_max)
        salary = (f"Salary: {j.salary_currency or 'currency not stated'} {low}–{high} / "
                  f"{j.salary_period or 'period not stated'}")
        paths = ["salary_min", "salary_max", "salary_currency", "salary_period"]
        category = JOB_SIDE if None in (j.salary_min, j.salary_max, j.salary_currency, j.salary_period) else None
    if floor.status == "UNKNOWN" and category is None:
        category = _category(FLOOR_REASON_CATEGORY, floor.reason, "compensation_floor")
    found = _flag_index(j, ("COMP_BELOW_FLOOR", "COMP_UNDETERMINED"))
    flag = found[1] if found and flag_route(j, found[1]) else None
    b.add(CONSTRAINTS, f"{salary} · Floor: {floor.status} — {_floor_display(floor, j.country)}",
          [*paths, floor_path] + ([f"{floor_path}.rule"] if floor.rule else []), category=category, flag=flag)

    # Job-specific constraint lines only (A2): declared authorization status,
    # a posting that states something about work authorization, an arrangement
    # other than the parser default.
    auth = checks.work_authorization
    auth_path = "transition_lens.constraint_checks.work_authorization"
    if auth.status != "UNKNOWN":
        found = _flag_index(j, ("WORK_AUTH_NOT_ELIGIBLE",))
        b.add(CONSTRAINTS, f"Work authorization ({auth.country}): {_auth_display(auth)} ({auth.source})",
              auth_path, flag=found[1] if found else None)
    if auth.job_side_status != "UNKNOWN" or auth.job_side_evidence:
        b.add(CONSTRAINTS, f"Posting work-authorization statement: {auth.job_side_status} ({auth.job_side_restriction_type})",
              [f"{auth_path}.job_side_status", f"{auth_path}.job_side_restriction_type"],
              category=JOB_SIDE if auth.job_side_status == "UNKNOWN" else None)
        for i, ref in enumerate(auth.job_side_evidence):
            b.evidence(CONSTRAINTS, ref, f"{auth_path}.job_side_evidence[{i}]", prefix="Posting evidence ")
    if j.remote_type == "unknown":
        b.add(CONSTRAINTS, "Work arrangement: not stated in the posting", "remote_type", category=JOB_SIDE)
    elif j.remote_type != "Onsite":
        b.add(CONSTRAINTS, f"Work arrangement: {j.remote_type}", "remote_type")

    # A9: readiness — the frozen interview-fit reasons verbatim, one seniority line.
    for i, reason in enumerate(j.interview_fit_reasons):
        b.add(CONSTRAINTS, reason, f"interview_fit_reasons[{i}]")
    found = _seniority_flag(j)
    if found:
        k, flag = found
        parts, paths = [], []
        for m, ref in enumerate(flag.evidence):
            paths.append(f"transition_lens.strategy_flags[{k}].evidence[{m}]")
            if ref.field == "role_level":
                parts.append(f"role level {ref.text}")
            elif ref.field == "job_title":
                parts.append(f'title "{ref.text}"')
            elif ref.field == "years_experience_required":
                parts.append(f"{ref.text} years required ({ref.rule_id})")
            else:
                parts.append(f"{ref.field} {ref.text} ({ref.rule_id})")
        b.add(CONSTRAINTS, "Seniority reach: " + "; ".join(parts), paths or f"transition_lens.strategy_flags[{k}]",
              flag="SENIORITY_REACH")


def _m_gaps(b: _Builder, run_has_classified_gaps: bool):
    j = b.job
    ledger = j.transition_lens.gap_ledger
    path = "transition_lens.gap_ledger"
    if ledger.settled_reason:
        category = _category(GAP_SETTLED_CATEGORY, ledger.settled_reason, "gap_ledger")
        b.add(GAPS, "No required techniques stated in the posting" + SETTLED_SUFFIX, f"{path}.settled_reason", category=category)
    gaps = ledger.required_unevidenced
    if gaps:
        found = _flag_index(j, ("EVIDENCE_GAPS",))
        b.add(GAPS, gap_text([g.technique for g in gaps]), [f"{path}.required_unevidenced", "technical_alignment.gaps"],
              category=NO_EVIDENCE, flag=found[1] if found else None)
        classified = [i for i, g in enumerate(gaps) if g.candidate_classification != "UNCLASSIFIED"]
        if classified:
            b.add(GAPS, "Declared classifications: " + "; ".join(
                f"{gaps[i].technique} — {gaps[i].candidate_classification}" for i in classified),
                [f"{path}.required_unevidenced[{i}]" for i in classified])
        unclassified = [i for i, g in enumerate(gaps) if g.candidate_classification == "UNCLASSIFIED"]
        if unclassified and run_has_classified_gaps:
            b.add(GAPS, "Not classified: " + ", ".join(gaps[i].technique for i in unclassified) + " — not declared in your profile",
                  [f"{path}.required_unevidenced[{i}]" for i in unclassified], category=PROFILE_DECLARATION)
    if ledger.nice_to_have:
        b.add(GAPS, "Nice to have: " + " · ".join(f"{n.technique} ({_display(IMPORTANCE_DISPLAY, n.importance)})"
                                                  for n in ledger.nice_to_have),
              [f"{path}.nice_to_have[{i}]" for i in range(len(ledger.nice_to_have))])


def _m_verify(b: _Builder):
    j = b.job
    lens = j.transition_lens
    floor = lens.constraint_checks.compensation_floor
    if floor.reason in VERIFY_JOB_SIDE_FLOOR_REASONS:
        b.add(VERIFY, f"V1 — Job-side compensation → {CONSTRAINTS}",
              "transition_lens.constraint_checks.compensation_floor.reason", verify="V1")
    if lens.work_profile.unknown_reason:
        b.add(VERIFY, f"V2 — Work not established → {CAREER}", "transition_lens.work_profile.unknown_reason", verify="V2")
    if j.work_authorization_evidence:
        b.add(VERIFY, f"V3 — Posting restriction → {CONSTRAINTS}", "work_authorization_evidence", verify="V3")
    gaps = lens.gap_ledger.required_unevidenced
    if gaps:
        b.add(VERIFY, f"V4 — {len(gaps)} required technique{'s' if len(gaps) != 1 else ''} with no profile evidence → {GAPS}",
              "transition_lens.gap_ledger.required_unevidenced", verify="V4")
    found = _seniority_flag(j)
    if found:
        b.add(VERIFY, f"V5 — Seniority reach → {CONSTRAINTS}", f"transition_lens.strategy_flags[{found[0]}]", verify="V5")
    if j.years_experience_required is None:
        b.add(VERIFY, f"V6 — Experience not stated → {CONSTRAINTS}", "years_experience_required", verify="V6")


# --- drill-down: the full V1 field-level audit view ---------------------------------------

def _a_header(b):
    j = b.job
    b.add(A_HEADER, f"job_title: {j.job_title}", "job_title")
    b.add(A_HEADER, f"company: {j.company}", "company")
    b.add(A_HEADER, f"canonical_url: {j.canonical_url}", "canonical_url")
    b.add(A_HEADER, f"country: {j.country}", "country")
    if j.job_age_days is None:
        b.add(A_HEADER, "job_age_days: not stated — Job-side UNKNOWN", "job_age_days", category=JOB_SIDE)
    else:
        b.add(A_HEADER, f"job_age_days: {j.job_age_days}", "job_age_days")
    if j.employment_type == "unknown":
        b.add(A_HEADER, "employment_type: unknown — Job-side UNKNOWN", "employment_type", category=JOB_SIDE)
    else:
        b.add(A_HEADER, f"employment_type: {j.employment_type}", "employment_type")
    if j.role_level == "Mid/Unspecified":
        b.add(A_HEADER, "role_level: Mid/Unspecified — Job-side UNKNOWN", "role_level", category=JOB_SIDE)
    else:
        b.add(A_HEADER, f"role_level: {j.role_level}", "role_level")
    b.add(A_HEADER, f"role_type: {j.role_type}", "role_type")
    b.add(A_HEADER, f"role_type_confidence: {_num(j.role_type_confidence)}", "role_type_confidence")


def _a_work(b):
    wp = b.job.transition_lens.work_profile
    if wp.unknown_reason:
        category = _category(WORK_UNKNOWN_CATEGORY, wp.unknown_reason, "work_profile")
        b.add(A_WORK, f"work_profile: UNKNOWN ({wp.unknown_reason}) — {category} UNKNOWN",
              "transition_lens.work_profile.unknown_reason", category=category)
    for i, label in enumerate(wp.labels):
        b.add(A_WORK, f"{label.label} ×{len(label.evidence)}", f"transition_lens.work_profile.labels[{i}]")


def _a_career(b):
    tr = b.job.transition_lens.trajectory
    for _, idx in _tier_groups(tr.tracks):
        for i in idx:
            match = tr.tracks[i]
            path = f"transition_lens.trajectory.tracks[{i}]"
            b.add(A_CAREER, f"{match.target_tier}: {match.track_id}", path)
            b.evidence(A_CAREER, match.evidence[0], f"{path}.evidence[0]", prefix=f"{match.track_id} evidence ")
    if tr.unknown_reason:
        category = _category(TRAJECTORY_UNKNOWN_CATEGORY, tr.unknown_reason, "trajectory")
        b.add(A_CAREER, f"direction: {tr.direction} ({tr.unknown_reason}) — {category} UNKNOWN",
              "transition_lens.trajectory.direction", category=category)
    else:
        b.add(A_CAREER, f"direction: {tr.direction}", "transition_lens.trajectory.direction")
    b.add(A_CAREER, f"mixed: {str(tr.mixed).lower()}", "transition_lens.trajectory.mixed")
    for i, g in enumerate(tr.guard_failures):
        b.add(A_CAREER, f"guard_failures: {g.track_id}", f"transition_lens.trajectory.guard_failures[{i}].track_id")


def _a_evidence(b):
    j = b.job
    t = j.technical_alignment
    if t.known:
        b.add(A_EVIDENCE, f"technical_alignment: {t.band}", "technical_alignment.band")
    else:
        category = _category(TECHNICAL_UNKNOWN_CATEGORY, t.unknown_reason, "technical_alignment")
        b.add(A_EVIDENCE, f"technical_alignment: UNKNOWN ({t.unknown_reason}) — {category}",
              "technical_alignment.unknown_reason", category=category)
    if t.reasons:
        b.add(A_EVIDENCE, t.reasons[0], "technical_alignment.reasons[0]")
    for i, p in enumerate(t.provenance):
        where = " / ".join(x for x in (p.get("source_field"), p.get("section_heading")) if x)
        b.add(A_EVIDENCE, f'{p["technique"]} — your level: {p["candidate_level"]} ({where}): "{p.get("context", "")}"',
              f"technical_alignment.provenance[{i}]", field=p.get("source_field"), heading=p.get("section_heading") or None)
    d = j.domain_alignment
    if d.known:
        b.add(A_EVIDENCE, f"domain_alignment: {d.band}", "domain_alignment.band")
    else:
        category = _category(DOMAIN_UNKNOWN_CATEGORY, d.unknown_reason, "domain_alignment")
        b.add(A_EVIDENCE, f"domain_alignment: UNKNOWN ({d.unknown_reason}) — {category} UNKNOWN",
              "domain_alignment.unknown_reason", category=category)
    if d.reasons:
        b.add(A_EVIDENCE, d.reasons[0], "domain_alignment.reasons[0]")
    p = j.personal_transition_fit
    if p.score is None:
        b.add(A_EVIDENCE, "personal_transition_fit: not assessed", "personal_transition_fit.score")
    else:
        b.add(A_EVIDENCE, f"personal_transition_fit: {_num(p.score)} (assessed: {', '.join(p.assessed)})",
              "personal_transition_fit")


def _a_gaps(b):
    ledger = b.job.transition_lens.gap_ledger
    if ledger.settled_reason:
        category = _category(GAP_SETTLED_CATEGORY, ledger.settled_reason, "gap_ledger")
        b.add(A_GAPS, f"gap_ledger: {ledger.settled_reason} — {category} UNKNOWN",
              "transition_lens.gap_ledger.settled_reason", category=category)
    for i, g in enumerate(ledger.required_unevidenced):
        classification = (f"candidate_classification: {g.candidate_classification} — Profile-declaration UNKNOWN"
                          if g.candidate_classification == "UNCLASSIFIED"
                          else f"candidate_classification: {g.candidate_classification} ({g.classification_source})")
        where = " / ".join(x for x in (g.source_field, g.section_heading) if x)
        b.add(A_GAPS, f'{gap_text([g.technique])} · market_frequency: {g.market_frequency} · {classification} · '
                      f'source: {where}: "{g.context}"',
              f"transition_lens.gap_ledger.required_unevidenced[{i}]", category=NO_EVIDENCE,
              field=g.source_field, heading=g.section_heading or None)
    for i, n in enumerate(ledger.nice_to_have):
        b.add(A_GAPS, f"nice_to_have: {n.technique} (importance: {n.importance})", f"transition_lens.gap_ledger.nice_to_have[{i}]")


def _a_constraints(b):
    j = b.job
    if j.salary_unknown:
        b.add(A_CONSTRAINTS, "salary: not disclosed — Job-side UNKNOWN", "salary_unknown", category=JOB_SIDE)
    else:
        for name in ("salary_min", "salary_max", "salary_currency", "salary_period"):
            value = getattr(j, name)
            if value is None:
                b.add(A_CONSTRAINTS, f"{name}: not stated — Job-side UNKNOWN", name, category=JOB_SIDE)
            else:
                b.add(A_CONSTRAINTS, f"{name}: {_num(value)}", name)
    floor = j.transition_lens.constraint_checks.compensation_floor
    if floor.status == "UNKNOWN":
        category = _category(FLOOR_REASON_CATEGORY, floor.reason, "compensation_floor")
        b.add(A_CONSTRAINTS, f"compensation_floor: UNKNOWN ({floor.reason}) — {category} UNKNOWN",
              "transition_lens.constraint_checks.compensation_floor", category=category)
    else:
        b.add(A_CONSTRAINTS, f"compensation_floor: {floor.status} ({floor.reason})", "transition_lens.constraint_checks.compensation_floor")
    if floor.rule:
        b.add(A_CONSTRAINTS, f"compensation_floor rule: {floor.rule.currency} {_num(floor.rule.monthly_amount)} per month "
                             f"({floor.rule.config_path})", "transition_lens.constraint_checks.compensation_floor.rule")
    auth = j.transition_lens.constraint_checks.work_authorization
    path = "transition_lens.constraint_checks.work_authorization"
    if auth.status == "UNKNOWN":
        b.add(A_CONSTRAINTS, f"work_authorization ({auth.country}): UNKNOWN (source: {auth.source}) — Profile-declaration UNKNOWN",
              path, category=PROFILE_DECLARATION)
    else:
        b.add(A_CONSTRAINTS, f"work_authorization ({auth.country}): {auth.status} (source: {auth.source})", path)
    if auth.job_side_status == "UNKNOWN":
        b.add(A_CONSTRAINTS, f"work_authorization job_side_status: UNKNOWN (job_side_restriction_type: "
                             f"{auth.job_side_restriction_type}) — Job-side UNKNOWN", f"{path}.job_side_status", category=JOB_SIDE)
    else:
        b.add(A_CONSTRAINTS, f"work_authorization job_side_status: {auth.job_side_status} (job_side_restriction_type: "
                             f"{auth.job_side_restriction_type})", f"{path}.job_side_status")
    for i, ref in enumerate(auth.job_side_evidence):
        b.evidence(A_CONSTRAINTS, ref, f"{path}.job_side_evidence[{i}]", prefix="HC-2 evidence ")
    b.add(A_CONSTRAINTS, f"location_normalized: {j.location_normalized}" if j.location_normalized
          else "location_normalized: not stated — Job-side UNKNOWN", "location_normalized",
          category=None if j.location_normalized else JOB_SIDE)
    if j.remote_type == "Onsite":
        b.add(A_CONSTRAINTS, f"remote_type: {ONSITE_DERIVATION}", "remote_type")
    elif j.remote_type == "unknown":
        b.add(A_CONSTRAINTS, "remote_type: unknown — Job-side UNKNOWN", "remote_type", category=JOB_SIDE)
    else:
        b.add(A_CONSTRAINTS, f"remote_type: {j.remote_type}", "remote_type")


def _a_readiness(b):
    j = b.job
    if j.years_experience_required is None:
        b.add(A_READINESS, "years_experience_required: not stated — Job-side UNKNOWN", "years_experience_required", category=JOB_SIDE)
    else:
        b.add(A_READINESS, f"years_experience_required: {_num(j.years_experience_required)}", "years_experience_required")
    if j.education_required is None:
        b.add(A_READINESS, "education_required: not stated — Job-side UNKNOWN", "education_required", category=JOB_SIDE)
    else:
        b.add(A_READINESS, f"education_required: {j.education_required}", "education_required")
    for i, reason in enumerate(j.interview_fit_reasons):
        b.add(A_READINESS, reason, f"interview_fit_reasons[{i}]")
    found = _seniority_flag(j)
    if found:
        k, flag = found
        for m, ref in enumerate(flag.evidence):
            b.evidence(A_READINESS, ref, f"transition_lens.strategy_flags[{k}].evidence[{m}]", prefix="SENIORITY_REACH evidence ")


def _a_ranking(b):
    j = b.job
    s = j.decision_stability
    b.add(A_RANKING, f"tier: {j.tier}", "tier")
    b.add(A_RANKING, f"overall_score: {_num(j.overall_score)}", "overall_score")
    b.add(A_RANKING, f"recommended_action: {j.recommended_action}", "recommended_action")
    b.add(A_RANKING, f"decision_stability: {s.stability_label} ({s.tier_floor}..{s.tier_ceiling})", "decision_stability")
    for i, u in enumerate(s.open_unknowns):
        b.add(A_RANKING, f"open_unknowns: {u.field} ({u.direction}) — {u.reason}", f"decision_stability.open_unknowns[{i}]")


def _a_stamp(b):
    j = b.job
    b.add(A_STAMP, f"as_of_date: {j.as_of_date}", "as_of_date")
    b.add(A_STAMP, f"transition_lens.version: {j.transition_lens.version}", "transition_lens.version")
    b.add(A_STAMP, f"personal_transition_fit.scale: {j.personal_transition_fit.scale}", "personal_transition_fit.scale")


def _drill(b: _Builder):
    j = b.job
    audit = _Audit(b)
    for part in (_a_header, _a_work, _a_career, _a_evidence, _a_gaps, _a_constraints, _a_readiness, _a_ranking, _a_stamp):
        part(audit)

    def add(section, text, path):
        b.add(section, text, path, drill=True)

    add(DRILL_COMPENSATION, "compensation_score: " + ("None" if j.compensation_score is None else _num(j.compensation_score)),
        "compensation_score")
    for i, r in enumerate(j.compensation_reasons):
        add(DRILL_COMPENSATION, r, f"compensation_reasons[{i}]")
    add(DRILL_COMPENSATION, "salary_min_myr: " + ("None" if j.salary_min_myr is None else _num(j.salary_min_myr)), "salary_min_myr")
    add(DRILL_COMPENSATION, "salary_max_myr: " + ("None" if j.salary_max_myr is None else _num(j.salary_max_myr)), "salary_max_myr")
    add(DRILL_COMPENSATION, f"salary_raw: {j.salary_raw}", "salary_raw")

    add(DRILL_ANNOTATIONS, f"opportunity_types: {', '.join(j.opportunity_types) or '(none)'}", "opportunity_types")
    for name in ("why_apply", "why_not", "key_gap"):
        for i, item in enumerate(getattr(j, name)):
            add(DRILL_ANNOTATIONS, f"{name}: {item}", f"{name}[{i}]")

    add(DRILL_COMPONENTS, f"job_quality_score: {_num(j.job_quality_score)}", "job_quality_score")
    for i, r in enumerate(j.job_quality_reasons):
        add(DRILL_COMPONENTS, f"job_quality_reasons: {r}", f"job_quality_reasons[{i}]")
    add(DRILL_COMPONENTS, f"interview_fit_score: {_num(j.interview_fit_score)}", "interview_fit_score")
    add(DRILL_COMPONENTS, f"location_score: {_num(j.location_score)}", "location_score")
    for i, r in enumerate(j.location_reasons):
        add(DRILL_COMPONENTS, f"location_reasons: {r}", f"location_reasons[{i}]")
    add(DRILL_COMPONENTS, f"job_risk_score: {_num(j.job_risk_score)}", "job_risk_score")
    add(DRILL_COMPONENTS, f"job_risk_penalty: {_num(j.job_risk_penalty)}", "job_risk_penalty")
    for i, r in enumerate(j.job_risk_reasons):
        add(DRILL_COMPONENTS, f"job_risk_reasons: {r}", f"job_risk_reasons[{i}]")
    for i, r in enumerate(j.network_reasons):
        add(DRILL_COMPONENTS, f"network_reasons: {r}", f"network_reasons[{i}]")

    for i, label in enumerate(j.transition_lens.work_profile.labels):
        for m, ref in enumerate(label.evidence):
            b.evidence(DRILL_WORK, ref, f"transition_lens.work_profile.labels[{i}].evidence[{m}]",
                       prefix=f"{label.label} evidence ", drill=True)

    for k, f in enumerate(j.transition_lens.strategy_flags):
        b.add(DRILL_FLAGS, f"{f.flag}: {f.reason}", f"transition_lens.strategy_flags[{k}]", drill=True, flag=f.flag)

    add(DRILL_DATA, f"unknown_fields: {', '.join(j.unknown_fields) or '(none)'}", "unknown_fields")
    add(DRILL_DATA, f"data_completeness_pct: {_num(j.data_completeness_pct)}", "data_completeness_pct")


# --- public API ---------------------------------------------------------------------------

def _check_inputs(jobs: list[ScoredJob]) -> None:
    missing = [j.job_id for j in jobs if j.transition_lens is None]
    if missing:
        raise ValueError(f"Decision Card needs ScoredJob after apply_lens; transition_lens is missing on {missing[:3]}")
    for name, values in (("as_of_date", {j.as_of_date for j in jobs}),
                         ("transition_lens.version", {j.transition_lens.version for j in jobs}),
                         ("personal_transition_fit.scale", {j.personal_transition_fit.scale for j in jobs})):
        if len(values) > 1:
            raise ValueError(f"Decision Card renders one run; found {name} values {sorted(map(str, values))}")


def _run_has_classified_gaps(jobs: list[ScoredJob]) -> bool:
    return any(g.candidate_classification != "UNCLASSIFIED"
               for j in jobs for g in j.transition_lens.gap_ledger.required_unevidenced)


# Application Strategy (optional, additive): coordinates of the D1 queue cell,
# the candidate's declared blocks, observable profile actions and actual
# network contacts. Rendered only from a supplied ApplicationStrategy record.
QUEUE_DISPLAY = {"PRIMARY": "Primary", "SECONDARY": "Secondary", "ADJACENT": "Adjacent", "NON_TARGET": "Non-target",
                 "UNTARGETED": "Untargeted", "OFF_VOCABULARY": "Off-vocabulary", "UNKNOWN": "Unknown"}


def _m_strategy(b: _Builder, strategy: ApplicationStrategy):
    j = b.job
    if strategy.job_id != j.job_id:
        raise ValueError(f"strategy {strategy.job_id} does not belong to job {j.job_id}")
    b.add(STRATEGY, f"Queue: {_display(QUEUE_DISPLAY, strategy.career_axis.queue_tier)} × {strategy.frozen_axis.recommended_action}",
          ["transition_lens.trajectory.direction", "recommended_action"])
    checks = j.transition_lens.constraint_checks
    for block in strategy.declared_blocks:
        if block.constraint == "COMP_FLOOR":
            rule = checks.compensation_floor.rule
            b.add(STRATEGY, f"Block: posted maximum is below your configured hard floor ({rule.currency} "
                            f"{_num(rule.monthly_amount)}/month)", "transition_lens.constraint_checks.compensation_floor")
        else:
            b.add(STRATEGY, f"Block: work authorization declared {block.status} for {checks.work_authorization.country}",
                  "transition_lens.constraint_checks.work_authorization")
    stability = j.decision_stability
    for action in strategy.profile_actions:
        n = len(action.technique)
        b.add(STRATEGY, f"Profile action: evidence for the {n} required technique{'s' if n != 1 else ''} in Evidence Gaps "
                        f"would resolve the open unknown technical_alignment (up_only) · stability "
                        f"{stability.stability_label} ({stability.tier_floor}..{stability.tier_ceiling})",
              [action.source_path, "transition_lens.gap_ledger.required_unevidenced"])
    if strategy.network:
        for i, reason in enumerate(j.network_reasons):
            b.add(STRATEGY, f"Network: {reason}", f"network_reasons[{i}]")
    record = strategy.decision_record
    if record is not None and record.current is not None:
        current = record.current
        b.add(STRATEGY, f"Your decision: {current.decision} on {current.decision_date} (declared effort {current.declared_effort}) "
                        f"· outcome {record.outcome}", "job_id")
    # drill-down: the strategy record with its declaration sources
    for block in strategy.declared_blocks:
        b.add(DRILL_STRATEGY, f"declared_block: {block.constraint} {block.status} (sources: {', '.join(block.source)})",
              block.source[-1], drill=True)
    for action in strategy.profile_actions:
        b.add(DRILL_STRATEGY, f"profile_action: {action.kind} — {', '.join(action.technique)}",
              [action.source_path, "transition_lens.gap_ledger.required_unevidenced"], drill=True)
    for i, g in enumerate(strategy.gap_strategy):
        b.add(DRILL_STRATEGY, f"gap_strategy: {g.technique} — candidate_classification {g.candidate_classification}",
              f"transition_lens.gap_ledger.required_unevidenced[{i}]", drill=True)


def build_card(job: ScoredJob, run_has_classified_gaps: bool = False, strategy: ApplicationStrategy | None = None) -> DecisionCard:
    b = _Builder(job)
    _m_header(b)
    _m_ranking(b)
    if strategy is not None:
        _m_strategy(b, strategy)
    _m_career(b)
    _m_work(b)
    _m_evidence(b)
    _m_constraints(b)
    _m_gaps(b, run_has_classified_gaps)
    _m_verify(b)
    # Every main-routed flag is attached exactly once to the line stating its fact.
    for flag in job.transition_lens.strategy_flags:
        attached = sum(l.flag == flag.flag for l in b.card.main)
        expected = 1 if flag_route(job, flag.flag) else 0
        if attached != expected:
            raise ValueError(f"flag {flag.flag} attached {attached} times on the main card (expected {expected})")
    _drill(b)
    return b.card


def build_cards(jobs: list[ScoredJob], strategies: list[ApplicationStrategy] | None = None) -> list[DecisionCard]:
    """One card per job, in frozen ranking order (overall_score, descending).
    With strategies, each card gains its Application Strategy section."""
    _check_inputs(jobs)
    ordered = sorted(jobs, key=lambda j: j.overall_score, reverse=True)
    classified = _run_has_classified_gaps(ordered)
    by_id = {s.job_id: s for s in strategies or []}
    return [build_card(j, classified, by_id.get(j.job_id)) for j in ordered]


def build_summary(jobs: list[ScoredJob]) -> RunSummary:
    """Run context (A2: facts identical across the run or sourced from a
    profile declaration) and run summary (counts of existing values). All
    aggregation is within this run, in first-appearance (frozen ranking)
    order; nothing is ranked or scored."""
    _check_inputs(jobs)
    ordered = sorted(jobs, key=lambda j: j.overall_score, reverse=True)
    summary = RunSummary(as_of_date=ordered[0].as_of_date if ordered else None)
    if not ordered:
        return summary

    def add(section, text, path, **kw):
        summary.lines.append(CardLine(section=section, text=text, source_path=path, **kw))

    # --- run context ---
    first = ordered[0]
    add(RUN_CONTEXT, f"as_of_date: {first.as_of_date}", "[*].as_of_date")
    add(RUN_CONTEXT, f"transition_lens.version: {first.transition_lens.version}", "[*].transition_lens.version")
    add(RUN_CONTEXT, f"personal_transition_fit.scale: {first.personal_transition_fit.scale}", "[*].personal_transition_fit.scale")

    for country in dict.fromkeys(j.country for j in ordered):
        checks = [j.transition_lens.constraint_checks for j in ordered if j.country == country]
        parts, unknown = [], False
        auths = Counter((c.work_authorization.status, c.work_authorization.source) for c in checks)
        for (status, source), n in auths.items():
            unknown |= status == "UNKNOWN"
            auth = next(c.work_authorization for c in checks
                        if (c.work_authorization.status, c.work_authorization.source) == (status, source))
            parts.append(f"Work authorization: {_auth_display(auth)} ({source}) [{n} jobs]")
        floors = Counter()
        for c in checks:
            f = c.compensation_floor
            if f.rule:
                floors[f"{f.rule.currency} {_num(f.rule.monthly_amount)}/month ({f.rule.config_path})"] += 1
            elif FLOOR_REASON_CATEGORY.get(f.reason) == PROFILE_DECLARATION:
                unknown = True
                floors[_floor_display(f, country)] += 1
        parts += [f"Compensation floor: {desc} [{n} jobs]" for desc, n in floors.items()]
        add(RUN_CONTEXT, f"{country} — " + "; ".join(parts), "[*].transition_lens.constraint_checks",
            category=PROFILE_DECLARATION if unknown else None)

    no_restriction = sum(j.transition_lens.constraint_checks.work_authorization.job_side_status == "UNKNOWN"
                         and not j.transition_lens.constraint_checks.work_authorization.job_side_evidence for j in ordered)
    if no_restriction:
        add(RUN_CONTEXT, f"No work-authorization restriction found in the posting [{no_restriction} jobs] "
                         "— a card shows the posting's statement only when one was found",
            "[*].transition_lens.constraint_checks.work_authorization.job_side_status", category=JOB_SIDE)
    onsite = sum(j.remote_type == "Onsite" for j in ordered)
    if onsite:
        add(RUN_CONTEXT, f"Work arrangement: {ONSITE_DERIVATION} [{onsite} jobs] — a card shows the arrangement only "
                         "when it differs", "[*].remote_type")
    mixed = sum(j.transition_lens.trajectory.mixed for j in ordered)
    add(RUN_CONTEXT, f"{MIXED_DISPLAY}: {mixed} jobs (shown on those cards); not on {len(ordered) - mixed} jobs",
        "[*].transition_lens.trajectory.mixed")
    gaps = [g for j in ordered for g in j.transition_lens.gap_ledger.required_unevidenced]
    if gaps and not _run_has_classified_gaps(ordered):
        with_gaps = sum(bool(j.transition_lens.gap_ledger.required_unevidenced) for j in ordered)
        add(RUN_CONTEXT, f"Gap classifications: none declared in your profile [{len(gaps)} required gaps across "
                         f"{with_gaps} jobs]", "[*].transition_lens.gap_ledger.required_unevidenced",
            category=PROFILE_DECLARATION)
    unknown_maturity = [j for j in ordered if not j.company_maturity_known]
    if unknown_maturity:
        reason = unknown_maturity[0].company_maturity_reasons[0] if unknown_maturity[0].company_maturity_reasons else "company maturity unknown"
        add(RUN_CONTEXT, f"Company maturity: {reason} — not available for any job (no data source) [{len(unknown_maturity)} jobs]",
            "[*].company_maturity_known", category=SYSTEM_LEVEL)

    # --- run summary ---
    directions = Counter(j.transition_lens.trajectory.direction for j in ordered)
    for d in [d for d in DIRECTIONS if d in directions] + [d for d in directions if d not in DIRECTIONS]:
        add(RUN_SUMMARY, f"direction {d}: {directions[d]} jobs", "[*].transition_lens.trajectory.direction")
    sets = Counter()
    for j in ordered:
        primary = [m.track_id for m in j.transition_lens.trajectory.tracks if m.target_tier == "primary"]
        if primary:
            sets[f"{primary[0]} only" if len(primary) == 1 else " + ".join(primary)] += 1
    for key, n in sets.items():
        add(RUN_SUMMARY, f"primary tracks {key}: {n} jobs", "[*].transition_lens.trajectory.tracks")
    flags = Counter(f.flag for j in ordered for f in j.transition_lens.strategy_flags)
    for flag, n in flags.items():
        add(RUN_SUMMARY, f"flag {flag}: {n} jobs", "[*].transition_lens.strategy_flags")
    for technique, n in gap_market([j.transition_lens.gap_ledger for j in ordered]):
        add(RUN_SUMMARY, f"gap_market {technique}: {n} jobs", "[*].transition_lens.gap_ledger.required_unevidenced")
    return summary


def _render_run_strategy(run_strategy: RunStrategy, cards: list[DecisionCard]) -> list[str]:
    names = {}
    for card in cards:
        heading = {l.source_path: l.text for l in card.main if l.source_path in ("job_title", "company")}
        names[card.job_id] = f"{heading['company']} · {heading['job_title']}"
    out = ["## Application Strategy", "",
           "Groups jobs by career tier and frozen action, in frozen ranking order within each group. "
           "A display grouping only — it does not rank, score or override anything.", "", "### Queue view", ""]
    for cell in run_strategy.queue:
        out.append(f"- Career: {_display(QUEUE_DISPLAY, cell.queue_tier)} × Frozen: {cell.frozen_action} — {len(cell.job_ids)} job{'s' if len(cell.job_ids) != 1 else ''}")
        out += [f"  - {names.get(job_id, job_id)}" for job_id in cell.job_ids]
    out += ["", "### Profile leverage", "", "How many job records each declaration, open unknown or required "
            "evidence gap touches in this run. Counts only.", ""]
    out += [f"- {e.text}" for e in run_strategy.profile_leverage]
    out += ["", "### Network", ""]
    out += ([f"- {names.get(job_id, job_id)}" for job_id in run_strategy.network_job_ids]
            or ["- No contact-based network signal in this run (no contacts recorded for these employers)"])
    return out + [""]


def render_markdown(cards: list[DecisionCard], summary: RunSummary, run_strategy: RunStrategy | None = None) -> str:
    out = ["# Decision Cards", "",
           f"Scoring run as_of_date: {summary.as_of_date}", "",
           "Presentation of frozen outputs only — nothing here changes a score, tier, ranking or action. "
           "Cards follow the frozen ranking order. Each card's drill-down holds the full audit view.", ""]
    for section in (RUN_CONTEXT, RUN_SUMMARY):
        out += [f"## {section}", ""] + [f"- {l.text}" for l in summary.lines if l.section == section] + [""]
    if run_strategy is not None:
        out += _render_run_strategy(run_strategy, cards)
    for card in cards:
        # job_title and company are rendered once, as the card heading.
        heading = {l.source_path: l.text for l in card.main if l.source_path in ("job_title", "company")}
        out += [f"## {heading['company']} · {heading['job_title']}", ""]
        for section in MAIN_SECTIONS:
            lines = [l for l in card.main if l.section == section and l.source_path not in heading]
            if not lines:
                continue
            if section != HEADER:
                out += [f"**{section}**", ""]
            out += [f"- {l.text}" for l in lines] + [""]
        out += ["<details><summary>Drill-down (full audit view)</summary>", ""]
        for section in dict.fromkeys(l.section for l in card.drill):
            out += [f"**{section}**", ""] + [f"- {l.text}" for l in card.drill if l.section == section] + [""]
        out += ["</details>", ""]
    return "\n".join(out)


def render_json(cards: list[DecisionCard], summary: RunSummary, run_strategy: RunStrategy | None = None,
                strategies: list[ApplicationStrategy] | None = None) -> str:
    payload = {"summary": summary.model_dump(mode="json"), "cards": [c.model_dump(mode="json") for c in cards]}
    if run_strategy is not None:
        payload["run_strategy"] = run_strategy.model_dump(mode="json")
    if strategies is not None:
        payload["strategies"] = [s.model_dump(mode="json") for s in strategies]
    return json.dumps(payload, indent=1, ensure_ascii=False)
