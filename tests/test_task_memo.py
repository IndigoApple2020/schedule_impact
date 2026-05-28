"""TASKMEMO HTML stripping and narrative chunk extraction."""

from pathlib import Path

import pytest

from schedule_impact.ingest.xer_reader import load_xer_tables
from schedule_impact.normalize.html_memo import strip_html_memo
from schedule_impact.normalize.task_memo import memos_to_narrative_chunks

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"

SAMPLE_HTML = (
    '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">'
    "<HTML><HEAD><META content=\"text/html; charset=unicode\"></HEAD>"
    "<BODY><P>GC - 3056</P>"
    "<P>PfA update - on track. Impacted by detail design assurance</P></BODY></HTML>"
)


@pytest.fixture(scope="module", autouse=True)
def ensure_fixtures() -> None:
    if not (FIXTURES / "2025-04.xer").exists():
        import subprocess
        import sys

        repo_root = Path(__file__).resolve().parents[1]
        subprocess.run(
            [sys.executable, str(repo_root / "scripts" / "build_synthetic_fixtures.py")],
            check=True,
        )


def test_strip_html_removes_tags_and_controls() -> None:
    raw = SAMPLE_HTML.replace("<P>", "\x7f\x7f<P>")
    text = strip_html_memo(raw)
    assert "<HTML>" not in text
    assert "GC - 3056" in text
    assert "detail design" in text


def test_memos_to_chunks_from_fixture() -> None:
    tables = load_xer_tables(
        FIXTURES / "2025-04.xer",
        tables=("TASK", "TASKMEMO", "MEMOTYPE"),
    )
    chunks = memos_to_narrative_chunks(
        tables["TASKMEMO"],
        tables["MEMOTYPE"],
        tables["TASK"],
        reporting_period="2025-04",
        programme_id="test_programme",
        planner_labels=["Planners Notes (CP)"],
    )
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["source"] == "xer_taskmemo"
    assert chunk["task_code"] == "SYN-200"
    assert "rework" in chunk["text"].lower()
    assert chunk["memo_type_label"] == "Planners Notes (CP)"
