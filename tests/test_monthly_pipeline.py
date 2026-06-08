"""End-to-end monthly pipeline on synthetic XER."""

from pathlib import Path

import pytest

from schedule_impact.pipeline.run_monthly import run_monthly

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


def test_run_monthly_detects_impact_and_float(tmp_path: Path) -> None:
    result = run_monthly(
        programme_id="test",
        current_xer=FIXTURES / "2025-04.xer",
        previous_xer=FIXTURES / "2025-03.xer",
        reporting_period="2025-04",
        previous_period="2025-03",
        output_dir=tmp_path,
    )
    assert result.impact_count >= 1
    assert result.float_count >= 1
    assert (result.output_dir / "incidents_2025-04.csv").is_file()
    assert (result.output_dir / "quality_assessment_2025-04.csv").is_file()
    # TASKMEMO body text is preserved in its own chunks CSV (not just the links)
    assert (result.output_dir / "taskmemo_chunks_2025-04.csv").is_file()
    assert result.memo_chunk_count >= 1
    # SYN-200 has memo about rework — keyword quality on float incident
    assert result.link_count >= 1
