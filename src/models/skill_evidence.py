from pydantic import BaseModel


class SkillEvidence(BaseModel):
    """One extracted technical concept for one job, with the provenance
    needed to explain *why* it was extracted and *why* it was given its
    importance. Not a score — a record of evidence.

    importance: "required" | "preferred" | "unknown".
    "unknown" means the posting gave no reliable signal about how much the
    concept matters (e.g. it only appears in prose outside any recognised
    requirements/preferred section). It is deliberately NOT the same as
    "preferred".
    """

    name: str
    kind: str                # "tool" | "technique"
    importance: str          # "required" | "preferred" | "unknown"
    importance_reason: str
    source_field: str        # "requirements_raw" | "description_raw"
    section_heading: str = ""  # heading the evidence sat under, if any
    matched_text: str = ""
    context: str = ""        # the sentence the mention appeared in (truncated)
    mention_count: int = 1
