"""Candidate-side technical evidence helpers (structure only, no scoring).

`profile.yaml` keeps a flat `skills` list and a `techniques` section; the
techniques section is what scoring/technical_alignment.py reads. This module reads
the techniques section and can validate that the two views agree with each
other and with the taxonomy's tool/technique split. It never infers a
technique the profile does not explicitly state.
"""

from src.enrichment.taxonomy import concept_kinds


def candidate_techniques(profile: dict) -> dict[str, str]:
    """{technique name: 'strong' | 'moderate'} exactly as written in the
    profile's `techniques` section. Absent section -> {} (all unknown)."""
    section = profile.get("techniques") or {}
    out: dict[str, str] = {}
    for level in ("strong", "moderate"):
        for name in section.get(level) or []:
            out[name] = level
    return out


def validate_profile_evidence(profile: dict) -> list[str]:
    """Consistency problems between profile.skills, profile.techniques and
    the taxonomy. Empty list = consistent."""
    kinds = concept_kinds()
    problems: list[str] = []
    techniques = candidate_techniques(profile)

    skills: dict[str, str] = {}
    for level in ("strong", "moderate"):
        for name in (profile.get("skills") or {}).get(level) or []:
            skills[name] = level

    for name, level in techniques.items():
        if kinds.get(name) != "technique":
            problems.append(f"techniques entry '{name}' is not a technique in the taxonomy (kind={kinds.get(name)})")
        if name not in skills:
            problems.append(f"techniques entry '{name}' is not listed under skills (would be inferred, not stated)")
        elif skills[name] != level:
            problems.append(f"'{name}' is {level} under techniques but {skills[name]} under skills")

    for name, level in skills.items():
        if kinds.get(name) == "technique" and name not in techniques:
            problems.append(f"skills entry '{name}' is a technique but missing from the techniques section")
    return problems
