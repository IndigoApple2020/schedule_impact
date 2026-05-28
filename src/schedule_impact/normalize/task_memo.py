"""Convert TASKMEMO + MEMOTYPE rows into narrative chunks for linking."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from schedule_impact.normalize.html_memo import strip_html_memo

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "p6_schema.yaml"


@lru_cache(maxsize=1)
def _load_planner_labels() -> list[str]:
    if not _CONFIG_PATH.is_file():
        return []
    data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return list(data.get("memo_types", {}).get("planner_note_labels", []))


def _memotype_label_map(memotype_rows: list[dict[str, str]]) -> dict[str, str]:
    id_col = "memo_type_id"
    label_col = "memo_type"
    out: dict[str, str] = {}
    for row in memotype_rows:
        mid = row.get(id_col, "").strip()
        label = row.get(label_col, "").strip()
        if mid:
            out[mid] = label
    return out


def _task_lookup(tasks: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("task_id", "").strip(): row for row in tasks if row.get("task_id", "").strip()}


def memos_to_narrative_chunks(
    task_memo_rows: list[dict[str, str]],
    memotype_rows: list[dict[str, str]],
    task_rows: list[dict[str, str]] | None = None,
    *,
    reporting_period: str,
    programme_id: str,
    planner_labels: list[str] | None = None,
    include_all_memo_types: bool = False,
) -> list[dict[str, Any]]:
    """
    Build narrative-chunk dicts from XER TASKMEMO.

    Each row becomes one chunk keyed by ``memo_id``, linked to ``task_id`` / ``task_code``.
    """
    labels_filter = planner_labels if planner_labels is not None else _load_planner_labels()
    label_by_id = _memotype_label_map(memotype_rows)
    tasks_by_id = _task_lookup(task_rows or [])

    chunks: list[dict[str, Any]] = []
    for row in task_memo_rows:
        memo_id = row.get("memo_id", "").strip()
        task_id = row.get("task_id", "").strip()
        memo_type_id = row.get("memo_type_id", "").strip()
        proj_id = row.get("proj_id", "").strip()
        memo_type_label = label_by_id.get(memo_type_id, memo_type_id)

        if not include_all_memo_types and labels_filter:
            if memo_type_label not in labels_filter:
                continue

        plain = strip_html_memo(row.get("task_memo", ""))
        if not plain:
            continue

        task = tasks_by_id.get(task_id, {})
        chunk_id = f"xer-memo-{memo_id}" if memo_id else f"xer-memo-{task_id}-{memo_type_id}"
        document_id = f"xer:{programme_id}:{proj_id}:taskmemo"

        chunks.append(
            {
                "chunk_id": chunk_id,
                "document_id": document_id,
                "source": "xer_taskmemo",
                "reporting_period": reporting_period,
                "proj_id": proj_id,
                "project_row_id": None,  # resolved later via project_mapping
                "task_id": task_id,
                "task_code": task.get("task_code", "").strip() or None,
                "memo_type_id": memo_type_id,
                "memo_type_label": memo_type_label,
                "text": plain,
                "section_title": memo_type_label,
                "extracted_refs": {"task_code": task.get("task_code")} if task.get("task_code") else {},
            }
        )
    return chunks
