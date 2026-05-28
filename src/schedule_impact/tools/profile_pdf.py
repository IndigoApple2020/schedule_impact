"""Build anonymized PDF structure reports — section headings and stats, no body text."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_ACTIVITY_CODE_PATTERN = re.compile(r"\b[A-Z]{2,5}-\d{3,6}\b")
_DATE_PATTERN = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
    re.IGNORECASE,
)
# Numbered heading patterns matching standard programme narrative ToC format:
#   Level 1:  "3  Consolidation"  or  "9  LU"
#   Level 2:  "8.2  Critical Path & Float Analysis"
_HEADING_L1 = re.compile(r"^(\d{1,2})\s{1,8}([A-Z][\w\s₂&/()–-]{1,70})\s*$")
_HEADING_L2 = re.compile(r"^(\d{1,2}\.\d{1,2})\s{1,8}([A-Z][\w\s&/()–-]{1,70})\s*$")


@dataclass
class SectionProfile:
    heading: str                    # heading text — structural metadata equivalent to a ToC entry
    level: int                      # 1 = top-level section, 2 = sub-section
    first_page: int                 # 1-indexed page number
    char_count: int                 # body text character count below this heading
    table_count: int                # tables found within this section
    table_shapes: list[list[int]]   # [[rows, cols], ...] per table


@dataclass
class PdfProfileReport:
    generated_at: str
    source_basename: str
    sha256_prefix: str
    page_count: int
    character_count: int
    sections: list[SectionProfile]
    activity_code_like_count: int
    date_like_count: int
    extract_error: str | None = None
    notes: list[str] = field(default_factory=list)


def _sha256_prefix(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:12]


def _extract_sections(
    path: Path,
) -> tuple[list[SectionProfile], int, int, int, int]:
    """Return (sections, page_count, total_chars, activity_code_count, date_count).

    Headings are retained as structural metadata (equivalent to a document ToC).
    No narrative body text is included in the output.
    """
    import pdfplumber

    sections: list[SectionProfile] = []

    # Mutable accumulator for the section currently being built
    acc: dict = {
        "heading": None,
        "level": 1,
        "first_page": 1,
        "body_chars": 0,
        "tables": [],
    }

    def _flush() -> None:
        if acc["heading"] is not None:
            sections.append(
                SectionProfile(
                    heading=acc["heading"],
                    level=acc["level"],
                    first_page=acc["first_page"],
                    char_count=acc["body_chars"],
                    table_count=len(acc["tables"]),
                    table_shapes=list(acc["tables"]),
                )
            )

    def _start(heading: str, level: int, page_num: int) -> None:
        _flush()
        acc["heading"] = heading
        acc["level"] = level
        acc["first_page"] = page_num
        acc["body_chars"] = 0
        acc["tables"] = []

    total_chars = 0
    activity_count = 0
    date_count = 0
    page_count = 0

    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            total_chars += len(text)
            activity_count += len(_ACTIVITY_CODE_PATTERN.findall(text))
            date_count += len(_DATE_PATTERN.findall(text))

            # Skip ToC pages: first 5 non-empty lines contain "contents"
            first_lines = [ln.strip() for ln in text.splitlines()[:6] if ln.strip()]
            if any("contents" in ln.lower() for ln in first_lines[:3]):
                continue

            # Extract tables on this page: record shape only, not cell values
            raw_tables = page.extract_tables() or []
            page_table_shapes: list[list[int]] = [
                [len(tbl), len(tbl[0])]
                for tbl in raw_tables
                if tbl and tbl[0]
            ]

            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                m2 = _HEADING_L2.match(stripped)
                m1 = None if m2 else _HEADING_L1.match(stripped)
                if m2:
                    _start(stripped, 2, page_num)
                elif m1:
                    _start(stripped, 1, page_num)
                elif acc["heading"] is not None:
                    acc["body_chars"] += len(stripped) + 1

            # Attribute this page's tables to the current section
            acc["tables"].extend(page_table_shapes)

    _flush()
    return sections, page_count, total_chars, activity_count, date_count


def profile_pdf_file(path: Path) -> PdfProfileReport:
    if not path.is_file():
        raise FileNotFoundError(path)

    report = PdfProfileReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_basename=path.name,
        sha256_prefix=_sha256_prefix(path),
        page_count=0,
        character_count=0,
        sections=[],
        activity_code_like_count=0,
        date_like_count=0,
    )
    try:
        sections, page_count, total_chars, activity_count, date_count = _extract_sections(path)
    except Exception as exc:
        report.extract_error = type(exc).__name__
        report.notes.append(f"Extraction failed ({exc}); only file metadata recorded.")
        return report

    report.page_count = page_count
    report.character_count = total_chars
    report.sections = sections
    report.activity_code_like_count = activity_count
    report.date_like_count = date_count

    top_sections = [s.heading for s in sections if s.level == 1]
    sub_types = sorted({s.heading.split(maxsplit=2)[-1] for s in sections if s.level == 2})
    table_sections = [s.heading for s in sections if s.table_count > 0]

    report.notes.append(
        f"{len(sections)} sections detected "
        f"({len(top_sections)} top-level, {len(sections) - len(top_sections)} sub-sections)."
    )
    if sub_types:
        report.notes.append(f"Sub-section types seen: {sub_types}")
    if table_sections:
        report.notes.append(f"Sections containing tables: {table_sections}")
    report.notes.append("No narrative body text included in this report.")
    return report


def write_report(report: PdfProfileReport, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")


def run_profile(pdf: Path, out: Path) -> PdfProfileReport:
    report = profile_pdf_file(pdf)
    write_report(report, out)
    return report
