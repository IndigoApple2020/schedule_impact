"""Command-line interface."""

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="schedule-impact",
        description="Analyse P6 schedules and link narrative PDFs to incidents.",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run-monthly", help="Detect incidents and score quality for one period")
    run.add_argument("--programme", required=True)
    run.add_argument("--period", required=True, help="Reporting period YYYY-MM")
    run.add_argument("--previous-period", required=True, help="Baseline period YYYY-MM")
    run.add_argument("--current-xer", type=Path, required=True)
    run.add_argument("--previous-xer", type=Path, required=True)
    run.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs"),
        help="Base output directory",
    )
    run.add_argument("--project-row-id", help="Override project_row_id (default: proj_id)")
    run.add_argument(
        "--quality-model",
        type=Path,
        help="Trained quality classifier .joblib (optional)",
    )
    run.add_argument(
        "--pdf",
        type=Path,
        action="append",
        dest="pdfs",
        default=[],
        metavar="PDF",
        help="PDF narrative file to extract chunks from (repeat for multiple)",
    )

    px = sub.add_parser(
        "profile-xer",
        help="Anonymized XER schema report (safe to share structure only)",
    )
    px.add_argument("--xer", type=Path, required=True)
    px.add_argument("--pair", type=Path, help="Previous month XER for match statistics")
    px.add_argument("--out", type=Path, required=True)

    pp = sub.add_parser(
        "profile-pdf",
        help="Anonymized PDF metadata report (no body text)",
    )
    pp.add_argument("--pdf", type=Path, required=True)
    pp.add_argument("--out", type=Path, required=True)

    pa = sub.add_parser(
        "profile-all",
        help="Combined XER + PDF structure report — safe to share, no row-level values",
    )
    pa.add_argument("--xer", type=Path, required=True, help="Current-period XER")
    pa.add_argument("--previous-xer", type=Path, help="Previous-period XER for match stats")
    pa.add_argument(
        "--pdf",
        type=Path,
        action="append",
        dest="pdfs",
        default=[],
        metavar="PDF",
        help="PDF narrative file (repeat for multiple)",
    )
    pa.add_argument("--programme", default="unknown", help="Programme ID label for the report")
    pa.add_argument("--out", type=Path, required=True, help="Output JSON path")

    rb = sub.add_parser(
        "run-batch",
        help="Run run-monthly for every consecutive XER pair found under --xer-root.",
    )
    rb.add_argument("--programme", required=True)
    rb.add_argument("--xer-root", type=Path, required=True,
                    help="Directory containing {period}/{file}.xer subdirectories")
    rb.add_argument("--pdf-root", type=Path,
                    help="Optional directory containing {period}/{file}.pdf subdirectories")
    rb.add_argument("--output-dir", type=Path, default=Path("outputs"))
    rb.add_argument("--project-row-id", help="Override project_row_id for all runs")
    rb.add_argument("--quality-model", type=Path,
                    help="Trained quality classifier .joblib (optional)")
    rb.add_argument("--skip-existing", action="store_true",
                    help="Skip periods whose incidents_*.csv already exists")
    rb.add_argument(
        "--period-regex",
        help="Custom regex for extracting period from XER filenames in flat layout. "
        "First match (or joined capture groups) is used. "
        "Default tries YYYY-MM, then C{N}/PfA{N}, then filename stem.",
    )

    am = sub.add_parser(
        "aggregate-memos",
        help="Combine taskmemo_chunks_*.csv from many runs into one CSV ready for text-classify.",
    )
    am.add_argument("--outputs-root", type=Path, required=True,
                    help="Root directory under which to recursively find memo CSVs")
    am.add_argument("--out", type=Path, required=True, help="Output combined CSV path")
    am.add_argument("--pattern", default="taskmemo_chunks_*.csv",
                    help="Filename pattern to match (default: taskmemo_chunks_*.csv)")

    es = sub.add_parser(
        "export-schedule",
        help="Dump XER tables to CSV (one per table) + optional month-over-month task delta. Local use only.",
    )
    es.add_argument("--xer", type=Path, required=True, help="XER to export")
    es.add_argument("--previous-xer", type=Path, help="If given, also write task_changes.csv")
    es.add_argument("--out", type=Path, required=True, help="Output directory")
    es.add_argument(
        "--all-tables",
        action="store_true",
        help="Export every XER table, not just the useful subset",
    )

    em = sub.add_parser(
        "export-memos",
        help="Bulk-export TASKMEMO plain text for team labeling (local only)",
    )
    em.add_argument("--programme", required=True)
    em.add_argument("--xer", type=Path, required=True, help="XER file or directory")
    em.add_argument("--out", type=Path, required=True)
    em.add_argument("--period", help="Reporting period YYYY-MM if not in path")
    em.add_argument(
        "--all-memo-types",
        action="store_true",
        help="Include all MEMOTYPE rows, not only planner labels",
    )
    em.add_argument(
        "--slipped-only",
        action="store_true",
        help="Only memos on tasks with finish slip vs --previous-xer (recommended for labeling)",
    )
    em.add_argument(
        "--previous-xer",
        type=Path,
        help="Previous month XER (required with --slipped-only)",
    )
    em.add_argument(
        "--min-slip-days",
        type=float,
        default=1.0,
        help="Minimum calendar-day slip to include when using --slipped-only",
    )

    ls = sub.add_parser("label-stats", help="Summarize labeled memo CSV (quality %%)")
    ls.add_argument("--labels", type=Path, required=True)
    ls.add_argument("--out", type=Path, required=True)

    tr = sub.add_parser("train-quality-model", help="Train TF-IDF quality classifier")
    tr.add_argument("--labels", type=Path, required=True)
    tr.add_argument("--model", type=Path, required=True)
    tr.add_argument("--report", type=Path, required=True)
    tr.add_argument("--test-size", type=float, default=0.2)

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "run-monthly":
        from schedule_impact.pipeline.run_monthly import run_monthly

        result = run_monthly(
            programme_id=args.programme,
            current_xer=args.current_xer,
            previous_xer=args.previous_xer,
            reporting_period=args.period,
            previous_period=args.previous_period,
            output_dir=args.output_dir,
            project_row_id=args.project_row_id,
            quality_model=args.quality_model,
            pdfs=args.pdfs or None,
        )
        print(
            f"Done: {result.incident_count} incidents "
            f"({result.impact_count} impact, {result.float_count} float), "
            f"{result.memo_chunk_count} memo chunks, "
            f"{result.pdf_chunk_count} PDF chunks, "
            f"{result.link_count} links, "
            f"{result.quality_flagged} quality-flagged -> {result.output_dir}"
        )
        sys.exit(0)

    if args.command == "profile-xer":
        from schedule_impact.tools.profile_xer import run_profile

        run_profile(args.xer, args.out, pair=args.pair)
        print(f"Wrote {args.out}")
        sys.exit(0)

    if args.command == "profile-pdf":
        from schedule_impact.tools.profile_pdf import run_profile

        run_profile(args.pdf, args.out)
        print(f"Wrote {args.out}")
        sys.exit(0)

    if args.command == "profile-all":
        import json
        from dataclasses import asdict

        from schedule_impact.tools.profile_pdf import profile_pdf_file
        from schedule_impact.tools.profile_xer import pair_match_stats, profile_xer_file, run_profile

        xer_report = profile_xer_file(args.xer)
        pm = pair_match_stats(args.previous_xer, args.xer) if args.previous_xer else None

        pdf_reports = []
        for pdf_path in args.pdfs:
            pdf_reports.append(asdict(profile_pdf_file(pdf_path)))

        combined = {
            "generated_at": xer_report.generated_at,
            "programme_id": args.programme,
            "xer": asdict(xer_report),
            "xer_pair_match": [asdict(s) for s in pm] if pm else None,
            "pdfs": pdf_reports,
        }

        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(combined, indent=2), encoding="utf-8")
        n_sections = sum(len(r.get("sections", [])) for r in pdf_reports)
        print(
            f"Wrote {args.out}  "
            f"({len(xer_report.xer_tables)} XER tables, "
            f"{len(pdf_reports)} PDF(s), "
            f"{n_sections} sections detected)"
        )
        sys.exit(0)

    if args.command == "run-batch":
        from schedule_impact.tools.batch_runner import run_batch

        summary = run_batch(
            programme_id=args.programme,
            xer_root=args.xer_root,
            output_dir=args.output_dir,
            pdf_root=args.pdf_root,
            quality_model=args.quality_model,
            project_row_id=args.project_row_id,
            skip_existing=args.skip_existing,
            period_regex=args.period_regex,
        )
        print(
            f"Batch run: {summary['pairs_succeeded']} succeeded, "
            f"{summary['pairs_skipped']} skipped, "
            f"{summary['pairs_failed']} failed "
            f"(out of {summary['pairs_attempted']} pairs from {summary['period_count']} periods)"
        )
        for s in summary["summaries"]:
            if "error" in s:
                print(f"  [FAIL] {s['period']} vs {s['previous_period']}: {s['error']}")
            elif s.get("skipped"):
                print(f"  [SKIP] {s['period']} (output already exists)")
            else:
                print(
                    f"  [OK]   {s['period']} vs {s['previous_period']}: "
                    f"{s['incident_count']} incidents, "
                    f"{s['memo_chunk_count']} memos, "
                    f"{s['pdf_chunk_count']} PDF chunks"
                )
        sys.exit(0)

    if args.command == "aggregate-memos":
        from schedule_impact.tools.aggregate_memos import aggregate

        counts = aggregate(
            outputs_root=args.outputs_root,
            out_csv=args.out,
            pattern=args.pattern,
        )
        print(
            f"Aggregated {counts['rows']} memo rows from {counts['files']} files "
            f"({counts['periods']} periods) -> {counts['out']}"
        )
        sys.exit(0)

    if args.command == "export-schedule":
        from schedule_impact.tools.export_schedule import run_export

        summary = run_export(
            current_xer=args.xer,
            out_dir=args.out,
            previous_xer=args.previous_xer,
            all_tables=args.all_tables,
        )
        n_tables = len(summary.get("tables", {}))
        n_rows = sum(summary.get("tables", {}).values())
        print(f"Exported {n_tables} table(s), {n_rows} total rows -> {args.out}")
        if "task_changes" in summary:
            tc = summary["task_changes"]
            cats = {k: v for k, v in tc.items() if not k.startswith("_")}
            print(
                f"task_changes.csv: {tc.get('_total_rows', 0)} task_codes  "
                f"({', '.join(f'{k}={v}' for k, v in sorted(cats.items()))})"
            )
        sys.exit(0)

    if args.command == "export-memos":
        from schedule_impact.labeling.export_memos import discover_xer_files, export_memos

        paths = discover_xer_files(args.xer)
        count = export_memos(
            xer_paths=paths,
            out_path=args.out,
            programme_id=args.programme,
            reporting_period=args.period,
            include_all_memo_types=args.all_memo_types,
            previous_xer=args.previous_xer,
            min_slip_days=args.min_slip_days,
            slipped_only=args.slipped_only,
        )
        mode = "slipped tasks only" if args.slipped_only else "all planner memos"
        print(f"Exported {count} memos ({mode}) from {len(paths)} XER file(s) -> {args.out}")
        sys.exit(0)

    if args.command == "label-stats":
        from schedule_impact.labeling.stats import stats_from_csv, write_stats_report

        stats = stats_from_csv(args.labels)
        write_stats_report(stats, args.out)
        rate = stats.quality_rate
        rate_s = f"{rate:.1%}" if rate is not None else "n/a"
        print(
            f"Labeled {stats.labeled_rows}/{stats.total_rows}; "
            f"quality={stats.quality_yes} ({rate_s}) -> {args.out}"
        )
        sys.exit(0)

    if args.command == "train-quality-model":
        from schedule_impact.labeling.train_quality import train_quality_classifier, write_train_report

        report = train_quality_classifier(
            args.labels,
            args.model,
            test_size=args.test_size,
        )
        write_train_report(report, args.report)
        print(
            f"Trained on {report.n_samples} rows; "
            f"accuracy={report.accuracy} f1_quality={report.f1_quality} -> {args.model}"
        )
        if report.warnings:
            for w in report.warnings:
                print(f"  warning: {w}")
        sys.exit(0)


if __name__ == "__main__":
    main()
