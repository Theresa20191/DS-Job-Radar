import re

PAREN_RE = re.compile(r"\(.*?\)")
NON_WORD_RE = re.compile(r"[^a-z0-9\s/&-]")
WHITESPACE_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Lowercased, punctuation-light form used for dedup matching and as
    classifier input. Not meant for display."""
    t = (title or "").lower().strip()
    t = PAREN_RE.sub(" ", t)
    t = NON_WORD_RE.sub(" ", t)
    t = WHITESPACE_RE.sub(" ", t).strip()
    return t
