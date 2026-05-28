"""Tests for text XER parsing."""

from pathlib import Path

import pytest

from schedule_impact.ingest.xer_reader import detect_xer_format, load_xer_tables
from schedule_impact.ingest.xer_text_parser import parse_text_xer, table_to_records

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


@pytest.fixture(scope="module", autouse=True)
def ensure_fixtures() -> None:
    if not (FIXTURES / "2025-03.xer").exists():
        import subprocess
        import sys

        repo_root = Path(__file__).resolve().parents[1]
        subprocess.run(
            [sys.executable, str(repo_root / "scripts" / "build_synthetic_fixtures.py")],
            check=True,
        )


def test_detects_text_format() -> None:
    assert detect_xer_format(FIXTURES / "2025-04.xer") == "text"


def test_load_task_rows() -> None:
    tables = load_xer_tables(FIXTURES / "2025-04.xer", tables=("TASK",))
    tasks = tables["TASK"]
    assert len(tasks) == 3
    assert tasks[0]["task_code"] == "SYN-100"
    assert tasks[0]["early_end_date"] == "2025-03-22 16:00"
    assert tasks[0]["driving_path_flag"] == "Y"


def test_parse_selected_tables_only() -> None:
    parsed = parse_text_xer(FIXTURES / "2025-04.xer", tables={"TASK"})
    assert "PROJECT" not in parsed
    records = table_to_records(parsed["TASK"])
    assert len(records) == 3
