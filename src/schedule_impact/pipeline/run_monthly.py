"""Orchestrate monthly incident detection, memo linking, and quality scoring."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from schedule_impact.classify.quality_scorer import assess_all
from schedule_impact.config_loader import load_settings
from schedule_impact.identify.incidents import detect_incidents, incidents_to_records
from schedule_impact.ingest.xer_reader import load_xer_tables
from schedule_impact.link.task_memo_linker import link_incidents_to_memos
from schedule_impact.normalize.task_memo import memos_to_narrative_chunks


@dataclass
class MonthlyRunResult:
    programme_id: str
    reporting_period: str
    previous_period: str
    incident_count: int
    impact_count: int
    float_count: int
    link_count: int
    quality_flagged: int
    output_dir: Path


_INCIDENT_FIELDS = [
    "incident_id", "incident_type", "project_row_id", "proj_id",
    "reporting_period", "previous_period", "primary_task_id", "task_code",
    "task_name", "delay_days", "delay_metric", "finish_field_used",
    "is_critical", "total_float_hours",
]
_LINK_FIELDS = [
    "incident_id", "chunk_id", "link_method", "confidence",
    "rationale", "memo_type_label",
]
_QUALITY_FIELDS = [
    "incident_id", "is_quality_related", "quality_confidence",
    "quality_signals", "classifier", "review_status", "has_linked_memo",
]


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = fieldnames or (list(rows[0].keys()) if rows else None)
    if not keys:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_monthly(
    *,
    programme_id: str,
    current_xer: Path,
    previous_xer: Path,
    reporting_period: str,
    previous_period: str,
    output_dir: Path,
    project_row_id: str | None = None,
    quality_model: Path | None = None,
) -> MonthlyRunResult:
    settings = load_settings()
    inc_cfg = settings.get("incidents", {})
    impact_thr = float(inc_cfg.get("impact_slip_threshold_days", 0))
    float_thr = float(inc_cfg.get("float_slip_threshold_days", 1))

    current_tables = load_xer_tables(
        current_xer,
        tables=("TASK", "TASKMEMO", "MEMOTYPE", "PROJECT"),
    )
    previous_tables = load_xer_tables(previous_xer, tables=("TASK",))

    incidents = detect_incidents(
        current_tables.get("TASK", []),
        previous_tables.get("TASK", []),
        programme_id=programme_id,
        reporting_period=reporting_period,
        previous_period=previous_period,
        project_row_id=project_row_id,
        impact_slip_threshold=impact_thr,
        float_slip_threshold=float_thr,
    )
    incident_rows = incidents_to_records(incidents)

    memo_chunks = memos_to_narrative_chunks(
        current_tables.get("TASKMEMO", []),
        current_tables.get("MEMOTYPE", []),
        current_tables.get("TASK", []),
        reporting_period=reporting_period,
        programme_id=programme_id,
    )
    links = link_incidents_to_memos(incident_rows, memo_chunks)
    quality_rows = assess_all(incident_rows, links, model_path=quality_model)

    out = output_dir / programme_id / reporting_period
    _write_csv(out / f"incidents_{reporting_period}.csv", incident_rows, _INCIDENT_FIELDS)
    _write_csv(out / f"incident_memo_links_{reporting_period}.csv", links, _LINK_FIELDS)
    _write_csv(out / f"quality_assessment_{reporting_period}.csv", quality_rows, _QUALITY_FIELDS)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "programme_id": programme_id,
        "reporting_period": reporting_period,
        "previous_period": previous_period,
        "current_xer": current_xer.name,
        "previous_xer": previous_xer.name,
        "incident_count": len(incident_rows),
        "impact_count": sum(1 for r in incident_rows if r["incident_type"] == "impact"),
        "float_count": sum(1 for r in incident_rows if r["incident_type"] == "float"),
        "link_count": len(links),
        "quality_flagged": sum(1 for r in quality_rows if r["is_quality_related"]),
    }
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return MonthlyRunResult(
        programme_id=programme_id,
        reporting_period=reporting_period,
        previous_period=previous_period,
        incident_count=len(incident_rows),
        impact_count=manifest["impact_count"],
        float_count=manifest["float_count"],
        link_count=len(links),
        quality_flagged=manifest["quality_flagged"],
        output_dir=out,
    )
