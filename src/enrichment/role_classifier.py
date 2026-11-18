"""Rule-based role classification (section 11 of the project spec).

Two independent axes:
  role_family  — what the title says (Data Scientist, ML Engineer, ...)
  role_type    — what the actual responsibilities look like, from content
                 (TRUE_DS vs DS_TITLE_BUT_ANALYTICS_HEAVY, etc.)

A job titled "Data Scientist" that is 70% SQL/dashboard work should NOT
score as a strong opportunity just because of its title — role_type is
what job_quality_score keys off. DS_TITLE_BUT_ANALYTICS_HEAVY is NOT a
hard exclusion (HC-1 revision): the job stays eligible and job_quality
scores it low.
"""

import re

TITLE_FAMILY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"senior.*data scientist|data scientist.*senior", re.I), "Senior Data Scientist"),
    (re.compile(r"\bdecision scientist\b", re.I), "Decision Scientist"),
    (re.compile(r"\brisk.*data scientist|\bdata scientist.*risk\b", re.I), "Risk Data Scientist"),
    (re.compile(r"\bfraud.*data scientist|\bdata scientist.*fraud\b", re.I), "Fraud Data Scientist"),
    (re.compile(r"\bproduct.*data scientist|\bdata scientist.*product\b", re.I), "Product Data Scientist"),
    (re.compile(r"\banalytics scientist\b", re.I), "Analytics Scientist"),
    (re.compile(r"\bml scientist\b|\bmachine learning scientist\b", re.I), "ML Scientist"),
    (re.compile(r"\bapplied scientist\b", re.I), "Applied Scientist"),
    (re.compile(r"\bdata scientist\b", re.I), "Data Scientist"),
    (re.compile(r"machine learning engineer|\bml engineer\b|\bmle\b|\bmlops\b|ai platform engineer", re.I), "ML Engineer"),
    (re.compile(r"\bbi analyst\b|business intelligence analyst", re.I), "BI Analyst"),
    (re.compile(r"\bbusiness analyst\b", re.I), "Business Analyst"),
    (re.compile(r"\bdata analyst\b", re.I), "Data Analyst"),
    (re.compile(r"software engineer|backend engineer|frontend engineer|full[- ]stack", re.I), "Software Engineer"),
]

DS_TITLED_FAMILIES = {
    "Data Scientist", "Senior Data Scientist", "Product Data Scientist",
    "Risk Data Scientist", "Fraud Data Scientist", "Decision Scientist",
    "Analytics Scientist", "ML Scientist", "Applied Scientist",
}

ML_SIGNALS = [
    "machine learning", "predictive model", "model deployment", "feature engineering",
    "deep learning", "neural network", "xgboost", "scikit-learn", "model training",
    "statistical model", "experimentation", "a/b test", "causal inference",
    "forecasting model", "\\bnlp\\b", "computer vision", "model evaluation", "mlops",
    "production ml", "model ownership",
]
REPORTING_SIGNALS = [
    "dashboard", "report generation", "mis report", "ad-hoc report", "ad hoc report",
    "data extraction", "stakeholder reporting", "business intelligence report",
    "excel report", "\\betl\\b only",
]

_ML_RE = [re.compile(p, re.I) for p in ML_SIGNALS]
_REPORTING_RE = [re.compile(p, re.I) for p in REPORTING_SIGNALS]


def classify_role_family(title: str) -> str:
    for pattern, family in TITLE_FAMILY_RULES:
        if pattern.search(title):
            return family
    return "Other"


def classify_role_level(title: str) -> str:
    t = title.lower()
    if "intern" in t:
        return "Intern"
    if "junior" in t or "entry level" in t:
        return "Junior"
    if "principal" in t:
        return "Principal"
    if "lead" in t:
        return "Lead"
    if "staff" in t:
        return "Staff"
    if "manager" in t or "head of" in t:
        return "Manager"
    if "senior" in t:
        return "Senior"
    return "Mid/Unspecified"


def classify_role_type(role_family: str, description_raw: str, requirements_raw: str) -> tuple[str, float]:
    """Returns (role_type, confidence 0-1)."""
    text = f"{description_raw}\n{requirements_raw}"
    if len(text.strip()) < 50:
        # Not enough text to judge content — say so rather than guessing.
        base_type = {
            "ML Engineer": "ML_ENGINEER",
            "Data Analyst": "ANALYST_NOT_DS",
            "Business Analyst": "ANALYST_NOT_DS",
            "BI Analyst": "ANALYST_NOT_DS",
            "Software Engineer": "SOFTWARE_ENGINEER",
        }.get(role_family)
        if role_family in DS_TITLED_FAMILIES:
            return "DS_TITLE_UNCLEAR", 0.1
        return base_type or "UNCLASSIFIED", 0.1

    ml_count = sum(1 for p in _ML_RE if p.search(text))
    reporting_count = sum(1 for p in _REPORTING_RE if p.search(text))

    if role_family in DS_TITLED_FAMILIES:
        # Resolution fix (scoring architecture audit, HC-1 substantive-DS
        # audit): previously only checked `ml_count == 0` before calling a
        # job DS_TITLE_BUT_ANALYTICS_HEAVY, so a job with e.g. ml_count=1
        # and reporting_count=2 (reporting signals clearly outnumber ML
        # signals, but there's one incidental ML mention) fell through to
        # DS_TITLE_UNCLEAR — an eligible, ambiguous bucket — rather than
        # being correctly recognized as analytics-heavy. Now compares
        # ml_count against reporting_count directly, regardless of
        # whether ml_count is exactly 0.
        if ml_count >= 2:
            return "TRUE_DS", min(1.0, 0.5 + 0.1 * ml_count)
        if ml_count >= 1 and ml_count > reporting_count:
            return "TRUE_DS", 0.55
        if reporting_count > ml_count and reporting_count > 0:
            return "DS_TITLE_BUT_ANALYTICS_HEAVY", min(1.0, 0.4 + 0.1 * reporting_count)
        return "DS_TITLE_UNCLEAR", 0.3

    if role_family == "ML Engineer":
        return "ML_ENGINEER", 0.8
    if role_family in ("Data Analyst", "Business Analyst", "BI Analyst"):
        return "ANALYST_NOT_DS", 0.8
    if role_family == "Software Engineer":
        return "SOFTWARE_ENGINEER", 0.8

    # role_family == "Other": the title didn't match any known pattern.
    # Judge by content rather than assuming it's noise — a handful of
    # genuinely relevant postings use unusual titles — but most "Other"
    # jobs (especially from loose-match sources like Hiredly) really are
    # unrelated noise, so require real ML signal before calling it DS.
    if ml_count >= 2:
        return "TRUE_DS", min(1.0, 0.4 + 0.1 * ml_count)
    return "NO_DS_SIGNAL", 0.6 if (reporting_count > 0 or ml_count == 0) else 0.3


CHARACTERISTIC_PATTERNS: dict[str, list[str]] = {
    "machine_learning": ["machine learning", "\\bml\\b"],
    "deep_learning": ["deep learning", "neural network"],
    "statistics": ["statistics", "statistical model", "hypothesis test"],
    "experimentation": ["experimentation", "experiment design"],
    "ab_testing": ["a/b test", "ab testing", "split test"],
    "forecasting": ["forecasting", "demand forecast"],
    "nlp": ["\\bnlp\\b", "natural language processing"],
    "computer_vision": ["computer vision", "image recognition", "object detection"],
    "recommendation": ["recommendation system", "recommender system"],
    "fraud": ["fraud"],
    "risk": ["\\brisk\\b"],
    "fintech": ["fintech", "financial technology", "banking", "payments"],
}
_CHAR_RE = {k: [re.compile(p, re.I) for p in v] for k, v in CHARACTERISTIC_PATTERNS.items()}


def classify_characteristics(text: str) -> dict[str, bool]:
    return {key: any(p.search(text) for p in patterns) for key, patterns in _CHAR_RE.items()}
