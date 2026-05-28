#!/usr/bin/env python3
"""Create committed synthetic text XER fixtures for tests."""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic"


def _line(kind: str, *fields: str) -> str:
    return f"%{kind}\t" + "\t".join(fields) + "\n"


def _write_xer(path: Path, task_rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "task_id",
        "proj_id",
        "wbs_id",
        "clndr_id",
        "task_code",
        "task_name",
        "status_code",
        "early_start_date",
        "early_end_date",
        "late_start_date",
        "late_end_date",
        "act_start_date",
        "act_end_date",
        "total_float_hr_cnt",
        "free_float_hr_cnt",
        "driving_path_flag",
    ]
    lines = [
        _line("T", "PROJECT"),
        _line("F", "proj_id", "proj_short_name", "plan_end_date"),
        _line("R", "P1", "Synthetic Project", "2025-12-31"),
        _line("T", "PROJWBS"),
        _line("F", "wbs_id", "proj_id", "wbs_name", "parent_wbs_id"),
        _line("R", "W1", "P1", "Area A", ""),
        _line("T", "TASK"),
        _line("F", *fields),
    ]
    for row in task_rows:
        lines.append(_line("R", *row))
    lines.extend(
        [
            _line("T", "MEMOTYPE"),
            _line("F", "memo_type_id", "memo_type"),
            _line("R", "1347", "Planners Notes (CP)"),
            _line("T", "TASKMEMO"),
            _line("F", "memo_id", "task_id", "memo_type_id", "proj_id", "task_memo"),
            _line(
                "R",
                "9001",
                "T2",
                "1347",
                "P1",
                '<!DOCTYPE HTML><HTML><BODY><P>SYN-200 delayed due to rework on prior activity.</P></BODY></HTML>',
            ),
        ]
    )
    path.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    march = [
        ["T1", "P1", "W1", "C1", "SYN-100", "Critical path activity", "TK_Active",
         "2025-03-01 08:00", "2025-03-15 16:00", "2025-03-01 08:00", "2025-03-15 16:00", "", "",
         "0", "0", "Y"],
        ["T2", "P1", "W1", "C1", "SYN-200", "Floated activity", "TK_Active",
         "2025-03-10 08:00", "2025-03-25 16:00", "2025-03-10 08:00", "2025-03-25 16:00", "", "",
         "40", "16", "N"],
        ["T3", "P1", "W1", "C1", "SYN-300", "Stable activity", "TK_Active",
         "2025-04-01 08:00", "2025-04-10 16:00", "2025-04-01 08:00", "2025-04-10 16:00", "", "",
         "80", "40", "N"],
    ]
    april = [
        ["T1", "P1", "W1", "C1", "SYN-100", "Critical path activity", "TK_Active",
         "2025-03-01 08:00", "2025-03-22 16:00", "2025-03-01 08:00", "2025-03-22 16:00", "", "",
         "0", "0", "Y"],
        ["T2", "P1", "W1", "C1", "SYN-200", "Floated activity", "TK_Active",
         "2025-03-10 08:00", "2025-04-02 16:00", "2025-03-10 08:00", "2025-04-02 16:00", "", "",
         "24", "8", "N"],
        ["T3", "P1", "W1", "C1", "SYN-300", "Stable activity", "TK_Active",
         "2025-04-01 08:00", "2025-04-10 16:00", "2025-04-01 08:00", "2025-04-10 16:00", "", "",
         "80", "40", "N"],
    ]
    _write_xer(OUT / "2025-03.xer", march)
    _write_xer(OUT / "2025-04.xer", april)
    print(f"Wrote synthetic text XER fixtures to {OUT}")


if __name__ == "__main__":
    main()
