"""domain_alignment — the D2 domain-evidence placement layer
(frozen: PTF-DR-002, PTF-ID-002, D2-a/b/c, J-4, J-6).

Known iff at least one QUALIFYING overlap between a profile domain term
and the posting exists; otherwise UNKNOWN (settled — the posting has been
read in full and the profile's domain list is flat). Known-zero is not
produced: no job-domain vocabulary exists.

Each mention is placed, sentence by sentence:
  1. non-evidence   benefits, equal-opportunity boilerplate, investors /
                    funding, "any industry welcome", partner-function
                    boilerplate, non-domain senses of ambiguous terms
  2. generic terms  (Risk, Credit, Insurance, Payments) qualify only in a
                    central placement; a generic peripheral mention is
                    non-evidence, so generic terms never produce LIMITED
  3. placement      defining (job title / team identity)   -> CORE
                    own work (task / responsibility)        -> SUBSTANTIVE
                    peripheral (employer background / client industry /
                    alternative-industry lists / preferred exposure) -> LIMITED
  4. band           the highest qualifying placement; evidence count never
                    raises it

"Risk" (D2-b, narrow R2) qualifies only when its own sentence establishes
fraud / abuse / account-or-platform integrity / financial-risk context, OR
a qualifying risk-control context is established at job level AND the
mention is central (title, team identity, task). Bare "risk", bare "risk
control(s)", bare "integrity", enterprise risk, risk governance, unrelated
risk advisory and AI-model risk/guardrails never qualify on their own.

Word-boundary matching and AML disambiguation are carried over unchanged
from the retired domain_fit module (naive substring matching matched AML
inside "seamless"; bare "AML" collides with "Applied Machine Learning").

Known limitation: matching is term-level, so it cannot tell sub-domains
apart (e.g. "KYC" identity verification vs transaction monitoring both
count as AML). Placement for sentences written in company voice ("we
build ...") is treated as employer background, not own work.
"""

import html
import re

from src.enrichment.requirements_sections import analyze
from src.models.ptf import DimensionResult, DomainEvidence
from src.scoring.ptf_scale import BAND_VALUES, band_rank

GENERIC_TERMS = frozenset({"Risk", "Credit", "Insurance", "Payments"})               # D2-a
SPECIFIC_TERMS = frozenset({"AML", "Fraud", "Financial Crime", "Credit Risk", "Fintech", "Banking"})

PLACEMENT_BAND = {"peripheral": "LIMITED", "own_work": "SUBSTANTIVE", "defining": "CORE"}

# --- term matching (carried over from domain_fit) ----------------------------

AML_STRONG_PHRASES = [
    "anti-money laundering", "anti money laundering", "aml compliance",
    "aml monitoring", "aml policy", "aml program", "aml regulation",
    "aml analyst", "aml investigation", "financial crime",
    "transaction monitoring", "know your customer", r"\bkyc\b",
    "sanctions screening", "suspicious activity report",
]
_AML_STRONG_RE = [re.compile(p, re.I) for p in AML_STRONG_PHRASES]
_AML_BARE_RE = re.compile(r"\baml\b", re.I)
_AML_FALSE_POSITIVE_CONTEXT_RE = re.compile(r"machine\s+learning", re.I)
_AML_CONTEXT_WINDOW = 60


def _is_aml_match(text: str) -> bool:
    if any(p.search(text) for p in _AML_STRONG_RE):
        return True
    for m in _AML_BARE_RE.finditer(text):
        window = text[max(0, m.start() - _AML_CONTEXT_WINDOW): m.end() + _AML_CONTEXT_WINDOW]
        if _AML_FALSE_POSITIVE_CONTEXT_RE.search(window):
            continue  # "Applied Machine Learning ... (AML)" — not Anti-Money-Laundering
        return True
    return False


def term_matches(term: str, text: str) -> bool:
    if term.strip().upper() == "AML":
        return _is_aml_match(text)
    return bool(re.search(r"\b" + re.escape(term.lower()) + r"\b", text, re.I))


# --- D2-c non-evidence ------------------------------------------------------------

_NON_EVIDENCE = [
    ("benefits", re.compile(r"\bbenefits\b|\bmaternity\b|\bwellbeing\b|\bperks?\b|medical and insurance", re.I)),
    ("equal_opportunity", re.compile(r"equal (employment )?opportunit|regardless of|national origin|without regard", re.I)),
    ("investor_funding", re.compile(r"\bfunding\b|\binvest(ment|or)s? from\b|\bbacked by\b", re.I)),
    ("any_industry_welcome", re.compile(r"\bany\b[^.]{0,40}\bindustr(y|ies)\b[^.]{0,20}\bwelcome", re.I)),
    ("partner_function", re.compile(r"\bpartner(ing)? with\b[^.]{0,40}\b(legal|compliance)\b", re.I)),
]
_OTHER_SENSE = {
    "Payments": re.compile(r"accounts payable|payment terms|payroll", re.I),
    "Insurance": re.compile(r"insurance benefits|medical and insurance", re.I),
    "Credit": re.compile(r"credit to\b|course credit|\bcredits\b", re.I),
}

# --- D2-b narrow R2 ---------------------------------------------------------------
# Bare "risk control(s)" and bare "integrity" are deliberately NOT here.
_RISK_QUALIFYING = re.compile(
    r"fraud|abuse|(account|platform|community) integrity|inauthentic|anti[- ]?spam|scam|money laundering|"
    r"\baml\b|\bkyc\b|financial crime|credit risk|market risk|\bloan|lending", re.I)
_RISK_EXCLUDED = re.compile(
    r"enterprise risk|risk governance|governance|risk management|advisory|mitigation|"
    r"bias/risk|risk minimi[sz]ation|risk identification|risk, issue", re.I)

# --- placement --------------------------------------------------------------------
_TEAM_IDENTITY = r"[\w\s&/()\-]{0,45}?\b(team|department|division|unit)\b"
_TASK = re.compile(r"^(you will|as a part of|develop|build|analy[sz]e|define|quantify|monitor|detect|identify|combat|"
                   r"handl|design|lead|drive|implement|own|align|investigate|model)|\byou will\b", re.I)
_COMPANY_VOICE = re.compile(r"\b(we|our)\b", re.I)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(text or ""))).strip()


def _sentences(job):
    """(field, zone, sentence) for title, company name, and every unit the
    evidence-layer section detector produces."""
    yield "job_title", "title", _clean(job.job_title)
    yield "company_normalized", "company", _clean(job.company_normalized)
    for unit in analyze(job.description_raw or "", job.requirements_raw or "").units:
        yield unit.source_field, unit.zone, _clean(unit.text)


def _term_pattern(term: str) -> str:
    return "kyc|aml|anti-money" if term == "AML" else re.escape(term)


def _placement(term: str, zone: str, text: str) -> tuple[str, str]:
    if zone == "title":
        return "defining", "job_title"
    if re.search(rf"(?:{_term_pattern(term)}){_TEAM_IDENTITY}", text, re.I):
        return "defining", "team_identity"
    if zone in ("required", "preferred", "mixed"):
        return "peripheral", "qualifications_industry_exposure"
    if zone == "company":
        return "peripheral", "employer_identity"
    if _TASK.search(text) and not _COMPANY_VOICE.search(text):
        return "own_work", "task_statement"
    return "peripheral", "employer_background_or_unsubstantiated"


def place_evidence(job, terms: list[str]) -> list[DomainEvidence]:
    sentences = list(_sentences(job))
    risk_context = next(
        (t for _, _, t in sentences
         if re.search(r"\brisk\b", t, re.I) and _RISK_QUALIFYING.search(t) and not _RISK_EXCLUDED.search(t)),
        None,
    )
    items: list[DomainEvidence] = []
    for field, zone, text in sentences:
        for term in terms:
            if not text or not term_matches(term, text):
                continue
            base = dict(term=term, field=field, sentence=text[:240],
                        classification="generic" if term in GENERIC_TERMS else "specific")

            non_evidence = next((name for name, rx in _NON_EVIDENCE if rx.search(text)), None)
            if non_evidence:
                items.append(DomainEvidence(**base, placement="non_evidence", rule=non_evidence))
                continue
            if term == "Risk" and _RISK_EXCLUDED.search(text) and not _RISK_QUALIFYING.search(text):
                items.append(DomainEvidence(**base, placement="non_evidence", rule="risk_excluded_sense",
                                            risk_rule="R2-narrow: excluded sense"))
                continue
            if term in _OTHER_SENSE and _OTHER_SENSE[term].search(text):
                items.append(DomainEvidence(**base, placement="non_evidence", rule="non_domain_sense"))
                continue

            placement, rule = _placement(term, zone, text)
            if base["classification"] == "generic" and placement == "peripheral":
                items.append(DomainEvidence(**base, placement="non_evidence", rule="generic_peripheral"))
                continue

            risk_rule = None
            if term == "Risk":
                if _RISK_QUALIFYING.search(text):
                    risk_rule = "R2-narrow: fraud/abuse/integrity/financial-risk context in sentence"
                elif risk_context:
                    risk_rule = f"R2-narrow: central mention; job-level risk-control context: '{risk_context[:80]}'"
                else:
                    items.append(DomainEvidence(**base, placement="non_evidence", rule="risk_no_qualifying_context",
                                                risk_rule="R2-narrow: no qualifying context"))
                    continue
            items.append(DomainEvidence(**base, placement=placement, rule=rule, risk_rule=risk_rule))
    return items


def score(job, profile: dict) -> DimensionResult:
    items = place_evidence(job, profile.get("domain") or [])
    qualifying = [i for i in items if i.placement in PLACEMENT_BAND]
    provenance = [i.model_dump() for i in items]

    if not qualifying:
        return DimensionResult(
            name="domain_alignment", known=False, unknown_reason="no_qualifying_overlap",
            reasons=["No qualifying overlap with your domain background was found in this posting"],
            provenance=provenance,
        )

    deciding = max(qualifying, key=lambda i: band_rank(PLACEMENT_BAND[i.placement]))
    band = PLACEMENT_BAND[deciding.placement]
    where = {"job_title": "job title", "team_identity": "team identity",
             "task_statement": "the job's own work"}.get(deciding.rule, deciding.rule.replace("_", " "))
    reasons = [f"Domain {band}: {deciding.term} — {where}: \"{deciding.sentence[:140]}\""]
    others = sorted({i.term for i in qualifying} - {deciding.term})
    if others:
        reasons.append(f"Also mentioned: {', '.join(others)} (does not raise the band)")
    return DimensionResult(name="domain_alignment", known=True, band=band, score=BAND_VALUES[band],
                           reasons=reasons, provenance=provenance)
