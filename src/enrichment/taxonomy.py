"""Compiled view of config/skills_taxonomy.yaml.

Every concept has a KIND ("tool" or "technique") and an optional `legacy`
flag. Legacy concepts are exactly the original flat taxonomy that fed the
legacy required_skills/preferred_skills lists (skill_extractor.py); the
flag keeps those lists byte-stable. Scoring no longer reads them — the
retired technical_fit did; technical_alignment reads skill_evidence.

Patterns are plain regexes, or mappings with optional context guards:
  requires - must match somewhere in a +/-CONTEXT_WINDOW char window
             around the hit (used for ambiguous bare words like
             "classification")
  excludes - vetoes the hit if it matches in that window (used for e.g.
             "regression testing")
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from src.config import load_skills_taxonomy

CONTEXT_WINDOW = 60
KINDS = ("tool", "technique")


@dataclass(frozen=True)
class PatternSpec:
    regex: re.Pattern
    requires: re.Pattern | None = None
    excludes: re.Pattern | None = None


@dataclass(frozen=True)
class Concept:
    name: str
    kind: str          # "tool" | "technique"
    legacy: bool
    patterns: tuple[PatternSpec, ...]


@dataclass(frozen=True)
class Mention:
    concept: str
    kind: str
    start: int
    end: int
    matched_text: str


def _compile_pattern(item) -> PatternSpec:
    if isinstance(item, str):
        return PatternSpec(re.compile(item, re.I))
    return PatternSpec(
        regex=re.compile(item["pattern"], re.I),
        requires=re.compile(item["requires"], re.I) if item.get("requires") else None,
        excludes=re.compile(item["excludes"], re.I) if item.get("excludes") else None,
    )


@lru_cache(maxsize=1)
def load_concepts() -> tuple[Concept, ...]:
    raw = load_skills_taxonomy()
    concepts: list[Concept] = []
    for section, kind in (("tools", "tool"), ("techniques", "technique")):
        for name, spec in (raw.get(section) or {}).items():
            concepts.append(
                Concept(
                    name=name,
                    kind=kind,
                    legacy=bool(spec.get("legacy", False)),
                    patterns=tuple(_compile_pattern(p) for p in spec["patterns"]),
                )
            )
    return tuple(concepts)


def concept_kinds() -> dict[str, str]:
    return {c.name: c.kind for c in load_concepts()}


def _hit_is_valid(spec: PatternSpec, text: str, m: re.Match) -> bool:
    if spec.requires is None and spec.excludes is None:
        return True
    window = text[max(0, m.start() - CONTEXT_WINDOW): m.end() + CONTEXT_WINDOW]
    if spec.excludes is not None and spec.excludes.search(window):
        return False
    if spec.requires is not None and not spec.requires.search(window):
        return False
    return True


def find_mentions(text: str, legacy_only: bool = False) -> list[Mention]:
    """All valid mentions of all (or only legacy) concepts in `text`,
    ordered by position. A concept can appear multiple times."""
    if not text:
        return []
    mentions: list[Mention] = []
    for concept in load_concepts():
        if legacy_only and not concept.legacy:
            continue
        for spec in concept.patterns:
            for m in spec.regex.finditer(text):
                if _hit_is_valid(spec, text, m):
                    mentions.append(Mention(concept.name, concept.kind, m.start(), m.end(), m.group(0)))
    mentions.sort(key=lambda x: (x.start, x.concept))
    return mentions
