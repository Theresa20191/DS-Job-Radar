"""data_completeness_pct — a raw, job-side data-richness metric,
independent of scoring, so sparse postings never look as well-described
as fully-enriched ones.

(The former confidence_score lived here. It was retired in the PTF
migration and replaced by decision stability — see scoring/stability.py.)

requirements_present (C-4) means: a requirements section was detected by
the evidence-layer section detector (ProcessedJob.requirements_section_status),
not merely that the source's requirements_raw field is non-empty. It is
independent of whether any REQUIRED technique exists, whether
technical_alignment is known, and whether technical uncertainty is open.
"""


def compute_completeness(job, config: dict) -> tuple[float, list[str]]:
    checklist = config["completeness"]["completeness_fields"]
    checks = {
        "title": bool(job.job_title.strip()),
        "company": bool(job.company.strip()),
        "location": bool(job.location_normalized.strip()) or job.remote_type == "Remote",
        "description_min_length": len(job.description_raw.strip()) >= 100,
        "requirements_present": job.requirements_section_status == "DETECTED",
        "salary_known": not job.salary_unknown,
        "years_experience_known": job.years_experience_required is not None,
        "employment_type_known": job.employment_type not in ("", "unknown"),
        "skills_extracted": bool(job.required_skills or job.preferred_skills),
    }
    unknown_fields = [field for field in checklist if not checks.get(field, False)]
    present = len(checklist) - len(unknown_fields)
    pct = round(100 * present / len(checklist), 1) if checklist else 0.0
    return pct, unknown_fields
