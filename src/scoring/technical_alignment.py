"""technical_alignment (frozen: PTF-DR-001, PTF-DR-005 + 005-A, J-2, J-3a, J-5).

Known iff at least one REQUIRED technique in the posting is evidenced in
the candidate profile's `techniques` section. Otherwise UNKNOWN:
  - unevidenced_required: the posting requires techniques, none of which
    the profile evidences (resolvable by the candidate's own input)
  - vacuous: the posting states zero REQUIRED techniques (including when
    no requirements section could be detected)
Tools, preferred-only and unknown-importance techniques never establish
the known state and, in v1, never change the band.

Band (highest supported by any single evidenced REQUIRED technique):
  SUBSTANTIVE  a specific (non-umbrella) technique the profile lists as strong
  LIMITED      otherwise — only umbrella techniques, or specific ones the
               profile lists as moderate
  CORE         defined, but unreachable in v1: technical centrality is not
               observable (an observability limit, not a lower value)

Required techniques the profile does not evidence are surfaced as gaps
("No evidence in your profile for X (required by this job)") — never as a
penalty and never as "candidate lacks X".
"""

from src.candidate import candidate_techniques
from src.models.ptf import DimensionResult
from src.scoring.ptf_scale import BAND_VALUES

UMBRELLA_TECHNIQUES = frozenset({"Machine Learning", "Deep Learning"})   # J-2


def gap_text(names: list[str]) -> str:
    return f"No evidence in your profile for {', '.join(names)} (required by this job)"


def score(job, profile: dict) -> DimensionResult:
    levels = candidate_techniques(profile)
    required = [e for e in job.skill_evidence if e.kind == "technique" and e.importance == "required"]
    required_names = sorted({e.name for e in required})
    evidenced = sorted({e.name for e in required if e.name in levels})
    unevidenced = [n for n in required_names if n not in levels]
    gaps = [gap_text(unevidenced)] if unevidenced else []

    if not required_names:
        return DimensionResult(
            name="technical_alignment", known=False, unknown_reason="vacuous",
            reasons=["The posting states no REQUIRED techniques — technical alignment cannot be assessed"],
        )
    if not evidenced:
        return DimensionResult(
            name="technical_alignment", known=False, unknown_reason="unevidenced_required",
            reasons=["None of this posting's REQUIRED techniques is evidenced in your profile — not assessed, not penalised"],
            gaps=gaps,
        )

    substantive = [n for n in evidenced if n not in UMBRELLA_TECHNIQUES and levels[n] == "strong"]
    band = "SUBSTANTIVE" if substantive else "LIMITED"
    listed = ", ".join(f"{n} ({levels[n]})" for n in evidenced)
    if substantive:
        reasons = [f"Technical SUBSTANTIVE: specific required technique(s) you list as strong — {', '.join(substantive)}"]
    elif all(n in UMBRELLA_TECHNIQUES for n in evidenced):
        reasons = [f"Technical LIMITED: only umbrella technique(s) evidenced — {listed}"]
    else:
        reasons = [f"Technical LIMITED: strongest specific required technique is one you list as moderate — {listed}"]
    reasons.append(f"Required techniques evidenced in your profile: {listed}")

    provenance = [
        dict(technique=e.name, candidate_level=levels[e.name], importance=e.importance,
             source_field=e.source_field, section_heading=e.section_heading, context=e.context,
             umbrella=e.name in UMBRELLA_TECHNIQUES)
        for e in required if e.name in levels
    ]
    return DimensionResult(name="technical_alignment", known=True, band=band, score=BAND_VALUES[band],
                           reasons=reasons, provenance=provenance, gaps=gaps)
