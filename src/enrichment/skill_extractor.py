"""LEGACY skill extraction — the behaviour the retired scoring/technical_fit.py
consumed via required_skills/preferred_skills. No score reads these lists now.

Kept behaviourally identical on purpose during the evidence-layer phase:
it reads only the taxonomy's `legacy: true` concepts (tools + the original
techniques) and keeps the original rule that any skill found outside a
requirements/preferred split becomes "preferred". That rule conflates
"unknown importance" with "preferred" — the evidence layer fixes this in
skill_evidence.py (importance = required | preferred | unknown, with
provenance), and technical_alignment reads that. These lists remain only
for data_completeness's `skills_extracted` check. Do not "fix" this module
without an explicit decision.
"""

from src.enrichment.taxonomy import find_mentions

PREFERRED_MARKERS = [
    "nice to have", "nice-to-have", "good to have", "preferred",
    "bonus", "added advantage", "is an advantage", "plus point",
]


def extract_skills(text: str) -> list[str]:
    if not text:
        return []
    return sorted({m.concept for m in find_mentions(text, legacy_only=True)})


def split_required_preferred(requirements_text: str) -> tuple[str, str]:
    """Splits requirements text at the first 'nice to have'-style marker.
    Everything before is treated as required; everything from the marker
    onward as preferred. If no marker is found, all of it is required."""
    lower = requirements_text.lower()
    positions = [lower.find(m) for m in PREFERRED_MARKERS if lower.find(m) != -1]
    if not positions:
        return requirements_text, ""
    idx = min(positions)
    return requirements_text[:idx], requirements_text[idx:]


def extract_required_and_preferred(description_raw: str, requirements_raw: str) -> tuple[list[str], list[str]]:
    required_text, preferred_text = split_required_preferred(requirements_raw)
    required = set(extract_skills(required_text))
    preferred = set(extract_skills(preferred_text)) - required
    # LEGACY: description-only mentions are treated as preferred. This is
    # the UNKNOWN-conflated-with-PREFERRED behaviour; see module docstring.
    description_only = set(extract_skills(description_raw)) - required - preferred
    preferred |= description_only
    return sorted(required), sorted(preferred)
