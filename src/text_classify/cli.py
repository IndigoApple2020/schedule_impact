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

    from text_classify.runner import run_classify, run_discover_only

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
            f"({counts['matches']} matches across {counts['rows_with_match']} rows{kw_str}) "
            f"-> {counts['out_dir']}"
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
