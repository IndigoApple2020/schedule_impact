"""Top-level orchestrators: read CSV → score → write the full output set.

Output set per run:
  matches.csv                  — sub-category matches above threshold
  category_matches.csv         — category-level matches above threshold
  row_scores.csv               — top sub-category match per input row
  all_scores_sub_long.csv      — every (row × sub_category) score
  all_scores_sub_wide.csv      — wide pivot of the same
  all_scores_cat_long.csv      — every (row × category) score
  all_scores_cat_wide.csv      — wide pivot
  keywords.csv                 — cross-row phrase mining (optional)

For multi-engine runs:
  combined_scores_sub.csv      — one wide row per (row, sub_category) with
                                 columns for tfidf_score / llm_embed_score /
                                 llm_prompt_score (only the engines that ran)
  combined_scores_cat.csv      — same for category level
"""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from text_classify.keyword_discovery import KeywordConfig, discover, discover_stratified
from text_classify.llm_classifier import (
    LlmEmbedConfig,
    LlmPromptConfig,
    score_all_embed,
    score_all_prompt,
)
from text_classify.schemas import KeywordRecord, MatchRecord, RowScore, ScoringResult, Taxonomy
from text_classify.taxonomy import load_taxonomy
from text_classify.tfidf_classifier import TfidfConfig, score_all

Engine = Literal["tfidf", "llm_embed", "llm_prompt"]


def _make_run_id(taxonomy: Taxonomy, engines: list[Engine]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    fp = hashlib.sha1(
        f"{taxonomy.name}-v{taxonomy.version}-{','.join(engines)}".encode()
    ).hexdigest()[:8]
    label = "-".join(engines) if len(engines) > 1 else engines[0]
    return f"{stamp}-{label}-{fp}"


def _read_input(input_csv: Path, *, id_column: str, text_column: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with input_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if id_column not in (reader.fieldnames or []):
            raise ValueError(f"id column '{id_column}' not in {input_csv}. Columns: {reader.fieldnames}")
        if text_column not in (reader.fieldnames or []):
            raise ValueError(f"text column '{text_column}' not in {input_csv}. Columns: {reader.fieldnames}")
        for row in reader:
            rows.append((str(row[id_column]).strip(), str(row.get(text_column, ""))))
    return rows


def _write_csv(path: Path, records: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def _write_parquet_optional(csv_path: Path, records: list[dict], fieldnames: list[str]) -> None:
    """Write a Parquet sibling for fast pandas re-reads.

    Silently skipped if pandas + a parquet engine (pyarrow / fastparquet) are
    not available. Skipped for empty data. Path is the CSV path with the
    extension swapped to ``.parquet``.
    """
    if not records:
        return
    try:
        import pandas as pd  # pandas is a core dep
    except ImportError:
        return
    parquet_path = csv_path.with_suffix(".parquet")
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df = pd.DataFrame(records, columns=fieldnames)
        df.to_parquet(parquet_path, index=False)
    except (ImportError, ValueError, RuntimeError, OSError):
        # No parquet engine installed (pyarrow / fastparquet missing) — silently skip
        pass


_MATCH_FIELDS = [
    "row_id", "category_id", "sub_category_id", "score",
    "method", "signals", "taxonomy_version", "run_id",
]
_ROW_SCORE_FIELDS = [
    "row_id", "top_category_id", "top_sub_category_id", "top_score",
    "n_matches_above_threshold", "match_summary",
]
_KEYWORD_FIELDS = [
    "phrase", "doc_count", "mean_tfidf", "interestingness", "sample_row_ids",
]
_KEYWORD_BY_CAT_FIELDS = [
    "category_id", "phrase", "doc_count", "mean_tfidf", "interestingness", "sample_row_ids",
]


def _write_stratified_keywords(path: Path, by_cat: dict) -> None:
    """Flatten ``{cat_id: [KeywordRecord, ...]}`` into a single CSV."""
    rows: list[dict] = []
    for cat_id in sorted(by_cat.keys()):
        for k in by_cat[cat_id]:
            rows.append({
                "category_id": cat_id,
                "phrase": k.phrase,
                "doc_count": k.doc_count,
                "mean_tfidf": k.mean_tfidf,
                "interestingness": k.interestingness,
                "sample_row_ids": "; ".join(k.sample_row_ids),
            })
    _write_csv(path, rows, _KEYWORD_BY_CAT_FIELDS)


def _score_with(
    engine: Engine,
    rows,
    taxonomy,
    threshold,
    run_id,
    llm_model,
    *,
    checkpoint_dir: Path | None = None,
    llm_backend: str | None = None,
    llm_base_url: str | None = None,
    llm_api_key: str | None = None,
) -> ScoringResult:
    if engine == "tfidf":
        cfg = TfidfConfig(threshold=threshold or taxonomy.default_threshold)
        return score_all(rows, taxonomy, config=cfg, run_id=run_id)

    # Build LLM kwargs only including fields the user actually overrode
    llm_kwargs: dict[str, Any] = {}
    if llm_model:    llm_kwargs["model"]    = llm_model
    if llm_backend:  llm_kwargs["backend"]  = llm_backend
    if llm_base_url: llm_kwargs["base_url"] = llm_base_url
    if llm_api_key:  llm_kwargs["api_key"]  = llm_api_key

    if engine == "llm_embed":
        cfg_e = LlmEmbedConfig(threshold=threshold or 0.55, **llm_kwargs)
        return score_all_embed(rows, taxonomy, config=cfg_e, run_id=run_id)
    if engine == "llm_prompt":
        if checkpoint_dir is not None:
            llm_kwargs["checkpoint_path"] = str(checkpoint_dir / "llm_prompt_checkpoint.ndjson")
        cfg_p = LlmPromptConfig(threshold=threshold or 0.5, **llm_kwargs)
        return score_all_prompt(rows, taxonomy, config=cfg_p, run_id=run_id)
    raise ValueError(f"Unknown engine: {engine}")


def _wide_scores(records: list[MatchRecord], *, key: str, level: Literal["sub", "cat"]) -> tuple[list[dict], list[str]]:
    """Pivot long-format scores into one row per input row × one column per target.

    Column name format:
      sub level:  '{category_id}.{sub_category_id}'
      cat level:  '{category_id}'

    Targets appear in first-seen order (which, for our scoring engines, is
    the taxonomy declaration order). Rows are sorted by ``row_id`` for
    determinism.
    """
    targets: list[str] = []
    seen_targets: set[str] = set()
    by_row: dict[str, dict[str, float]] = defaultdict(dict)
    row_id_order: list[str] = []

    for rec in records:
        col = (
            f"{rec.category_id}.{rec.sub_category_id}"
            if level == "sub"
            else rec.category_id
        )
        if col not in seen_targets:
            seen_targets.add(col)
            targets.append(col)
        if rec.row_id not in by_row:
            row_id_order.append(rec.row_id)
        by_row[rec.row_id][col] = rec.score

    fieldnames = ["row_id", *targets]
    rows = []
    for row_id in sorted(row_id_order):
        scores = by_row[row_id]
        out: dict[str, Any] = {"row_id": row_id}
        for t in targets:
            out[t] = scores.get(t, "")
        rows.append(out)
    return rows, fieldnames


def _combined_wide(results: list[ScoringResult], *, level: Literal["sub", "cat"]) -> tuple[list[dict], list[str]]:
    """Multi-engine wide pivot — one row per (row_id, target), columns per engine.

    Adds an ``ensemble_score`` column = mean of the populated per-engine scores
    on each row, so downstream code can sort by combined signal without first
    deciding on an aggregation rule.
    """
    by_key: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    methods_seen: list[str] = []
    for r in results:
        if r.method not in methods_seen:
            methods_seen.append(r.method)
        records = r.all_sub_scores if level == "sub" else r.all_cat_scores
        for rec in records:
            key = (rec.row_id, f"{rec.category_id}.{rec.sub_category_id}" if level == "sub" else rec.category_id)
            by_key[key].setdefault("row_id", rec.row_id)
            by_key[key].setdefault("category_id", rec.category_id)
            if level == "sub":
                by_key[key].setdefault("sub_category_id", rec.sub_category_id)
            by_key[key][f"{r.method}_score"] = rec.score

    # Ensemble = mean of populated engine scores (skips missing)
    method_cols = [f"{m}_score" for m in methods_seen]
    for row in by_key.values():
        vals = [row[c] for c in method_cols if isinstance(row.get(c), (int, float))]
        row["ensemble_score"] = round(sum(vals) / len(vals), 4) if vals else ""

    base_fields = ["row_id", "category_id"]
    if level == "sub":
        base_fields.append("sub_category_id")
    fieldnames = base_fields + method_cols + ["ensemble_score"]

    # Deterministic order: (row_id, category_id, sub_category_id)
    sort_key = (
        (lambda r: (r["row_id"], r["category_id"], r.get("sub_category_id", "")))
        if level == "sub"
        else (lambda r: (r["row_id"], r["category_id"]))
    )
    return sorted(by_key.values(), key=sort_key), fieldnames


def _write_engine_outputs(out_dir: Path, result: ScoringResult, taxonomy: Taxonomy) -> None:
    """Write the per-engine CSV set into ``out_dir``."""
    _write_csv(out_dir / "matches.csv", [asdict(m) for m in result.sub_matches], _MATCH_FIELDS)
    _write_csv(out_dir / "category_matches.csv", [asdict(m) for m in result.cat_matches], _MATCH_FIELDS)
    _write_csv(out_dir / "row_scores.csv", [asdict(s) for s in result.summaries], _ROW_SCORE_FIELDS)
    sub_long_records = [asdict(r) for r in result.all_sub_scores]
    cat_long_records = [asdict(r) for r in result.all_cat_scores]
    _write_csv(out_dir / "all_scores_sub_long.csv", sub_long_records, _MATCH_FIELDS)
    _write_csv(out_dir / "all_scores_cat_long.csv", cat_long_records, _MATCH_FIELDS)
    _write_parquet_optional(out_dir / "all_scores_sub_long.csv", sub_long_records, _MATCH_FIELDS)
    _write_parquet_optional(out_dir / "all_scores_cat_long.csv", cat_long_records, _MATCH_FIELDS)

    sub_wide_rows, sub_wide_fields = _wide_scores(result.all_sub_scores, key="row_id", level="sub")
    _write_csv(out_dir / "all_scores_sub_wide.csv", sub_wide_rows, sub_wide_fields)
    _write_parquet_optional(out_dir / "all_scores_sub_wide.csv", sub_wide_rows, sub_wide_fields)
    cat_wide_rows, cat_wide_fields = _wide_scores(result.all_cat_scores, key="row_id", level="cat")
    _write_csv(out_dir / "all_scores_cat_wide.csv", cat_wide_rows, cat_wide_fields)
    _write_parquet_optional(out_dir / "all_scores_cat_wide.csv", cat_wide_rows, cat_wide_fields)


def run_classify(
    *,
    input_csv: Path,
    taxonomy_path: Path,
    out_dir: Path,
    id_column: str = "row_id",
    text_column: str = "root_cause",
    engine: Engine = "tfidf",
    threshold: float | None = None,
    discover_keywords: bool = True,
    llm_model: str | None = None,
    llm_backend: str | None = None,
    llm_base_url: str | None = None,
    llm_api_key: str | None = None,
    resume_dir: Path | None = None,
) -> dict[str, int]:
    """Run a single engine and emit the full CSV set into a timestamped subdir.

    ``resume_dir`` (LLM prompt only): use this existing run directory rather
    than creating a new timestamped one. Any existing
    ``llm_prompt_checkpoint.ndjson`` is read first; rows already in it are
    skipped, and new rows are appended.
    """
    taxonomy = load_taxonomy(taxonomy_path)
    rows = _read_input(input_csv, id_column=id_column, text_column=text_column)
    run_id = _make_run_id(taxonomy, [engine])

    if resume_dir is not None:
        run_dir = resume_dir
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = out_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
    result = _score_with(
        engine, rows, taxonomy, threshold, run_id, llm_model,
        checkpoint_dir=run_dir,
        llm_backend=llm_backend,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
    )
    _write_engine_outputs(run_dir, result, taxonomy)

    counts = {
        "input_rows": len(rows),
        "sub_matches": len(result.sub_matches),
        "cat_matches": len(result.cat_matches),
        "rows_with_match": sum(1 for s in result.summaries if s.n_matches_above_threshold > 0),
    }

    if discover_keywords:
        keywords = discover(rows, taxonomy=taxonomy)
        flat = [
            {**asdict(k), "sample_row_ids": "; ".join(k.sample_row_ids)}
            for k in keywords
        ]
        _write_csv(run_dir / "keywords.csv", flat, _KEYWORD_FIELDS)
        counts["keywords"] = len(keywords)
        # Stratified pass — recurring phrases per top category
        strat = discover_stratified(rows, result.summaries, taxonomy=taxonomy)
        _write_stratified_keywords(run_dir / "keywords_by_category.csv", strat)
        counts["keywords_by_category_total"] = sum(len(v) for v in strat.values())

    counts["out_dir"] = str(run_dir)  # type: ignore[assignment]
    return counts


def run_classify_multi(
    *,
    input_csv: Path,
    taxonomy_path: Path,
    out_dir: Path,
    engines: list[Engine],
    id_column: str = "row_id",
    text_column: str = "root_cause",
    threshold: float | None = None,
    discover_keywords: bool = True,
    llm_model: str | None = None,
) -> dict[str, int]:
    """Run multiple engines on the same input and emit combined wide CSVs.

    Each engine still writes its own per-engine output subdirectory under the
    run dir; an additional ``combined_scores_sub.csv`` and
    ``combined_scores_cat.csv`` provide a side-by-side view.
    """
    if not engines:
        raise ValueError("engines must be non-empty")
    taxonomy = load_taxonomy(taxonomy_path)
    rows = _read_input(input_csv, id_column=id_column, text_column=text_column)
    run_id = _make_run_id(taxonomy, engines)
    run_dir = out_dir / run_id

    results: list[ScoringResult] = []
    counts: dict[str, Any] = {"input_rows": len(rows), "engines": list(engines)}

    for engine in engines:
        engine_dir = run_dir / engine
        engine_dir.mkdir(parents=True, exist_ok=True)
        result = _score_with(engine, rows, taxonomy, threshold, run_id, llm_model, checkpoint_dir=engine_dir)
        results.append(result)
        _write_engine_outputs(engine_dir, result, taxonomy)
        counts[f"{engine}_sub_matches"] = len(result.sub_matches)
        counts[f"{engine}_cat_matches"] = len(result.cat_matches)

    # Combined side-by-side wide tables across engines
    combined_sub_rows, combined_sub_fields = _combined_wide(results, level="sub")
    combined_cat_rows, combined_cat_fields = _combined_wide(results, level="cat")
    _write_csv(run_dir / "combined_scores_sub.csv", combined_sub_rows, combined_sub_fields)
    _write_csv(run_dir / "combined_scores_cat.csv", combined_cat_rows, combined_cat_fields)
    _write_parquet_optional(run_dir / "combined_scores_sub.csv", combined_sub_rows, combined_sub_fields)
    _write_parquet_optional(run_dir / "combined_scores_cat.csv", combined_cat_rows, combined_cat_fields)

    if discover_keywords:
        keywords = discover(rows, taxonomy=taxonomy)
        flat = [
            {**asdict(k), "sample_row_ids": "; ".join(k.sample_row_ids)}
            for k in keywords
        ]
        _write_csv(run_dir / "keywords.csv", flat, _KEYWORD_FIELDS)
        counts["keywords"] = len(keywords)
        # Stratified pass using the FIRST engine's summaries to assign top category
        strat = discover_stratified(rows, results[0].summaries, taxonomy=taxonomy)
        _write_stratified_keywords(run_dir / "keywords_by_category.csv", strat)
        counts["keywords_by_category_total"] = sum(len(v) for v in strat.values())

    counts["out_dir"] = str(run_dir)
    return counts


def run_eval(
    *,
    matches_csv: Path,
    labels_csv: Path,
    out_dir: Path,
    all_scores_csv: Path | None = None,
    threshold: float | None = None,
) -> dict[str, Any]:
    """Evaluate predictions against labels.

    Inputs:
      matches_csv  — threshold-filtered prediction CSV (from a classify run)
      OR
      all_scores_csv + threshold — derive predictions from the full score
        matrix at the given threshold

      labels_csv — columns: row_id, category_id, sub_category_id (sub may be blank)

    Outputs:
      eval_summary.csv — per (sub-)category metrics + micro/macro overall
      eval_errors.csv  — every FP and FN row for manual review
    """
    from text_classify.evaluator import evaluate
    from text_classify.schemas import MatchRecord

    # Load labels
    labels: list[tuple[str, str, str]] = []
    with labels_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"row_id", "category_id"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(
                f"labels CSV must contain columns {sorted(required)}, found {reader.fieldnames}"
            )
        for row in reader:
            labels.append((
                str(row["row_id"]).strip(),
                str(row["category_id"]).strip(),
                str(row.get("sub_category_id", "")).strip(),
            ))

    # Load predictions
    predictions: list[MatchRecord] = []
    if all_scores_csv is not None:
        if threshold is None:
            raise ValueError("threshold is required when using --all-scores")
        with all_scores_csv.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    score = float(row.get("score", 0.0))
                except (TypeError, ValueError):
                    continue
                if score < threshold:
                    continue
                predictions.append(MatchRecord(
                    row_id=str(row["row_id"]).strip(),
                    category_id=str(row["category_id"]).strip(),
                    sub_category_id=str(row.get("sub_category_id", "")).strip(),
                    score=score,
                    method=str(row.get("method", "")),  # type: ignore[arg-type]
                    signals=str(row.get("signals", "")),
                    taxonomy_version=int(row.get("taxonomy_version") or 0),
                    run_id=str(row.get("run_id", "")),
                ))
    else:
        with matches_csv.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                predictions.append(MatchRecord(
                    row_id=str(row["row_id"]).strip(),
                    category_id=str(row["category_id"]).strip(),
                    sub_category_id=str(row.get("sub_category_id", "")).strip(),
                    score=float(row.get("score", 0.0)),
                    method=str(row.get("method", "")),  # type: ignore[arg-type]
                    signals=str(row.get("signals", "")),
                    taxonomy_version=int(row.get("taxonomy_version") or 0),
                    run_id=str(row.get("run_id", "")),
                ))

    eval_rows, errors = evaluate(predictions, labels)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        out_dir / "eval_summary.csv",
        [asdict(r) for r in eval_rows],
        ["level", "category_id", "sub_category_id", "tp", "fp", "fn",
         "precision", "recall", "f1", "n_labels", "n_predictions"],
    )
    _write_csv(
        out_dir / "eval_errors.csv",
        [asdict(e) for e in errors],
        ["kind", "level", "row_id", "category_id", "sub_category_id", "score"],
    )

    overall_micro = next((r for r in eval_rows if r.level == "overall" and r.category_id == "micro"), None)
    return {
        "n_labels": len(labels),
        "n_predictions": len(predictions),
        "micro_precision": overall_micro.precision if overall_micro else 0.0,
        "micro_recall": overall_micro.recall if overall_micro else 0.0,
        "micro_f1": overall_micro.f1 if overall_micro else 0.0,
        "out_dir": str(out_dir),
    }


def run_sample_cli(
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
) -> dict[str, Any]:
    """Thin wrapper exposing the sampler to the CLI dispatcher."""
    from text_classify.sampling import run_sample

    return run_sample(
        input_csv=input_csv,
        classify_run_dir=classify_run_dir,
        out_csv=out_csv,
        total=total,
        id_column=id_column,
        text_column=text_column,
        by_category=by_category,
        by_score_band=by_score_band,
        random_seed=random_seed,
    )


def run_discover_only(
    *,
    input_csv: Path,
    out_dir: Path,
    id_column: str = "row_id",
    text_column: str = "root_cause",
    taxonomy_path: Path | None = None,
    min_doc_count: int = 5,
    max_doc_count: int = 5000,
) -> dict[str, int]:
    rows = _read_input(input_csv, id_column=id_column, text_column=text_column)
    taxonomy = load_taxonomy(taxonomy_path) if taxonomy_path else None
    cfg = KeywordConfig(min_doc_count=min_doc_count, max_doc_count=max_doc_count)
    keywords = discover(rows, taxonomy=taxonomy, config=cfg)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_dir = out_dir / f"{stamp}-keywords"
    flat = [
        {**asdict(k), "sample_row_ids": "; ".join(k.sample_row_ids)}
        for k in keywords
    ]
    _write_csv(run_dir / "keywords.csv", flat, _KEYWORD_FIELDS)
    return {"input_rows": len(rows), "keywords": len(keywords), "out_dir": str(run_dir)}
