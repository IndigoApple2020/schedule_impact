"""Strip HTML from Primavera TASKMEMO.task_memo fields."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        return " ".join(self._parts)


# P6 / MSHTML exports sometimes embed 0x7F between tags
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_html_memo(raw: str | None) -> str:
    """Return plain text from a TASKMEMO HTML body."""
    if not raw:
        return ""
    cleaned = _CONTROL_CHARS.sub(" ", raw.strip())
    cleaned = html.unescape(cleaned)
    parser = _TextExtractor()
    try:
        parser.feed(cleaned)
        parser.close()
    except Exception:
        # Fallback: crude tag removal
        text = re.sub(r"<[^>]+>", " ", cleaned)
        return " ".join(text.split())
    return " ".join(parser.text().split())
