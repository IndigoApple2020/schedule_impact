"""Tasks with finish slip vs a previous XER snapshot."""

from __future__ import annotations

from pathlib import Path

from schedule_impact.ingest.xer_reader import load_xer_tables
from schedule_impact.normalize.p6_tasks import finish_slip_days, match_tasks


def slipped_task_ids(
    current_xer: Path,
    previous_xer: Path,
    *,
    min_slip_days: float = 1.0,
) -> dict[str, float]:
    """
    Return ``{task_id: slip_days}`` for tasks whose finish moved later by at least ``min_slip_days``.
    """
    current = load_xer_tables(current_xer, tables=("TASK",)).get("TASK", [])
    previous = load_xer_tables(previous_xer, tables=("TASK",)).get("TASK", [])

    slipped: dict[str, float] = {}
    for cur, prev in match_tasks(current, previous):
        if prev is None:
            continue
        slip = finish_slip_days(cur, prev)
        if slip is not None and slip >= min_slip_days:
            slipped[cur.task_id] = slip
    return slipped
