"""Keyword-based quality scoring."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_CONFIG = Path(__file__).resolve().parents[3] / "config" / "quality_keywords.yaml"


@lru_cache(maxsize=1)
def _load_keywords() -> tuple[list[str], list[str]]:
    if not _CONFIG.is_file():
        return [], []
    data = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}
    cats = data.get("categories", {})
    return list(cats.get("quality", [])), list(cats.get("not_quality", []))


def keyword_quality_score(text: str) -> tuple[float, list[str]]:
    """Return score 0–1 and matched quality keywords."""
    if not text.strip():
        return 0.0, []
    lower = text.lower()
    quality, not_quality = _load_keywords()
    hits = [k for k in quality if k.lower() in lower]
    down = [k for k in not_quality if k.lower() in lower]
    if not hits and not down:
        return 0.0, []
    score = len(hits) / (len(hits) + len(down) + 1)
    if hits:
        score = min(1.0, 0.4 + 0.15 * len(hits))
    if down:
        score = max(0.0, score - 0.2 * len(down))
    return score, hits
