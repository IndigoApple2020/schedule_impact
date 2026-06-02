"""Tests for the evaluator (precision/recall/F1 against hand-labelled rows)."""

import csv
from pathlib import Path

from text_classify.evaluator import evaluate
from text_classify.runner import run_eval
from text_classify.schemas import MatchRecord


def _m(row_id: str, cat: str, sub: str, score: float = 0.8) -> MatchRecord:
    return MatchRecord(
        row_id=row_id, category_id=cat, sub_category_id=sub,
        score=score, method="tfidf", signals="",
    )


def test_perfect_match() -> None:
    preds = [_m("R001", "design", "inadequate_design")]
    labels = [("R001", "design", "inadequate_design")]
    rows, errors = evaluate(preds, labels)
    overall = next(r for r in rows if r.level == "overall" and r.category_id == "micro")
    assert overall.precision == 1.0
    assert overall.recall == 1.0
    assert overall.f1 == 1.0
    assert errors == []


def test_false_positive_counted() -> None:
    """A wrong prediction inside the labelled scope must count as a false positive."""
    preds = [
        _m("R001", "design", "inadequate_design"),
        _m("R002", "design", "inadequate_design"),  # Wrong call on R002 — R002 is labelled differently
    ]
    labels = [
        ("R001", "design", "inadequate_design"),
        ("R002", "materials", "faulty_material"),   # R002 is in scope but its true label differs
    ]
    rows, errors = evaluate(preds, labels)
    sub = next(r for r in rows if r.level == "sub_category" and r.sub_category_id == "inadequate_design")
    assert sub.tp == 1
    assert sub.fp == 1
    fp_errors = [e for e in errors if e.kind == "fp" and e.level == "sub_category"]
    assert any(e.row_id == "R002" for e in fp_errors)


def test_false_negative_counted() -> None:
    preds = [_m("R001", "design", "inadequate_design")]
    labels = [
        ("R001", "design", "inadequate_design"),
        ("R002", "materials", "faulty_material"),  # FN: not predicted
    ]
    rows, errors = evaluate(preds, labels)
    fn_errors = [e for e in errors if e.kind == "fn" and e.level == "sub_category"]
    assert any(e.row_id == "R002" and e.sub_category_id == "faulty_material" for e in fn_errors)


def test_category_level_rollup() -> None:
    """A category-level positive fires if ANY sub-category of that category is predicted/labelled."""
    preds = [_m("R001", "design", "inadequate_design")]
    labels = [("R001", "design", "late_design_change")]  # different sub-cat, same cat
    rows, _errors = evaluate(preds, labels)
    cat = next(r for r in rows if r.level == "category" and r.category_id == "design")
    # Both pred and label hit the "design" category at row R001 -> TP at category level
    assert cat.tp == 1
    sub_iad = next(r for r in rows if r.level == "sub_category" and r.sub_category_id == "inadequate_design")
    assert sub_iad.fp == 1  # FP at sub-category level
    sub_ldc = next(r for r in rows if r.level == "sub_category" and r.sub_category_id == "late_design_change")
    assert sub_ldc.fn == 1


def test_macro_average_present() -> None:
    preds = [
        _m("R001", "design", "inadequate_design"),
        _m("R002", "materials", "faulty_material"),
    ]
    labels = [
        ("R001", "design", "inadequate_design"),
        ("R002", "materials", "faulty_material"),
    ]
    rows, _ = evaluate(preds, labels)
    assert any(r.level == "overall" and r.category_id == "macro" for r in rows)


def test_predictions_outside_labelled_scope_ignored() -> None:
    """Predictions for rows that don't appear in labels must not count as FPs."""
    preds = [
        _m("R001", "design", "inadequate_design"),    # in scope, TP
        _m("R999", "design", "inadequate_design"),    # NOT labelled — must be ignored
        _m("R999", "materials", "faulty_material"),   # NOT labelled — must be ignored
    ]
    labels = [("R001", "design", "inadequate_design")]
    rows, errors = evaluate(preds, labels)
    overall = next(r for r in rows if r.level == "overall" and r.category_id == "micro")
    # 1 TP, 0 FP, 0 FN
    assert overall.tp == 1
    assert overall.fp == 0
    assert overall.fn == 0
    assert overall.precision == 1.0
    assert overall.recall == 1.0
    # No FP errors for R999
    assert not any(e.row_id == "R999" for e in errors)


def test_run_eval_writes_csvs(tmp_path: Path) -> None:
    matches_csv = tmp_path / "matches.csv"
    labels_csv = tmp_path / "labels.csv"
    out_dir = tmp_path / "eval"

    with matches_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["row_id", "category_id", "sub_category_id", "score", "method", "signals", "taxonomy_version", "run_id"])
        w.writeheader()
        w.writerow({"row_id": "R001", "category_id": "design", "sub_category_id": "inadequate_design",
                    "score": 0.8, "method": "tfidf", "signals": "", "taxonomy_version": 1, "run_id": ""})
        w.writerow({"row_id": "R002", "category_id": "design", "sub_category_id": "inadequate_design",
                    "score": 0.7, "method": "tfidf", "signals": "", "taxonomy_version": 1, "run_id": ""})

    with labels_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["row_id", "category_id", "sub_category_id"])
        w.writeheader()
        w.writerow({"row_id": "R001", "category_id": "design", "sub_category_id": "inadequate_design"})

    summary = run_eval(matches_csv=matches_csv, labels_csv=labels_csv, out_dir=out_dir)
    assert (out_dir / "eval_summary.csv").is_file()
    assert (out_dir / "eval_errors.csv").is_file()
    assert summary["n_labels"] == 1
    assert summary["n_predictions"] == 2     # raw prediction count loaded from CSV
    # Eval is now scoped to labelled row_ids only.
    # R002's prediction is outside scope (no label for R002) → ignored.
    # Only R001 counts: 1 TP, 0 FP, 0 FN → precision=1.0, recall=1.0
    assert summary["micro_precision"] == 1.0
    assert summary["micro_recall"] == 1.0


def test_run_eval_from_all_scores_with_threshold(tmp_path: Path) -> None:
    all_scores = tmp_path / "all_scores.csv"
    labels_csv = tmp_path / "labels.csv"
    out_dir = tmp_path / "eval"

    with all_scores.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["row_id", "category_id", "sub_category_id", "score", "method", "signals", "taxonomy_version", "run_id"])
        w.writeheader()
        # R001 inadequate_design at 0.6 should be kept at threshold 0.5
        w.writerow({"row_id": "R001", "category_id": "design", "sub_category_id": "inadequate_design",
                    "score": 0.6, "method": "tfidf", "signals": "", "taxonomy_version": 1, "run_id": ""})
        # R001 late_design_change at 0.3 should be discarded
        w.writerow({"row_id": "R001", "category_id": "design", "sub_category_id": "late_design_change",
                    "score": 0.3, "method": "tfidf", "signals": "", "taxonomy_version": 1, "run_id": ""})

    with labels_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["row_id", "category_id", "sub_category_id"])
        w.writeheader()
        w.writerow({"row_id": "R001", "category_id": "design", "sub_category_id": "inadequate_design"})

    summary = run_eval(
        matches_csv=tmp_path / "missing.csv",  # ignored when all_scores_csv given
        labels_csv=labels_csv,
        out_dir=out_dir,
        all_scores_csv=all_scores,
        threshold=0.5,
    )
    assert summary["n_predictions"] == 1   # only the 0.6 row passed
    assert summary["micro_f1"] == 1.0
