from src.config import load_recruiters
from src.enrichment.employer_classifier import classify_employer
from src.enrichment.requirement_extractor import extract_education, extract_years_experience
from src.enrichment.role_classifier import (
    classify_characteristics,
    classify_role_family,
    classify_role_level,
    classify_role_type,
)
from src.enrichment.skill_evidence import build_evidence
from src.enrichment.skill_extractor import extract_required_and_preferred
from src.models.processed_job import ProcessedJob


def enrich_job(job: ProcessedJob) -> ProcessedJob:
    """Fills in role classification, skills, characteristics, employer
    clarity, and requirement extraction. Pure function — returns an
    updated copy."""
    full_text = f"{job.description_raw}\n{job.requirements_raw}"

    role_family = classify_role_family(job.job_title)
    role_level = classify_role_level(job.job_title)
    role_type, role_type_confidence = classify_role_type(role_family, job.description_raw, job.requirements_raw)

    required_skills, preferred_skills = extract_required_and_preferred(job.description_raw, job.requirements_raw)
    characteristics = classify_characteristics(full_text)

    years_experience = extract_years_experience(job.requirements_raw) or extract_years_experience(job.description_raw)
    education = extract_education(job.requirements_raw) or extract_education(job.description_raw)

    employer_clarity, employer_clarity_reason = classify_employer(job, load_recruiters())

    evidence, sections = build_evidence(job.description_raw, job.requirements_raw)
    section_headings = (
        [f"required: {h}" for h in sections.required_headings]
        + [f"preferred: {h}" for h in sections.preferred_headings]
        + [f"other: {h}" for h in sections.other_headings]
        + [f"mixed: {h}" for h in sections.mixed_headings]
    )

    return job.model_copy(
        update={
            "tools": sorted(e.name for e in evidence if e.kind == "tool"),
            "techniques": sorted(e.name for e in evidence if e.kind == "technique"),
            "skill_evidence": evidence,
            "requirements_section_status": sections.status,
            "requirements_section_source": sections.source,
            "requirements_section_raw": sections.requirements_section_raw,
            "preferred_section_raw": sections.preferred_section_raw,
            "section_headings": section_headings,
            "role_family": role_family,
            "role_level": role_level,
            "role_type": role_type,
            "role_type_confidence": role_type_confidence,
            "required_skills": required_skills,
            "preferred_skills": preferred_skills,
            "years_experience_required": years_experience,
            "education_required": education,
            "employer_clarity": employer_clarity,
            "employer_clarity_reason": employer_clarity_reason,
            **characteristics,
        }
    )
