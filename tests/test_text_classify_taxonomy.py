"""Taxonomy loading & validation tests."""

from pathlib import Path

import pytest

from text_classify.taxonomy import load_taxonomy

FIXTURE = Path(__file__).parent / "fixtures" / "text_classify" / "taxonomy.yaml"


def test_load_taxonomy_basic() -> None:
    tax = load_taxonomy(FIXTURE)
    assert tax.name == "test_taxonomy"
    assert tax.version == 1
    assert tax.default_threshold == 0.2
    assert len(tax.categories) == 2


def test_iter_sub_categories_order() -> None:
    tax = load_taxonomy(FIXTURE)
    pairs = list(tax.iter_sub_categories())
    assert len(pairs) == 4
    # Declaration order is preserved
    assert pairs[0][1].id == "inadequate_design"
    assert pairs[-1][1].id == "supply_delay"


def test_seed_keywords_loaded_as_tuple() -> None:
    tax = load_taxonomy(FIXTURE)
    inadequate = next(s for _, s in tax.iter_sub_categories() if s.id == "inadequate_design")
    assert isinstance(inadequate.seed_keywords, tuple)
    assert "incomplete design" in inadequate.seed_keywords


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_taxonomy(tmp_path / "nope.yaml")


def test_duplicate_subcategory_id_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
name: bad
categories:
  - id: a
    label: A
    description: x
    sub_categories:
      - id: x
        label: X
        description: x
      - id: x
        label: X again
        description: x
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate sub_category id"):
        load_taxonomy(p)


def test_construction_taxonomy_example_loads() -> None:
    """The shipped construction example must parse cleanly."""
    repo_root = Path(__file__).resolve().parents[1]
    p = repo_root / "config" / "taxonomies" / "construction_root_cause.example.yaml"
    tax = load_taxonomy(p)
    cat_ids = [c.id for c in tax.categories]
    assert "poor_planning_design" in cat_ids
    assert "poor_quality_culture" in cat_ids
    # All categories must have at least one sub-category
    for cat in tax.categories:
        assert len(cat.sub_categories) >= 1
