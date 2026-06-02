"""Stratified sampling for hand-labelling and selective LLM scoring.

After running ``classify-tfidf`` on the full corpus you typically want a
representative subset to (a) feed into the slower LLM engines and (b) hand-label
for evaluation. ``stratified_sample`` returns a row subset balanced across
``top_category_id`` (so every category gets covered) and optionally across
score bands (so easy/medium/hard rows are all represented).

The output CSV has the same ``id_column`` and ``text_column`` as the input
so it can be passed straight back into any ``classify-*`` command.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


_DEFAULT_BANDS = (("low", 0.0, 0.2), ("med", 0.2, 0.5), ("high", 0.5, 1.0001))


def _band(score: float) -> str:
    for name, lo, hi in _DEFAULT_BANDS:
        if lo <= score < hi:
            return name
    return "high"


def _read_row_scores(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Return ``{row_id: {top_category_id, top_sub_category_id, top_score, ...}}``."""
    path = run_dir / "row_scores.csv"
    if not path.is_file():
        raise FileNotFoundError(
            f"row_scores.csv not found in {run_dir}. "
            f"Run classify-tfidf first to produce it."
        )
    out: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                top_score = float(row.get("top_score") or 0.0)
            except (TypeError, ValueError):
                top_score = 0.0
            out[str(row["row_id"]).strip()] = {
                "top_category_id": (row.get("top_category_id") or "").strip(),
                "top_sub_category_id": (row.get("top_sub_category_id") or "").strip(),
                "top_score": top_score,
                "score_band": _band(top_score),
            }
    return out


def stratified_sample(
    *,
    input_csv: Path,
    classify_run_dir: Path,
    total: int,
    id_column: str = "row_id",
    text_column: str = "root_cause",
    by_category: bool = True,
    by_score_band: bool = True,
    random_seed: int = 42,
) -> list[dict[str, Any]]:
    """Build a stratified row sample.

    Strategy:
      1. Group rows by ``(top_category_id, score_band)`` (drop axes by config)
      2. Allocate roughly ``total / n_groups`` to each group
      3. Random sample within each group with ``random_seed``
      4. Top up randomly from the remainder if any group came up short

    Returns a list of dicts ready to write as CSV, carrying the input columns
    plus ``top_category_id``, ``top_sub_category_id``, ``top_score``,
    ``score_band``.
    """
    import random

    row_meta = _read_row_scores(classify_run_dir)

    # Load original input rows
    input_rows: list[dict[str, Any]] = []
    fieldnames: list[str] = []
    with input_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        for r in reader:
            rid = str(r.get(id_column, "")).strip()
            if not rid:
                continue
            meta = row_meta.get(rid)
            if meta is None:
                continue  # row not in classify run — skip
            r = dict(r)
            r["top_category_id"] = meta["top_category_id"]
            r["top_sub_category_id"] = meta["top_sub_category_id"]
            r["top_score"] = meta["top_score"]
            r["score_band"] = meta["score_band"]
            input_rows.append(r)

    if not input_rows:
        return []

    # Build stratification key
    def key_of(r: dict[str, Any]) -> tuple[str, ...]:
        parts: list[str] = []
        if by_category:
            parts.append(r.get("top_category_id") or "")
        if by_score_band:
            parts.append(r.get("score_band") or "")
        return tuple(parts) or ("_all",)

    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for r in input_rows:
        buckets.setdefault(key_of(r), []).append(r)

    rng = random.Random(random_seed)

    # Allocate per-bucket: at least 1 from each non-empty bucket, then balance
    n_buckets = len(buckets)
    per_bucket = max(1, total // n_buckets)

    sample: list[dict[str, Any]] = []
    leftovers: list[dict[str, Any]] = []
    for bucket in buckets.values():
        if len(bucket) <= per_bucket:
            sample.extend(bucket)
        else:
            shuffled = bucket.copy()
            rng.shuffle(shuffled)
            sample.extend(shuffled[:per_bucket])
            leftovers.extend(shuffled[per_bucket:])

    # Top up to target by drawing from leftovers
    if len(sample) < total and leftovers:
        rng.shuffle(leftovers)
        sample.extend(leftovers[: total - len(sample)])

    # Trim if we overshot
    if len(sample) > total:
        rng.shuffle(sample)
        sample = sample[:total]

    # Sort for deterministic output
    sample.sort(key=lambda r: (r.get("top_category_id") or "", r.get(id_column) or ""))
    return sample


def write_sample_csv(
    sample: list[dict[str, Any]],
    out_path: Path,
    *,
    extra_fieldnames: list[str] = (
        "top_category_id", "top_sub_category_id", "top_score", "score_band",
    ),
) -> None:
    """Write the sample, preserving original input columns + sampler metadata."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not sample:
        out_path.write_text("", encoding="utf-8-sig")
        return

    base_fields = [k for k in sample[0].keys() if k not in extra_fieldnames]
    fieldnames = base_fields + list(extra_fieldnames)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sample)


def run_sample(
    *,
    input_csv: Path,
    classify_run_dir: Path,
    out_csv: Path,
    total: int,
    id_column: str = "row_id",
    text_column: str = "root_cause",
    by_category: bool = True,
    by_score_band: bool = True,
    random_seed: int = 42,
) -> dict[str, int]:
    """CLI entry: build a stratified sample and write it as CSV."""
    sample = stratified_sample(
        input_csv=input_csv,
        classify_run_dir=classify_run_dir,
        total=total,
        id_column=id_column,
        text_column=text_column,
        by_category=by_category,
        by_score_band=by_score_band,
        random_seed=random_seed,
    )
    write_sample_csv(sample, out_csv)
    # Count per-category distribution for the summary
    by_cat: dict[str, int] = {}
    for r in sample:
        cat = r.get("top_category_id") or "(none)"
        by_cat[cat] = by_cat.get(cat, 0) + 1
    return {"sampled": len(sample), "categories": len(by_cat), "out": str(out_csv), "per_category": by_cat}
