"""job_quality_score (renamed/rescoped from career_transition_score):
candidate-independent — "is this substantive DS/ML work with real
modelling ownership, rather than reporting/analyst work wearing a DS
title?"

role_type (enrichment/role_classifier.py) is the SOLE numeric driver.
Previously this score also added a separate +3/-4 per-keyword bonus on
top of the role_type-derived base — but role_type is itself derived from
counting a near-identical set of ML-signal keywords, so that bonus was
counting the same evidence twice within one score (found in the scoring
architecture audit). The keyword matches are now explanatory evidence
only — they explain *why* role_type landed where it did, and no longer
independently move the number.

`profile` is accepted for interface consistency with the other scoring
modules but is never read — this score is candidate-independent by
definition, and this was true even before the rename."""

ROLE_TYPE_BASE = {
    "TRUE_DS": 75,
    "DS_TITLE_UNCLEAR": 45,
    "DS_TITLE_BUT_ANALYTICS_HEAVY": 20,  # eligible (not an HC-1 exclusion); this low base is what separates it
    "ML_ENGINEER": 55,
    "UNCLASSIFIED": 35,
    "ANALYST_NOT_DS": 10,
    "SOFTWARE_ENGINEER": 10,
    "NO_DS_SIGNAL": 10,
}


def score(job, profile: dict, config: dict) -> dict:
    cfg = config["job_quality"]
    text = f"{job.description_raw}\n{job.requirements_raw}".lower()

    positive_hits = [kw for kw in cfg["positive_keywords"] if kw in text]
    negative_hits = [kw for kw in cfg["negative_keywords"] if kw in text]

    value = float(ROLE_TYPE_BASE.get(job.role_type, 35))

    reasons = []
    if job.role_type == "TRUE_DS":
        reasons.append(f"Content reads as genuine DS work (role_type=TRUE_DS, confidence {job.role_type_confidence:.2f})")
    elif job.role_type == "DS_TITLE_BUT_ANALYTICS_HEAVY":
        reasons.append("DS-titled but content reads analytics/reporting-heavy — limited modelling ownership")
    elif job.role_type == "DS_TITLE_UNCLEAR":
        reasons.append("DS-titled but description too thin to confirm real modelling content")
    elif job.role_type == "ML_ENGINEER":
        reasons.append("ML Engineer track — adjacent to DS, values differ (more deployment, less modelling ownership)")

    if positive_hits:
        reasons.append(f"Mentions: {', '.join(positive_hits[:5])}")
    if negative_hits:
        reasons.append(f"Also mentions lower-value work: {', '.join(negative_hits[:3])}")

    return {"score": value, "known": True, "reasons": reasons}
