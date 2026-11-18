"""network_bonus is an ADDITIVE signal, separate from the core weighted
score (per this turn's requirement #3) — networking access shouldn't be
diluted into the fit score, but it's also not something to silently
ignore. Manual input only (section 19) — never scrapes personal contacts."""

import csv
from pathlib import Path

from src.normalizers.company_normalizer import normalize_company

STRENGTH_WEIGHT = {"strong": 1.0, "medium": 0.6, "weak": 0.3}


def load_contacts(path: Path) -> dict[str, list[dict]]:
    if not path.exists():
        return {}
    by_company: dict[str, list[dict]] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            company = row.get("company", "")
            if not company:
                continue
            by_company.setdefault(normalize_company(company), []).append(row)
    return by_company


def score(job, contacts_by_company: dict, config: dict) -> dict:
    matches = contacts_by_company.get(job.company_normalized, [])
    if not matches:
        return {"bonus": 0.0, "reasons": []}

    max_bonus = config.get("network_bonus_max", 10)
    best = max(matches, key=lambda c: STRENGTH_WEIGHT.get(str(c.get("strength", "")).strip().lower(), 0.3))
    weight = STRENGTH_WEIGHT.get(str(best.get("strength", "")).strip().lower(), 0.3)
    bonus = round(max_bonus * weight, 1)

    name = best.get("contact_name", "").strip()
    relationship = best.get("relationship", "").strip()
    reason = f"Contact at {job.company}: {name} ({relationship})" if name else f"Contact at {job.company}"
    return {"bonus": bonus, "reasons": [reason]}
