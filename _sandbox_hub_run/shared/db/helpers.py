"""SQLite helpers — no global connection cache; callers manage lifecycle."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping


def connect(db_path: Path | str, *, row_factory: bool = True) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    if row_factory:
        conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_from_schema(db_path: Path | str, schema_sql: str) -> Path:
    path = Path(db_path)
    with connect(path) as conn:
        conn.executescript(schema_sql)
        conn.commit()
    return path


def upsert_row(
    conn: sqlite3.Connection,
    table: str,
    row: Mapping[str, Any],
    conflict_cols: Iterable[str],
) -> None:
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)
    conflict = ", ".join(conflict_cols)
    conflict_set = set(conflict_cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in conflict_set)
    sql = (
        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in cols])
