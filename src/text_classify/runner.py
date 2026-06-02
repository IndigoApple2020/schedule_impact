"""Top-level orchestrators: read CSV → score → write CSVs."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from text_classify.keyword_discovery import KeywordConfig, discover
from text_classify.llm_classifier import (
    LlmEmbedConfig,
    LlmPromptConfig,
    score_rows_embed,
    score_rows_prompt,
)
from text_classify.schemas import KeywordRecord, MatchRecord, RowScore, Taxonomy
from text_classify.taxonomy import load_taxonomy
from text_classify.tfidf_classifier import TfidfConfig, score_rows

Engine = Literal["tfidf", "llm_embed", "llm_prompt"]


def _make_run_id(taxonomy: Taxonomy, engine: Engine) -> str:
    """Deterministic-ish run identifier: timestamp + taxonomy fingerprint."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    h = hashlib.sha1(f"{taxonomy.name}-v{taxonomy.version}-{engine}".encode()).hexdigest()[:8]
    return f"{stamp}-{engine}-{h}"


def _read_input(input_csv: Path, *, id_column: str, text_column: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with input_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if id_column not in (reader.fieldnames or []):
            raise ValueError(
                f"id column '{id_column}' not found in {input_csv}. "
                f"Available columns: {reader.fieldnames}"
            )
        if text_column not in (reader.fieldnames or []):
            raise ValueError(
                f"text column '{text_column}' not found in {input_csv}. "
                f"Available columns: {reader.fieldnames}"
            )
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
    """Read input rows, score against taxonomy, write matches.csv + row_scores.csv.

    If ``discover_keywords`` is True, also runs the cross-row keyword discovery
    and writes ``keywords.csv`` into the same output directory.
    """
    taxonomy = load_taxonomy(taxonomy_path)
    rows = _read_input(input_csv, id_column=id_column, text_column=text_column)
    run_id = _make_run_id(taxonomy, engine)

    if engine == "tfidf":
        cfg = TfidfConfig(threshold=threshold or taxonomy.default_threshold)
        matches, summaries = score_rows(rows, taxonomy, config=cfg, run_id=run_id)
    elif engine == "llm_embed":
        cfg_e = LlmEmbedConfig(
            threshold=threshold or 0.55,
            **({"model": llm_model} if llm_model else {}),
        )
        matches, summaries = score_rows_embed(rows, taxonomy, config=cfg_e, run_id=run_id)
    elif engine == "llm_prompt":
        cfg_p = LlmPromptConfig(
            threshold=threshold or 0.5,
            **({"model": llm_model} if llm_model else {}),
        )
        matches, summaries = score_rows_prompt(rows, taxonomy, config=cfg_p, run_id=run_id)
    else:
        raise ValueError(f"Unknown engine: {engine}")

    out_dir = out_dir / run_id
    _write_csv(out_dir / "matches.csv", [asdict(m) for m in matches], _MATCH_FIELDS)
    _write_csv(out_dir / "row_scores.csv", [asdict(s) for s in summaries], _ROW_SCORE_FIELDS)

    counts = {
        "input_rows": len(rows),
        "matches": len(matches),
        "rows_with_match": sum(1 for s in summaries if s.n_matches_above_threshold > 0),
    }

    if discover_keywords:
        keywords = discover(rows, taxonomy=taxonomy)
        flat = [
            {**asdict(k), "sample_row_ids": "; ".join(k.sample_row_ids)}
            for k in keywords
        ]
        _write_csv(out_dir / "keywords.csv", flat, _KEYWORD_FIELDS)
        counts["keywords"] = len(keywords)

    counts["out_dir"] = str(out_dir)  # type: ignore[assignment]
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
    """Run only the cross-row keyword discovery pass (no classification)."""
    rows = _read_input(input_csv, id_column=id_column, text_column=text_column)
    taxonomy = load_taxonomy(taxonomy_path) if taxonomy_path else None
    cfg = KeywordConfig(min_doc_count=min_doc_count, max_doc_count=max_doc_count)
    keywords = discover(rows, taxonomy=taxonomy, config=cfg)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = out_dir / f"{stamp}-keywords"
    flat = [
        {**asdict(k), "sample_row_ids": "; ".join(k.sample_row_ids)}
        for k in keywords
    ]
    _write_csv(out_dir / "keywords.csv", flat, _KEYWORD_FIELDS)
    return {"input_rows": len(rows), "keywords": len(keywords), "out_dir": str(out_dir)}
