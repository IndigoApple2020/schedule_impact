"""Export normalized schedule data to CSV files for local analysis.

Outputs **contain raw task names, codes, dates, and memo text** — these CSVs
are for use on the secure laptop only. Do not share or commit.

Two commands worth of work in one module:

1. :func:`export_xer_tables`        — dump every (useful) XER table to its own CSV.
2. :func:`export_task_changes`      — wide month-over-month delta keyed by task_code.

Used together you get both raw and analytical views of the schedule.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from schedule_impact.ingest.xer_reader import list_xer_tables, load_xer_tables
from schedule_impact.normalize.p6_tasks import (
    TaskSnapshot,
    finish_slip_days,
    row_to_snapshot,
)

# Default subset — the tables that carry meaningful schedule content.
# Pass `all_tables=True` to dump every table found in the XER.
USEFUL_TABLES: tuple[str, ...] = (
    "PROJECT",
    "PROJWBS",
    "CALENDAR",
    "TASK",
    "TASKPRED",
    "TASKACTV",
    "ACTVCODE",
    "ACTVTYPE",
    "MEMOTYPE",
    "TASKMEMO",
    "WBSMEMO",
)

# Columns produced in task_changes.csv (wide format, one row per task_code).
_TASK_CHANGES_FIELDS: list[str] = [
    "task_code",
    "delta_category",
    "task_id_curr",
    "task_id_prev",
    "task_name",
    "task_type",
    "wbs_id",
    "status_code_prev",
    "status_code_curr",
    "early_end_date_prev",
    "early_end_date_curr",
    "act_end_date_prev",
    "act_end_date_curr",
    "finish_slip_calendar_days",
    "total_float_hr_cnt_prev",
    "total_float_hr_cnt_curr",
    "total_float_change_hr",
    "driving_path_flag_prev",
    "driving_path_flag_curr",
    "is_critical_prev",
    "is_critical_curr",
    "finish_field_used_prev",
    "finish_field_used_curr",
]


def export_xer_tables(
    xer_path: Path,
    out_dir: Path,
    *,
    tables: list[str] | None = None,
    all_tables: bool = False,
) -> dict[str, int]:
    """Write each requested XER table to ``out_dir/{TABLE_NAME}.csv``.

    Returns a ``{table_name: row_count}`` summary.
    """
    if not xer_path.is_file():
        raise FileNotFoundError(xer_path)

    available = list_xer_tables(xer_path)
    if all_tables:
        wanted = available
    elif tables is not None:
        wanted = [t for t in tables if t in available]
    else:
        wanted = [t for t in USEFUL_TABLES if t in available]

    out_dir.mkdir(parents=True, exist_ok=True)
    loaded = load_xer_tables(xer_path, tables=tuple(wanted))

    counts: dict[str, int] = {}
    for table_name in wanted:
        rows = loaded.get(table_name, [])
        counts[table_name] = len(rows)
        path = out_dir / f"{table_name}.csv"
        if not rows:
            # Still produce an empty file so downstream tooling sees a placeholder
            path.write_text("", encoding="utf-8-sig")
            continue
        # Use the union of keys across all rows to handle XERs with sparse columns
        fieldnames: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return counts


def _classify_delta(
    prev: TaskSnapshot | None,
    curr: TaskSnapshot | None,
    slip: float | None,
) -> str:
    """Return a comma-separated tag string describing the change."""
    tags: list[str] = []
    if prev is None and curr is not None:
        tags.append("new")
    elif curr is None and prev is not None:
        tags.append("removed")
    else:
        # Both sides exist
        prev_completed = bool(prev and prev.finish_field_used and "act" in prev.finish_field_used)
        curr_completed = bool(curr and curr.finish_field_used and "act" in curr.finish_field_used)
        if curr_completed and not prev_completed:
            tags.append("completed")
        if slip is not None:
            if slip > 0:
                tags.append("slipped")
            elif slip < 0:
                tags.append("accelerated")
        if prev is not None and curr is not None and prev.is_critical != curr.is_critical:
            tags.append("became_critical" if curr.is_critical else "lost_criticality")
        if prev is not None and curr is not None and prev.status_code != curr.status_code:
            tags.append("status_changed")
        if not tags:
            tags.append("stable")
    return ",".join(tags)


def _float_change(prev: TaskSnapshot | None, curr: TaskSnapshot | None) -> float | None:
    if prev is None or curr is None:
        return None
    if prev.total_float_hours is None or curr.total_float_hours is None:
        return None
    return curr.total_float_hours - prev.total_float_hours


def _delta_row(
    code: str,
    prev_row: dict[str, str] | None,
    curr_row: dict[str, str] | None,
) -> dict[str, Any]:
    prev = row_to_snapshot(prev_row) if prev_row else None
    curr = row_to_snapshot(curr_row) if curr_row else None

    slip: float | None = None
    if prev is not None and curr is not None:
        slip = finish_slip_days(curr, prev)

    name = (curr_row or prev_row or {}).get("task_name", "")
    task_type = (curr_row or prev_row or {}).get("task_type", "")
    wbs_id = (curr_row or prev_row or {}).get("wbs_id", "")

    return {
        "task_code": code,
        "delta_category": _classify_delta(prev, curr, slip),
        "task_id_curr": curr.task_id if curr else "",
        "task_id_prev": prev.task_id if prev else "",
        "task_name": name,
        "task_type": task_type,
        "wbs_id": wbs_id,
        "status_code_prev": prev.status_code if prev else "",
        "status_code_curr": curr.status_code if curr else "",
        "early_end_date_prev": (prev_row or {}).get("early_end_date", ""),
        "early_end_date_curr": (curr_row or {}).get("early_end_date", ""),
        "act_end_date_prev": (prev_row or {}).get("act_end_date", ""),
        "act_end_date_curr": (curr_row or {}).get("act_end_date", ""),
        "finish_slip_calendar_days": slip if slip is not None else "",
        "total_float_hr_cnt_prev": prev.total_float_hours if prev and prev.total_float_hours is not None else "",
        "total_float_hr_cnt_curr": curr.total_float_hours if curr and curr.total_float_hours is not None else "",
        "total_float_change_hr": _float_change(prev, curr) if _float_change(prev, curr) is not None else "",
        "driving_path_flag_prev": prev.driving_path_flag if prev else "",
        "driving_path_flag_curr": curr.driving_path_flag if curr else "",
        "is_critical_prev": prev.is_critical if prev else "",
        "is_critical_curr": curr.is_critical if curr else "",
        "finish_field_used_prev": prev.finish_field_used if prev else "",
        "finish_field_used_curr": curr.finish_field_used if curr else "",
    }


def export_task_changes(
    current_xer: Path,
    previous_xer: Path,
    out_path: Path,
) -> dict[str, int]:
    """Write a wide month-over-month task delta CSV.

    Joins by ``task_code``. Returns counts per ``delta_category`` for the
    pipeline summary.
    """
    cur = load_xer_tables(current_xer, tables=("TASK",)).get("TASK", [])
    prv = load_xer_tables(previous_xer, tables=("TASK",)).get("TASK", [])

    by_code_curr: dict[str, dict[str, str]] = {}
    by_code_prev: dict[str, dict[str, str]] = {}
    for row in cur:
        code = (row.get("task_code") or "").strip()
        if code:
            by_code_curr[code] = row
    for row in prv:
        code = (row.get("task_code") or "").strip()
        if code:
            by_code_prev[code] = row

    all_codes = sorted(set(by_code_curr) | set(by_code_prev))
    rows = [_delta_row(code, by_code_prev.get(code), by_code_curr.get(code)) for code in all_codes]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_TASK_CHANGES_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # Category counts (split comma-separated tags so each contributes)
    counts: dict[str, int] = {}
    for r in rows:
        for tag in r["delta_category"].split(","):
            counts[tag] = counts.get(tag, 0) + 1
    counts["_total_rows"] = len(rows)
    return counts


def run_export(
    *,
    current_xer: Path,
    out_dir: Path,
    previous_xer: Path | None = None,
    all_tables: bool = False,
) -> dict[str, Any]:
    """Top-level orchestrator: dump tables + optional task-change delta."""
    table_counts = export_xer_tables(current_xer, out_dir, all_tables=all_tables)
    summary: dict[str, Any] = {"tables": table_counts}

    if previous_xer is not None:
        delta_path = out_dir / "task_changes.csv"
        category_counts = export_task_changes(current_xer, previous_xer, delta_path)
        summary["task_changes"] = category_counts
        summary["task_changes_path"] = str(delta_path)
    return summary
