"""Dataclasses shared across the text_classify pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class SubCategory:
    id: str
    label: str
    description: str
    seed_keywords: tuple[str, ...]


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    description: str
    seed_keywords: tuple[str, ...]
    sub_categories: tuple[SubCategory, ...]


@dataclass(frozen=True)
class Taxonomy:
    name: str
    version: int
    description: str
    default_threshold: float
    categories: tuple[Category, ...]

    def iter_sub_categories(self):
        """Yield (Category, SubCategory) pairs in declaration order."""
        for cat in self.categories:
            for sub in cat.sub_categories:
                yield cat, sub


Method = Literal["tfidf", "llm_embed", "llm_prompt"]


@dataclass
class MatchRecord:
    """One (row_id, category, sub_category) match above threshold."""

    row_id: str
    category_id: str
    sub_category_id: str
    score: float
    method: Method
    signals: str = ""             # semicolon-delimited matched terms / rationale
    taxonomy_version: int = 0
    run_id: str = ""


@dataclass
class RowScore:
    """Wide per-row summary."""

    row_id: str
    top_category_id: str | None
    top_sub_category_id: str | None
    top_score: float
    n_matches_above_threshold: int
    match_summary: str            # "cat1:0.62; cat2:0.41"


@dataclass
class KeywordRecord:
    """One discovered cross-row keyword/phrase."""

    phrase: str
    doc_count: int
    mean_tfidf: float
    interestingness: float
    sample_row_ids: list[str] = field(default_factory=list)
