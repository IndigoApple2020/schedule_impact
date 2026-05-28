"""Labeling export, stats, and training."""

from pathlib import Path

import pytest

from schedule_impact.labeling.export_memos import export_memos
from schedule_impact.labeling.labels_io import parse_quality_label
from schedule_impact.labeling.stats import stats_from_csv

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"
OUT = Path(__file__).parent / "fixtures" / "labeling_tmp"


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


def test_export_and_stats(tmp_path_factory: pytest.TempPathFactory) -> None:
    out = tmp_path_factory.mktemp("label") / "memos.csv"
    n = export_memos(
        xer_paths=[FIXTURES / "2025-04.xer"],
        out_path=out,
        programme_id="test",
    )
    assert n == 1

    # Simulate team labels
    text = out.read_text(encoding="utf-8-sig")
    labeled = text.replace(
        ",,,,",
        ",yes,defect rework,reviewer,2025-05-01,",
        1,
    )
    labeled_path = out.with_name("labeled.csv")
    labeled_path.write_text(labeled, encoding="utf-8-sig")

    stats = stats_from_csv(labeled_path)
    assert stats.labeled_rows == 1
    assert stats.quality_yes == 1
    assert stats.quality_rate == 1.0


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("yes", True),
        ("no", False),
        ("", None),
    ],
)
def test_parse_labels(raw: str, expected: bool | None) -> None:
    assert parse_quality_label(raw) is expected


def test_train_requires_ml_extra(tmp_path_factory: pytest.TempPathFactory) -> None:
    pytest.importorskip("sklearn")
    from schedule_impact.labeling.train_quality import train_quality_classifier

    out = tmp_path_factory.mktemp("train") / "labeled.csv"
    export_memos(xer_paths=[FIXTURES / "2025-04.xer"], out_path=out, programme_id="test")
    rows = out.read_text(encoding="utf-8-sig").strip().splitlines()
    header = rows[0]
    # duplicate row with opposite label for training
    data = rows[1]
    yes = data.replace(",,,,", ",yes,,a,2025-05-01,")
    no = data.replace(",,,,", ",no,,b,2025-05-01,").replace("SYN-200", "SYN-201")
    out.write_text("\n".join([header, yes, no]) + "\n", encoding="utf-8-sig")

    model = tmp_path_factory.mktemp("model") / "clf.joblib"
    report = tmp_path_factory.mktemp("rep") / "report.json"
    result = train_quality_classifier(out, model, min_samples=2)
    assert result.n_samples == 2
    assert model.is_file()
