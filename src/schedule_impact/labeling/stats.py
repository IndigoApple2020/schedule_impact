"""Summarise human labeling results."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from schedule_impact.labeling.labels_io import labeled_rows, parse_quality_label, read_labeling_csv


@dataclass
class LabelingStats:
    total_rows: int
    labeled_rows: int
    unlabeled_rows: int
    quality_yes: int
    quality_no: int
    quality_rate: float | None
    by_memo_type: dict[str, dict[str, int]]


def compute_stats(rows: list[dict[str, str]]) -> LabelingStats:
    total = len(rows)
    labeled = labeled_rows(rows)
    yes = sum(1 for r in labeled if r["_label_bool"] is True)
    no = len(labeled) - yes

    by_type: dict[str, dict[str, int]] = {}
    for row in labeled:
        mt = row.get("memo_type_label") or "unknown"
        bucket = by_type.setdefault(mt, {"yes": 0, "no": 0})
        if row["_label_bool"] is True:
            bucket["yes"] += 1
        else:
            bucket["no"] += 1

    rate = (yes / len(labeled)) if labeled else None
    return LabelingStats(
        total_rows=total,
        labeled_rows=len(labeled),
        unlabeled_rows=total - len(labeled),
        quality_yes=yes,
        quality_no=no,
        quality_rate=round(rate, 4) if rate is not None else None,
        by_memo_type=by_type,
    )


def stats_from_csv(path: Path) -> LabelingStats:
    return compute_stats(read_labeling_csv(path))


def write_stats_report(stats: LabelingStats, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(asdict(stats), indent=2), encoding="utf-8")
