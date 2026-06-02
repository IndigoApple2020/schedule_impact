"""TF-IDF scoring tests."""

import csv
from pathlib import Path

import pytest

# Skip the whole module if scikit-learn isn't installed
pytest.importorskip("sklearn")

from text_classify.runner import run_classify
from text_classify.taxonomy import load_taxonomy
from text_classify.tfidf_classifier import TfidfConfig, score_rows

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "text_classify"
TAX_PATH = FIXTURE_DIR / "taxonomy.yaml"
CSV_PATH = FIXTURE_DIR / "issues_sample.csv"


def _load_rows() -> list[tuple[str, str]]:
    with CSV_PATH.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [(r["row_id"], r["root_cause"]) for r in reader]


def test_inadequate_design_matches_design_row() -> None:
    tax = load_taxonomy(TAX_PATH)
    rows = _load_rows()
    matches, _summaries = score_rows(rows, tax, config=TfidfConfig(threshold=0.1, min_df=1))
    # R001 ("Incomplete design...missing key dimensions") should match inadequate_design
    r1 = [m for m in matches if m.row_id == "R001" and m.sub_category_id == "inadequate_design"]
    assert r1, "Expected R001 to match inadequate_design"


def test_faulty_material_matches_material_row() -> None:
    tax = load_taxonomy(TAX_PATH)
    rows = _load_rows()
    matches, _ = score_rows(rows, tax, config=TfidfConfig(threshold=0.1, min_df=1))
    r2 = [m for m in matches if m.row_id == "R002" and m.sub_category_id == "faulty_material"]
    assert r2, "Expected R002 to match faulty_material"


def test_signals_record_matched_seed_words() -> None:
    tax = load_taxonomy(TAX_PATH)
    rows = _load_rows()
    matches, _ = score_rows(rows, tax, config=TfidfConfig(threshold=0.1, min_df=1))
    # The R001 inadequate_design match should report "incomplete design" or similar
    target = next(m for m in matches if m.row_id == "R001" and m.sub_category_id == "inadequate_design")
    assert target.signals, "Expected non-empty signals for a strong match"


def test_run_classify_writes_outputs(tmp_path: Path) -> None:
    counts = run_classify(
        input_csv=CSV_PATH,
        taxonomy_path=TAX_PATH,
        out_dir=tmp_path,
        threshold=0.1,
    )
    assert counts["input_rows"] == 10
    assert counts["sub_matches"] > 0
    run_dir = Path(counts["out_dir"])
    assert (run_dir / "matches.csv").is_file()
    assert (run_dir / "category_matches.csv").is_file()
    assert (run_dir / "row_scores.csv").is_file()
    assert (run_dir / "all_scores_sub_long.csv").is_file()
    assert (run_dir / "all_scores_sub_wide.csv").is_file()
    assert (run_dir / "all_scores_cat_long.csv").is_file()
    assert (run_dir / "all_scores_cat_wide.csv").is_file()
    assert (run_dir / "keywords.csv").is_file()


def test_all_scores_sub_long_has_full_matrix(tmp_path: Path) -> None:
    """Long CSV must include every (row × sub_category) pair, not just matches."""
    counts = run_classify(
        input_csv=CSV_PATH,
        taxonomy_path=TAX_PATH,
        out_dir=tmp_path,
        threshold=0.1,
    )
    run_dir = Path(counts["out_dir"])
    with (run_dir / "all_scores_sub_long.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # 10 input rows × 4 sub-categories in the test taxonomy = 40
    assert len(rows) == 40


def test_all_scores_cat_long_has_full_matrix(tmp_path: Path) -> None:
    counts = run_classify(
        input_csv=CSV_PATH,
        taxonomy_path=TAX_PATH,
        out_dir=tmp_path,
        threshold=0.1,
    )
    run_dir = Path(counts["out_dir"])
    with (run_dir / "all_scores_cat_long.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # 10 input rows × 2 categories
    assert len(rows) == 20
    # sub_category_id should be blank for category-level rows
    assert all(r["sub_category_id"] == "" for r in rows)


def test_run_classify_no_keywords_skips_keywords(tmp_path: Path) -> None:
    counts = run_classify(
        input_csv=CSV_PATH,
        taxonomy_path=TAX_PATH,
        out_dir=tmp_path,
        threshold=0.1,
        discover_keywords=False,
    )
    run_dir = Path(counts["out_dir"])
    assert not (run_dir / "keywords.csv").exists()
    assert "keywords" not in counts


def test_score_all_returns_category_scores() -> None:
    from text_classify.tfidf_classifier import TfidfConfig, score_all

    tax = load_taxonomy(TAX_PATH)
    rows = _load_rows()
    result = score_all(rows, tax, config=TfidfConfig(threshold=0.1, min_df=1))
    # 10 rows × 2 categories
    assert len(result.all_cat_scores) == 20
    # Category-level records have empty sub_category_id
    assert all(r.sub_category_id == "" for r in result.all_cat_scores)
    # Should produce at least one category-level match above threshold
    assert len(result.cat_matches) > 0


def test_row_scores_summary_contains_top_match() -> None:
    tax = load_taxonomy(TAX_PATH)
    rows = _load_rows()
    _matches, summaries = score_rows(rows, tax, config=TfidfConfig(threshold=0.1, min_df=1))
    by_id = {s.row_id: s for s in summaries}
    # R001 best match should be in the design category
    assert by_id["R001"].top_category_id == "design"
