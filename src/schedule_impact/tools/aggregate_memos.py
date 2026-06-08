"""Combine taskmemo_chunks_*.csv outputs from multiple runs into one CSV.

The output is shaped to feed straight into ``text-classify`` — ``row_id`` and
``root_cause`` are the default column names that ``classify-tfidf``,
``classify-llm-prompt``, and ``discover-keywords`` expect.

Provenance columns (programme, period, task_id, task_code, memo_type_label)
are retained so you can join the classification results back to the
original incidents later.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Output schema: id + text columns come first (named for text-classify defaults),
# then provenance columns kept for downstream joining.
AGGREGATE_FIELDS = [
    "row_id",                    # = chunk_id from the source CSV
    "root_cause",                # = text from the source CSV
    "programme_id",
    "reporting_period",
    "source",                    # always "xer_taskmemo" for this aggregator
    "proj_id",
    "task_id",
    "task_code",
    "memo_type_id",
    "memo_type_label",
    "section_title",
    "source_file",               # absolute path of the originating CSV
]


def _iter_memo_csvs(root: Path, pattern: str) -> Iterable[Path]:
    """Yield CSV files matching ``pattern`` under ``root``."""
    if not root.exists():
        raise FileNotFoundError(root)
    yield from sorted(root.rglob(pattern))


def _infer_programme(csv_path: Path, default: str) -> str:
    """Pull the programme id from the standard outputs/{programme}/{period}/ layout."""
    parts = csv_path.parts
    # outputs/{programme}/{period}/taskmemo_chunks_{period}.csv
    if "outputs" in parts:
        i = parts.index("outputs")
        if i + 1 < len(parts):
            return parts[i + 1]
    return default


def aggregate(
    *,
    outputs_root: Path,
    out_csv: Path,
    pattern: str = "taskmemo_chunks_*.csv",
    default_programme: str = "unknown",
) -> dict[str, Any]:
    """Walk ``outputs_root``, gather all matching memo CSVs, write a unified file.

    Returns counts: ``{files: N, rows: N, periods: N, out: path}``.
    """
    csv_paths = list(_iter_memo_csvs(outputs_root, pattern))
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    rows_written = 0
    periods_seen: set[str] = set()
    with out_csv.open("w", encoding="utf-8-sig", newline="") as out_handle:
        writer = csv.DictWriter(out_handle, fieldnames=AGGREGATE_FIELDS, extrasaction="ignore")
        writer.writeheader()

        for csv_path in csv_paths:
            programme = _infer_programme(csv_path, default_programme)
            with csv_path.open(encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for src in reader:
                    out_row = {
                        "row_id": src.get("chunk_id", ""),
                        "root_cause": src.get("text", ""),
                        "programme_id": programme,
                        "reporting_period": src.get("reporting_period", ""),
                        "source": src.get("source", "xer_taskmemo"),
                        "proj_id": src.get("proj_id", ""),
                        "task_id": src.get("task_id", ""),
                        "task_code": src.get("task_code", ""),
                        "memo_type_id": src.get("memo_type_id", ""),
                        "memo_type_label": src.get("memo_type_label", ""),
                        "section_title": src.get("section_title", ""),
                        "source_file": str(csv_path),
                    }
                    if out_row["reporting_period"]:
                        periods_seen.add(out_row["reporting_period"])
                    # Skip empty memos — text-classify treats them as zero-score anyway
                    if out_row["root_cause"].strip():
                        writer.writerow(out_row)
                        rows_written += 1

    return {
        "files": len(csv_paths),
        "rows": rows_written,
        "periods": len(periods_seen),
        "out": str(out_csv),
    }
