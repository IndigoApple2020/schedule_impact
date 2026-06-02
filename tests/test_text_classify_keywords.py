"""Cross-row keyword discovery tests."""

import csv
from pathlib import Path

import pytest

pytest.importorskip("sklearn")

from text_classify.keyword_discovery import KeywordConfig, discover
from text_classify.taxonomy import load_taxonomy

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "text_classify"
TAX_PATH = FIXTURE_DIR / "taxonomy.yaml"
CSV_PATH = FIXTURE_DIR / "issues_sample.csv"


def _load_rows() -> list[tuple[str, str]]:
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [(r["row_id"], r["root_cause"]) for r in reader]


def test_discover_finds_recurring_phrases() -> None:
    rows = _load_rows()
    # min_doc_count=2 because our fixture is tiny
    keywords = discover(rows, config=KeywordConfig(min_doc_count=2, max_doc_count=10))
    phrases = {k.phrase for k in keywords}
    # "drawing" appears across multiple rows in the fixture
    assert any("drawing" in p for p in phrases)


def test_discover_returns_sample_row_ids() -> None:
    rows = _load_rows()
    keywords = discover(rows, config=KeywordConfig(min_doc_count=2, max_doc_count=10))
    # Each keyword should record at least one sample row_id
    for k in keywords:
        assert len(k.sample_row_ids) >= 1
        assert all(rid.startswith("R") for rid in k.sample_row_ids)


def test_discover_with_taxonomy_suppresses_seed_phrases() -> None:
    rows = _load_rows()
    tax = load_taxonomy(TAX_PATH)
    keywords = discover(
        rows,
        config=KeywordConfig(min_doc_count=2, max_doc_count=10),
        taxonomy=tax,
    )
    phrases = {k.phrase for k in keywords}
    # "design" is a category seed — should be suppressed
    assert "design" not in phrases


def test_discover_returns_empty_on_empty_corpus() -> None:
    keywords = discover([], config=KeywordConfig(min_doc_count=2))
    assert keywords == []
