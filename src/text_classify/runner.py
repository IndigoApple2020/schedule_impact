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

from text_classify.keyword_discovery import KeywordConfig, discover
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


def _score_with(
    engine: Engine,
    rows,
    taxonomy,
    threshold,
    run_id,
    llm_model,
    *,
    checkpoint_dir: Path | None = None,
) -> ScoringResult:
    if engine == "tfidf":
        cfg = TfidfConfig(threshold=threshold or taxonomy.default_threshold)
        return score_all(rows, taxonomy, config=cfg, run_id=run_id)
    if engine == "llm_embed":
        cfg_e = LlmEmbedConfig(
            threshold=threshold or 0.55,
            **({"model": llm_model} if llm_model else {}),
        )
        return score_all_embed(rows, taxonomy, config=cfg_e, run_id=run_id)
    if engine == "llm_prompt":
        cfg_kwargs: dict[str, Any] = {"threshold": threshold or 0.5}
        if llm_model:
            cfg_kwargs["model"] = llm_model
        if checkpoint_dir is not None:
            cfg_kwargs["checkpoint_path"] = str(checkpoint_dir / "llm_prompt_checkpoint.ndjson")
        return score_all_prompt(rows, taxonomy, config=LlmPromptConfig(**cfg_kwargs), run_id=run_id)
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
    _write_csv(out_dir / "all_scores_sub_long.csv",
               [asdict(r) for r in result.all_sub_scores], _MATCH_FIELDS)
    _write_csv(out_dir / "all_scores_cat_long.csv",
               [asdict(r) for r in result.all_cat_scores], _MATCH_FIELDS)

    sub_wide_rows, sub_wide_fields = _wide_scores(result.all_sub_scores, key="row_id", level="sub")
    _write_csv(out_dir / "all_scores_sub_wide.csv", sub_wide_rows, sub_wide_fields)
    cat_wide_rows, cat_wide_fields = _wide_scores(result.all_cat_scores, key="row_id", level="cat")
    _write_csv(out_dir / "all_scores_cat_wide.csv", cat_wide_rows, cat_wide_fields)


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
) -> dict[str, int]:
    """Run a single engine and emit the full CSV set into a timestamped subdir."""
    taxonomy = load_taxonomy(taxonomy_path)
    rows = _read_input(input_csv, id_column=id_column, text_column=text_column)
    run_id = _make_run_id(taxonomy, [engine])

    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    result = _score_with(engine, rows, taxonomy, threshold, run_id, llm_model, checkpoint_dir=run_dir)
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

    if discover_keywords:
        keywords = discover(rows, taxonomy=taxonomy)
        flat = [
            {**asdict(k), "sample_row_ids": "; ".join(k.sample_row_ids)}
            for k in keywords
        ]
        _write_csv(run_dir / "keywords.csv", flat, _KEYWORD_FIELDS)
        counts["keywords"] = len(keywords)

    counts["out_dir"] = str(run_dir)
    return counts


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
