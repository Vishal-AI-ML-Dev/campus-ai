"""Untrusted-text handling for the interview engine.

Resume Markdown, job descriptions and candidate answers are DATA. They are
sanitized, truncated and wrapped in tags that the system prompt declares as
data-only, and anything the model claims about them (grounding quotes,
evidence quotes) is verified against the original text in code.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Our own wrapper tags; any look-alike inside untrusted text is neutralized so
# the content cannot "close" its data block and start issuing instructions.
DATA_TAGS = (
    "resume",
    "job_description",
    "candidate_answer",
    "verified_profile",
    "context_note",
    # Phase 8.3 (coding review): problem text, the candidate's code and their
    # typed explanation. Code is read as data and never executed.
    "problem_statement",
    "candidate_code",
    "candidate_explanation",
    # Phase 8.4: institute-authored company/role context (untrusted data).
    "company_context",
)
_TAG_RE = re.compile(
    r"</?\s*(" + "|".join(DATA_TAGS) + r"|system|assistant|user|instructions?|data)\b[^>]*>?",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"\s+")


def _strip_invisible(text: str) -> str:
    """Drop format characters (zero-width spaces/joiners, bidi marks...).

    They survive NFKC and could hide a tag name from the regex below.
    """
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def clean_text(text: str | None, max_chars: int) -> str:
    """Strip control/invisible chars and look-alike wrapper tags; truncate."""
    if not text:
        return ""
    text = _strip_invisible(unicodedata.normalize("NFKC", text))
    text = _CONTROL_RE.sub("", text)
    text = _TAG_RE.sub("[removed-tag]", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n[truncated]"
    return text


def wrap(tag: str, text: str) -> str:
    """Wrap sanitized text in a data tag (tag must be one of DATA_TAGS)."""
    assert tag in DATA_TAGS
    body = text if text else "(none provided)"
    return f"<{tag}>\n{body}\n</{tag}>"


def normalize(text: str) -> str:
    """Lower-case, collapse whitespace and drop Markdown emphasis for matching."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = re.sub(r"[*_`#>|]", " ", text)
    return _SPACE_RE.sub(" ", text).strip()


def quote_in(quote: str, source: str, *, min_len: int = 4) -> bool:
    """True when ``quote`` occurs (normalized) in ``source``."""
    q = normalize(quote)
    if len(q) < min_len:
        return False
    return q in normalize(source)


def is_similar(a: str, b: str, threshold: float = 0.85) -> bool:
    """Near-duplicate check for questions (case/space-insensitive)."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def single_line(text: str, max_chars: int) -> str:
    """Collapse whitespace, drop tags and cap length (short one-line fields)."""
    text = clean_text(text, max_chars * 2)
    text = _SPACE_RE.sub(" ", text).strip()
    return text[:max_chars]


# Job role / company names: letters (any script), digits, spaces and a little
# punctuation. Everything else is dropped, so these short fields can sit in
# the system prompt without carrying instructions or markup.
_LABEL_DROP = re.compile(r"[^\w .,&()/+#'-]", re.UNICODE)


def safe_label(text: str | None, max_chars: int) -> str:
    text = _LABEL_DROP.sub(" ", clean_text(text, max_chars * 2))
    return _SPACE_RE.sub(" ", text).strip()[:max_chars]
