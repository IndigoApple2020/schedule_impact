"""Bulk-export TASKMEMO rows to a labeling spreadsheet."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from schedule_impact.ingest.xer_reader import load_xer_tables
from schedule_impact.normalize.task_memo import memos_to_narrative_chunks

# Columns team fills during review (leave blank on export)
LABEL_COLUMNS = (
    "is_quality_related",
    "quality_notes",
    "labeled_by",
    "labeled_at",
)

EXPORT_COLUMNS = (
    "memo_id",
    "chunk_id",
    "programme_id",
    "reporting_period",
    "proj_id",
    "task_id",
    "task_code",
    "task_name",
    "finish_slip_days",
    "memo_type_id",
    "memo_type_label",
    "text_plain",
    "char_count",
    *LABEL_COLUMNS,
)


def _infer_period_from_path(path: Path) -> str | None:
    match = re.search(r"(20\d{2})[-_](\d{2})", path.as_posix())
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return None


def _chunks_from_xer(
    xer_path: Path,
    *,
    programme_id: str,
    reporting_period: str | None,
    include_all_memo_types: bool,
) -> tuple[str, list[dict[str, Any]]]:
    period = reporting_period or _infer_period_from_path(xer_path) or "unknown"
    tables = load_xer_tables(
        xer_path,
        tables=("TASK", "TASKMEMO", "MEMOTYPE"),
    )
    if not tables.get("TASKMEMO"):
        return period, []

    chunks = memos_to_narrative_chunks(
        tables["TASKMEMO"],
        tables["MEMOTYPE"],
        tables["TASK"],
        reporting_period=period,
        programme_id=programme_id,
        include_all_memo_types=include_all_memo_types,
    )

    tasks = {r.get("task_id", "").strip(): r for r in tables["TASK"]}
    rows: list[dict[str, Any]] = []
    for ch in chunks:
        task = tasks.get(ch.get("task_id") or "", {})
        memo_id = ch["chunk_id"].replace("xer-memo-", "", 1) if ch["chunk_id"].startswith("xer-memo-") else ""
        rows.append(
            {
                "memo_id": memo_id,
                "chunk_id": ch["chunk_id"],
                "programme_id": programme_id,
                "reporting_period": period,
                "proj_id": ch.get("proj_id") or "",
                "task_id": ch.get("task_id") or "",
                "task_code": ch.get("task_code") or "",
                "task_name": task.get("task_name", "").strip(),
                "finish_slip_days": "",
                "memo_type_id": ch.get("memo_type_id") or "",
                "memo_type_label": ch.get("memo_type_label") or "",
                "text_plain": ch["text"],
                "char_count": len(ch["text"]),
                "is_quality_related": "",
                "quality_notes": "",
                "labeled_by": "",
                "labeled_at": "",
            }
        )
    return period, rows


def collect_memo_rows(
    xer_paths: list[Path],
    *,
    programme_id: str,
    reporting_period: str | None = None,
    include_all_memo_types: bool = False,
    slipped_task_slip: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for path in xer_paths:
        _, rows = _chunks_from_xer(
            path,
            programme_id=programme_id,
            reporting_period=reporting_period,
            include_all_memo_types=include_all_memo_types,
        )
        for row in rows:
            key = row["chunk_id"]
            if key in seen:
                continue
            tid = (row.get("task_id") or "").strip()
            if slipped_task_slip is not None:
                if tid not in slipped_task_slip:
                    continue
                row["finish_slip_days"] = slipped_task_slip[tid]
            else:
                row["finish_slip_days"] = ""
            seen.add(key)
            all_rows.append(row)
    return all_rows


def write_labeling_csv(rows: list[dict[str, Any]], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def export_memos(
    *,
    xer_paths: list[Path],
    out_path: Path,
    programme_id: str,
    reporting_period: str | None = None,
    include_all_memo_types: bool = False,
    previous_xer: Path | None = None,
    min_slip_days: float = 1.0,
    slipped_only: bool = False,
) -> int:
    slipped: dict[str, float] | None = None
    if slipped_only:
        if previous_xer is None:
            raise ValueError("--slipped-only requires --previous-xer")
        if len(xer_paths) != 1:
            raise ValueError("--slipped-only supports a single current XER file")
        slipped = slipped_task_slip_from_pair(xer_paths[0], previous_xer, min_slip_days=min_slip_days)

    rows = collect_memo_rows(
        xer_paths,
        programme_id=programme_id,
        reporting_period=reporting_period,
        include_all_memo_types=include_all_memo_types,
        slipped_task_slip=slipped,
    )
    return write_labeling_csv(rows, out_path)


def slipped_task_slip_from_pair(
    current_xer: Path,
    previous_xer: Path,
    *,
    min_slip_days: float,
) -> dict[str, float]:
    from schedule_impact.labeling.slipped_tasks import slipped_task_ids

    return slipped_task_ids(current_xer, previous_xer, min_slip_days=min_slip_days)


def discover_xer_files(xer: Path) -> list[Path]:
    if xer.is_file():
        return [xer]
    if xer.is_dir():
        return sorted(xer.rglob("*.xer"))
    raise FileNotFoundError(xer)
