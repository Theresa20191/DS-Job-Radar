"""Detects requirements-like sections inside free text and splits text into
sentence-level units tagged with the zone they sit in.

Why this exists: MyCareersFuture exposes no usable requirements field
(`otherRequirements` is None on every record) — requirements are written
inline in the description under headings like "Requirements", "Minimum
Qualifications", "What we're looking for", "About you", "Nice to have".

Zones:
  required     - under a requirements-style heading (or the source's own
                 requirements field)
  preferred    - under a preferred/nice-to-have heading
  other        - a recognised NON-requirement section (Responsibilities,
                 About us, Benefits, Travel requirements ...)
  mixed        - a heading naming both (e.g. "Responsibilities &
                 Requirements") — cannot tell which lines are which
  unsectioned  - text before/outside any recognised heading

The detector never guesses: text is only "required"/"preferred" if a
heading (or an explicit sentence-level cue) says so. Everything else stays
unsectioned/other/mixed, which downstream maps to importance "unknown".
"""

import re
from dataclasses import dataclass

from src.enrichment.taxonomy import find_mentions

# --- heading vocabulary -------------------------------------------------

_PREFERRED_KEYS = re.compile(
    r"preferred|nice[- ]to[- ]have|good[- ]to[- ]have|bonus|added advantage|desirable|"
    r"advantageous|an advantage|would be a plus|not required",
    re.I,
)
_NOT_A_REQUIREMENT = re.compile(r"travel requirements?|reporting requirements?|corporate requirements?|visa requirements?", re.I)
_OTHER_KEYS = re.compile(
    r"responsibilit|what you(?:'ll| will)? (?:do|be doing|build|champion|enjoy|gain|learn)|duties|"
    r"about (?:us|the (?:role|company|team|job|position)|our)|the role\b|your role\b|role overview|"
    r"job description|position (?:summary|overview)|company (?:overview|description|profile)|"
    r"department overview|\boverview\b|benefits|what we offer|we offer|perks|why (?:join|you)|"
    r"our (?:culture|team|values)|how to apply|apply now|working hours|salary|compensation|"
    r"objectives of this role|^description$|why work|what will you (?:do|be doing)|"
    r"tech(?:nology)? stack|our stack|tools we use",
    re.I,
)
_REQUIRED_KEYS = re.compile(
    r"requirements?|qualifications?|looking for|you(?:'ll| will) need|what you need|you need|"
    r"you have|you(?:'ve| have) got|you(?:'ll| will)? bring|what you(?:'ll| will)? bring|who you are|about you|your profile|"
    r"candidate profile|must[- ]have|essential|mandatory|minimum|basic|ideal (?:candidate|experience|profile)|"
    r"person specification|skills? (?:and|&|/) (?:experience|qualification|requirement|attribute)s?|"
    r"(?:technical|core|key|required|relevant|essential) (?:skills|competenc\w*|knowledge|experience)|"
    r"skills? required|experience required|skills (?:for|to) succe\w+|skills and attributes|"
    r"knowledge (?:&|and) competenc\w*|your skills",
    re.I,
)
_NOT_CANDIDATE_LEAD = re.compile(
    r"^(?:business|stakeholders?|customers?|clients?|users?|data|systems?|functional|regulatory|compliance|reporting|project|product)\b",
    re.I,
)
# Bare single-word headings that are only trusted when they are the WHOLE line
_WEAK_REQUIRED_EXACT = {"skills", "experience", "skills & experience", "skills and experience",
                        "experience and skills", "qualifications & experience", "qualifications and experience"}
_MAX_HEADING_WORDS = 10
_MAX_RESIDUAL_WORDS = 3   # words beyond the recognised key phrase

# Closing/company boilerplate that follows a requirements list without its own
# heading ("equal opportunity...", "Join us at X..."). Once seen, the zone
# stops being required/preferred — otherwise a tool mentioned in a closing
# culture blurb would be marked "required".
_BOILERPLATE = re.compile(
    r"equal[- ]opportunit|equal[- ]employment|affirmative action|(?:does|do) not discriminate|"
    r"protected (?:veteran|class)|diversity,? equity|committed to (?:diversity|creating an inclusive)|"
    r"background (?:verification|check)|personal data|privacy (?:policy|notice)|\bpdpa\b|"
    r"\bby (?:applying|submitting)\b|apply (?:now|online|today)|to apply,? please|"
    r"\bjoin us (?:at|in|to|and)\b|\bcome join\b|shortlisted candidates|we regret|"
    r"(?:send|email|submit) (?:your )?(?:cv|resume)",
    re.I,
)
_PAREN_CONTINUATION = re.compile(r"^\(.{1,40}\)$")


@dataclass(frozen=True)
class Heading:
    kind: str        # required | preferred | other | mixed
    text: str        # the heading as written (cleaned)
    remainder: str   # text on the same line after "Heading:" ("" if none)


def _clean_line(line: str) -> str:
    s = line.strip().replace("’", "'")
    s = re.sub(r"^[\s\-\*•·▪●◦>#]+", "", s)
    s = re.sub(r"^\d+[\.\)]\s+", "", s)
    return s.strip()


def _residual_words(text: str, *patterns: re.Pattern) -> int:
    residual = text
    for p in patterns:
        residual = p.sub(" ", residual)
    return len(re.findall(r"[A-Za-z0-9']+", residual))


def _classify_heading_text(h: str) -> str | None:
    """Kind for an already-isolated heading string, or None if not a heading."""
    hl = h.lower().strip(" :-–—")
    if not hl:
        return None
    # A heading never names a technical concept: "Python and SQL. Nice to have"
    # is content with a cue, not a heading, and must not be swallowed.
    if find_mentions(hl):
        return None
    words = re.findall(r"[a-z0-9']+", hl)
    if not words or len(words) > _MAX_HEADING_WORDS:
        return None
    # Headings don't carry quantities ("3 years of relevant experience" is a
    # requirement bullet, not a heading).
    if re.search(r"\d", hl):
        return None

    if _NOT_A_REQUIREMENT.search(hl):
        return "other"
    # "Business requirements: ..." / "Data requirements" describe the WORK
    # (a responsibility bullet), not the candidate's requirements.
    if _NOT_CANDIDATE_LEAD.match(hl) and _REQUIRED_KEYS.search(hl) and not _PREFERRED_KEYS.search(hl):
        return None

    pref = bool(_PREFERRED_KEYS.search(hl))
    req = bool(_REQUIRED_KEYS.search(hl))
    oth = bool(_OTHER_KEYS.search(hl))

    if pref and not (req and oth):
        if _residual_words(hl, _PREFERRED_KEYS, _REQUIRED_KEYS) <= _MAX_RESIDUAL_WORDS:
            return "preferred"
        return None
    if req and oth:
        return "mixed" if _residual_words(hl, _REQUIRED_KEYS, _OTHER_KEYS) <= _MAX_RESIDUAL_WORDS else None
    if req:
        return "required" if _residual_words(hl, _REQUIRED_KEYS) <= _MAX_RESIDUAL_WORDS else None
    if oth:
        return "other" if _residual_words(hl, _OTHER_KEYS) <= _MAX_RESIDUAL_WORDS else None
    if hl in _WEAK_REQUIRED_EXACT:
        return "required"
    return None


def classify_heading(line: str) -> Heading | None:
    """Recognise a heading line, either bare ("Requirements") or inline
    ("Requirements: 3+ years of Python"). Sentences are never headings."""
    s = _clean_line(line)
    if not s or len(s) > 200 or s.startswith("("):
        return None

    # inline form: short "Heading:" prefix followed by content
    m = re.match(r"^([^:]{2,60}?)\s*[:：]\s*(\S.*)$", s)
    if m:
        kind = _classify_heading_text(m.group(1))
        if kind:
            return Heading(kind, m.group(1).strip(), m.group(2).strip())

    question_heading = s.endswith("?") and re.match(r"(?i)^(?:what|who|why|how|where)\b", s)
    if len(s) <= 80 and (question_heading or not s.endswith((".", "!", "?", ",", ";"))):
        kind = _classify_heading_text(s.rstrip("?"))
        if kind:
            return Heading(kind, s.rstrip(" :-–—"), "")
    return None


# --- sentence-level cues ------------------------------------------------

_PREFERRED_CUE = re.compile(
    r"\ba (?:big |strong |definite |real )?plus\b|\bis (?:a )?plus\b|\bplus points?\b|\bbonus points?\b|"
    r"\b(?:a |an )(?:bonus|advantage)\b|\badded advantage\b|\bnice[- ]to[- ]have\b|\bgood[- ]to[- ]have\b|"
    r"\bpreferred\b|\bdesirable\b|\bnot required\b|\boptional\b|\bideally\b",
    re.I,
)
_REQUIRED_CUE = re.compile(
    r"\bmust[- ]have\b|\bmust (?:have|know|possess|be proficient|be able)\b|\brequired\b|\bmandatory\b|"
    r"\bessential\b|\bminimum of\b|\brequires\b",
    re.I,
)


def sentence_cue(sentence: str) -> tuple[str | None, str]:
    """('preferred'|'required'|None, cue text). Conflicting cues -> (None, 'conflict')."""
    pref = _PREFERRED_CUE.search(sentence)
    req = _REQUIRED_CUE.search(re.sub(r"\bnot required\b", " ", sentence, flags=re.I))
    if pref and req:
        return None, "conflict"
    if pref:
        return "preferred", pref.group(0).strip()
    if req:
        return "required", req.group(0).strip()
    return None, ""


# --- segmentation -------------------------------------------------------

@dataclass(frozen=True)
class Unit:
    text: str
    zone: str          # required | preferred | other | mixed | unsectioned
    heading: str       # governing heading text ("" if none)
    source_field: str  # "requirements_raw" | "description_raw"


@dataclass
class SectionAnalysis:
    units: list[Unit]
    required_headings: list[str]
    preferred_headings: list[str]
    other_headings: list[str]
    mixed_headings: list[str]
    requirements_section_raw: str
    preferred_section_raw: str
    status: str        # "DETECTED" | "UNKNOWN"
    source: str        # "source_field" | "description_heading" | "both" | "none"


_SENTENCE_SPLIT = re.compile(r"(?<=[.;!?])\s+")


def segment(text: str, source_field: str, default_zone: str) -> tuple[list[Unit], dict[str, list[str]]]:
    units: list[Unit] = []
    headings: dict[str, list[str]] = {"required": [], "preferred": [], "other": [], "mixed": []}
    zone, heading_text = default_zone, ""
    for raw_line in (text or "").split("\n"):
        if not raw_line.strip():
            continue
        h = classify_heading(raw_line)
        if h:
            zone, heading_text = h.kind, h.text
            headings[h.kind].append(h.text)
            body = h.remainder
        else:
            body = _clean_line(raw_line)
        if not body:
            continue
        if not h and _PAREN_CONTINUATION.match(body) and units and units[-1].zone == zone:
            # wrapped continuation of the previous bullet, e.g. "(is a bonus)"
            units[-1] = Unit(units[-1].text + " " + body, zone, units[-1].heading, source_field)
            continue
        if not h and zone in ("required", "preferred") and _BOILERPLATE.search(body):
            zone, heading_text = "other", "closing boilerplate"
        for sentence in _SENTENCE_SPLIT.split(body):
            sentence = sentence.strip()
            if sentence:
                units.append(Unit(sentence, zone, heading_text, source_field))
    return units, headings


def analyze(description_raw: str, requirements_raw: str) -> SectionAnalysis:
    units: list[Unit] = []
    req_units, req_headings = segment(requirements_raw or "", "requirements_raw", default_zone="required")
    desc_units, desc_headings = segment(description_raw or "", "description_raw", default_zone="unsectioned")
    units.extend(req_units)
    units.extend(desc_units)

    has_source_field = any(u.zone in ("required", "preferred") for u in req_units)
    desc_section_units = [u for u in desc_units if u.zone in ("required", "preferred")]
    has_desc_headings = bool(desc_section_units)

    if has_source_field and has_desc_headings:
        source = "both"
    elif has_source_field:
        source = "source_field"
    elif has_desc_headings:
        source = "description_heading"
    else:
        source = "none"

    return SectionAnalysis(
        units=units,
        required_headings=req_headings["required"] + desc_headings["required"],
        preferred_headings=req_headings["preferred"] + desc_headings["preferred"],
        other_headings=req_headings["other"] + desc_headings["other"],
        mixed_headings=req_headings["mixed"] + desc_headings["mixed"],
        requirements_section_raw="\n".join(u.text for u in units if u.zone == "required"),
        preferred_section_raw="\n".join(u.text for u in units if u.zone == "preferred"),
        status="DETECTED" if source != "none" else "UNKNOWN",
        source=source,
    )
