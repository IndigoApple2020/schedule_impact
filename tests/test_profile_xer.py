"""Tests for anonymized XER profiling (synthetic fixtures only)."""

from pathlib import Path

import pytest

from schedule_impact.tools.profile_xer import profile_xer_file, pair_match_stats

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


def test_profile_lists_tables_and_counts() -> None:
    report = profile_xer_file(FIXTURES / "2025-04.xer", tables=["TASK", "PROJECT"])
    assert "TASK" in report.tables
    assert report.tables["TASK"].row_count == 3
    col_names = [c.name for c in report.tables["TASK"].columns]
    assert "task_code" in col_names
    assert report.source_basename == "2025-04.xer"


def test_pair_match_rates_without_exposing_codes() -> None:
    stats = pair_match_stats(FIXTURES / "2025-03.xer", FIXTURES / "2025-04.xer")
    assert len(stats) == 1
    s = stats[0]
    assert s.key == "task_code"
    assert s.matched == 3
    assert s.match_rate_previous == 1.0
    assert s.match_rate_current == 1.0
