"""One-time copy of locally stored pre-game lines into the live database.

Patch files live in data/live_patch/<name>.json.gz (built by
tools/export_live_patch.py). Each file is applied once per database; the
name is recorded in the data_patches table. Only prediction lock columns and
betting_lines are written — no user, account, or subscription tables.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PATCH_DIR = Path(__file__).resolve().parent / "data" / "live_patch"
LOCK_COLS = (
    "expected_home_score", "expected_away_score",
    "lock_xs_spread", "lock_xs_total", "lock_pl_spread", "lock_pl_total",
    "lock_card_json",
)
LINE_COLS = ("spread", "total", "home_moneyline", "away_moneyline")


BETTING_LINES_EXTRA = (
    ("sport", "TEXT"), ("game_date", "TEXT"), ("home_team", "TEXT"), ("away_team", "TEXT"),
    ("home_moneyline", "INTEGER"), ("away_moneyline", "INTEGER"),
    ("source", "TEXT"), ("fetched_at", "TEXT"),
)
PREDICTION_LOCK_TYPES = (
    ("expected_home_score", "REAL"), ("expected_away_score", "REAL"),
    ("lock_xs_spread", "REAL"), ("lock_xs_total", "REAL"),
    ("lock_pl_spread", "REAL"), ("lock_pl_total", "REAL"), ("lock_card_json", "TEXT"),
)


def ensure_schema(conn) -> None:
    """Give an old 4-column betting_lines the columns the app reads and writes.

    Without `sport` / moneyline columns every book price fetch kept only the
    spread and total. Old rows get their sport, date and teams from predictions
    so lookups by sport still find them.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS betting_lines "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, spread REAL, total REAL)"
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(betting_lines)")}
    added = [c for c, _ in BETTING_LINES_EXTRA if c not in cols]
    for col, typ in BETTING_LINES_EXTRA:
        if col not in cols:
            conn.execute(f"ALTER TABLE betting_lines ADD COLUMN {col} {typ}")
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    if pcols:
        for col, typ in PREDICTION_LOCK_TYPES:
            if col not in pcols:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} {typ}")
    tagged = conn.execute(
        "UPDATE betting_lines SET "
        "sport = (SELECT p.sport FROM predictions p WHERE p.game_id = betting_lines.game_id LIMIT 1), "
        "game_date = COALESCE(game_date, (SELECT p.game_date FROM predictions p "
        "WHERE p.game_id = betting_lines.game_id LIMIT 1)), "
        "home_team = COALESCE(home_team, (SELECT p.home_team_id FROM predictions p "
        "WHERE p.game_id = betting_lines.game_id LIMIT 1)), "
        "away_team = COALESCE(away_team, (SELECT p.away_team_id FROM predictions p "
        "WHERE p.game_id = betting_lines.game_id LIMIT 1)) "
        "WHERE sport IS NULL AND game_id IN (SELECT game_id FROM predictions)"
    ).rowcount
    conn.commit()
    if added or tagged:
        logger.warning("[live-patch] betting_lines columns added %s, old rows tagged %s", added, tagged)


def _apply(conn, patch: dict) -> tuple[int, int, int]:
    preds = lines_upd = lines_new = 0
    for r in patch.get("predictions") or []:
        sets = [c for c in LOCK_COLS if r.get(c) is not None]
        if not sets:
            continue
        cur = conn.execute(
            f"UPDATE predictions SET {', '.join(f'{c} = ?' for c in sets)} "
            "WHERE sport = ? AND game_id = ? AND home_team_id = ?",
            (*(r[c] for c in sets), r["sport"], r["game_id"], r["home_team_id"]),
        )
        preds += cur.rowcount
    for r in patch.get("betting_lines") or []:
        last = conn.execute(
            "SELECT id FROM betting_lines WHERE sport = ? AND game_id = ? "
            "ORDER BY fetched_at DESC LIMIT 1",
            (r["sport"], r["game_id"]),
        ).fetchone()
        if last is not None:
            conn.execute(
                "UPDATE betting_lines SET spread = COALESCE(?, spread), total = COALESCE(?, total), "
                "home_moneyline = COALESCE(?, home_moneyline), "
                "away_moneyline = COALESCE(?, away_moneyline) WHERE id = ?",
                (*(r.get(c) for c in LINE_COLS), last[0]),
            )
            lines_upd += 1
        else:
            conn.execute(
                "INSERT INTO betting_lines (sport, game_id, game_date, home_team, away_team, "
                "spread, total, home_moneyline, away_moneyline, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["sport"], r["game_id"], r["game_date"], r["home_team"], r["away_team"],
                    *(r.get(c) for c in LINE_COLS), r.get("source") or "pl_book_odds_api",
                    r.get("fetched_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            lines_new += 1
    return preds, lines_upd, lines_new


def apply_pending(db_path: str) -> None:
    if os.environ.get("PL_LIVE_PATCH") == "0":
        return
    files = sorted(PATCH_DIR.glob("*.json.gz")) if PATCH_DIR.is_dir() else []
    try:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        ensure_schema(conn)
        if not files:
            conn.close()
            return
        conn.execute(
            "CREATE TABLE IF NOT EXISTS data_patches (name TEXT PRIMARY KEY, applied_at TEXT, summary TEXT)"
        )
        conn.commit()
    except Exception as exc:
        logger.warning("[live-patch] cannot open database: %s", exc)
        return
    try:
        for path in files:
            name = path.name
            if conn.execute("SELECT 1 FROM data_patches WHERE name = ?", (name,)).fetchone():
                continue
            try:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    patch = json.load(fh)
                conn.execute("BEGIN IMMEDIATE")
                preds, lines_upd, lines_new = _apply(conn, patch)
                summary = f"predictions={preds} lines_updated={lines_upd} lines_added={lines_new}"
                conn.execute(
                    "INSERT INTO data_patches (name, applied_at, summary) VALUES (?, ?, ?)",
                    (name, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), summary),
                )
                conn.commit()
                logger.warning("[live-patch] applied %s: %s", name, summary)
            except Exception as exc:
                conn.rollback()
                logger.warning("[live-patch] %s failed, nothing written: %s", name, exc)
    finally:
        conn.close()
