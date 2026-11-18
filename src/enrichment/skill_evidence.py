"""Per-job technical evidence: every tool and technique found in the
posting, with an importance of required | preferred | unknown and the
provenance for it. This is EVIDENCE, not a score;
scoring/technical_alignment.py reads its REQUIRED techniques.

Importance rules (in priority order, per sentence containing a mention):
  1. explicit sentence cue: "is a plus", "nice to have", "preferred" ->
     preferred; "must have", "required", "mandatory" -> required
     (conflicting cues -> fall back to the zone below)
  2. the zone the sentence sits in (see requirements_sections.py):
       required zone  -> required   (heading or the source's own field)
       preferred zone -> preferred
       other / mixed / unsectioned -> UNKNOWN
Across several mentions of one concept: required beats preferred beats
unknown. A mention outside any requirements/preferred section is never
promoted to "preferred" just because it wasn't found in a required section.
"""

from src.enrichment.requirements_sections import SectionAnalysis, analyze, sentence_cue
from src.enrichment.taxonomy import find_mentions
from src.models.skill_evidence import SkillEvidence

_RANK = {"required": 2, "preferred": 1, "unknown": 0}
_CONTEXT_MAX = 160


def _classify_unit(zone: str, heading: str, cue: str | None, cue_text: str, source_field: str) -> tuple[str, str]:
    if cue == "preferred":
        return "preferred", f"sentence contains preferred cue '{cue_text}'"
    if cue == "required":
        return "required", f"sentence contains required cue '{cue_text}'"

    conflict = " (conflicting cues in sentence ignored)" if cue_text == "conflict" else ""
    if zone == "required":
        where = "source requirements field" if source_field == "requirements_raw" and not heading else f"'{heading}' section"
        return "required", f"under {where}{conflict}"
    if zone == "preferred":
        return "preferred", f"under '{heading}' section{conflict}"
    if zone == "other":
        return "unknown", f"mentioned under '{heading}' (describes the work; requirement level not stated){conflict}"
    if zone == "mixed":
        return "unknown", f"under mixed heading '{heading}' (requirements and non-requirements not separable){conflict}"
    return "unknown", f"mentioned outside any recognised requirements/preferred section{conflict}"


def build_evidence(description_raw: str, requirements_raw: str) -> tuple[list[SkillEvidence], SectionAnalysis]:
    analysis = analyze(description_raw, requirements_raw)

    per_concept: dict[str, dict] = {}
    for unit in analysis.units:
        mentions = find_mentions(unit.text)
        if not mentions:
            continue
        cue, cue_text = sentence_cue(unit.text)
        importance, reason = _classify_unit(unit.zone, unit.heading, cue, cue_text, unit.source_field)
        for m in mentions:
            entry = per_concept.setdefault(m.concept, {"kind": m.kind, "items": [], "count": 0})
            entry["count"] += 1
            entry["items"].append((importance, reason, unit, m.matched_text))

    evidence: list[SkillEvidence] = []
    for name, entry in per_concept.items():
        best = max(entry["items"], key=lambda it: _RANK[it[0]])
        importance, reason, unit, matched_text = best
        others = sorted({it[0] for it in entry["items"]} - {importance}, key=lambda i: -_RANK[i])
        if others:
            reason += f"; also mentioned elsewhere with importance: {', '.join(others)}"
        evidence.append(
            SkillEvidence(
                name=name,
                kind=entry["kind"],
                importance=importance,
                importance_reason=reason,
                source_field=unit.source_field,
                section_heading=unit.heading,
                matched_text=matched_text,
                context=unit.text[:_CONTEXT_MAX],
                mention_count=entry["count"],
            )
        )
    evidence.sort(key=lambda e: (e.kind, e.name))
    return evidence, analysis
