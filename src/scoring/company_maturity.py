"""company_ds_maturity_score: no enrichment source exists yet in the MVP
(section 16 — do not invent information). Always unknown for now; the
module exists so V2 (company/Google-search enrichment) has a clean seam
to fill in without changing overall.py's interface."""


def score(job, profile: dict, config: dict) -> dict:
    return {
        "score": None,  # unknown, not 0.0 — never displays as "worst possible"
        "known": False,
        "reasons": ["No company maturity evidence available (no enrichment source in MVP)"],
    }
