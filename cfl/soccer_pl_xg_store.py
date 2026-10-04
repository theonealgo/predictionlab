"""Soccer-only PL-xG persistence. Does not touch generic H2H columns."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

_LIVE_ROOT = Path(__file__).resolve().parent
DEFAULT_DB = _LIVE_ROOT / "data" / "soccer_pl_expected.sqlite"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS soccer_pl_expected (
    game_id TEXT PRIMARY KEY,
    asof_date TEXT NOT NULL,
    home_team TEXT,
    away_team TEXT,
    league TEXT,
    soccer_pl_expected_home REAL NOT NULL,
    soccer_pl_expected_away REAL NOT NULL,
    soccer_pl_expected_total REAL NOT NULL,
    soccer_pl_expected_p_over_15 REAL,
    soccer_pl_expected_p_over_25 REAL,
    soccer_pl_expected_p_over_35 REAL,
    soccer_pl_expected_method TEXT,
    soccer_pl_expected_n_home INTEGER,
    soccer_pl_expected_n_away INTEGER,
    actual_home REAL,
    actual_away REAL,
    book_total REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(CREATE_SQL)
    return conn


def upsert_rows(rows: Iterable[dict], db_path: Path | None = None) -> int:
    conn = connect(db_path)
    n = 0
    try:
        for r in rows:
            conn.execute(
                """
                INSERT INTO soccer_pl_expected (
                    game_id, asof_date, home_team, away_team, league,
                    soccer_pl_expected_home, soccer_pl_expected_away, soccer_pl_expected_total,
                    soccer_pl_expected_p_over_15, soccer_pl_expected_p_over_25, soccer_pl_expected_p_over_35,
                    soccer_pl_expected_method, soccer_pl_expected_n_home, soccer_pl_expected_n_away,
                    actual_home, actual_away, book_total
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(game_id) DO UPDATE SET
                    asof_date=excluded.asof_date,
                    soccer_pl_expected_home=excluded.soccer_pl_expected_home,
                    soccer_pl_expected_away=excluded.soccer_pl_expected_away,
                    soccer_pl_expected_total=excluded.soccer_pl_expected_total,
                    soccer_pl_expected_p_over_15=excluded.soccer_pl_expected_p_over_15,
                    soccer_pl_expected_p_over_25=excluded.soccer_pl_expected_p_over_25,
                    soccer_pl_expected_p_over_35=excluded.soccer_pl_expected_p_over_35,
                    soccer_pl_expected_method=excluded.soccer_pl_expected_method,
                    soccer_pl_expected_n_home=excluded.soccer_pl_expected_n_home,
                    soccer_pl_expected_n_away=excluded.soccer_pl_expected_n_away,
                    actual_home=excluded.actual_home,
                    actual_away=excluded.actual_away,
                    book_total=excluded.book_total
                """,
                (
                    r.get("game_id"),
                    r.get("asof_date"),
                    r.get("home_team"),
                    r.get("away_team"),
                    r.get("league"),
                    r.get("soccer_pl_expected_home"),
                    r.get("soccer_pl_expected_away"),
                    r.get("soccer_pl_expected_total"),
                    r.get("soccer_pl_expected_p_over_15"),
                    r.get("soccer_pl_expected_p_over_25"),
                    r.get("soccer_pl_expected_p_over_35"),
                    r.get("soccer_pl_expected_method"),
                    r.get("soccer_pl_expected_n_home"),
                    r.get("soccer_pl_expected_n_away"),
                    r.get("actual_home"),
                    r.get("actual_away"),
                    r.get("book_total"),
                ),
            )
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def fetch_one(game_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM soccer_pl_expected WHERE game_id = ?", (game_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
