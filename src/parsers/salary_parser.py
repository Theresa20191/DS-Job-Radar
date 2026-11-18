"""Parses salary_raw (as written by each source's scraper) into structured
min/max/currency/period. Currency is inferred from source, not from text,
because neither source states it explicitly in the salary field — MCF is
Singapore-only (SGD) and Hiredly is Malaysia-only (MYR) by construction.

Never invents a salary — if nothing parseable is present, salary_unknown=True
and every numeric field stays None."""

import json
import re

from src.config import load_scoring

UNKNOWN = {
    "salary_min": None,
    "salary_max": None,
    "salary_currency": None,
    "salary_period": None,
    "salary_unknown": True,
}

SOURCE_CURRENCY = {
    "mycareersfuture": "SGD",
    "hiredly": "MYR",
}


def parse_salary(salary_raw: str, source: str) -> dict:
    if not salary_raw:
        return dict(UNKNOWN)

    if source == "mycareersfuture":
        try:
            data = json.loads(salary_raw)
        except (json.JSONDecodeError, TypeError):
            return dict(UNKNOWN)
        if not isinstance(data, dict):
            return dict(UNKNOWN)
        minimum, maximum = data.get("minimum"), data.get("maximum")
        if minimum is None and maximum is None:
            return dict(UNKNOWN)
        period = (data.get("type") or {}).get("salaryType") or "Monthly"
        return {
            "salary_min": float(minimum) if minimum is not None else None,
            "salary_max": float(maximum) if maximum is not None else (float(minimum) if minimum is not None else None),
            "salary_currency": "SGD",
            "salary_period": period,
            "salary_unknown": False,
        }

    if source == "hiredly":
        text = salary_raw.strip()
        if not text or text.lower() in ("undisclosed", "negotiable", ""):
            return dict(UNKNOWN)
        nums = [float(x.replace(",", "")) for x in re.findall(r"[\d,]+(?:\.\d+)?", text)]
        if not nums:
            return dict(UNKNOWN)
        lo, hi = (nums[0], nums[1]) if len(nums) >= 2 else (nums[0], nums[0])
        return {
            "salary_min": lo,
            "salary_max": hi,
            "salary_currency": "MYR",
            "salary_period": "Monthly",  # Hiredly listings are conventionally monthly MYR figures
            "salary_unknown": False,
        }

    return dict(UNKNOWN)


def to_myr(salary_min: float | None, salary_max: float | None, currency: str | None) -> tuple[float | None, float | None]:
    """Cross-currency comparison only — uses an approximate configured FX
    rate, not a live rate. Never used for anything but relative scoring."""
    if salary_min is None and salary_max is None:
        return None, None
    if currency == "MYR" or currency is None:
        return salary_min, salary_max
    if currency == "SGD":
        rate = load_scoring()["compensation"]["sgd_to_myr_rate"]
        lo = salary_min * rate if salary_min is not None else None
        hi = salary_max * rate if salary_max is not None else None
        return lo, hi
    return None, None
