#!/usr/bin/env python3
"""
Emit book_odds_engine reference lines for today's slate (read-only DB).

Does not import or modify NHL77FINAL odds logic or 2025_sports/odds_engine/.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore


def _db_path() -> str:
    data_dir = "/data" if os.path.isdir("/data") else "."
    return os.path.join(data_dir, "sports_predictions_original.db")


def _today_et() -> str:
    if ZoneInfo:
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def _p_home(row: sqlite3.Row) -> float:
    for key in ("win_probability", "xgboost_home_prob", "elo_home_prob"):
        v = row[key]
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 1.0:
            f = f / 100.0
        return max(1e-4, min(1.0 - 1e-4, f))
    return 0.5


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from src.odds.book_odds_engine import build_book_odds_bundle

    db = os.environ.get("PL_DB_PATH") or _db_path()
    if not os.path.isfile(db):
        print(json.dumps({"error": "database not found", "path": db}, indent=2))
        return 1

    today = os.environ.get("PL_SAMPLE_DATE") or _today_et()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    engine_by_gid: dict[str, dict] = {}
    try:
        eo_rows = conn.execute(
            """
            SELECT game_id, sport, home_moneyline, away_moneyline, spread, total, source
            FROM engine_odds
            WHERE game_date = ?
            """,
            (today,),
        ).fetchall()
        engine_by_gid = {str(r["game_id"]): {k: r[k] for k in r.keys()} for r in eo_rows}
    except sqlite3.Error:
        engine_by_gid = {}

    sql_with_status = """
        SELECT p.game_id, p.sport, p.game_date, p.home_team_id, p.away_team_id,
               p.win_probability, p.xgboost_home_prob, p.elo_home_prob,
               g.home_score, g.away_score, g.status AS game_status
        FROM predictions p
        LEFT JOIN games g ON g.game_id = p.game_id AND g.sport = p.sport
        WHERE p.game_date = ?
          AND (
            g.game_id IS NULL
            OR (
              (g.home_score IS NULL OR g.away_score IS NULL)
              AND (
                g.status IS NULL
                OR LOWER(TRIM(COALESCE(g.status, ''))) NOT IN (
                  'final', 'postponed', 'canceled', 'cancelled',
                  'completed', 'closed'
                )
              )
            )
          )
        ORDER BY p.sport, p.home_team_id, p.away_team_id
    """
    sql_fallback = """
        SELECT p.game_id, p.sport, p.game_date, p.home_team_id, p.away_team_id,
               p.win_probability, p.xgboost_home_prob, p.elo_home_prob,
               g.home_score, g.away_score, NULL AS game_status
        FROM predictions p
        LEFT JOIN games g ON g.game_id = p.game_id AND g.sport = p.sport
        WHERE p.game_date = ?
          AND (g.home_score IS NULL OR g.away_score IS NULL)
        ORDER BY p.sport, p.home_team_id, p.away_team_id
    """
    try:
        rows = conn.execute(sql_with_status, (today,)).fetchall()
    except sqlite3.OperationalError:
        rows = conn.execute(sql_fallback, (today,)).fetchall()

    if not rows:
        try:
            rows = conn.execute(
                """
                SELECT p.game_id, p.sport, p.game_date, p.home_team_id, p.away_team_id,
                       p.win_probability, p.xgboost_home_prob, p.elo_home_prob,
                       g.home_score, g.away_score, g.status AS game_status
                FROM predictions p
                LEFT JOIN games g ON g.game_id = p.game_id AND g.sport = p.sport
                WHERE p.game_date = ?
                  AND (
                    g.game_id IS NULL
                    OR (
                      (g.home_score IS NULL OR g.away_score IS NULL)
                      AND (
                        g.status IS NULL
                        OR LOWER(TRIM(COALESCE(g.status, ''))) NOT IN (
                          'final', 'postponed', 'canceled', 'cancelled',
                          'completed', 'closed'
                        )
                      )
                    )
                  )
                ORDER BY p.sport, p.home_team_id, p.away_team_id
                """,
                (today,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                """
                SELECT p.game_id, p.sport, p.game_date, p.home_team_id, p.away_team_id,
                       p.win_probability, p.xgboost_home_prob, p.elo_home_prob,
                       g.home_score, g.away_score, NULL AS game_status
                FROM predictions p
                LEFT JOIN games g ON g.game_id = p.game_id AND g.sport = p.sport
                WHERE p.game_date = ?
                ORDER BY p.sport, p.home_team_id, p.away_team_id
                """,
                (today,),
            ).fetchall()

    conn.close()

    out: dict = {
        "generated_at_et": today,
        "database": os.path.abspath(db),
        "note": "Parallel book_odds_engine only; production odds unchanged. "
        "When games.status exists, final/postponed/cancelled rows are excluded.",
        "games": [],
    }

    for r in rows:
        sport = (r["sport"] or "NBA").strip().upper()
        gid = str(r["game_id"] or f"{r['away_team_id']}@{r['home_team_id']}")
        ph = _p_home(r)
        bundle = build_book_odds_bundle(gid, sport, ph, vig=0.045)
        bundle["matchup"] = {
            "away": r["away_team_id"],
            "home": r["home_team_id"],
            "sport": sport,
            "game_date": r["game_date"],
            "prob_source_order": "win_probability → xgboost_home_prob → elo_home_prob",
        }
        # Pick strength vs coin flip (0–100), for comparison only.
        bundle["confidence_vs_coinflip_pct"] = round(abs(ph - 0.5) * 200.0, 2)
        if "game_status" in r.keys() and r["game_status"] is not None:
            bundle["game_status"] = r["game_status"]
        if gid in engine_by_gid:
            bundle["db_engine_odds"] = engine_by_gid[gid]
        out["games"].append(bundle)

    samples_dir = root / "src" / "odds" / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    out_path = samples_dir / f"book_reference_{today}.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    out["written_to"] = str(out_path)

    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
