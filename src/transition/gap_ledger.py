"""Gap Ledger (Transition Lens V1, annotation-only).

An action-oriented VIEW over existing evidence — it adds no new judgement:
  - required_unevidenced: REQUIRED techniques in the posting with no
    evidence in the candidate profile. Derived from the same inputs and the
    same helper (candidate_techniques) as the frozen technical_alignment
    gaps, so the two cannot disagree. Wording is always "No evidence in your
    profile for X" — never "you lack X"; a gap is never a penalty.
  - nice_to_have: preferred / unknown-importance techniques not evidenced.
  - candidate_classification: only what the candidate declares in
    profile.yaml transition.gap_classifications (translation | portfolio |
    transferable | not_pursuing). Nothing is inferred — "critical" is not
    produced (technique centrality is unobservable) and "transferable" is
    never derived from a technique-relationship map.
"""

from collections import Counter

from src.candidate import candidate_techniques
from src.enrichment.taxonomy import concept_kinds
from src.models.transition import GAP_CLASSIFICATIONS, GapItem, GapLedger, NiceToHave

CLASSIFICATIONS_PATH = "profile.transition.gap_classifications"


def validate_gap_classifications(classifications: dict) -> None:
    kinds = concept_kinds()
    for technique, value in (classifications or {}).items():
        if kinds.get(technique) != "technique":
            raise ValueError(f"{CLASSIFICATIONS_PATH}: '{technique}' is not a technique in the taxonomy")
        if value not in GAP_CLASSIFICATIONS or value == "UNCLASSIFIED":
            raise ValueError(f"{CLASSIFICATIONS_PATH}.{technique}: '{value}' is not one of {GAP_CLASSIFICATIONS[1:]}")


def build_gap_ledger(job, profile: dict) -> GapLedger:
    levels = candidate_techniques(profile)
    classifications = (profile.get("transition") or {}).get("gap_classifications") or {}
    techniques = [e for e in job.skill_evidence if e.kind == "technique"]
    required = {e.name: e for e in techniques if e.importance == "required"}
    if not required:
        return GapLedger(settled_reason="vacuous_requirements")

    gaps = [
        GapItem(technique=name, source_field=ev.source_field, section_heading=ev.section_heading, context=ev.context,
                candidate_classification=classifications.get(name, "UNCLASSIFIED"),
                classification_source=f"{CLASSIFICATIONS_PATH}.{name}" if name in classifications else None)
        for name, ev in sorted(required.items()) if name not in levels
    ]
    nice = sorted({(e.name, e.importance) for e in techniques
                   if e.importance in ("preferred", "unknown") and e.name not in levels and e.name not in required})
    return GapLedger(required_unevidenced=gaps, nice_to_have=[NiceToHave(technique=n, importance=i) for n, i in nice])


def with_market_frequency(ledgers: list[GapLedger]) -> list[GapLedger]:
    """Fill market_frequency: in how many jobs of this run the technique is
    required with no profile evidence."""
    frequency = Counter(g.technique for ledger in ledgers for g in ledger.required_unevidenced)
    return [ledger.model_copy(update={"required_unevidenced": [
        g.model_copy(update={"market_frequency": frequency[g.technique]}) for g in ledger.required_unevidenced]})
        for ledger in ledgers]


def gap_market(ledgers: list[GapLedger]) -> list[tuple[str, int]]:
    """Run-level view: evidence gaps ranked by how many jobs require them."""
    return Counter(g.technique for ledger in ledgers for g in ledger.required_unevidenced).most_common()
