"""employer_clarity classification (P1-2).

This never tries to guess who the real underlying employer is — only
whether the POSTING company itself looks like a staffing/recruitment
agency rather than a direct employer. Three independent signals, checked
in order:

1. Behavioral evidence from dedup: if the same job posting also appeared
   under a different company name (dedup/deduplicator.py's pass-3
   content-similarity merge populates `duplicate_companies`), that's
   direct, explicit evidence — a real employer doesn't repost its own
   opening under someone else's name. This is derived from what we
   observed, not a guess.
2. Name-pattern matching against config/recruiters.yaml — a maintained
   list of known staffing agencies plus generic keywords. This IS a
   heuristic and is documented as such; it will have false negatives
   (agencies not on the list) and, rarely, false positives (a direct
   employer whose name happens to contain a generic keyword).
3. A regulatory agency marker in the job text: a Singapore MOM
   employment-agency licence number ("EA Reg No: 20C0312", "EA Licence:
   23S2061"), an EA personnel registration number ("EA Personnel Reg No:
   R1234567") or a Malaysian JTKSM licence ("JTKSM 1172"). Only these
   explicit markers count; "our client", "staffing", "talent acquisition"
   and similar wording also appear in direct-employer postings and are
   never used.
   The same numbers written WITHOUT the "EA" prefix ("Reg ID: R1100001",
   "Licence No. 12C3456", "EPS Consultants Pte Ltd |95C5630") count only
   next to the posting company's own name: a licence or registration
   keyword with the number within PROXIMITY characters of the name, or a
   bare licence number directly after the name. The text is compared with
   HTML tags removed. Numbers away from the company name (an unrelated
   reference, a client's details) never count.

The Career Coach consumes the result: a RECRUITER posting sets research
effort HIGH (E-S1), gets no contact credit (P-4) and is labelled
RECRUITER_POSTING (P-5), so it can change the next action and placement.
"""

import re

RECRUITER_KEYWORD_PATTERNS_CACHE = None

# Regulatory agency markers (signal 3). "EA" is matched case-sensitively; the
# words around it are not. MOM licence numbers are 2 digits, C or S, 4 digits;
# personnel registration numbers are R + 7 or 8 digits.
_SEP = r"[\s:.#]*"
AGENCY_MARKER_PATTERNS = (
    re.compile(rf"\bEA\b{_SEP}(?:(?i:reg(?:istration)?|licen[cs]e|lic|no|number)\b\.?{_SEP}){{0,3}}\d{{2}}[CS]\d{{4}}\b"),
    re.compile(rf"\bEA\b{_SEP}(?i:personnel\b[\s.]*)?(?i:reg(?:istration)?)\b\.?{_SEP}(?:(?i:no|number)\b\.?{_SEP})?R\d{{7,8}}\b"),
    re.compile(r"\bJTKSM\b[\s:./#-]*(?:(?i:licen[cs]e|lesen|no|number)\b\.?[\s:./#-]*){0,2}[A-Z0-9/-]*\d"),
)


# Unprefixed forms (signal 3, near the company name only). A keyword, an
# optional "No"/"Number"/"ID", then a MOM licence or personnel registration number.
_KEYWORD_NUMBER = re.compile(
    rf"\b(?i:licen[cs]e|lic|reg(?:istration)?)\b\.?{_SEP}(?:(?i:no|number|id)\b\.?{_SEP})?(?:\d{{2}}[CS]\d{{4}}|R\d{{7,8}})\b")
_LEGAL_SUFFIX = re.compile(r"[\s,.]*(?:\b(?:pte\.?\s*ltd|pte|private\s+limited|limited|ltd|sdn\.?\s*bhd|bhd|berhad|inc|llc|llp)"
                           r"|\((?:s|m|singapore|malaysia)\))\.?\s*$", re.I)     # one trailing legal suffix
PROXIMITY = 100
MIN_NAME = 4


def _company_core(company: str) -> str | None:
    """The company name without legal suffixes, or None when too short to
    identify the company in free text."""
    core = (company or "").strip()
    while True:
        stripped = _LEGAL_SUFFIX.sub("", core, count=1).strip(" ,.")
        if stripped == core:
            break
        core = stripped
    return core if len(re.sub(r"\W", "", core)) >= MIN_NAME else None


def _near_company_marker(text: str, company: str) -> str | None:
    core = _company_core(company)
    if core is None:
        return None
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
    name = re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in core.split()) + r"(?!\w)", re.I)
    names = [m.span() for m in name.finditer(text)]
    if not names:
        return None
    for m in _KEYWORD_NUMBER.finditer(text):
        if any(m.start() - end <= PROXIMITY and start - m.end() <= PROXIMITY for start, end in names):
            return m.group(0)
    for start, end in names:
        m = re.compile(r"(?:\s*(?i:pte\.?\s*ltd|sdn\.?\s*bhd|ltd|limited)\.?)?[\s|:,/()-]{0,5}(\d{2}[CS]\d{4})\b").match(text, end)
        if m:
            return text[start:m.end()]
    return None


def _agency_marker(job) -> str | None:
    text = f"{getattr(job, 'description_raw', '') or ''}\n{getattr(job, 'requirements_raw', '') or ''}"
    for pattern in AGENCY_MARKER_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return _near_company_marker(text, getattr(job, "company", "") or "")


def _compile_generic_keywords(generic_keywords: list[str]) -> list[re.Pattern]:
    return [re.compile(r"\b" + re.escape(kw.lower()) + r"\b", re.I) for kw in generic_keywords]


def classify_employer(job, recruiters_config: dict) -> tuple[str, str]:
    """Returns (employer_clarity, employer_clarity_reason)."""
    if job.duplicate_companies:
        others = ", ".join(job.duplicate_companies)
        return (
            "RECRUITER",
            f"Same job posting also appeared under a different company name ({others}) — "
            f"behavior consistent with a recruitment/staffing agency reposting a client's role",
        )

    company_lower = (job.company or "").lower()
    if not company_lower.strip():
        return "UNKNOWN", "No company name available"

    for known_name in recruiters_config.get("known_recruiter_names", []):
        if known_name.lower() in company_lower:
            return "RECRUITER", f"Company name matches known staffing/recruitment agency '{known_name}'"

    generic_patterns = _compile_generic_keywords(recruiters_config.get("generic_keywords", []))
    for pattern in generic_patterns:
        if pattern.search(company_lower):
            return "RECRUITER", f"Company name contains staffing/recruitment industry term ('{pattern.pattern}')"

    marker = _agency_marker(job)
    if marker:
        return "RECRUITER", f"Job description states an employment-agency registration ('{marker}')"

    return "DIRECT_EMPLOYER", "No recruiter/staffing-agency signal found — assumed to be the direct employer"
