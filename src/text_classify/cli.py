"""text-classify CLI: taxonomy-driven scoring + keyword discovery."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="text-classify",
        description="Score free-text rows against a taxonomy (TF-IDF or LLM) "
        "and discover cross-row keywords.",
    )
    sub = parser.add_subparsers(dest="command")

    # ----------------------------------------------------------------- tfidf
    tf = sub.add_parser(
        "classify-tfidf",
        help="Score rows using TF-IDF cosine similarity against per-sub-category seed pseudo-docs.",
    )
    tf.add_argument("--input", type=Path, required=True, help="Input CSV with id + text columns")
    tf.add_argument("--taxonomy", type=Path, required=True, help="Path to taxonomy YAML")
    tf.add_argument("--out", type=Path, required=True, help="Output directory (subdir per run)")
    tf.add_argument("--id-column", default="row_id")
    tf.add_argument("--text-column", default="root_cause")
    tf.add_argument("--threshold", type=float, default=None,
                    help="Score threshold (overrides taxonomy default_threshold)")
    tf.add_argument("--no-keywords", action="store_true",
                    help="Skip the cross-row keyword discovery pass")

    # ------------------------------------------------------------- llm embed
    le = sub.add_parser(
        "classify-llm-embed",
        help="Score rows using Ollama embeddings (cosine vs seed pseudo-doc embeddings).",
    )
    le.add_argument("--input", type=Path, required=True)
    le.add_argument("--taxonomy", type=Path, required=True)
    le.add_argument("--out", type=Path, required=True)
    le.add_argument("--id-column", default="row_id")
    le.add_argument("--text-column", default="root_cause")
    le.add_argument("--threshold", type=float, default=None)
    le.add_argument("--model", default=None, help="Ollama model name (default: nomic-embed-text)")
    le.add_argument("--no-keywords", action="store_true")

    # ------------------------------------------------------------ llm prompt
    lp = sub.add_parser(
        "classify-llm-prompt",
        help="Score rows by sending each to an Ollama chat model with structured JSON output.",
    )
    lp.add_argument("--input", type=Path, required=True)
    lp.add_argument("--taxonomy", type=Path, required=True)
    lp.add_argument("--out", type=Path, required=True)
    lp.add_argument("--id-column", default="row_id")
    lp.add_argument("--text-column", default="root_cause")
    lp.add_argument("--threshold", type=float, default=None)
    lp.add_argument("--model", default=None, help="Ollama chat model name (default: llama3.1:8b)")
    lp.add_argument("--no-keywords", action="store_true")

    # ----------------------------------------------------------- classify-multi
    mu = sub.add_parser(
        "classify-multi",
        help="Run 2+ engines on the same input and emit combined side-by-side score CSVs.",
    )
    mu.add_argument("--input", type=Path, required=True)
    mu.add_argument("--taxonomy", type=Path, required=True)
    mu.add_argument("--out", type=Path, required=True)
    mu.add_argument(
        "--engines",
        required=True,
        help="Comma-separated list of engines (e.g. 'tfidf,llm_embed')",
    )
    mu.add_argument("--id-column", default="row_id")
    mu.add_argument("--text-column", default="root_cause")
    mu.add_argument("--threshold", type=float, default=None)
    mu.add_argument("--model", default=None,
                    help="Ollama model override (applied to whichever LLM engines are selected)")
    mu.add_argument("--no-keywords", action="store_true")

    # -------------------------------------------------------------------- eval
    ev = sub.add_parser(
        "eval",
        help="Evaluate predictions against hand-labelled rows (precision/recall/F1).",
    )
    ev.add_argument("--labels", type=Path, required=True,
                    help="CSV with columns row_id, category_id, sub_category_id")
    ev.add_argument("--matches", type=Path,
                    help="Threshold-filtered matches.csv from a classify run")
    ev.add_argument("--all-scores", type=Path,
                    help="Full all_scores_sub_long.csv (used with --threshold)")
    ev.add_argument("--threshold", type=float,
                    help="Threshold for deriving predictions from --all-scores")
    ev.add_argument("--out", type=Path, required=True, help="Output directory")

    # ------------------------------------------------------ discover-keywords
    dk = sub.add_parser(
        "discover-keywords",
        help="Cross-row phrase mining only (no classification).",
    )
    dk.add_argument("--input", type=Path, required=True)
    dk.add_argument("--out", type=Path, required=True)
    dk.add_argument("--taxonomy", type=Path, default=None,
                    help="Optional taxonomy to suppress phrases that match existing seeds")
    dk.add_argument("--id-column", default="row_id")
    dk.add_argument("--text-column", default="root_cause")
    dk.add_argument("--min-doc-count", type=int, default=5)
    dk.add_argument("--max-doc-count", type=int, default=5000)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    from text_classify.runner import run_classify, run_classify_multi, run_discover_only, run_eval

    if args.command in {"classify-tfidf", "classify-llm-embed", "classify-llm-prompt"}:
        engine = {
            "classify-tfidf": "tfidf",
            "classify-llm-embed": "llm_embed",
            "classify-llm-prompt": "llm_prompt",
        }[args.command]
        counts = run_classify(
            input_csv=args.input,
            taxonomy_path=args.taxonomy,
            out_dir=args.out,
            id_column=args.id_column,
            text_column=args.text_column,
            engine=engine,  # type: ignore[arg-type]
            threshold=args.threshold,
            discover_keywords=not args.no_keywords,
            llm_model=getattr(args, "model", None),
        )
        kw_str = f", {counts.get('keywords', 0)} keywords" if "keywords" in counts else ""
        print(
            f"Classified {counts['input_rows']} rows  "
            f"({counts['sub_matches']} sub-matches, {counts['cat_matches']} cat-matches "
            f"across {counts['rows_with_match']} rows{kw_str}) "
            f"-> {counts['out_dir']}"
        )
        sys.exit(0)

    if args.command == "classify-multi":
        engines_in = [e.strip() for e in args.engines.split(",") if e.strip()]
        valid = {"tfidf", "llm_embed", "llm_prompt"}
        for e in engines_in:
            if e not in valid:
                print(f"Unknown engine '{e}'. Must be one of: {sorted(valid)}", file=sys.stderr)
                sys.exit(2)
        counts = run_classify_multi(
            input_csv=args.input,
            taxonomy_path=args.taxonomy,
            out_dir=args.out,
            engines=engines_in,  # type: ignore[arg-type]
            id_column=args.id_column,
            text_column=args.text_column,
            threshold=args.threshold,
            discover_keywords=not args.no_keywords,
            llm_model=args.model,
        )
        summary_parts = [
            f"{e}: {counts.get(f'{e}_sub_matches', 0)} sub + {counts.get(f'{e}_cat_matches', 0)} cat"
            for e in engines_in
        ]
        kw_str = f", {counts.get('keywords', 0)} keywords" if "keywords" in counts else ""
        print(
            f"Multi-engine run: {counts['input_rows']} rows  "
            f"[{'; '.join(summary_parts)}{kw_str}]  -> {counts['out_dir']}"
        )
        sys.exit(0)

    if args.command == "eval":
        if not args.matches and not args.all_scores:
            print("eval requires either --matches or --all-scores", file=sys.stderr)
            sys.exit(2)
        if args.all_scores and args.threshold is None:
            print("--all-scores requires --threshold", file=sys.stderr)
            sys.exit(2)
        counts = run_eval(
            matches_csv=args.matches,
            labels_csv=args.labels,
            out_dir=args.out,
            all_scores_csv=args.all_scores,
            threshold=args.threshold,
        )
        print(
            f"Eval: {counts['n_labels']} labels vs {counts['n_predictions']} predictions  "
            f"micro P={counts['micro_precision']:.3f} R={counts['micro_recall']:.3f} "
            f"F1={counts['micro_f1']:.3f}  -> {counts['out_dir']}"
        )
        sys.exit(0)

    if args.command == "discover-keywords":
        counts = run_discover_only(
            input_csv=args.input,
            out_dir=args.out,
            id_column=args.id_column,
            text_column=args.text_column,
            taxonomy_path=args.taxonomy,
            min_doc_count=args.min_doc_count,
            max_doc_count=args.max_doc_count,
        )
        print(
            f"Discovered {counts['keywords']} phrases across {counts['input_rows']} rows "
            f"-> {counts['out_dir']}"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
