"""Text preprocessing for the text_classify pipeline."""

from __future__ import annotations

import re

# Strip control chars and most punctuation, but keep hyphens and slashes
# (preserve codes like ITP-123 / RFI/45).
_NORMALISE_PUNCT = re.compile(r"[^\w\s\-/]")
_WHITESPACE = re.compile(r"\s+")


def normalise(text: str | None) -> str:
    """Lightweight normalisation: lower, strip punct (keep hyphens/slashes), collapse whitespace.

    Preserves alphanumeric / hyphenated codes so downstream tokenisers can keep
    them as single tokens.
    """
    if not text:
        return ""
    t = str(text).lower()
    t = _NORMALISE_PUNCT.sub(" ", t)
    t = _WHITESPACE.sub(" ", t).strip()
    return t


def normalise_many(texts: list[str | None]) -> list[str]:
    return [normalise(t) for t in texts]
