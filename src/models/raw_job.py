from datetime import datetime, timezone

from pydantic import BaseModel, Field


class RawJob(BaseModel):
    """Immutable raw layer. Preserves original source text — never edit
    a RawJob after creation; normalization happens downstream in ProcessedJob."""

    job_id: str
    source: str
    source_job_id: str
    source_url: str

    company: str
    job_title: str
    job_description: str

    location_raw: str = ""
    employment_type_raw: str = ""
    salary_raw: str = ""

    posted_date_raw: str = ""
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    company_raw: str = ""

    query_used: str = ""

    # Fields present on some sources but not others go here verbatim,
    # untouched, so nothing the source gave us is thrown away.
    source_extra: dict = Field(default_factory=dict)
