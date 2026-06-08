"""Run ``run-monthly`` for every consecutive XER pair under a root directory.

Assumes the standard data layout:

    xer_root/{period}/{anything}.xer
    pdf_root/{period}/{anything}.pdf      (optional)

where ``{period}`` is any string that sorts chronologically (e.g. ``2025-04``
or ``C38``). The first ``.xer`` file in each period directory is used; if
``pdf_root`` is supplied, the first ``.pdf`` matching the current period is
attached.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from schedule_impact.pipeline.run_monthly import run_monthly

_logger = logging.getLogger(__name__)


def discover_periods(xer_root: Path) -> list[tuple[str, Path]]:
    """Return ``[(period, first_xer_path), ...]`` sorted by period."""
    if not xer_root.exists():
        raise FileNotFoundError(xer_root)
    pairs: list[tuple[str, Path]] = []
    for sub in sorted(xer_root.iterdir()):
        if not sub.is_dir():
            continue
        xers = sorted(sub.glob("*.xer"))
        if not xers:
            continue
        pairs.append((sub.name, xers[0]))
    return pairs


def find_pdf(pdf_root: Path | None, period: str) -> Path | None:
    if pdf_root is None or not pdf_root.exists():
        return None
    period_dir = pdf_root / period
    if period_dir.is_dir():
        pdfs = sorted(period_dir.glob("*.pdf"))
        if pdfs:
            return pdfs[0]
    return None


def run_batch(
    *,
    programme_id: str,
    xer_root: Path,
    output_dir: Path,
    pdf_root: Path | None = None,
    quality_model: Path | None = None,
    project_row_id: str | None = None,
    skip_existing: bool = False,
) -> dict[str, Any]:
    """Run the monthly pipeline for every consecutive XER pair in ``xer_root``.

    Each iteration calls :func:`schedule_impact.pipeline.run_monthly.run_monthly`
    with the current period's XER + the previous period's XER, plus a matching
    PDF if one is found under ``pdf_root/{current_period}/``.

    ``skip_existing``: if True, skip a period when ``incidents_{period}.csv``
    already exists in the output directory (useful for resuming after a crash
    or only running new months).

    Returns a summary dict with per-period counts.
    """
    periods = discover_periods(xer_root)
    if len(periods) < 2:
        raise ValueError(
            f"Need at least 2 period subdirectories under {xer_root}; found {len(periods)}."
        )

    summaries: list[dict[str, Any]] = []
    for i in range(1, len(periods)):
        prev_period, prev_xer = periods[i - 1]
        curr_period, curr_xer = periods[i]

        expected_csv = output_dir / programme_id / curr_period / f"incidents_{curr_period}.csv"
        if skip_existing and expected_csv.is_file():
            _logger.info("Skipping %s — %s already exists", curr_period, expected_csv)
            summaries.append({
                "period": curr_period,
                "previous_period": prev_period,
                "skipped": True,
            })
            continue

        pdf = find_pdf(pdf_root, curr_period)
        _logger.info(
            "Running %s vs %s%s",
            curr_period, prev_period,
            f" with PDF {pdf.name}" if pdf else "",
        )

        try:
            result = run_monthly(
                programme_id=programme_id,
                current_xer=curr_xer,
                previous_xer=prev_xer,
                reporting_period=curr_period,
                previous_period=prev_period,
                output_dir=output_dir,
                project_row_id=project_row_id,
                quality_model=quality_model,
                pdfs=[pdf] if pdf else None,
            )
            summaries.append({
                "period": curr_period,
                "previous_period": prev_period,
                "incident_count": result.incident_count,
                "impact_count": result.impact_count,
                "float_count": result.float_count,
                "memo_chunk_count": result.memo_chunk_count,
                "pdf_chunk_count": result.pdf_chunk_count,
                "link_count": result.link_count,
                "quality_flagged": result.quality_flagged,
                "output_dir": str(result.output_dir),
                "skipped": False,
            })
        except Exception as exc:
            _logger.error("Run failed for %s: %s", curr_period, exc)
            summaries.append({
                "period": curr_period,
                "previous_period": prev_period,
                "error": str(exc),
                "skipped": False,
            })

    return {
        "programme_id": programme_id,
        "period_count": len(periods),
        "pairs_attempted": len(summaries),
        "pairs_succeeded": sum(1 for s in summaries if "incident_count" in s),
        "pairs_skipped": sum(1 for s in summaries if s.get("skipped")),
        "pairs_failed": sum(1 for s in summaries if "error" in s),
        "summaries": summaries,
    }
