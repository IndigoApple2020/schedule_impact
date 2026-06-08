"""Run ``run-monthly`` for every consecutive XER pair under a root directory.

Two supported layouts (auto-detected):

**Subdirectory layout** (preferred):
    xer_root/{period}/{anything}.xer
    pdf_root/{period}/{anything}.pdf      (optional)

**Flat layout** (fallback when no subdir contains XERs):
    xer_root/{anything-with-period-in-filename}.xer
    pdf_root/{anything-with-period-in-filename}.pdf

In flat layout the period is extracted from the filename:
  1. ``YYYY-MM`` or ``YYYY_MM`` pattern (e.g. ``schedule_2025-04.xer`` → ``"2025-04"``)
  2. ``C{N}`` or ``PfA{N}`` cycle pattern (e.g. ``HS2-PfA38.xer`` → ``"PfA38"``)
  3. Custom ``period_regex`` (first match used as period)
  4. Fallback: the filename stem (without extension)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from schedule_impact.pipeline.run_monthly import run_monthly

_logger = logging.getLogger(__name__)

# Built-in patterns for inferring period from filename (tried in order)
_DATE_PATTERN = re.compile(r"(20\d{2})[-_]?(\d{2})(?!\d)")
_CYCLE_PATTERN = re.compile(r"(?:^|[^A-Za-z])(P?fA\d{1,3}|C\d{1,3})(?![A-Za-z\d])", re.IGNORECASE)


def infer_period_from_filename(name: str, custom_regex: re.Pattern[str] | None = None) -> str:
    """Extract a period token from an XER filename.

    Order: custom_regex (if given) → YYYY-MM → C{N}/PfA{N} → filename stem.
    """
    stem = Path(name).stem
    if custom_regex is not None:
        m = custom_regex.search(stem)
        if m:
            # If there are capture groups, join them; else use the whole match
            if m.groups():
                return "-".join(g for g in m.groups() if g is not None)
            return m.group(0)
    m = _DATE_PATTERN.search(stem)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _CYCLE_PATTERN.search(stem)
    if m:
        return m.group(1)
    return stem


def discover_periods(
    xer_root: Path,
    *,
    period_regex: str | None = None,
) -> list[tuple[str, Path]]:
    """Return ``[(period, xer_path), ...]`` sorted by period.

    Tries subdirectory layout first. Falls back to flat layout (one XER per
    file in ``xer_root``) if no subdirectory contains XERs.
    """
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

    if pairs:
        return pairs

    # Flat layout fallback
    compiled = re.compile(period_regex) if period_regex else None
    flat_xers = sorted(xer_root.glob("*.xer"))
    for xer in flat_xers:
        period = infer_period_from_filename(xer.name, compiled)
        pairs.append((period, xer))
    return sorted(pairs, key=lambda p: p[0])


def find_pdf(
    pdf_root: Path | None,
    period: str,
    *,
    period_regex: str | None = None,
) -> Path | None:
    """Locate a PDF for a given period in subdir or flat layout."""
    if pdf_root is None or not pdf_root.exists():
        return None
    # Subdir layout
    period_dir = pdf_root / period
    if period_dir.is_dir():
        pdfs = sorted(period_dir.glob("*.pdf"))
        if pdfs:
            return pdfs[0]
    # Flat layout — match any PDF whose inferred period equals this period
    compiled = re.compile(period_regex) if period_regex else None
    for pdf in sorted(pdf_root.glob("*.pdf")):
        if infer_period_from_filename(pdf.name, compiled) == period:
            return pdf
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
    period_regex: str | None = None,
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
    periods = discover_periods(xer_root, period_regex=period_regex)
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

        pdf = find_pdf(pdf_root, curr_period, period_regex=period_regex)
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
