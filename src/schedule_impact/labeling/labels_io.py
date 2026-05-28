"""Read and validate team labeling spreadsheets."""

from __future__ import annotations

import csv
from pathlib import Path

from schedule_impact.labeling.export_memos import EXPORT_COLUMNS, LABEL_COLUMNS

_TRUTHY = frozenset({"1", "true", "yes", "y", "quality", "quality_issue", "quality-related"})
_FALSY = frozenset({"0", "false", "no", "n", "not_quality", "non-quality", "other"})


def parse_quality_label(raw: str) -> bool | None:
    value = (raw or "").strip().lower()
    if not value:
        return None
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    raise ValueError(f"Unrecognized is_quality_related value: {raw!r}")


def read_labeling_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Empty CSV: {path}")
        missing = set(EXPORT_COLUMNS) - set(reader.fieldnames)
        if missing:
            raise ValueError(f"Missing columns in {path}: {sorted(missing)}")
        return [dict(row) for row in reader]


def labeled_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        try:
            label = parse_quality_label(row.get("is_quality_related", ""))
        except ValueError:
            continue
        if label is None:
            continue
        enriched = dict(row)
        enriched["_label_bool"] = label
        out.append(enriched)
    return out
