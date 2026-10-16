"""Ladder database: its own SQLite file, separate from the predictions database.

On Render the file lives on the persistent disk (/data) so it survives deploys.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS ladder_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,              -- active | completed | failed | no_pick | expired
    start_date TEXT NOT NULL,          -- ET date of Day 1
    cycle_end_date TEXT NOT NULL,      -- start_date + 9 days
    start_amount_cents INTEGER NOT NULL,
    current_amount_cents INTEGER NOT NULL,
    final_day INTEGER,                 -- last ladder day that had a pick or no-pick record
    end_reason TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ladder_one_active
    ON ladder_runs(status) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS ladder_one_start_date ON ladder_runs(start_date);

CREATE TABLE IF NOT EXISTS ladder_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ladder_id INTEGER NOT NULL REFERENCES ladder_runs(id),
    day_number INTEGER NOT NULL,
    pick_date TEXT NOT NULL,           -- ET date the pick was published for
    result TEXT NOT NULL,              -- pending | win | loss | push | void | no_pick
    sport TEXT,
    game_id TEXT,
    event_label TEXT,
    home_team TEXT,
    away_team TEXT,
    pick_team TEXT,
    pick_side TEXT,                    -- HOME | AWAY | OVER | UNDER
    market TEXT,                       -- moneyline | spread | total
    line REAL,                         -- spread line for the picked side, or the total
    pick_label TEXT,                   -- bet as placed, e.g. 'Under 6.5'
    pick_type TEXT,                    -- pick type judged by our graded record
    model_name TEXT,
    model_prob REAL,
    market_prob REAL,
    credible_prob REAL,
    hist_hit_rate REAL,
    hist_n INTEGER,
    american_odds INTEGER,
    decimal_odds REAL,
    opponent_american_odds INTEGER,
    stake_cents INTEGER,
    profit_cents INTEGER,
    amount_after_cents INTEGER,
    event_start TEXT,                  -- UTC ISO start time
    selected_at TEXT NOT NULL,
    settled_at TEXT,
    final_home_score INTEGER,
    final_away_score INTEGER,
    note TEXT,
    UNIQUE(ladder_id, day_number)
);
CREATE UNIQUE INDEX IF NOT EXISTS ladder_days_one_game
    ON ladder_days(ladder_id, game_id) WHERE game_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS ladder_no_pick_dates (
    pick_date TEXT PRIMARY KEY,        -- ET date with no qualifying Day 1 pick
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ladder_social_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ladder_id INTEGER NOT NULL,
    day_number INTEGER NOT NULL,       -- 0 for ladder result posts
    kind TEXT NOT NULL,                -- day_live | ladder_result
    platform TEXT NOT NULL,
    status TEXT NOT NULL,              -- pending | sending | posted | failed | needs_review
    attempts INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    external_id TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(ladder_id, day_number, kind, platform)
);
"""


def db_path() -> str:
    explicit = (os.environ.get("LADDER_DB_PATH") or "").strip()
    if explicit:
        return explicit
    if os.path.isdir("/data"):
        return "/data/ladder.db"
    return str(_ROOT / "data" / "ladder.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    conn.executescript(SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(ladder_days)")}
    for col, typ in _ADDED_COLUMNS:
        if col not in have:
            try:
                conn.execute(f"ALTER TABLE ladder_days ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # another connection added it first
    return conn


_ADDED_COLUMNS = (("line", "REAL"), ("pick_label", "TEXT"), ("pick_type", "TEXT"), ("model_name", "TEXT"))


@contextmanager
def write_txn(conn: sqlite3.Connection):
    """One writer at a time across threads and processes."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
