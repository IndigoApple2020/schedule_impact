"""Normalise TASK rows and month-on-month comparison."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dateutil import parser as date_parser

from schedule_impact.config_loader import load_p6_schema


@dataclass
class TaskSnapshot:
    task_id: str
    proj_id: str
    task_code: str
    task_name: str
    wbs_id: str
    status_code: str
    finish_date: datetime | None
    finish_field_used: str
    total_float_hours: float
    free_float_hours: float
    is_critical: bool
    driving_path_flag: str


def _schema() -> dict[str, Any]:
    return load_p6_schema()


def _float(val: str | None) -> float:
    if val is None or str(val).strip() == "":
        return 0.0
    try:
        return float(val)
    except ValueError:
        return 0.0


def parse_p6_datetime(raw: str | None) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    return date_parser.parse(str(raw).strip())


def is_critical(row: dict[str, str], schema: dict[str, Any] | None = None) -> bool:
    schema = schema or _schema()
    crit = schema.get("criticality", {})
    threshold = float(crit.get("float_hours_critical_threshold", 0))
    total_float = _float(row.get("total_float_hr_cnt"))
    if total_float <= threshold:
        return True
    if crit.get("use_driving_path_flag", True):
        flag = (row.get("driving_path_flag") or "").strip()
        if flag in crit.get("driving_path_true_values", ["Y"]):
            return True
    return False


def pick_finish(row: dict[str, str], schema: dict[str, Any] | None = None) -> tuple[datetime | None, str]:
    schema = schema or _schema()
    comp = schema.get("comparison", {})
    status_vals = set(schema.get("status", {}).get("completed_values", ["TK_Complete"]))
    status = (row.get("status_code") or "").strip()
    act_field = comp.get("finish_field_actualized", "act_end_date")
    early_field = comp.get("finish_field", "early_end_date")

    if status in status_vals:
        act = parse_p6_datetime(row.get(act_field))
        if act:
            return act, act_field
    return parse_p6_datetime(row.get(early_field)), early_field


def row_to_snapshot(row: dict[str, str]) -> TaskSnapshot | None:
    task_id = (row.get("task_id") or "").strip()
    task_code = (row.get("task_code") or "").strip()
    if not task_id or not task_code:
        return None
    finish, finish_field = pick_finish(row)
    return TaskSnapshot(
        task_id=task_id,
        proj_id=(row.get("proj_id") or "").strip(),
        task_code=task_code,
        task_name=(row.get("task_name") or "").strip(),
        wbs_id=(row.get("wbs_id") or "").strip(),
        status_code=(row.get("status_code") or "").strip(),
        finish_date=finish,
        finish_field_used=finish_field,
        total_float_hours=_float(row.get("total_float_hr_cnt")),
        free_float_hours=_float(row.get("free_float_hr_cnt")),
        is_critical=is_critical(row),
        driving_path_flag=(row.get("driving_path_flag") or "").strip(),
    )


def finish_slip_days(current: TaskSnapshot, previous: TaskSnapshot) -> float | None:
    if current.finish_date is None or previous.finish_date is None:
        return None
    delta = current.finish_date.date() - previous.finish_date.date()
    return float(delta.days)


def match_tasks(
    current_rows: list[dict[str, str]],
    previous_rows: list[dict[str, str]],
) -> list[tuple[TaskSnapshot, TaskSnapshot | None]]:
    schema = _schema()
    keys = schema.get("comparison", {}).get("match_keys", ["task_code"])
    fallback = schema.get("comparison", {}).get("match_fallback", ["task_id"])

    all_keys = list(dict.fromkeys(keys + fallback))
    prev_by: dict[str, dict[str, dict[str, str]]] = {k: {} for k in all_keys}
    for row in previous_rows:
        for k in all_keys:
            val = (row.get(k) or "").strip()
            if val:
                prev_by[k][val] = row

    results: list[tuple[TaskSnapshot, TaskSnapshot | None]] = []
    for row in current_rows:
        cur = row_to_snapshot(row)
        if not cur:
            continue
        prev_row = None
        for key_list in (keys, fallback):
            for k in key_list:
                val = (row.get(k) or "").strip()
                if val and val in prev_by.get(k, {}):
                    prev_row = prev_by[k][val]
                    break
            if prev_row:
                break
        prev_snap = row_to_snapshot(prev_row) if prev_row else None
        results.append((cur, prev_snap))
    return results
