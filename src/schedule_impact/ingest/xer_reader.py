"""Load Primavera P6 .xer files (text %T/%F/%R or SQLite)."""

from __future__ import annotations

from pathlib import Path

from schedule_impact.ingest.xer_text_parser import (
    XerTable,
    detect_xer_format,
    list_text_tables,
    parse_text_xer,
    table_to_records,
)

__all__ = [
    "DEFAULT_TABLES",
    "OPTIONAL_NARRATIVE_TABLES",
    "detect_xer_format",
    "load_xer_tables",
    "list_xer_tables",
]

# Core tables for incident detection and linking
DEFAULT_TABLES = (
    "PROJECT",
    "PROJWBS",
    "TASK",
    "TASKPRED",
    "CALENDAR",
)

# In-schedule narrative (HTML memos) + PDFs
NARRATIVE_TABLES = (
    "MEMOTYPE",
    "TASKMEMO",
)

OPTIONAL_NARRATIVE_TABLES = NARRATIVE_TABLES + (
    "TASKACTV",
    "ACTVCODE",
)


def load_xer_tables(
    path: Path,
    *,
    tables: tuple[str, ...] | None = None,
) -> dict[str, list[dict[str, str]]]:
    """Return ``{table_name: [row_dict, ...]}`` for the requested tables."""
    table_list = tables or DEFAULT_TABLES
    fmt = detect_xer_format(path)

    if fmt == "text":
        parsed = parse_text_xer(path, tables=set(table_list))
        return {name: table_to_records(parsed[name]) for name in table_list if name in parsed}

    # SQLite (some export paths)
    import sqlite3

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        result: dict[str, list[dict[str, str]]] = {}
        for name in table_list:
            try:
                rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
            except sqlite3.OperationalError:
                result[name] = []
                continue
            result[name] = [{k: "" if row[k] is None else str(row[k]) for k in row.keys()} for row in rows]
        return result
    finally:
        conn.close()


def list_xer_tables(path: Path) -> list[str]:
    if detect_xer_format(path) == "text":
        return list_text_tables(path)
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()
