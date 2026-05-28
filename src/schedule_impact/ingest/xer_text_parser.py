"""Parse Primavera P6 text XER exports (%T / %F / %R)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class XerTable:
    name: str
    fields: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


def detect_xer_format(path: Path) -> str:
    """Return ``text`` or ``sqlite``."""
    with path.open("rb") as f:
        head = f.read(256)
    if head.startswith(b"SQLite format 3"):
        return "sqlite"
    return "text"


def _split_payload(line: str, *, allow_space_fallback: bool = False) -> list[str]:
    """Split line body after %X marker; prefer tab delimiter (P6 standard)."""
    if len(line) < 2 or line[0] != "%":
        return []
    rest = line[2:].lstrip("\t ")
    if not rest:
        return []
    if "\t" in rest:
        return [part.strip() for part in rest.split("\t")]
    if allow_space_fallback:
        return rest.split()
    # Non-tab data row: return as single token to avoid silently corrupting field offsets
    return [rest]


def iter_text_xer(path: Path, *, encoding: str = "utf-8") -> Iterator[tuple[str, str, list[str]]]:
    """
  Yield ``(kind, table_name, fields)`` where kind is ``table`` | ``fields`` | ``row``.

    ``table_name`` is set only for ``table`` events; otherwise empty string.
    """
    current_table = ""
    current_fields: list[str] = []

    with path.open(encoding=encoding, errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if not line or line[0] != "%" or len(line) < 2:
                continue
            marker = line[1]
            if marker == "T":
                payload = _split_payload(line, allow_space_fallback=True)
                if payload:
                    current_table = payload[0].strip()
                    current_fields = []
                    yield "table", current_table, []
            elif marker == "F":
                payload = _split_payload(line, allow_space_fallback=True)
                current_fields = [p.strip() for p in payload if p.strip()]
                yield "fields", current_table, list(current_fields)
            elif marker == "R" and current_fields:
                yield "row", current_table, _split_payload(line)


def parse_text_xer(
    path: Path,
    *,
    tables: set[str] | None = None,
    encoding: str = "utf-8",
) -> dict[str, XerTable]:
    """Parse selected tables into :class:`XerTable` records (row dicts via :func:`table_to_records`)."""
    out: dict[str, XerTable] = {}
    want = tables

    for kind, table_name, fields in iter_text_xer(path, encoding=encoding):
        if not table_name:
            continue
        if want is not None and table_name not in want:
            continue
        if kind == "table":
            out.setdefault(table_name, XerTable(name=table_name))
        elif kind == "fields":
            out[table_name].fields = fields
        elif kind == "row":
            out[table_name].rows.append(fields)

    if want is not None:
        for name in want:
            out.setdefault(name, XerTable(name=name))
    return out


def table_to_records(table: XerTable) -> list[dict[str, str]]:
    """Map row lists to dicts using the table's %F field list."""
    records: list[dict[str, str]] = []
    if not table.fields:
        return records
    width = len(table.fields)
    for row in table.rows:
        padded = row + [""] * (width - len(row))
        records.append(dict(zip(table.fields, padded[:width], strict=False)))
    return records


def list_text_tables(path: Path, *, encoding: str = "utf-8") -> list[str]:
    names: list[str] = []
    for kind, table_name, _ in iter_text_xer(path, encoding=encoding):
        if kind == "table" and table_name and table_name not in names:
            names.append(table_name)
    return names
