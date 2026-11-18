"""Extracts years-of-experience and education requirements from free text.
Returns None rather than guessing when nothing matches."""

import re

# Years of experience: every "<number> year(s)" phrase is a candidate, and the
# smallest one is the requirement (as before). A number is digits or an English
# word, optionally followed by a numeric echo ("three (3) years"). For a range
# ("2-5", "2–5", "one to two") the LOWER bound is the minimum; "N+" and
# "N or more" are minimums of N. A phrase is skipped when an upper-bound
# qualifier precedes it ("up to 2 years" is not a minimum), when a
# non-experience noun follows it ("1 year contract", "3 years remaining"), or
# when the number is implausible as an experience requirement (company-age
# claims such as "over 100 years").
WORD_NUMBERS = {word: n for n, word in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen".split())}
_NUMBER = r"(?:(?<![\d.])\d+(?:\.\d+)?|\b(?:" + "|".join(WORD_NUMBERS) + r")\b)"
YEARS_PHRASE_RE = re.compile(
    rf"(?P<low>{_NUMBER})\s*(?:\(\s*(?P<echo>\d+(?:\.\d+)?)\s*\))?\s*(?:\+|plus\b)?\s*"
    rf"(?:(?:-|to)\s*{_NUMBER}\s*(?:\(\s*\d+(?:\.\d+)?\s*\))?\s*\+?\s*)?"
    r"(?:or\s+more\s+|or\s+above\s+|and\s+above\s+)?(?:years?|yrs?)\b", re.I)
UPPER_BOUND_BEFORE_RE = re.compile(
    r"\b(?:up\s+to|maximum(?:\s+of)?|max\.?|no\s+more\s+than|not\s+more\s+than|less\s+than|under|below)\s*$", re.I)
NOT_EXPERIENCE_AFTER_RE = re.compile(
    r"^\s*-?\s*(?:contract|project|programme|program|term|tenure|warranty|old|ago|remaining|anniversary|history|"
    r"bond|renewable|period|plan|roadmap|horizon|lease|subscription|guarantee|of\s+age)\b", re.I)
MAX_PLAUSIBLE_YEARS = 30.0


def _number(token: str) -> float:
    word = token.lower()
    return float(WORD_NUMBERS[word]) if word in WORD_NUMBERS else float(token)


def years_experience_candidates(text: str) -> list[float]:
    """Every years-of-experience requirement the text states, in order. The
    Coach also uses this to find the sentence it cites for the requirement."""
    if not text:
        return []
    text = text.replace("–", "-").replace("—", "-")
    found = []
    for m in YEARS_PHRASE_RE.finditer(text):
        if UPPER_BOUND_BEFORE_RE.search(text[max(0, m.start() - 40):m.start()]) \
                or NOT_EXPERIENCE_AFTER_RE.search(text[m.end():m.end() + 40]):
            continue
        years = _number(m.group("echo") or m.group("low"))
        if years <= MAX_PLAUSIBLE_YEARS:
            found.append(years)
    return found


def extract_years_experience(text: str) -> float | None:
    """The minimum years of experience the text asks for, or None."""
    found = years_experience_candidates(text)
    return min(found) if found else None


def extract_education(text: str) -> str | None:
    if not text:
        return None
    t = text.lower()
    if "phd" in t or "doctorate" in t:
        return "PhD"
    if "master" in t or "postgraduate" in t:
        return "Master"
    if "bachelor" in t or "degree in" in t or "undergraduate" in t:
        return "Bachelor"
    if "diploma" in t:
        return "Diploma"
    return None
