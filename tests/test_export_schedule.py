"""Tests for export-schedule (CSV dump + task delta)."""

import csv
from pathlib import Path

import pytest

from schedule_impact.tools.export_schedule import export_task_changes, export_xer_tables, run_export

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


def test_export_xer_tables_writes_csvs(tmp_path: Path) -> None:
    counts = export_xer_tables(FIXTURES / "2025-04.xer", tmp_path)
    assert counts.get("TASK") == 3
    assert (tmp_path / "TASK.csv").is_file()
    assert (tmp_path / "PROJECT.csv").is_file()

    # Read TASK.csv and check it has the expected columns and rows
    with (tmp_path / "TASK.csv").open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert len(rows) == 3
        assert "task_code" in rows[0]
        assert "early_end_date" in rows[0]


def test_export_task_changes_categorizes(tmp_path: Path) -> None:
    out = tmp_path / "task_changes.csv"
    counts = export_task_changes(FIXTURES / "2025-04.xer", FIXTURES / "2025-03.xer", out)
    assert out.is_file()
    # All 3 synthetic tasks share codes between months — should produce 3 rows
    assert counts["_total_rows"] == 3
    # Two tasks (SYN-100 and SYN-200) have finish dates that moved later → slipped
    assert counts.get("slipped", 0) >= 2


def test_run_export_with_previous_xer(tmp_path: Path) -> None:
    summary = run_export(
        current_xer=FIXTURES / "2025-04.xer",
        out_dir=tmp_path,
        previous_xer=FIXTURES / "2025-03.xer",
    )
    assert "tables" in summary
    assert "task_changes" in summary
    assert (tmp_path / "task_changes.csv").is_file()
    assert (tmp_path / "TASK.csv").is_file()
