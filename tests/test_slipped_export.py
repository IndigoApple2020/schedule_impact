"""Export memos filtered to slipped tasks only."""

from pathlib import Path

import pytest

from schedule_impact.labeling.export_memos import export_memos

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


def test_slipped_only_exports_memo_on_slipped_task(tmp_path: Path) -> None:
    out = tmp_path / "slipped.csv"
    n = export_memos(
        xer_paths=[FIXTURES / "2025-04.xer"],
        out_path=out,
        programme_id="test",
        previous_xer=FIXTURES / "2025-03.xer",
        slipped_only=True,
        min_slip_days=1,
    )
    assert n == 1
    text = out.read_text(encoding="utf-8-sig")
    assert "SYN-200" in text
    assert "finish_slip_days" in text


def test_slipped_only_excludes_stable_task(tmp_path: Path) -> None:
    out = tmp_path / "slipped.csv"
    export_memos(
        xer_paths=[FIXTURES / "2025-04.xer"],
        out_path=out,
        programme_id="test",
        previous_xer=FIXTURES / "2025-03.xer",
        slipped_only=True,
    )
    text = out.read_text(encoding="utf-8-sig")
    assert "SYN-300" not in text
