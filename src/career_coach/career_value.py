"""Career value (CV-1..CV-8) and resume track (RT-1..RT-6).

A job maps to every track whose configured condition holds over its own
work evidence; the career-value state then follows the candidate's DECLARED
tier for the matched tracks. Secondary is never promoted (CV-4)."""

import re
from dataclasses import dataclass, field

from src.career_coach.substance import Sentence, SubstanceResult
from src.models.career_coach import CoachConfig, CoachDeclarations, JobProjection, TrackCondition

TIER_ORDER = ("primary", "secondary", "adjacent_ok")          # RT-1
SPECIFICITY_ORDER = ("DOMAIN", "FUNCTION", "GENERAL")         # RT-2


@dataclass
class TrackMatch:
    track: str
    declared_tier: str
    specificity: str | None
    defining: bool
    clauses: list[str] = field(default_factory=list)
    evidence: list[Sentence] = field(default_factory=list)


class _Ctx:
    def __init__(self, job: JobProjection, sub: SubstanceResult):
        self.job, self.sub = job, sub


def _holds(cond: TrackCondition, ctx: _Ctx, clauses: list[str], evidence: list[Sentence]) -> bool:
    """All keys must hold. Clauses and evidence are appended only on success."""
    local_clauses: list[str] = []
    local_evidence: list[Sentence] = []
    sub, job = ctx.sub, ctx.job

    if cond.activity_present is not None:
        present = [a for a in cond.activity_present if sub.count(a)]
        if not present:
            return False
        local_clauses.append(f"{'/'.join(present)} evidenced")
        local_evidence += [h.sentence for a in present for h in sub.hits[a]]
    if cond.activity_absent is not None:
        if any(sub.count(a) for a in cond.activity_absent):
            return False
        local_clauses.append(f"no {'/'.join(cond.activity_absent)}")
    if cond.central_work_in is not None:
        if sub.central_work not in cond.central_work_in:
            return False
        local_clauses.append(f"central work {sub.central_work}")
        local_evidence += [h.sentence for h in sub.hits[sub.central_work]]
    if cond.domain is not None:
        terms = [t for t in cond.domain.terms if sub.domain_placements.get(t, set()) & set(cond.domain.placements)]
        if not terms:
            return False
        placed = [f"{t} ({'/'.join(sorted(sub.domain_placements[t] & set(cond.domain.placements)))})" for t in terms]
        local_clauses.append("domain " + ", ".join(placed))
        local_evidence += [s for t in terms for s in sub.domain_hits.get(t, [])]
    if cond.title_regex is not None:
        if not re.search(cond.title_regex, job.job_title, re.I):
            return False
        local_clauses.append("title matches")
    if cond.work_sentence_regex is not None:
        matched = [s for s in sub.work if re.search(cond.work_sentence_regex, s.text, re.I)]
        if not matched:
            return False
        local_clauses.append("work statement matches")
        local_evidence += matched
    if cond.role_family_in is not None:
        if job.role_family not in cond.role_family_in:
            return False
        local_clauses.append(f"role family {job.role_family}")
    if cond.activity_evidence is not None:
        ae = cond.activity_evidence
        matched = [h.sentence for a in ae.activities for h in sub.hits.get(a, [])
                   if (ae.matching is None or re.search(ae.matching, h.sentence.text, re.I))
                   and (ae.not_matching is None or not re.search(ae.not_matching, h.sentence.text, re.I))]
        if not matched:
            return False
        local_clauses.append(f"{'/'.join(ae.activities)} statement qualifies")
        local_evidence += matched
    if cond.all_of is not None:
        for child in cond.all_of:
            if not _holds(child, ctx, local_clauses, local_evidence):
                return False
    if cond.any_of is not None:
        for child in cond.any_of:
            if _holds(child, ctx, local_clauses, local_evidence):
                break
        else:
            return False

    clauses.extend(local_clauses)
    evidence.extend(local_evidence)
    return True


def match_tracks(job: JobProjection, sub: SubstanceResult, config: CoachConfig,
                 declarations: CoachDeclarations) -> list[TrackMatch]:
    tier_of = {t: tier for tier in ("primary", "secondary", "adjacent_ok", "non_target")
               for t in getattr(declarations.target_tracks, tier)}
    ctx, matches = _Ctx(job, sub), []
    for track, spec in config.tracks.items():                     # config order = RT-4
        clauses: list[str] = []
        evidence: list[Sentence] = []
        if _holds(spec.match, ctx, clauses, evidence):
            unique = sorted({s.index: s for s in evidence}.values(), key=lambda s: s.index)
            matches.append(TrackMatch(track, tier_of[track], spec.specificity, spec.defining, clauses, unique))
    return matches


def order_candidates(matches: list[TrackMatch], config: CoachConfig) -> list[tuple[TrackMatch, str]]:
    """RT-1..RT-4 over targeted tracks. Returns [(match, deciding key vs the next)]."""
    position = {t: i for i, t in enumerate(config.tracks)}
    candidates = [m for m in matches if m.declared_tier in TIER_ORDER]

    def key(m: TrackMatch):
        return (TIER_ORDER.index(m.declared_tier), SPECIFICITY_ORDER.index(m.specificity),
                -len(m.evidence), position[m.track])

    ordered = sorted(candidates, key=key)
    result = []
    for i, m in enumerate(ordered):
        if i + 1 < len(ordered):
            a, b = key(m), key(ordered[i + 1])
            decided = next(f"RT-{n + 1}" for n in range(4) if a[n] != b[n])
        else:
            decided = "RT-1"
        result.append((m, decided))
    return result


def career_value(sub: SubstanceResult, matches: list[TrackMatch], config: CoachConfig
                 ) -> tuple[str, str, TrackMatch | None]:
    """(state, deciding rule, deciding track)."""
    if sub.status == "UNKNOWN":
        return "UNKNOWN", "CV-1", None
    defining = [m for m in matches if m.defining and m.declared_tier == "non_target"]
    if defining:
        return "NON_TARGET", "CV-2", defining[0]
    ordered = [m for m, _ in order_candidates(matches, config)]
    for tier, state, rule in (("primary", "PRIMARY", "CV-3"), ("secondary", "SECONDARY", "CV-4"),
                              ("adjacent_ok", "ADJACENT", "CV-5")):
        in_tier = [m for m in ordered if m.declared_tier == tier]
        if in_tier:
            return state, rule, in_tier[0]                        # CV-8
    non_target = [m for m in matches if m.declared_tier == "non_target"]
    if non_target:
        return "NON_TARGET", "CV-6", non_target[0]
    return "OFF_VOCABULARY", "CV-7", None
