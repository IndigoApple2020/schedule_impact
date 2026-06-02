"""Precision / Recall / F1 of predictions against hand-labelled rows.

Inputs:
  predictions: list[MatchRecord]      — typically the filtered matches.csv
  labels: list[tuple[row_id, category_id, sub_category_id | ""]]

Outputs:
  - per-sub-category metrics (TP/FP/FN/P/R/F1)
  - per-category metrics (rolled up: a row is a category positive if ANY
    sub-category of that category is predicted/labelled)
  - overall micro and macro averages
  - error list: every FP and FN with row_id and the offending pair, for
    spot-checking the taxonomy seeds
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from text_classify.schemas import MatchRecord

Level = Literal["sub_category", "category", "overall"]
Errors = Literal["fp", "fn"]


@dataclass
class EvalRow:
    level: Level
    category_id: str
    sub_category_id: str
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    n_labels: int          # number of positive labels for this target
    n_predictions: int     # number of positive predictions for this target


@dataclass
class ErrorRow:
    kind: Errors           # "fp" = predicted but not labelled; "fn" = labelled but not predicted
    level: Level           # "sub_category" or "category"
    row_id: str
    category_id: str
    sub_category_id: str
    score: float           # 0.0 for FN (no prediction); prediction score for FP


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _metrics(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = _safe_div(tp, tp + fp)
    r = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * p * r, p + r) if (p + r) else 0.0
    return round(p, 4), round(r, 4), round(f1, 4)


def evaluate(
    predictions: list[MatchRecord],
    labels: list[tuple[str, str, str]],
) -> tuple[list[EvalRow], list[ErrorRow]]:
    """Compute per-target metrics + flat error list.

    ``labels`` entries with empty ``sub_category_id`` are treated as
    category-only positives (they contribute to category-level metrics but
    not sub-category-level).
    """
    # Build label sets
    labels_sub: set[tuple[str, str, str]] = set()
    labels_cat: set[tuple[str, str]] = set()
    for rid, cid, sid in labels:
        labels_cat.add((rid, cid))
        if sid:
            labels_sub.add((rid, cid, sid))

    # Build prediction sets and score lookup (for reporting score on FPs)
    preds_sub: set[tuple[str, str, str]] = set()
    preds_cat_with_score: dict[tuple[str, str], float] = {}
    preds_sub_with_score: dict[tuple[str, str, str], float] = {}
    for p in predictions:
        if p.sub_category_id:
            key = (p.row_id, p.category_id, p.sub_category_id)
            preds_sub.add(key)
            preds_sub_with_score[key] = max(p.score, preds_sub_with_score.get(key, 0.0))
        cat_key = (p.row_id, p.category_id)
        preds_cat_with_score[cat_key] = max(p.score, preds_cat_with_score.get(cat_key, 0.0))
    preds_cat: set[tuple[str, str]] = set(preds_cat_with_score)

    # Per (cat, sub) counts
    sub_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for key in preds_sub | labels_sub:
        rid, cid, sid = key
        if key in preds_sub and key in labels_sub:
            sub_counts[(cid, sid)]["tp"] += 1
        elif key in preds_sub:
            sub_counts[(cid, sid)]["fp"] += 1
        else:
            sub_counts[(cid, sid)]["fn"] += 1

    # Per cat counts
    cat_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for key in preds_cat | labels_cat:
        rid, cid = key
        if key in preds_cat and key in labels_cat:
            cat_counts[cid]["tp"] += 1
        elif key in preds_cat:
            cat_counts[cid]["fp"] += 1
        else:
            cat_counts[cid]["fn"] += 1

    rows: list[EvalRow] = []
    for (cid, sid), c in sorted(sub_counts.items()):
        p, r, f1 = _metrics(c["tp"], c["fp"], c["fn"])
        rows.append(EvalRow(
            level="sub_category", category_id=cid, sub_category_id=sid,
            tp=c["tp"], fp=c["fp"], fn=c["fn"],
            precision=p, recall=r, f1=f1,
            n_labels=c["tp"] + c["fn"], n_predictions=c["tp"] + c["fp"],
        ))
    for cid, c in sorted(cat_counts.items()):
        p, r, f1 = _metrics(c["tp"], c["fp"], c["fn"])
        rows.append(EvalRow(
            level="category", category_id=cid, sub_category_id="",
            tp=c["tp"], fp=c["fp"], fn=c["fn"],
            precision=p, recall=r, f1=f1,
            n_labels=c["tp"] + c["fn"], n_predictions=c["tp"] + c["fp"],
        ))

    # Micro overall (sum tp/fp/fn across all sub-cat targets)
    micro_tp = sum(c["tp"] for c in sub_counts.values())
    micro_fp = sum(c["fp"] for c in sub_counts.values())
    micro_fn = sum(c["fn"] for c in sub_counts.values())
    p, r, f1 = _metrics(micro_tp, micro_fp, micro_fn)
    rows.append(EvalRow(
        level="overall", category_id="micro", sub_category_id="",
        tp=micro_tp, fp=micro_fp, fn=micro_fn,
        precision=p, recall=r, f1=f1,
        n_labels=micro_tp + micro_fn, n_predictions=micro_tp + micro_fp,
    ))

    # Macro overall (unweighted mean of per-sub metrics)
    if sub_counts:
        per_p = [_metrics(c["tp"], c["fp"], c["fn"])[0] for c in sub_counts.values()]
        per_r = [_metrics(c["tp"], c["fp"], c["fn"])[1] for c in sub_counts.values()]
        per_f = [_metrics(c["tp"], c["fp"], c["fn"])[2] for c in sub_counts.values()]
        rows.append(EvalRow(
            level="overall", category_id="macro", sub_category_id="",
            tp=micro_tp, fp=micro_fp, fn=micro_fn,
            precision=round(sum(per_p) / len(per_p), 4),
            recall=round(sum(per_r) / len(per_r), 4),
            f1=round(sum(per_f) / len(per_f), 4),
            n_labels=len(sub_counts),
            n_predictions=len(sub_counts),
        ))

    # Errors list — FPs and FNs row-by-row, for manual review
    errors: list[ErrorRow] = []
    for rid, cid, sid in sorted(preds_sub - labels_sub):
        errors.append(ErrorRow(
            kind="fp", level="sub_category",
            row_id=rid, category_id=cid, sub_category_id=sid,
            score=preds_sub_with_score.get((rid, cid, sid), 0.0),
        ))
    for rid, cid, sid in sorted(labels_sub - preds_sub):
        errors.append(ErrorRow(
            kind="fn", level="sub_category",
            row_id=rid, category_id=cid, sub_category_id=sid,
            score=0.0,
        ))
    for rid, cid in sorted(preds_cat - labels_cat):
        errors.append(ErrorRow(
            kind="fp", level="category",
            row_id=rid, category_id=cid, sub_category_id="",
            score=preds_cat_with_score.get((rid, cid), 0.0),
        ))
    for rid, cid in sorted(labels_cat - preds_cat):
        errors.append(ErrorRow(
            kind="fn", level="category",
            row_id=rid, category_id=cid, sub_category_id="",
            score=0.0,
        ))

    return rows, errors
