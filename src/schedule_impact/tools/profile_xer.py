"""Build anonymized XER structure reports — no row-level values."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from schedule_impact.ingest.xer_reader import detect_xer_format
from schedule_impact.ingest.xer_text_parser import iter_text_xer


@dataclass
class ColumnProfile:
    name: str
    declared_type: str
    null_fraction: float


@dataclass
class TableProfile:
    row_count: int
    columns: list[ColumnProfile]


@dataclass
class PairMatchStats:
    key: str
    count_previous: int
    count_current: int
    matched: int
    only_previous: int
    only_current: int
    match_rate_previous: float
    match_rate_current: float


@dataclass
class XerProfileReport:
    generated_at: str
    source_basename: str
    sha256_prefix: str
    xer_format: str
    xer_tables: list[str]
    tables: dict[str, TableProfile] = field(default_factory=dict)
    pair_match: list[PairMatchStats] | None = None
    memotype_labels: list[str] = field(default_factory=list)
    actv_code_type_names: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _sha256_prefix(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:12]


def _profile_text_table(path: Path, table: str) -> TableProfile:
    fields: list[str] = []
    row_count = 0
    null_counts: dict[str, int] = {}

    for kind, table_name, payload in iter_text_xer(path):
        if table_name != table:
            continue
        if kind == "fields":
            fields = payload
            null_counts = {name: 0 for name in fields}
        elif kind == "row" and fields:
            row_count += 1
            for idx, name in enumerate(fields):
                val = payload[idx].strip() if idx < len(payload) else ""
                if not val:
                    null_counts[name] = null_counts.get(name, 0) + 1

    columns = [
        ColumnProfile(
            name=name,
            declared_type="TEXT",
            null_fraction=round((null_counts.get(name, 0) / row_count) if row_count else 0.0, 4),
        )
        for name in fields
    ]
    return TableProfile(row_count=row_count, columns=columns)


def _open_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _list_sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def _profile_sqlite_table(conn: sqlite3.Connection, table: str) -> TableProfile:
    row_count = conn.execute(f'SELECT COUNT(*) AS c FROM "{table}"').fetchone()["c"]
    cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns: list[ColumnProfile] = []
    for col in cols:
        name = col["name"]
        declared = col["type"] or "TEXT"
        nulls = conn.execute(
            f'SELECT SUM(CASE WHEN "{name}" IS NULL OR "{name}" = \'\' THEN 1 ELSE 0 END) AS n FROM "{table}"'
        ).fetchone()["n"]
        null_fraction = (nulls / row_count) if row_count else 0.0
        columns.append(
            ColumnProfile(
                name=name,
                declared_type=declared,
                null_fraction=round(null_fraction, 4),
            )
        )
    return TableProfile(row_count=row_count, columns=columns)


def _task_codes_text(path: Path, table: str = "TASK") -> set[str]:
    fields: list[str] = []
    codes: set[str] = set()
    code_idx: int | None = None

    for kind, table_name, payload in iter_text_xer(path):
        if table_name != table:
            continue
        if kind == "fields":
            fields = payload
            code_idx = fields.index("task_code") if "task_code" in fields else None
        elif kind == "row" and code_idx is not None and code_idx < len(payload):
            val = payload[code_idx].strip()
            if val:
                codes.add(val)
    return codes


def _task_codes_sqlite(conn: sqlite3.Connection, table: str = "TASK") -> set[str]:
    if table not in _list_sqlite_tables(conn):
        return set()
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    names = {c["name"] for c in info}
    if "task_code" not in names:
        return set()
    rows = conn.execute(
        f'SELECT DISTINCT "task_code" FROM "{table}" WHERE "task_code" IS NOT NULL AND "task_code" != \'\''
    ).fetchall()
    return {str(r["task_code"]) for r in rows}


def _string_column_text(path: Path, table: str, column: str) -> list[str]:
    """Return distinct non-empty values for one column from a text XER table."""
    fields: list[str] = []
    col_idx: int | None = None
    seen: set[str] = set()
    for kind, table_name, payload in iter_text_xer(path):
        if table_name != table:
            continue
        if kind == "fields":
            fields = payload
            col_idx = fields.index(column) if column in fields else None
        elif kind == "row" and col_idx is not None and col_idx < len(payload):
            val = payload[col_idx].strip()
            if val:
                seen.add(val)
    return sorted(seen)


def _string_column_sqlite(conn: sqlite3.Connection, table: str, column: str) -> list[str]:
    tables = _list_sqlite_tables(conn)
    if table not in tables:
        return []
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    if column not in {c["name"] for c in info}:
        return []
    rows = conn.execute(
        f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL AND "{column}" != \'\' ORDER BY "{column}"'
    ).fetchall()
    return [str(r[0]) for r in rows]


def profile_xer_file(path: Path, *, tables: list[str] | None = None) -> XerProfileReport:
    if not path.is_file():
        raise FileNotFoundError(path)

    fmt = detect_xer_format(path)
    from schedule_impact.ingest.xer_reader import DEFAULT_TABLES, list_xer_tables

    table_list = list(tables) if tables else list(DEFAULT_TABLES)
    all_tables = list_xer_tables(path)
    selected = [t for t in table_list if t in all_tables]
    missing = [t for t in table_list if t not in all_tables]

    report = XerProfileReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        source_basename=path.name,
        sha256_prefix=_sha256_prefix(path),
        xer_format=fmt,
        xer_tables=all_tables,
    )
    if missing:
        report.notes.append(f"Requested tables not present: {missing}")
    if "TASKMEMO" in all_tables:
        report.notes.append(
            "TASKMEMO present — planner HTML memos available (strip + link via task_id/task_code)."
        )
    elif "MEMOTYPE" in all_tables:
        report.notes.append("MEMOTYPE present but TASKMEMO not seen — check export options.")

    if fmt == "text":
        for table in selected:
            report.tables[table] = _profile_text_table(path, table)
        if "MEMOTYPE" in all_tables:
            report.memotype_labels = _string_column_text(path, "MEMOTYPE", "memo_type")
        for actv_table, actv_col in [("ACTVTYPE", "actv_code_type"), ("ACTVCODE", "actv_short_name")]:
            if actv_table in all_tables:
                vals = _string_column_text(path, actv_table, actv_col)
                if vals:
                    report.actv_code_type_names = vals
                    break
    else:
        conn = _open_sqlite(path)
        try:
            for table in selected:
                report.tables[table] = _profile_sqlite_table(conn, table)
            if "MEMOTYPE" in all_tables:
                report.memotype_labels = _string_column_sqlite(conn, "MEMOTYPE", "memo_type")
            for actv_table, actv_col in [("ACTVTYPE", "actv_code_type"), ("ACTVCODE", "actv_short_name")]:
                if actv_table in all_tables:
                    vals = _string_column_sqlite(conn, actv_table, actv_col)
                    if vals:
                        report.actv_code_type_names = vals
                        break
        finally:
            conn.close()

    if report.memotype_labels:
        report.notes.append(f"MEMOTYPE labels found: {report.memotype_labels}")
    if report.actv_code_type_names:
        report.notes.append(f"Activity code types found: {report.actv_code_type_names}")
    return report


def pair_match_stats(previous: Path, current: Path, keys: list[str] | None = None) -> list[PairMatchStats]:
    keys = keys or ["task_code"]
    results: list[PairMatchStats] = []

    for key in keys:
        if key != "task_code":
            continue
        if detect_xer_format(previous) == "text":
            set_a = _task_codes_text(previous)
            set_b = _task_codes_text(current)
        else:
            conn_a = _open_sqlite(previous)
            conn_b = _open_sqlite(current)
            try:
                set_a = _task_codes_sqlite(conn_a)
                set_b = _task_codes_sqlite(conn_b)
            finally:
                conn_a.close()
                conn_b.close()

        matched = len(set_a & set_b)
        only_a = len(set_a - set_b)
        only_b = len(set_b - set_a)
        results.append(
            PairMatchStats(
                key=key,
                count_previous=len(set_a),
                count_current=len(set_b),
                matched=matched,
                only_previous=only_a,
                only_current=only_b,
                match_rate_previous=round(matched / len(set_a), 4) if set_a else 0.0,
                match_rate_current=round(matched / len(set_b), 4) if set_b else 0.0,
            )
        )
    return results


def write_report(report: XerProfileReport, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")


def run_profile(
    xer: Path,
    out: Path,
    *,
    pair: Path | None = None,
    tables: list[str] | None = None,
) -> XerProfileReport:
    from schedule_impact.ingest.xer_reader import DEFAULT_TABLES

    table_list = list(tables) if tables else list(DEFAULT_TABLES)
    report = profile_xer_file(xer, tables=table_list)
    if pair is not None:
        report.pair_match = pair_match_stats(pair, xer)
        report.notes.append(f"Paired with baseline: {pair.name}")
    write_report(report, out)
    return report
