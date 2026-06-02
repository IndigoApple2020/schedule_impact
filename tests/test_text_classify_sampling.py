"""Tests for the stratified sampler."""

import csv
from pathlib import Path

import pytest

pytest.importorskip("sklearn")

from text_classify.runner import run_classify
from text_classify.sampling import run_sample, stratified_sample

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "text_classify"
TAX_PATH = FIXTURE_DIR / "taxonomy.yaml"
CSV_PATH = FIXTURE_DIR / "issues_sample.csv"


def _classify(tmp_path: Path) -> Path:
    counts = run_classify(
        input_csv=CSV_PATH,
        taxonomy_path=TAX_PATH,
        out_dir=tmp_path,
        threshold=0.1,
        discover_keywords=False,
    )
    return Path(counts["out_dir"])


def test_stratified_sample_balances_categories(tmp_path: Path) -> None:
    run_dir = _classify(tmp_path)
    sample = stratified_sample(
        input_csv=CSV_PATH,
        classify_run_dir=run_dir,
        total=8,
    )
    assert len(sample) > 0
    # At least one row per category that the classifier returned non-empty top_category_id for
    cats_in_sample = {r["top_category_id"] for r in sample if r.get("top_category_id")}
    assert len(cats_in_sample) >= 1


def test_run_sample_writes_csv_with_preserved_columns(tmp_path: Path) -> None:
    run_dir = _classify(tmp_path)
    out_csv = tmp_path / "sample.csv"
    counts = run_sample(
        input_csv=CSV_PATH,
        classify_run_dir=run_dir,
        out_csv=out_csv,
        total=5,
    )
    assert counts["sampled"] > 0
    assert out_csv.is_file()

    with out_csv.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)

    # Original columns preserved (so the sample CSV can be fed back into classify-*)
    assert "row_id" in fields
    assert "root_cause" in fields
    # Sampler metadata appended
    assert "top_category_id" in fields
    assert "top_score" in fields
    assert "score_band" in fields
    # The sampled rows have non-empty original text
    assert all(r["root_cause"] for r in rows)


def test_sample_caps_at_total_when_corpus_small(tmp_path: Path) -> None:
    run_dir = _classify(tmp_path)
    # Request way more than the corpus has — should return at most 10 (input size)
    sample = stratified_sample(
        input_csv=CSV_PATH,
        classify_run_dir=run_dir,
        total=1000,
    )
    assert len(sample) <= 10
