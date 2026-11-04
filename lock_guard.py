"""Database guard for published picks.

Customers screenshot cards before a game and compare them with the graded
result later. These triggers keep what was published:

- lock_* lines (PL / XSharp spread and total): first write wins. A later
  UPDATE that changes a saved value is undone.
- model chances and projected scores: frozen once the game has a final score.

Blank values can still be filled. Triggers live in the database, so every
writer (app, scripts, data patches) is held to the same rule.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

LOCKED_LINES = ("lock_pl_spread", "lock_pl_total", "lock_xs_spread", "lock_xs_total")
FROZEN_AFTER_FINAL = (
    "win_probability", "elo_home_prob", "xgboost_home_prob", "logistic_home_prob",
    "catboost_home_prob", "meta_home_prob", "glicko_home_prob", "trueskill_home_prob",
    "expected_home_score", "expected_away_score",
)


def ensure_lock_guard(conn) -> int:
    """Create the guard triggers that are missing. Returns how many were added."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    if not cols or "id" not in cols:
        return 0
    game_cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    added = 0
    for col in LOCKED_LINES:
        name = f"pl_guard_lock_{col}"
        if col not in cols or name in have:
            continue
        conn.execute(
            f"CREATE TRIGGER {name} AFTER UPDATE OF {col} ON predictions "
            f"WHEN OLD.{col} IS NOT NULL AND NEW.{col} IS NOT OLD.{col} "
            f"BEGIN UPDATE predictions SET {col} = OLD.{col} WHERE id = NEW.id; END"
        )
        added += 1
    if {"game_id", "home_score"} <= game_cols and "game_id" in cols:
        for col in FROZEN_AFTER_FINAL:
            name = f"pl_guard_final_{col}"
            if col not in cols or name in have:
                continue
            conn.execute(
                f"CREATE TRIGGER {name} AFTER UPDATE OF {col} ON predictions "
                f"WHEN OLD.{col} IS NOT NULL AND NEW.{col} IS NOT OLD.{col} "
                f"AND EXISTS (SELECT 1 FROM games g WHERE g.game_id = NEW.game_id "
                f"AND g.home_score IS NOT NULL) "
                f"BEGIN UPDATE predictions SET {col} = OLD.{col} WHERE id = NEW.id; END"
            )
            added += 1
    if added:
        conn.commit()
        logger.warning("[lock-guard] %s guard triggers added", added)
    return added
