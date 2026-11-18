"""HC-2 work-authorization classification — explicit JOB-side foreigner
ineligibility only.

Semantic test: does the posting explicitly deny eligibility to foreign
applicants? Nothing about the candidate is compared or inferred — there
is no candidate citizenship/work-authorization field and none is read.

  FAIL    an explicit exclusion ("Malaysian citizens only", "No
          foreigners", "Foreign nationals are not eligible", ...) AND no
          statement that foreign/non-national applicants may be eligible.
  PASS    explicit openness to foreign applicants ("open to all
          nationalities", "not limited to Malaysians", "foreigners
          encouraged to apply", ...) and no exclusion.
  UNKNOWN everything else — always eligible:
            - restriction/preference language that does NOT deny all
              foreign applicants: "no visa sponsorship", "must have a
              valid work permit/pass", "SC/PR only", "local candidates
              only" (local may mean residence), "Malaysians preferred";
            - contradictory evidence (an exclusion plus a statement that
              non-nationals may apply) — both pieces of evidence kept;
            - no relevant language at all.

Nothing is inferred from location, company, salary currency, language,
title wording, employer nationality, or absence of sponsorship. Matching
is phrase-level with negation guards, never bare-word presence: "not
limited to Malaysians" and "foreigners encouraged" must not trip an
exclusion. Equal-opportunity boilerplate ("regardless of nationality /
national origin") is neither openness nor restriction.
"""

import html
import re
from typing import NamedTuple

from src.models.work_authorization_evidence import WorkAuthorizationEvidence

FOREIGNERS_NOT_ACCEPTED = "FOREIGNERS_NOT_ACCEPTED"
NATIONALITY_SPECIFIC = "NATIONALITY_SPECIFIC"
OTHER_EXPLICIT_RESTRICTION = "OTHER_EXPLICIT_RESTRICTION"
NONE = "NONE"
UNKNOWN = "UNKNOWN"
EXCLUSION_TYPES = (FOREIGNERS_NOT_ACCEPTED, NATIONALITY_SPECIFIC)

# Internal signal kinds. Conditional openness ("non-Malaysians with a
# valid work permit are eligible") is reported as OTHER_EXPLICIT_RESTRICTION
# but still contradicts an exclusion, so it needs its own kind here.
_EXCL_FOREIGN, _EXCL_NATIONALITY, _OPEN, _COND_OPEN, _RESTRICTION, _PREFERENCE = range(6)
_PUBLIC_TYPE = {
    _EXCL_FOREIGN: FOREIGNERS_NOT_ACCEPTED,
    _EXCL_NATIONALITY: NATIONALITY_SPECIFIC,
    _OPEN: NONE,
    _COND_OPEN: OTHER_EXPLICIT_RESTRICTION,
    _RESTRICTION: OTHER_EXPLICIT_RESTRICTION,
    _PREFERENCE: UNKNOWN,
}

SOURCE_FIELDS = ("job_title", "requirements_raw", "description_raw")
_CONTEXT_CHARS = 90

_DEMONYM = r"(?:malaysians?|singaporeans?)"
_NATION = r"(?:malaysian|malaysia|singaporean|singapore)"
_NATION_OR_LOCAL = r"(?:malaysian|malaysia|singaporean|singapore|local)"
_CITIZEN = r"(?:citizens?|nationals?|citizenship)"
_FOREIGN = r"(?:foreigners?|foreign\s+(?:nationals?|applicants?|candidates?|workers?|students?))"
_NON_NATIONAL = r"non[-\s]?(?:malaysians?|singaporeans?|citizens?|locals?)"
_ACCEPT = r"(?:welcome[sd]?|eligible|encouraged?|accepted|invited|may\s+apply|can\s+apply|are\s+free\s+to\s+apply)"
_CONDITION = r"(?:valid\s+work|work\s+(?:permit|pass|visa)|employment\s+pass|visa|permit|right\s+to\s+work)"

_EXCLUSION_PATTERNS = [
    (_EXCL_FOREIGN, re.compile(r"\bno\s+" + _FOREIGN + r"\b", re.I)),
    (_EXCL_FOREIGN, re.compile(
        r"\b" + _FOREIGN + r"\s+(?:are\s+|is\s+|will\s+)?(?:not|ineligible)\b"
        r"(?:\s+(?:be\s+)?(?:accepted|eligible|allowed|considered|entertained|permitted))?", re.I)),
    (_EXCL_FOREIGN, re.compile(r"\bnot\s+open\s+to\s+" + _FOREIGN + r"\b", re.I)),
    (_EXCL_FOREIGN, re.compile(r"\b" + _FOREIGN + r"\s+need\s+not\s+apply\b", re.I)),
    # "Malaysian citizens only", "Local Malaysian citizens only", "Local citizens only"
    (_EXCL_NATIONALITY, re.compile(
        r"\b(?:local\s+)?" + _NATION_OR_LOCAL + r"\s+" + _CITIZEN + r"\s+only\b", re.I)),
    # "Only Malaysian citizens", "only local citizens"
    (_EXCL_NATIONALITY, re.compile(
        r"\bonly\s+(?:local\s+)?" + _NATION_OR_LOCAL + r"\s+" + _CITIZEN + r"\b", re.I)),
    # "Malaysians only", "open to Singaporeans only"
    (_EXCL_NATIONALITY, re.compile(r"\b" + _DEMONYM + r"\s+only\b", re.I)),
    # "Malaysian candidates only" (NOT "local candidates only" — local may mean residence)
    (_EXCL_NATIONALITY, re.compile(r"\b(?:local\s+)?" + _NATION + r"\s+(?:candidates?|applicants?)\s+only\b", re.I)),
    # "Must be a Malaysian citizen", "Applicants must be Malaysian citizens"
    (_EXCL_NATIONALITY, re.compile(
        r"\b(?:(?:applicants?|candidates?)\s+)?must\s+be\s+(?:an?\s+)?" + _NATION_OR_LOCAL + r"\s+" + _CITIZEN + r"\b", re.I)),
    # "Malaysian nationality required", "Malaysian citizenship is mandatory"
    (_EXCL_NATIONALITY, re.compile(
        r"\b" + _NATION + r"\s+(?:citizenship|nationality)\s+(?:is\s+)?(?:required|mandatory|compulsory)\b", re.I)),
]

_OPENNESS_PATTERNS = [
    re.compile(r"\bopen\s+to\s+all\s+nationalit(?:y|ies)\b", re.I),
    re.compile(r"\ball\s+nationalities\s+(?:are\s+)?(?:welcome|eligible|may\s+apply|can\s+apply)\b", re.I),
    re.compile(r"\bvisa\s+sponsorship\s+(?:is\s+)?(?:available|provided|offered)\b", re.I),
    re.compile(r"\bwill\s+sponsor\b", re.I),
    re.compile(r"\bsponsorship\s+(?:is\s+)?provided\b", re.I),
    re.compile(r"\bnot\s+(?:limited|restricted)\s+to\s+" + _DEMONYM + r"\b", re.I),
    re.compile(r"\bnot\s+(?:limited|restricted)\s+to\s+" + _NATION + r"\s+" + _CITIZEN + r"\b", re.I),
]

# Subject + acceptance statements ("Non-Malaysians are welcome", "Foreigner
# students encourage to apply", "open to ... non-Malaysians"), classified as
# openness or conditional openness depending on whether the sentence states
# a condition (work permit, pass, visa).
_ACCEPTING_SUBJECT_RE = re.compile(r"\b(?:" + _FOREIGN + r"|" + _NON_NATIONAL + r")\b", re.I)
_ACCEPT_AFTER_RE = re.compile(r"^[^.;!?]{0,80}?\b" + _ACCEPT + r"\b", re.I)
_OPEN_TO_BEFORE_RE = re.compile(r"\bopen\s+to\b[^.;!?]{0,80}$", re.I)
_NEGATION_RE = re.compile(r"\b(?:not|no|never|ineligible|cannot|can't)\b", re.I)
_CONDITION_RE = re.compile(r"\b" + _CONDITION, re.I)

_RESTRICTION_PATTERNS = [
    re.compile(r"\bno\s+(?:visa\s+)?sponsorships?\b", re.I),
    re.compile(r"\b(?:visa\s+)?sponsorship\s+(?:is\s+)?(?:not\s+(?:available|provided|offered)|unavailable)\b", re.I),
    re.compile(r"\b(?:unable|not\s+able)\s+to\s+(?:provide\s+)?(?:visa\s+)?sponsor(?:ship)?\b", re.I),
    re.compile(r"\bmust\s+(?:have|hold|possess)\s+(?:an?\s+)?(?:valid\s+)?(?:work\s+(?:permit|pass|visa|authori[sz]ation)|employment\s+pass|right\s+to\s+work)\b", re.I),
    re.compile(r"\b(?:existing|valid|current)\s+(?:work\s+(?:authori[sz]ation|permit|pass|visa)|right\s+to\s+work)\s+(?:is\s+)?required\b", re.I),
    re.compile(r"\b(?:legally\s+)?(?:eligible|authori[sz]ed)\s+to\s+work\s+in\b", re.I),
    re.compile(r"\bSC\s*/\s*PRs?\b(?:\s+only)?", re.I),
    re.compile(r"\b(?:" + _NATION + r"\s+)?" + r"(?:citizens?|" + _DEMONYM + r")\s*(?:and|or|/|&)\s*(?:PRs?|permanent\s+residents?)\b(?:\s+only)?", re.I),
    re.compile(r"\blocal\s+(?:candidates?|applicants?)\s+only\b", re.I),
]

# A preference must be about a PERSON category ("local candidates",
# "Malaysian PR", "Singaporeans") — never "local language proficiency is
# preferred", which is a language requirement, not work-authorization.
_PERSON = r"(?:candidates?|applicants?|citizens?|nationals?|PRs?|permanent\s+residents?|talents?|hires?)"
_PREFERRED_GROUP = r"(?:(?:local|" + _NATION + r")\s+" + _PERSON + r"|" + _DEMONYM + r")"
_PREFERENCE_PATTERNS = [
    re.compile(r"\b" + _PREFERRED_GROUP + r"\s+(?:are\s+|is\s+|will\s+be\s+)?(?:highly\s+|strongly\s+)?preferred\b", re.I),
    re.compile(r"\bpreference\s+(?:will\s+be\s+)?given\s+to\s+" + _PREFERRED_GROUP + r"\b", re.I),
]

# Priority when two matches overlap the same text: the more decisive one wins.
_PRIORITY = {_EXCL_FOREIGN: 0, _EXCL_NATIONALITY: 0, _OPEN: 1, _COND_OPEN: 1, _RESTRICTION: 2, _PREFERENCE: 3}


class WorkAuthorizationResult(NamedTuple):
    status: str
    restriction_type: str
    reason: str
    evidence: list[WorkAuthorizationEvidence]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(text or ""))).strip()


def _context(text: str, start: int, end: int) -> str:
    lo, hi = max(0, start - _CONTEXT_CHARS), min(len(text), end + _CONTEXT_CHARS)
    return ("…" if lo > 0 else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")


def _negated(text: str, start: int) -> bool:
    """A preceding "not"/"non-" turns an apparent exclusion into its
    opposite ("not only Malaysian citizens", "non-Malaysians only")."""
    before = text[max(0, start - 25):start].lower()
    return bool(re.search(r"\bnot\s+(?:\w+\s+)?$|non[-\s]?$", before))


def _find_signals(text: str) -> list[tuple[int, int, int]]:
    """(kind, start, end) for every signal in one cleaned text."""
    found: list[tuple[int, int, int]] = []

    for kind, rx in _EXCLUSION_PATTERNS:
        for m in rx.finditer(text):
            if not _negated(text, m.start()):
                found.append((kind, m.start(), m.end()))

    for rx in _OPENNESS_PATTERNS:
        found.extend((_OPEN, m.start(), m.end()) for m in rx.finditer(text))

    for m in _ACCEPTING_SUBJECT_RE.finditer(text):
        after = _ACCEPT_AFTER_RE.match(text[m.end():])
        before = _OPEN_TO_BEFORE_RE.search(text[max(0, m.start() - 90):m.start()])
        if after and not _NEGATION_RE.search(after.group(0)):
            start, end = m.start(), m.end() + after.end()
        elif before and not _NEGATION_RE.search(before.group(0)):
            start, end = m.start() - len(before.group(0)), m.end()
        else:
            continue
        sentence_end = re.search(r"[.;!?]|$", text[end:]).start() + end
        kind = _COND_OPEN if _CONDITION_RE.search(text[start:sentence_end]) else _OPEN
        found.append((kind, start, sentence_end if kind == _COND_OPEN else end))

    for rx in _RESTRICTION_PATTERNS:
        found.extend((_RESTRICTION, m.start(), m.end()) for m in rx.finditer(text))
    for rx in _PREFERENCE_PATTERNS:
        found.extend((_PREFERENCE, m.start(), m.end()) for m in rx.finditer(text))

    # Drop matches overlapping a more decisive one (e.g. the "Malaysian
    # PR" inside "Malaysian PR preferred" is a preference, not a separate
    # restriction), and exact duplicates.
    found.sort(key=lambda s: (_PRIORITY[s[0]], s[1]))
    kept: list[tuple[int, int, int]] = []
    for kind, start, end in found:
        if any(start < k_end and k_start < end for _, k_start, k_end in kept):
            continue
        kept.append((kind, start, end))
    return sorted(kept, key=lambda s: s[1])


def _quote(ev: WorkAuthorizationEvidence) -> str:
    return f"'{ev.matched_text}' ({ev.source_field})"


def classify_work_authorization(job) -> WorkAuthorizationResult:
    signals: list[tuple[int, WorkAuthorizationEvidence]] = []
    for field in SOURCE_FIELDS:
        text = _clean(getattr(job, field, "") or "")
        for kind, start, end in _find_signals(text):
            signals.append((kind, WorkAuthorizationEvidence(
                matched_text=text[start:end],
                restriction_type=_PUBLIC_TYPE[kind],
                source_field=field,
                context=_context(text, start, end),
            )))

    evidence = [ev for _, ev in signals]
    exclusions = [ev for kind, ev in signals if kind in (_EXCL_FOREIGN, _EXCL_NATIONALITY)]
    openness = [ev for kind, ev in signals if kind in (_OPEN, _COND_OPEN)]
    unconditional_open = [ev for kind, ev in signals if kind == _OPEN]
    restrictions = [ev for kind, ev in signals if kind in (_COND_OPEN, _RESTRICTION)]

    if exclusions and openness:
        return WorkAuthorizationResult(
            "UNKNOWN", UNKNOWN,
            f"Posting contains contradictory citizenship/work-authorization statements "
            f"({_quote(exclusions[0])} vs {_quote(openness[0])}) — not treated as an exclusion; "
            f"verify your own eligibility",
            evidence,
        )
    if exclusions:
        first = next((ev for ev in exclusions if ev.restriction_type == FOREIGNERS_NOT_ACCEPTED), exclusions[0])
        return WorkAuthorizationResult(
            "FAIL", first.restriction_type,
            f"Posting explicitly denies eligibility to foreign applicants ({first.restriction_type}): "
            f"{_quote(first)} — \"{first.context}\"",
            evidence,
        )
    if unconditional_open:
        return WorkAuthorizationResult(
            "PASS", NONE,
            f"Posting explicitly states openness to foreign applicants: {_quote(unconditional_open[0])}",
            evidence,
        )
    if restrictions:
        return WorkAuthorizationResult(
            "UNKNOWN", OTHER_EXPLICIT_RESTRICTION,
            f"Posting mentions a citizenship/work-authorization requirement ({_quote(restrictions[0])}) — "
            f"it does not explicitly exclude foreign applicants; verify your own eligibility",
            evidence,
        )
    if evidence:
        return WorkAuthorizationResult(
            "UNKNOWN", UNKNOWN,
            f"Posting mentions a citizenship/work-authorization preference ({_quote(evidence[0])}) — "
            f"a preference, not an exclusion; verify your own eligibility",
            evidence,
        )
    return WorkAuthorizationResult("UNKNOWN", UNKNOWN, "No explicit work-authorization requirement stated", [])
