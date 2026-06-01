"""Extract narrative chunks from programme PDF reports.

Section-based extraction calibrated for the standard programme narrative format
(numbered headings, prose sub-sections, occasional milestone tables).

Public entry point: :func:`extract_pdf_chunks` returns dict records compatible
with the narrative-chunk structure used by `link/task_memo_linker.py` and the
downstream pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Heading patterns matching the programme narrative ToC format.
_HEADING_L1 = re.compile(r"^(\d{1,2})\s{1,8}([A-Z][\w\s₂&/()–-]{1,70})\s*$")
_HEADING_L2 = re.compile(r"^(\d{1,2}\.\d{1,2})\s{1,8}([A-Z][\w\s&/()–-]{1,70})\s*$")

# Trailing page-number patterns that appear on ToC entries
# (e.g. "Period Overview 14", "Main Works - 4 -").
_TRAILING_PAGE_NUM = re.compile(r"\s+(?:[-–]\s*)?\d{1,3}(?:\s*[-–])?\s*$")

# Reference extraction (populates extracted_refs on each chunk)
_ACTIVITY_CODE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}-\d{3,6}\b")
_DATE_REF = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
    re.IGNORECASE,
)

# Default sub-section heading → semantic type. Order matters: more specific
# phrases must come first so partial-match doesn't fire on the wrong key.
DEFAULT_SUBSECTION_MAP: dict[str, str] = {
    "critical path": "cp_float_analysis",
    "float analysis": "cp_float_analysis",
    "period progress": "period_progress",
    "period overview": "period_overview",
    "mitigation & opportunities": "mitigation",
    "mitigations": "mitigation",
    "mitigation": "mitigation",
    "look ahead": "lookahead",
    "lookahead": "lookahead",
    "key decisions": "key_decisions",
    "key decision": "key_decisions",
    "programme assumptions": "assumptions",
    "assumptions": "assumptions",
}


@dataclass
class ExtractedSection:
    section_number: str       # e.g. "3.2"
    parent_number: str        # top-level number, e.g. "3"
    heading: str              # cleaned heading text (no number, no page suffix)
    full_heading: str         # "3.2 Critical Path & Float Analysis"
    section_type: str | None  # mapped sub-section type, or None
    first_page: int           # 1-indexed
    text: str                 # body prose (headings excluded)


def _classify_subsection(heading: str, subsection_map: dict[str, str]) -> str | None:
    """Map a sub-section heading to its semantic type using substring match."""
    lower = heading.lower()
    for key, type_name in subsection_map.items():
        if key in lower:
            return type_name
    return None


def _is_toc_heading(raw_text: str) -> bool:
    """True if the matched heading text ends with a page-number suffix.

    Headings on the ToC pages carry trailing page numbers ('… 14', '… - 4 -');
    body headings do not. We use that to suppress the duplicate ToC entries.
    """
    return bool(_TRAILING_PAGE_NUM.search(raw_text))


def extract_sections(
    pdf_path: Path,
    *,
    subsection_map: dict[str, str] | None = None,
) -> list[ExtractedSection]:
    """Parse a PDF into a flat list of sections (level-1 and level-2).

    Headings on ToC pages are skipped — ToC pages are detected either by the
    word "Contents" near the top of the page, or by a trailing page-number on
    the heading text itself.
    """
    import pdfplumber

    sub_map = subsection_map if subsection_map is not None else DEFAULT_SUBSECTION_MAP
    sections: list[ExtractedSection] = []

    cur: dict[str, Any] = {
        "number": None,
        "parent": None,
        "heading": None,
        "full": None,
        "first_page": 1,
        "lines": [],
    }

    def _flush() -> None:
        if cur["number"] is None:
            return
        text = "\n".join(cur["lines"]).strip()
        if not text:
            return  # heading with no body — skip
        sections.append(
            ExtractedSection(
                section_number=cur["number"],
                parent_number=cur["parent"],
                heading=cur["heading"],
                full_heading=cur["full"],
                section_type=_classify_subsection(cur["heading"], sub_map),
                first_page=cur["first_page"],
                text=text,
            )
        )

    def _start(number: str, parent: str, heading: str, page_num: int) -> None:
        _flush()
        cur["number"] = number
        cur["parent"] = parent
        cur["heading"] = heading
        cur["full"] = f"{number} {heading}"
        cur["first_page"] = page_num
        cur["lines"] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""

            # Skip pages whose first lines are dominated by the word "Contents"
            first_lines = [ln.strip() for ln in text.splitlines()[:6] if ln.strip()]
            if any("contents" in ln.lower() for ln in first_lines[:3]):
                continue

            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    if cur["number"] is not None:
                        cur["lines"].append("")
                    continue

                m2 = _HEADING_L2.match(stripped)
                m1 = None if m2 else _HEADING_L1.match(stripped)

                if m2:
                    raw = m2.group(2).strip()
                    if _is_toc_heading(raw):
                        # ToC entry — close any current section but don't open this
                        _flush()
                        cur["number"] = None
                        cur["lines"] = []
                        continue
                    _start(m2.group(1), m2.group(1).split(".")[0], raw, page_num)
                elif m1:
                    raw = m1.group(2).strip()
                    if _is_toc_heading(raw):
                        _flush()
                        cur["number"] = None
                        cur["lines"] = []
                        continue
                    _start(m1.group(1), m1.group(1), raw, page_num)
                elif cur["number"] is not None:
                    cur["lines"].append(stripped)

    _flush()
    return sections


def _extracted_refs(text: str) -> dict[str, list[str]]:
    return {
        "activity_codes": sorted(set(_ACTIVITY_CODE.findall(text))),
        "dates": sorted(set(_DATE_REF.findall(text))),
    }


def sections_to_chunks(
    sections: list[ExtractedSection],
    *,
    pdf_path: Path,
    programme_id: str,
    reporting_period: str,
    project_row_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Convert extracted sections to narrative-chunk dict records.

    ``project_row_map`` maps **top-level section number** (e.g. ``"8"``) to a
    stable ``project_row_id`` (e.g. ``"PRJ-HS2"``). Sections without a mapping
    get ``project_row_id = None`` (still useful — they'll just need manual
    triage or downstream WBS matching to land).
    """
    doc_basename = pdf_path.stem
    doc_id = f"pdf:{programme_id}:{doc_basename}"
    row_map = project_row_map or {}

    chunks: list[dict[str, Any]] = []
    for sec in sections:
        chunks.append(
            {
                "chunk_id": f"pdf-{doc_basename}-s{sec.section_number}",
                "document_id": doc_id,
                "source": "pdf",
                "reporting_period": reporting_period,
                "proj_id": None,
                "project_row_id": row_map.get(sec.parent_number),
                "task_id": None,
                "task_code": None,
                "memo_type_label": None,
                "text": sec.text,
                "section_title": sec.full_heading,
                "section_type": sec.section_type,
                "section_number": sec.section_number,
                "parent_section_number": sec.parent_number,
                "first_page": sec.first_page,
                "extracted_refs": _extracted_refs(sec.text),
            }
        )
    return chunks


def extract_pdf_chunks(
    pdf_path: Path,
    *,
    programme_id: str,
    reporting_period: str,
    project_row_map: dict[str, str] | None = None,
    subsection_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """High-level entry point: PDF path → list of narrative chunk records."""
    sections = extract_sections(pdf_path, subsection_map=subsection_map)
    return sections_to_chunks(
        sections,
        pdf_path=pdf_path,
        programme_id=programme_id,
        reporting_period=reporting_period,
        project_row_map=project_row_map,
    )
