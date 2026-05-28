"""Train a baseline text classifier from labeled TASKMEMO exports."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from schedule_impact.labeling.labels_io import labeled_rows, read_labeling_csv


@dataclass
class TrainReport:
    trained_at: str
    n_samples: int
    n_quality: int
    n_not_quality: int
    test_size: float
    accuracy: float | None
    f1_quality: float | None
    model_path: str
    warnings: list[str]


def train_quality_classifier(
    labels_csv: Path,
    model_out: Path,
    *,
    test_size: float = 0.2,
    random_state: int = 42,
    min_samples: int = 20,
) -> TrainReport:
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, f1_score
        from sklearn.model_selection import train_test_split
        import joblib
    except ImportError as exc:
        raise ImportError(
            "Install ML extras: pip install -e \".[ml]\""
        ) from exc

    rows = labeled_rows(read_labeling_csv(labels_csv))
    warnings: list[str] = []

    texts = [r["text_plain"] for r in rows]
    y = [bool(r["_label_bool"]) for r in rows]
    n_yes = sum(y)
    n_no = len(y) - n_yes

    if len(rows) < min_samples:
        warnings.append(f"Only {len(rows)} labeled rows; recommend >={min_samples} before trusting metrics.")
    if n_yes == 0 or n_no == 0:
        raise ValueError("Need at least one positive and one negative labeled example.")

    effective_test = test_size if len(rows) >= 10 else 0.0
    if effective_test > 0:
        X_train, X_test, y_train, y_test = train_test_split(
            texts,
            y,
            test_size=effective_test,
            random_state=random_state,
            stratify=y if min(n_yes, n_no) >= 2 else None,
        )
    else:
        X_train, y_train = texts, y
        X_test, y_test = [], []

    pipeline = _build_pipeline(TfidfVectorizer, LogisticRegression, n_samples=len(rows))
    pipeline.fit(X_train, y_train)

    accuracy: float | None = None
    f1: float | None = None
    if X_test:
        pred = pipeline.predict(X_test)
        accuracy = float(accuracy_score(y_test, pred))
        f1 = float(f1_score(y_test, pred, pos_label=True, zero_division=0))
    elif effective_test == 0:
        warnings.append("Hold-out metrics skipped (fewer than 10 labeled rows).")

    model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, model_out)

    return TrainReport(
        trained_at=datetime.now(timezone.utc).isoformat(),
        n_samples=len(rows),
        n_quality=n_yes,
        n_not_quality=n_no,
        test_size=test_size,
        accuracy=round(accuracy, 4) if accuracy is not None else None,
        f1_quality=round(f1, 4) if f1 is not None else None,
        model_path=str(model_out),
        warnings=warnings,
    )


def _build_pipeline(vectorizer_cls, classifier_cls, *, n_samples: int):
    from sklearn.pipeline import Pipeline

    # min_df=2 needs several documents; use 1 for small labeling pilots
    min_df = 2 if n_samples >= 30 else 1
    return Pipeline(
        [
            (
                "tfidf",
                vectorizer_cls(max_features=50_000, ngram_range=(1, 2), min_df=min_df),
            ),
            ("clf", classifier_cls(max_iter=1000, class_weight="balanced")),
        ]
    )


def predict_quality(text: str, model_path: Path) -> tuple[bool, float]:
    """Return (is_quality, probability_quality)."""
    import joblib

    pipeline = joblib.load(model_path)
    proba = pipeline.predict_proba([text])[0]
    classes = list(pipeline.named_steps["clf"].classes_)
    if True in classes:
        idx = classes.index(True)
    else:
        idx = 1
    p = float(proba[idx])
    return p >= 0.5, p


def write_train_report(report: TrainReport, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
