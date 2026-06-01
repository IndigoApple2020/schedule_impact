"""Unit tests for PDF extraction helpers (no real PDF required)."""

from pathlib import Path

from schedule_impact.ingest.pdf_extractor import (
    DEFAULT_SUBSECTION_MAP,
    ExtractedSection,
    _classify_subsection,
    _extracted_refs,
    _is_toc_heading,
    sections_to_chunks,
)


def test_classify_subsection_known_types() -> None:
    assert _classify_subsection("Period Overview", DEFAULT_SUBSECTION_MAP) == "period_overview"
    assert _classify_subsection("Critical Path & Float Analysis", DEFAULT_SUBSECTION_MAP) == "cp_float_analysis"
    assert _classify_subsection("Critical Path and Float Analysis", DEFAULT_SUBSECTION_MAP) == "cp_float_analysis"
    assert _classify_subsection("Mitigation", DEFAULT_SUBSECTION_MAP) == "mitigation"
    assert _classify_subsection("Mitigation & Opportunities", DEFAULT_SUBSECTION_MAP) == "mitigation"
    assert _classify_subsection("Mitigations", DEFAULT_SUBSECTION_MAP) == "mitigation"
    assert _classify_subsection("Look Ahead", DEFAULT_SUBSECTION_MAP) == "lookahead"
    assert _classify_subsection("Key Decisions", DEFAULT_SUBSECTION_MAP) == "key_decisions"
    assert _classify_subsection("Programme Assumptions", DEFAULT_SUBSECTION_MAP) == "assumptions"


def test_classify_distinguishes_progress_from_overview() -> None:
    assert _classify_subsection("Period Progress", DEFAULT_SUBSECTION_MAP) == "period_progress"
    assert _classify_subsection("Period Overview", DEFAULT_SUBSECTION_MAP) == "period_overview"


def test_classify_unknown_returns_none() -> None:
    assert _classify_subsection("Something Else", DEFAULT_SUBSECTION_MAP) is None


def test_is_toc_heading_detects_page_suffix() -> None:
    assert _is_toc_heading("Period Overview 14")
    assert _is_toc_heading("Critical Path and Float Analysis 36")
    assert _is_toc_heading("Main Works - 4 -")


def test_is_toc_heading_body_heading_is_not_toc() -> None:
    assert not _is_toc_heading("Period Overview")
    assert not _is_toc_heading("Critical Path & Float Analysis")
    assert not _is_toc_heading("Mitigation")


def test_extracted_refs_finds_codes_and_dates() -> None:
    text = "EUS-12345 slipped past 12/05/2025; see also HS2-9001 review on 3 Jun 2025."
    refs = _extracted_refs(text)
    assert "EUS-12345" in refs["activity_codes"]
    assert "HS2-9001" in refs["activity_codes"]
    assert any("2025" in d for d in refs["dates"])


def test_sections_to_chunks_maps_project_row() -> None:
    sections = [
        ExtractedSection(
            section_number="8.2",
            parent_number="8",
            heading="Critical Path & Float Analysis",
            full_heading="8.2 Critical Path & Float Analysis",
            section_type="cp_float_analysis",
            first_page=21,
            text="Some HS2 critical path narrative.",
        ),
        ExtractedSection(
            section_number="9.1",
            parent_number="9",
            heading="Period Overview",
            full_heading="9.1 Period Overview",
            section_type="period_overview",
            first_page=22,
            text="LU period overview prose.",
        ),
    ]
    chunks = sections_to_chunks(
        sections,
        pdf_path=Path("Programme_Narrative_Apr2025.pdf"),
        programme_id="HS2",
        reporting_period="2025-04",
        project_row_map={"8": "PRJ-HS2", "9": "PRJ-LU"},
    )
    assert len(chunks) == 2
    assert chunks[0]["project_row_id"] == "PRJ-HS2"
    assert chunks[0]["section_type"] == "cp_float_analysis"
    assert chunks[0]["source"] == "pdf"
    assert chunks[0]["chunk_id"] == "pdf-Programme_Narrative_Apr2025-s8.2"
    assert chunks[1]["project_row_id"] == "PRJ-LU"


def test_sections_to_chunks_unmapped_section_has_none_project_row() -> None:
    sections = [
        ExtractedSection(
            section_number="3.1",
            parent_number="3",
            heading="Period Overview",
            full_heading="3.1 Period Overview",
            section_type="period_overview",
            first_page=14,
            text="Consolidation overview.",
        ),
    ]
    chunks = sections_to_chunks(
        sections,
        pdf_path=Path("narrative.pdf"),
        programme_id="HS2",
        reporting_period="2025-04",
        project_row_map={"8": "PRJ-HS2"},
    )
    assert chunks[0]["project_row_id"] is None
