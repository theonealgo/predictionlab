"""One-time copy of locally stored pre-game lines into the live database.

Patch files live in data/live_patch/<name>.json.gz (built by
tools/export_live_patch.py). Each file is applied once per database; the
name is recorded in the data_patches table. Only prediction lock / model
columns, betting_lines, and missing finished games are written — no user,
account, or subscription tables.
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
MODEL_COLS = (
    "win_probability", "elo_home_prob", "xgboost_home_prob",
    "glicko_home_prob", "trueskill_home_prob",
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


def _insert_missing_games(conn, rows: list) -> tuple[int, int]:
    """Finished games the live database never stored or never scored (models and the
    ladder's graded record read them). A score live already has is never changed."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    if not cols or not rows:
        return 0, 0
    added = scored = 0
    for r in rows:
        have = conn.execute(
            "SELECT home_score FROM games WHERE sport = ? AND game_id = ? LIMIT 1",
            (r["sport"], r["game_id"]),
        ).fetchone()
        if have:
            if have[0] is None and r.get("home_score") is not None and r.get("away_score") is not None:
                scored += conn.execute(
                    "UPDATE games SET home_score = ?, away_score = ?, status = ? "
                    "WHERE sport = ? AND game_id = ? AND home_score IS NULL",
                    (r["home_score"], r["away_score"], r.get("status") or "final", r["sport"], r["game_id"]),
                ).rowcount
            continue
        keys = [k for k in r if k in cols and k != "id"]
        conn.execute(
            f"INSERT INTO games ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
            tuple(r[k] for k in keys),
        )
        added += 1
    return added, scored


def _apply(conn, patch: dict) -> tuple[int, int, int]:
    preds = lines_upd = lines_new = 0
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    for r in patch.get("predictions") or []:
        sets = [c for c in (*LOCK_COLS, *MODEL_COLS) if r.get(c) is not None and c in pcols]
        if not sets:
            continue
        # Fill-only: a lock or model value live already saved is never replaced
        # (published / graded lines must stay what customers saw before the game).
        cur = conn.execute(
            f"UPDATE predictions SET {', '.join(f'{c} = COALESCE({c}, ?)' for c in sets)} "
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
                "UPDATE betting_lines SET spread = COALESCE(spread, ?), total = COALESCE(total, ?), "
                "home_moneyline = COALESCE(home_moneyline, ?), "
                "away_moneyline = COALESCE(away_moneyline, ?) WHERE id = ?",
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


# ESPN renamed these schools; stored games under the old name are the same team,
# so the models read one history.
TEAM_RENAMES_PATCH = "team_renames_2026-11-03"
TEAM_RENAMES = (
    ("NCAAB", "New Orleans Privateers", "LSU New Orleans Privateers"),
    ("NCAAB", "St. Thomas-Minnesota Tommies", "St. Thomas Tommies"),
)
_TEAM_NAME_COLS = (
    ("games", ("home_team_id", "away_team_id")),
    ("predictions", ("home_team_id", "away_team_id", "predicted_winner", "actual_winner")),
    ("betting_lines", ("home_team", "away_team")),
)


def apply_team_renames(db_path: str) -> None:
    if os.environ.get("PL_LIVE_PATCH") == "0":
        return
    try:
        conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception as exc:
        logger.warning("[live-patch] cannot open database: %s", exc)
        return
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS data_patches (name TEXT PRIMARY KEY, applied_at TEXT, summary TEXT)"
        )
        if conn.execute("SELECT 1 FROM data_patches WHERE name = ?", (TEAM_RENAMES_PATCH,)).fetchone():
            return
        conn.execute("BEGIN IMMEDIATE")
        changed = 0
        for table, cols in _TEAM_NAME_COLS:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "sport" not in have:
                continue
            for col in cols:
                if col not in have:
                    continue
                for sport, old, new in TEAM_RENAMES:
                    changed += conn.execute(
                        f"UPDATE {table} SET {col} = ? WHERE sport = ? AND {col} = ?",
                        (new, sport, old),
                    ).rowcount
        conn.execute(
            "INSERT INTO data_patches (name, applied_at, summary) VALUES (?, ?, ?)",
            (TEAM_RENAMES_PATCH, datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), f"cells={changed}"),
        )
        conn.execute("COMMIT")
        logger.warning("[live-patch] applied %s: cells=%s", TEAM_RENAMES_PATCH, changed)
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        logger.warning("[live-patch] %s failed, nothing written: %s", TEAM_RENAMES_PATCH, exc)
    finally:
        conn.close()


def apply_pending(db_path: str) -> None:
    apply_team_renames(db_path)
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
                games_new, games_scored = _insert_missing_games(conn, patch.get("games") or [])
                preds, lines_upd, lines_new = _apply(conn, patch)
                summary = (
                    f"games_added={games_new} games_scored={games_scored} predictions={preds} "
                    f"lines_updated={lines_upd} lines_added={lines_new}"
                )
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
