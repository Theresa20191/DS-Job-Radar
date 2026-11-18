import re

from bs4 import BeautifulSoup


def clean_html(html: str) -> str:
    """Strips HTML markup to plain text. Does not otherwise rewrite content —
    the raw HTML stays untouched in the raw layer; this only affects the
    processed layer's *_raw text fields."""
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator="\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text
