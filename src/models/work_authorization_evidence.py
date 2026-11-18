from pydantic import BaseModel


class WorkAuthorizationEvidence(BaseModel):
    """One piece of job-side citizenship/work-authorization language, with
    the provenance needed to show *why* HC-2 reached its status. Not a
    decision — a record of evidence (same role as SkillEvidence).

    restriction_type:
      FOREIGNERS_NOT_ACCEPTED    explicit "no foreigners"-style exclusion
      NATIONALITY_SPECIFIC       explicit "X citizens only"-style exclusion
      OTHER_EXPLICIT_RESTRICTION a stated condition that does NOT deny all
                                 foreign applicants (work permit required,
                                 no sponsorship, SC/PR only, "local candidates
                                 only", or foreigners accepted conditionally)
      NONE                       explicit openness to foreign applicants
      UNKNOWN                    relevant language that establishes no
                                 restriction either way (e.g. "Malaysians
                                 preferred")
    Only the first two can make HC-2 FAIL.
    """

    matched_text: str
    restriction_type: str
    source_field: str        # "job_title" | "requirements_raw" | "description_raw"
    context: str = ""        # surrounding text, tags stripped (truncated)
