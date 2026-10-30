"""Date-keyed rows from betting_lines / predictions for the finals book / PL enrich.

`substr(game_date,1,10) IN (...)` cannot use an index, so every call scanned the
whole table. The rows only change with the DB file, so each table is read once
per DB change (row order kept), grouped by date, and shared by every hub copy.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

_TTL = 600.0
_LOCK = threading.Lock()
_TABLES: dict = {}

_LOCK_KEYS = (
    "book_home_moneyline",
    "book_away_moneyline",
    "pl_model_home_ml",
    "pl_model_away_ml",
)

_SQL = {
    "betting_lines": (
        "SELECT rowid, game_id, home_moneyline, away_moneyline, game_date, "
        "home_team, away_team FROM betting_lines"
    ),
    "predictions": (
        "SELECT rowid, game_id, lock_card_json, game_date, "
        "home_team_id, away_team_id FROM predictions"
    ),
}


class _Row(tuple):
    """Read like sqlite3.Row: r["game_id"]."""

    __slots__ = ()
    _cols: dict = {}

    def __getitem__(self, key):
        if isinstance(key, str):
            key = self._cols[key]
        return tuple.__getitem__(self, key)


class _BookRow(_Row):
    __slots__ = ()
    _cols = {
        "game_id": 0,
        "home_moneyline": 1,
        "away_moneyline": 2,
        "game_date": 3,
        "home_team": 4,
        "away_team": 5,
    }


class _PredRow(_Row):
    __slots__ = ()
    _cols = {
        "game_id": 0,
        "lock_card_json": 1,
        "game_date": 2,
        "home_team_id": 3,
        "away_team_id": 4,
    }


def _stamp(db_path: str):
    try:
        st = os.stat(db_path)
    except OSError:
        return None
    try:
        wal = os.stat(db_path + "-wal")
        wal_stamp = (wal.st_mtime_ns, wal.st_size) if wal.st_size else None
    except OSError:
        wal_stamp = None
    return (st.st_mtime_ns, st.st_size, wal_stamp)


def _date_key(value: Any):
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode()
        except UnicodeDecodeError:
            return None
    return str(value)[:10]


def _lock_subset(raw: Any):
    if not raw:
        return None
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw) if isinstance(raw, str) else {}
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
    sub = {k: data.get(k) for k in _LOCK_KEYS if data.get(k) is not None}
    return sub or None


def _load(con, table: str) -> dict:
    by_date: dict = {}
    for r in con.execute(_SQL[table]):
        dk = _date_key(r[4] if table == "betting_lines" else r[3])
        if dk is None:
            continue
        if table == "betting_lines":
            if r[2] is None:
                continue
            row = _BookRow((r[1], r[2], r[3], r[4], r[5], r[6]))
        else:
            row = _PredRow((r[1], _lock_subset(r[2]), r[3], r[4], r[5]))
        by_date.setdefault(dk, []).append((r[0], row))
    return by_date


def rows_for_dates(con, db_path: str, table: str, dates) -> list:
    """Same rows, same order as `SELECT ... WHERE substr(game_date,1,10) IN (dates)`.

    betting_lines rows also need `home_moneyline IS NOT NULL`. predictions rows
    carry the lock card as a small dict (only the moneyline keys).
    """
    stamp = _stamp(db_path)
    key = (os.path.abspath(db_path), table)
    hit = _TABLES.get(key)
    if not (hit and stamp is not None and hit[0] == stamp and time.time() - hit[1] < _TTL):
        with _LOCK:
            hit = _TABLES.get(key)
            if not (hit and stamp is not None and hit[0] == stamp and time.time() - hit[1] < _TTL):
                by_date = _load(con, table)
                hit = (stamp, time.time(), by_date)
                if stamp is not None:
                    _TABLES[key] = hit
    by_date = hit[2]
    picked: list = []
    for d in set(dates):
        picked.extend(by_date.get(d) or ())
    picked.sort(key=lambda t: t[0])
    return [row for _, row in picked]
