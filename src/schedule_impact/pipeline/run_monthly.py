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
from schedule_impact.ingest.pdf_extractor import extract_pdf_chunks
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
    memo_chunk_count: int
    pdf_chunk_count: int
    quality_flagged: int
    output_dir: Path


_INCIDENT_FIELDS = [
    "incident_id", "incident_type", "project_row_id", "proj_id",
    "reporting_period", "previous_period", "primary_task_id", "task_code",
    "task_name", "task_type", "delay_days", "delay_metric", "finish_field_used",
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
_PDF_CHUNK_FIELDS = [
    "chunk_id", "document_id", "source", "reporting_period",
    "project_row_id", "section_number", "parent_section_number",
    "section_type", "section_title", "first_page",
    "text", "activity_codes", "dates",
]
_TASKMEMO_CHUNK_FIELDS = [
    "chunk_id", "document_id", "source", "reporting_period",
    "proj_id", "task_id", "task_code",
    "memo_type_id", "memo_type_label", "section_title", "text",
]
# Joined view: one row per (incident, linked chunk). Incidents with no links
# emit one row with blank link/chunk columns so they aren't lost.
_REVIEW_FIELDS = [
    # Incident columns
    "incident_id", "incident_type", "project_row_id", "primary_task_id",
    "task_code", "task_name", "task_type",
    "delay_days", "is_critical", "total_float_hours",
    "finish_field_used",
    # Link columns
    "link_chunk_id", "link_method", "link_confidence", "link_rationale",
    # Chunk columns (from whichever source matched)
    "chunk_source", "chunk_section_label", "chunk_text",
]


def _build_review_records(
    incident_rows: list[dict[str, Any]],
    links: list[dict[str, Any]],
    memo_chunks: list[dict[str, Any]],
    pdf_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Left-join incidents → links → chunks for a single review CSV.

    One row per (incident, linked chunk). Incidents with no links get one
    row with blank link/chunk columns — never dropped.

    Chunk source is either ``xer_taskmemo`` (a planner memo) or ``pdf``
    (a narrative section). ``chunk_section_label`` carries the relevant
    label from each source: ``memo_type_label`` for memos, ``section_title``
    for PDF sections.
    """
    chunks_by_id: dict[str, dict[str, Any]] = {}
    for chunk in memo_chunks:
        chunks_by_id[chunk["chunk_id"]] = chunk
    for chunk in pdf_chunks:
        chunks_by_id[chunk["chunk_id"]] = chunk

    links_by_inc: dict[str, list[dict[str, Any]]] = {}
    for lnk in links:
        links_by_inc.setdefault(lnk["incident_id"], []).append(lnk)

    review: list[dict[str, Any]] = []
    for inc in incident_rows:
        inc_links = links_by_inc.get(inc["incident_id"], [])
        if not inc_links:
            review.append({
                **inc,
                "link_chunk_id": "",
                "link_method": "",
                "link_confidence": "",
                "link_rationale": "",
                "chunk_source": "",
                "chunk_section_label": "",
                "chunk_text": "",
            })
            continue
        for lnk in inc_links:
            chunk = chunks_by_id.get(lnk["chunk_id"], {})
            review.append({
                **inc,
                "link_chunk_id": lnk.get("chunk_id", ""),
                "link_method": lnk.get("link_method", ""),
                "link_confidence": lnk.get("confidence", ""),
                "link_rationale": lnk.get("rationale", ""),
                "chunk_source": chunk.get("source", ""),
                "chunk_section_label": (
                    chunk.get("memo_type_label")
                    or chunk.get("section_title")
                    or ""
                ),
                "chunk_text": chunk.get("text", ""),
            })
    return review


def _flatten_pdf_chunk_for_csv(chunk: dict[str, Any]) -> dict[str, Any]:
    refs = chunk.get("extracted_refs") or {}
    out = {k: chunk.get(k) for k in _PDF_CHUNK_FIELDS if k not in ("activity_codes", "dates")}
    out["activity_codes"] = "; ".join(refs.get("activity_codes", []))
    out["dates"] = "; ".join(refs.get("dates", []))
    return out


def _flatten_taskmemo_chunk_for_csv(chunk: dict[str, Any]) -> dict[str, Any]:
    """Project a memo chunk dict onto the CSV column set, dropping nested fields."""
    return {k: chunk.get(k) for k in _TASKMEMO_CHUNK_FIELDS}


def _link_pdf_chunks_by_project_row(
    incidents: list[dict[str, Any]],
    pdf_chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Low-confidence Phase 3 stub: link incidents to PDF chunks sharing a project_row_id.

    Confidence is set higher when the chunk is a ``cp_float_analysis`` or
    ``mitigation`` section — those are the sub-sections that genuinely discuss
    schedule incidents and root causes.
    """
    by_row: dict[str, list[dict[str, Any]]] = {}
    for chunk in pdf_chunks:
        prid = chunk.get("project_row_id")
        if prid:
            by_row.setdefault(prid, []).append(chunk)

    high_conf_types = {"cp_float_analysis", "mitigation"}
    links: list[dict[str, Any]] = []
    for inc in incidents:
        prid = inc.get("project_row_id")
        for chunk in by_row.get(prid, []):
            stype = chunk.get("section_type")
            conf = 0.65 if stype in high_conf_types else 0.4
            links.append(
                {
                    "incident_id": inc["incident_id"],
                    "chunk_id": chunk["chunk_id"],
                    "link_method": "project_row",
                    "confidence": conf,
                    "rationale": f"PDF section {chunk.get('section_number')} ({stype}) on project_row={prid}",
                    "memo_type_label": None,
                    "text_plain": chunk.get("text"),
                }
            )
    return links


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
    pdfs: list[Path] | None = None,
) -> MonthlyRunResult:
    settings = load_settings()
    inc_cfg = settings.get("incidents", {})
    impact_thr = float(inc_cfg.get("impact_slip_threshold_days", 0))
    float_thr = float(inc_cfg.get("float_slip_threshold_days", 1))
    pdf_cfg = settings.get("pdf", {})
    project_row_map = pdf_cfg.get("project_row_map") or {}

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
    memo_links = link_incidents_to_memos(incident_rows, memo_chunks)

    pdf_chunks: list[dict[str, Any]] = []
    for pdf_path in pdfs or []:
        pdf_chunks.extend(
            extract_pdf_chunks(
                pdf_path,
                programme_id=programme_id,
                reporting_period=reporting_period,
                project_row_map=project_row_map,
            )
        )
    pdf_links = _link_pdf_chunks_by_project_row(incident_rows, pdf_chunks)
    links = memo_links + pdf_links

    quality_rows = assess_all(incident_rows, links, model_path=quality_model)

    out = output_dir / programme_id / reporting_period
    _write_csv(out / f"incidents_{reporting_period}.csv", incident_rows, _INCIDENT_FIELDS)
    _write_csv(out / f"incident_memo_links_{reporting_period}.csv", links, _LINK_FIELDS)
    _write_csv(out / f"quality_assessment_{reporting_period}.csv", quality_rows, _QUALITY_FIELDS)
    # Always emit TASKMEMO chunks (the body text was previously consumed
    # in-memory by the quality scorer and then lost)
    _write_csv(
        out / f"taskmemo_chunks_{reporting_period}.csv",
        [_flatten_taskmemo_chunk_for_csv(c) for c in memo_chunks],
        _TASKMEMO_CHUNK_FIELDS,
    )
    if pdf_chunks:
        _write_csv(
            out / f"narrative_chunks_{reporting_period}.csv",
            [_flatten_pdf_chunk_for_csv(c) for c in pdf_chunks],
            _PDF_CHUNK_FIELDS,
        )

    # Pre-joined review CSV: incidents + their links + chunk text in one place.
    # This is the file most useful for a human reviewer (one row per
    # incident-narrative pairing; unlinked incidents kept with blank fields).
    review_rows = _build_review_records(incident_rows, links, memo_chunks, pdf_chunks)
    _write_csv(out / f"incident_review_{reporting_period}.csv", review_rows, _REVIEW_FIELDS)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "programme_id": programme_id,
        "reporting_period": reporting_period,
        "previous_period": previous_period,
        "current_xer": current_xer.name,
        "previous_xer": previous_xer.name,
        "pdfs": [p.name for p in (pdfs or [])],
        "incident_count": len(incident_rows),
        "impact_count": sum(1 for r in incident_rows if r["incident_type"] == "impact"),
        "float_count": sum(1 for r in incident_rows if r["incident_type"] == "float"),
        "memo_link_count": len(memo_links),
        "pdf_link_count": len(pdf_links),
        "link_count": len(links),
        "memo_chunk_count": len(memo_chunks),
        "pdf_chunk_count": len(pdf_chunks),
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
        memo_chunk_count=len(memo_chunks),
        pdf_chunk_count=len(pdf_chunks),
        quality_flagged=manifest["quality_flagged"],
        output_dir=out,
    )
