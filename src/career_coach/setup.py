"""Career Coach setup validation (Gate 1): parses config/career_coach.yaml
and the profile's `career_coach:` declarations into their frozen models and
checks everything that must hold across the two before any rule runs.

No decision logic lives here. Nothing is inferred: a missing declaration is
an error, not a default.
"""

import hashlib
import json
import re

from src.models.career_coach import (
    DECLARABLE_LEVELS,
    TRACK_TIERS,
    CoachConfig,
    CoachDeclarations,
    TrackCondition,
)


def parse_config(raw: dict) -> CoachConfig:
    return CoachConfig.model_validate(raw)


def parse_declarations(profile: dict) -> CoachDeclarations:
    if "career_coach" not in profile:
        raise ValueError("profile has no `career_coach:` section (M2)")
    return CoachDeclarations.model_validate(profile["career_coach"])


def content_hash(obj) -> str:
    """Stable hash of a loaded YAML/JSON object (run.input_hashes)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _regexes(cond: TrackCondition) -> list[str]:
    found = [r for r in (cond.title_regex, cond.work_sentence_regex) if r]
    if cond.activity_evidence:
        found += [r for r in (cond.activity_evidence.matching, cond.activity_evidence.not_matching) if r]
    for child in (cond.any_of or []) + (cond.all_of or []):
        found += _regexes(child)
    return found


def _domain_terms(cond: TrackCondition) -> list[str]:
    found = list(cond.domain.terms) if cond.domain else []
    for child in (cond.any_of or []) + (cond.all_of or []):
        found += _domain_terms(child)
    return found


def validate_setup(config: CoachConfig, declarations: CoachDeclarations, profile: dict) -> list[str]:
    """Cross-checks between config and declarations. Empty list = valid."""
    problems: list[str] = []

    # CV-0: every vocabulary track declared in exactly one tier, nothing else declared.
    declared: dict[str, list[str]] = {}
    for tier in TRACK_TIERS:
        for track in getattr(declarations.target_tracks, tier):
            declared.setdefault(track, []).append(tier)
    for track, tiers in declared.items():
        if len(tiers) > 1:
            problems.append(f"CV-0: {track} declared in more than one tier {tiers}")
    for track in sorted(set(config.tracks) - set(declared)):
        problems.append(f"CV-0: vocabulary track {track} is not declared in any tier")
    for track in sorted(set(declared) - set(config.tracks)):
        problems.append(f"CV-0: declared track {track} is not in the vocabulary")

    # Tier-dependent track properties (CV-2, RT-2, RT-5).
    non_target = set(declarations.target_tracks.non_target)
    in_positioning: dict[str, list[str]] = {}
    for pos_id, pos in config.positionings.items():
        for track in pos.tracks:
            in_positioning.setdefault(track, []).append(pos_id)
    for track, spec in config.tracks.items():
        if spec.defining and track not in non_target:
            problems.append(f"CV-2: only non-target tracks can be defining ({track})")
        targeted = track in declared and track not in non_target
        if targeted and spec.specificity is None:
            problems.append(f"RT-2: targeted track {track} needs a specificity")
        if targeted and len(in_positioning.get(track, [])) != 1:
            problems.append(f"RT-5: targeted track {track} must map to exactly one positioning")
        if track in non_target and track in in_positioning:
            problems.append(f"RT-6: non-target track {track} cannot have a positioning")
    for track in sorted(set(in_positioning) - set(config.tracks)):
        problems.append(f"RT-5: positioning references unknown track {track}")

    # S-4 / SN-4 / ED-1 completeness.
    if sorted(config.activity_precedence) != sorted(config.activities) or \
            len(config.activity_precedence) != len(set(config.activity_precedence)):
        problems.append("S-4: activity_precedence must list every activity exactly once")
    if len(config.activities) != 10:
        problems.append("S-2: all 10 activities must be configured")
    if set(config.level_rungs) != set(DECLARABLE_LEVELS):
        problems.append("SN-4: level_rungs must cover exactly the declarable levels")

    # D-1: domain vocabulary covers every domain term used and every profile domain.
    for track, spec in config.tracks.items():
        for term in _domain_terms(spec.match):
            if term not in config.domains:
                problems.append(f"D-1: track {track} uses undefined domain term {term!r}")
    for term in profile.get("domain", []):
        if term not in config.domains:
            problems.append(f"A-D: profile domain {term!r} is not in the domain vocabulary")
    for term in declarations.experience.relevant_domain_years:
        if term not in config.domains:
            problems.append(f"EX-5: relevant_domain_years key {term!r} is not a configured domain")

    # Every regex compiles.
    patterns = [p for spec in config.activities.values() for pat in spec.patterns for p in (pat.verbs, pat.objects)]
    patterns += [p for spec in config.activities.values() for p in spec.exclusions]
    patterns += [p for terms in config.domains.values() for p in terms]
    patterns += config.work_text.responsibility_headings + config.work_text.boilerplate_headings
    patterns += [p for spec in config.tracks.values() for p in _regexes(spec.match)]
    for pattern in patterns:
        try:
            re.compile(pattern, re.I)
        except re.error as e:
            problems.append(f"regex {pattern!r} does not compile: {e}")

    # Declarations that reference config ids.
    if declarations.resume_variants_ready is not None:
        for pos in declarations.resume_variants_ready:
            if pos not in config.positionings:
                problems.append(f"O-1: resume_variants_ready names unknown positioning {pos!r}")
    return problems
