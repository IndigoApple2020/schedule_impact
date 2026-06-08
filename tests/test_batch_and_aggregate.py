"""Tests for run-batch and aggregate-memos."""

import csv
import shutil
from pathlib import Path

import pytest

from schedule_impact.tools.aggregate_memos import aggregate
from schedule_impact.tools.batch_runner import discover_periods, find_pdf, run_batch

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


# ----------------------- batch runner -----------------------

def _build_xer_root(tmp_path: Path) -> Path:
    """Lay out two period subdirectories pointing at the synthetic fixtures."""
    root = tmp_path / "xer"
    (root / "2025-03").mkdir(parents=True)
    (root / "2025-04").mkdir(parents=True)
    shutil.copy(FIXTURES / "2025-03.xer", root / "2025-03" / "schedule.xer")
    shutil.copy(FIXTURES / "2025-04.xer", root / "2025-04" / "schedule.xer")
    return root


def test_discover_periods_sorts_chronologically(tmp_path: Path) -> None:
    root = _build_xer_root(tmp_path)
    found = discover_periods(root)
    assert [p for p, _ in found] == ["2025-03", "2025-04"]


def test_find_pdf_returns_none_when_no_pdf_root(tmp_path: Path) -> None:
    assert find_pdf(None, "2025-04") is None


def test_run_batch_processes_consecutive_pairs(tmp_path: Path) -> None:
    xer_root = _build_xer_root(tmp_path)
    out_dir = tmp_path / "out"
    summary = run_batch(
        programme_id="test",
        xer_root=xer_root,
        output_dir=out_dir,
    )
    # 2 periods → 1 consecutive pair
    assert summary["pairs_attempted"] == 1
    assert summary["pairs_succeeded"] == 1
    assert summary["pairs_failed"] == 0
    assert (out_dir / "test" / "2025-04" / "incidents_2025-04.csv").is_file()


def test_run_batch_skip_existing(tmp_path: Path) -> None:
    xer_root = _build_xer_root(tmp_path)
    out_dir = tmp_path / "out"
    # First pass — produces output
    run_batch(programme_id="test", xer_root=xer_root, output_dir=out_dir)
    # Second pass with skip_existing — should skip the same period
    summary = run_batch(
        programme_id="test", xer_root=xer_root,
        output_dir=out_dir, skip_existing=True,
    )
    assert summary["pairs_skipped"] == 1
    assert summary["pairs_succeeded"] == 0


# ----------------------- aggregate memos -----------------------

def test_aggregate_combines_multiple_period_csvs(tmp_path: Path) -> None:
    # Build a fake outputs tree with two periods, each with a memo CSV
    out_root = tmp_path / "outputs"
    for period in ("2025-03", "2025-04"):
        period_dir = out_root / "HS2" / period
        period_dir.mkdir(parents=True)
        with (period_dir / f"taskmemo_chunks_{period}.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as f:
            w = csv.DictWriter(f, fieldnames=[
                "chunk_id", "document_id", "source", "reporting_period",
                "proj_id", "task_id", "task_code",
                "memo_type_id", "memo_type_label", "section_title", "text",
            ])
            w.writeheader()
            w.writerow({
                "chunk_id": f"xer-memo-{period}-1",
                "document_id": "doc",
                "source": "xer_taskmemo",
                "reporting_period": period,
                "proj_id": "P1",
                "task_id": "T1",
                "task_code": "A100",
                "memo_type_id": "1",
                "memo_type_label": "Planners Notes",
                "section_title": "Planners Notes",
                "text": f"Rework noted in {period}",
            })

    aggregated = tmp_path / "all_memos.csv"
    counts = aggregate(outputs_root=out_root, out_csv=aggregated)

    assert counts["files"] == 2
    assert counts["rows"] == 2
    assert counts["periods"] == 2

    with aggregated.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    # text-classify default column names are present
    assert "row_id" in rows[0]
    assert "root_cause" in rows[0]
    assert rows[0]["root_cause"].startswith("Rework noted")
    # Programme is inferred from outputs/<programme>/<period>/ layout
    assert all(r["programme_id"] == "HS2" for r in rows)


def test_aggregate_skips_empty_memos(tmp_path: Path) -> None:
    out_root = tmp_path / "outputs"
    period_dir = out_root / "HS2" / "2025-04"
    period_dir.mkdir(parents=True)
    with (period_dir / "taskmemo_chunks_2025-04.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        w = csv.DictWriter(f, fieldnames=["chunk_id", "text", "reporting_period"])
        w.writeheader()
        w.writerow({"chunk_id": "c1", "text": "Real memo", "reporting_period": "2025-04"})
        w.writerow({"chunk_id": "c2", "text": "", "reporting_period": "2025-04"})

    aggregated = tmp_path / "all_memos.csv"
    counts = aggregate(outputs_root=out_root, out_csv=aggregated)
    # Empty memo dropped
    assert counts["rows"] == 1
