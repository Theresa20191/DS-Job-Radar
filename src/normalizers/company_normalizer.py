import re

LEGAL_SUFFIXES = [
    "sdn. bhd.", "sdn bhd", "pte. ltd.", "pte ltd", "private limited",
    "berhad", "corporation", "limited", "llc", "inc.", "inc", "ltd.", "ltd", "corp",
]
NON_WORD_RE = re.compile(r"[^a-z0-9\s&-]")
WHITESPACE_RE = re.compile(r"\s+")


def normalize_company(name: str) -> str:
    """Lowercased, legal-suffix-stripped form used for dedup matching."""
    t = (name or "").lower().strip()
    for suffix in LEGAL_SUFFIXES:
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    t = NON_WORD_RE.sub("", t)
    t = WHITESPACE_RE.sub(" ", t).strip()
    return t
