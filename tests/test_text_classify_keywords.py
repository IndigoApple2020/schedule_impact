"""Cross-row keyword discovery tests."""

import csv
from pathlib import Path

import pytest

pytest.importorskip("sklearn")

from text_classify.keyword_discovery import KeywordConfig, discover, discover_stratified
from text_classify.schemas import RowScore
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


def test_stratified_groups_by_top_category() -> None:
    rows = _load_rows()
    # Hand-build summaries: 6 rows tagged design, 4 tagged materials
    design_ids = {"R001", "R003", "R005", "R006", "R008", "R009"}
    summaries = [
        RowScore(
            row_id=rid,
            top_category_id="design" if rid in design_ids else "materials",
            top_sub_category_id="x",
            top_score=0.5,
            n_matches_above_threshold=1,
            match_summary="",
        )
        for rid, _ in rows
    ]
    out = discover_stratified(
        rows,
        summaries,
        base_config=KeywordConfig(min_doc_count=2, max_doc_count=10),
        top_n_per_category=10,
        min_rows_per_category=3,
    )
    # Both categories should produce some recurring phrases
    assert "design" in out
    assert "materials" in out
    # Each category's keywords should reflect its own corpus
    design_phrases = {k.phrase for k in out["design"]}
    materials_phrases = {k.phrase for k in out["materials"]}
    # "drawing" is common in design rows
    assert any("drawing" in p for p in design_phrases)


def test_stratified_skips_small_categories() -> None:
    rows = _load_rows()
    # Only 2 rows tagged design — below default min_rows_per_category=5
    summaries = [
        RowScore(
            row_id=rid,
            top_category_id="design" if rid in {"R001", "R003"} else "materials",
            top_sub_category_id="x",
            top_score=0.5,
            n_matches_above_threshold=1,
            match_summary="",
        )
        for rid, _ in rows
    ]
    out = discover_stratified(
        rows,
        summaries,
        base_config=KeywordConfig(min_doc_count=2, max_doc_count=10),
        min_rows_per_category=5,
    )
    assert "design" not in out
