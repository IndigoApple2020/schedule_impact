"""Link incidents to TASKMEMO narrative chunks by task_id."""

from __future__ import annotations

from typing import Any


def link_incidents_to_memos(
    incidents: list[dict[str, Any]],
    memo_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_task: dict[str, list[dict[str, Any]]] = {}
    for chunk in memo_chunks:
        tid = (chunk.get("task_id") or "").strip()
        if tid:
            by_task.setdefault(tid, []).append(chunk)

    links: list[dict[str, Any]] = []
    for inc in incidents:
        tid = (inc.get("primary_task_id") or "").strip()
        for chunk in by_task.get(tid, []):
            links.append(
                {
                    "incident_id": inc["incident_id"],
                    "chunk_id": chunk["chunk_id"],
                    "link_method": "task_memo",
                    "confidence": 0.95,
                    "rationale": f"TASKMEMO on task_id={tid}",
                    "memo_type_label": chunk.get("memo_type_label"),
                    "text_plain": chunk.get("text"),
                }
            )
    return links
