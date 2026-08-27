"""Sync CFL schedule into sqlite and write predictions."""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("CFL_SANDBOX_DB") or (ROOT / "database" / "cfl_sandbox.db"))
SCHEMA_PATH = ROOT / "database" / "schema.sql"

# Load sibling modules by path to avoid colliding with other isolation packages.
import importlib.util

def _load(name, rel):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod

_fetch = _load("cfl_fetch_mod", "engine/fetch.py")
_predict = _load("cfl_predict_mod", "engine/predict.py")
_models = _load("cfl_models_v2", "engine/models_v2.py")
CFL_TEAMS_FALLBACK = _fetch.CFL_TEAMS_FALLBACK
fetch_espn_teams = _fetch.fetch_espn_teams
fetch_official_all_games = _fetch.fetch_official_all_games
fetch_h2h_history = _fetch.fetch_h2h_history
games_in_window = _fetch.games_in_window
is_regular_season_round = _fetch.is_regular_season_round
cfl_season_year = _fetch.cfl_season_year
_event_year = _fetch._event_year
build_profiles = _predict.build_profiles
predict_matchup = _predict.predict_matchup  # baseline (Current) — kept for backtests
build_state = _models.build_state
build_live_state = _models.build_live_state
predict_matchup_v2 = _models.predict_matchup_v2
MODEL_NAME = getattr(_models, "MODEL_NAME", "cfl_v3_cal_blend")
# Env escape hatch to force baseline predict path (debug only).
USE_V1 = os.environ.get("CFL_USE_V1", "").strip() in ("1", "true", "yes")


def connect() -> sqlite3.Connection:
    global DB_PATH
    try:
        from shared.db import ensure_writable_sqlite

        DB_PATH = ensure_writable_sqlite(DB_PATH)
    except Exception:
        pass
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> Path:
    with connect() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    return DB_PATH


def sync_schedule(*, refresh_cache: bool = False) -> dict[str, Any]:
    init_db()
    games = fetch_official_all_games(use_cache=not refresh_cache)
    teams = fetch_espn_teams()
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        seen_names: set[str] = set()
        for tid, name, short in list(teams) + list(CFL_TEAMS_FALLBACK):
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            # Prefer stable slug ids from fallback when names match
            row = conn.execute("SELECT team_id FROM cfl_teams WHERE name=?", (name,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE cfl_teams SET short_name=?, updated_at=? WHERE name=?",
                    (short, now, name),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO cfl_teams (team_id, name, short_name, elo, updated_at)
                    VALUES (?, ?, ?, 1500, ?)
                    """,
                    (tid, name, short, now),
                )
        for g in games:
            conn.execute(
                """
                INSERT INTO cfl_games (
                  game_id, cfl_id, game_date, home_team, away_team,
                  home_score, away_score, status, round_name, source, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(game_id) DO UPDATE SET
                  home_score=excluded.home_score,
                  away_score=excluded.away_score,
                  status=excluded.status,
                  round_name=excluded.round_name,
                  source=excluded.source,
                  updated_at=excluded.updated_at
                """,
                (
                    g["game_id"],
                    g.get("cfl_id"),
                    g["game_date"],
                    g["home_team"],
                    g["away_team"],
                    g.get("home_score"),
                    g.get("away_score"),
                    g.get("status"),
                    g.get("round_name"),
                    g.get("source"),
                    now,
                ),
            )
        conn.commit()
    return {"games": len(games), "teams": len(teams), "db": str(DB_PATH)}


def sync_h2h_history(*, refresh_cache: bool = False) -> dict[str, Any]:
    """Upsert prior-season regular-season finals for H2H L10 only.

    Rows are tagged source='h2h-history' and must not train Elo or appear as
    current-season results.
    """
    init_db()
    games = fetch_h2h_history(use_cache=not refresh_cache)
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        for g in games:
            conn.execute(
                """
                INSERT INTO cfl_games (
                  game_id, cfl_id, game_date, home_team, away_team,
                  home_score, away_score, status, round_name, source, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(game_id) DO UPDATE SET
                  home_score=excluded.home_score,
                  away_score=excluded.away_score,
                  status=excluded.status,
                  round_name=excluded.round_name,
                  source=excluded.source,
                  updated_at=excluded.updated_at
                """,
                (
                    g["game_id"],
                    g.get("cfl_id"),
                    g["game_date"],
                    g["home_team"],
                    g["away_team"],
                    g.get("home_score"),
                    g.get("away_score"),
                    g.get("status"),
                    g.get("round_name"),
                    g.get("source"),
                    now,
                ),
            )
        conn.commit()
    return {"h2h_history_games": len(games)}


def _load_completed(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM cfl_games
        WHERE status = 'complete'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND IFNULL(source, '') != 'h2h-history'
        ORDER BY game_date ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _store_profiles(conn: sqlite3.Connection, profiles: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for name, p in profiles.items():
        conn.execute(
            """
            INSERT INTO cfl_team_stats (
              team_name, games_played, wins, losses, points_for, points_against,
              off_eff, def_eff, to_diff, form_last5, qb_rating, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(team_name) DO UPDATE SET
              games_played=excluded.games_played, wins=excluded.wins, losses=excluded.losses,
              points_for=excluded.points_for, points_against=excluded.points_against,
              off_eff=excluded.off_eff, def_eff=excluded.def_eff, to_diff=excluded.to_diff,
              form_last5=excluded.form_last5, qb_rating=excluded.qb_rating, updated_at=excluded.updated_at
            """,
            (
                name, p.games, p.wins, p.losses, p.pf, p.pa,
                p.off_eff, p.def_eff, p.to_diff, p.form_last5, p.qb_rating, now,
            ),
        )
        conn.execute(
            "UPDATE cfl_teams SET elo=?, updated_at=? WHERE name=?",
            (p.elo, now, name),
        )


def backfill_historical_predictions(*, force: bool = False) -> dict[str, Any]:
    """Walk-forward lock picks for completed games that have no prediction.

    For each complete game (oldest → newest), train on *prior* completed games
    only, then write a prediction. Never uses that game's final score as a
    feature. Skips games that already have a row unless force — or when the
    stored model_name is not the accepted sandbox model.
    """
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    written = 0
    skipped = 0
    hist_name = f"{MODEL_NAME}_hist" if not USE_V1 else "cfl_elo_form_v1_hist"
    with connect() as conn:
        completed = _load_completed(conn)
        existing_models = {
            r["game_id"]: r["model_name"]
            for r in conn.execute("SELECT game_id, model_name FROM cfl_predictions").fetchall()
        }
        # Accepted model path: expanding EngineState (same as walk_forward)
        st = None if USE_V1 else _models.EngineState()
        prior: list[dict[str, Any]] = []
        for g in completed:
            gid = g["game_id"]
            prev_model = existing_models.get(gid)
            if USE_V1:
                need = force or gid not in existing_models
            else:
                need = force or gid not in existing_models or (
                    prev_model is not None and MODEL_NAME not in str(prev_model)
                )
            if not need:
                skipped += 1
                prior.append(g)
                if not USE_V1:
                    assert st is not None
                    # Keep ratings in sync even when skipping writes
                    if g.get("home_score") is not None:
                        st.observe(g)
                continue

            if USE_V1:
                elo, profiles = build_profiles(prior)
                pred = predict_matchup(
                    g["home_team"],
                    g["away_team"],
                    elo=elo,
                    profiles=profiles,
                    game_date=g.get("game_date"),
                )
            else:
                assert st is not None
                if len(st.cal_probs) >= 8:
                    st.refit_calibrator()
                pred = predict_matchup_v2(
                    g["home_team"],
                    g["away_team"],
                    state=st,
                    game_date=g.get("game_date"),
                )
                hs, as_ = int(g["home_score"]), int(g["away_score"])
                y = 1.0 if hs > as_ else (0.0 if hs < as_ else 0.5)
                if y in (0.0, 1.0):
                    st.cal_probs.append(pred["raw_home_win_prob"])
                    st.cal_y.append(y)

            conn.execute("DELETE FROM cfl_predictions WHERE game_id=?", (gid,))
            pred_id = f"cp_{uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO cfl_predictions (
                  pred_id, game_id, created_at, model_name,
                  home_win_prob, away_win_prob, predicted_home_score, predicted_away_score,
                  model_spread, model_total, pick_ml, confidence, explanation
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pred_id,
                    gid,
                    now,
                    hist_name,
                    pred["home_win_prob"],
                    pred["away_win_prob"],
                    pred["predicted_home_score"],
                    pred["predicted_away_score"],
                    pred["model_spread"],
                    pred["model_total"],
                    pred["pick_ml"],
                    pred["confidence"],
                    pred.get("explanation") or "Walk-forward pre-game lock (historical backfill).",
                ),
            )
            written += 1
            prior.append(g)
            if not USE_V1:
                assert st is not None
                st.observe(g)
        # Refresh end-of-season profiles for upcoming picks / UI stats
        elo, profiles = build_profiles(completed)
        _store_profiles(conn, profiles)
        conn.commit()
    return {
        "backfilled": written,
        "skipped_existing": skipped,
        "completed": len(completed),
        "model_name": hist_name,
        "stale_flag": None,
    }


def ensure_predictions(*, refresh: bool = False) -> dict[str, Any]:
    meta = sync_schedule(refresh_cache=refresh)
    try:
        meta = {**meta, **sync_h2h_history(refresh_cache=refresh)}
    except Exception:
        pass
    hist = backfill_historical_predictions(force=False)
    meta = {**meta, **hist}
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        completed = _load_completed(conn)
        elo, profiles = build_profiles(completed)
        _store_profiles(conn, profiles)
        state = None if USE_V1 else build_live_state(completed)

        upcoming = conn.execute(
            """
            SELECT * FROM cfl_games
            WHERE lower(status) IN ('scheduled', 'pre-game', 'pregame', 'status_scheduled', 'live')
               OR (home_score IS NULL AND away_score IS NULL AND date(game_date) >= date('now', '-1 day'))
            ORDER BY game_date ASC
            """
        ).fetchall()
        # Prefer near window
        all_games = [dict(r) for r in conn.execute("SELECT * FROM cfl_games").fetchall()]
        window = games_in_window(all_games, days_back=1, days_fwd=21)
        scheduled = [
            g for g in window
            if (g.get("status") or "").lower() not in {"complete", "final", "closed"}
            or (g.get("home_score") is None and g.get("away_score") is None)
        ]
        if not scheduled:
            scheduled = [dict(r) for r in upcoming]

        written = 0
        cards: list[dict[str, Any]] = []
        live_name = "cfl_elo_form_v1" if USE_V1 else MODEL_NAME
        for g in scheduled:
            ht = (g.get("home_team") or "").strip()
            at = (g.get("away_team") or "").strip()
            if not ht or not at or ht.upper() == "TBD" or at.upper() == "TBD":
                continue
            existing = conn.execute(
                "SELECT * FROM cfl_predictions WHERE game_id=? ORDER BY created_at ASC LIMIT 1",
                (g["game_id"],),
            ).fetchone()
            if existing:
                locked = dict(existing)
                cards.append({**g, **locked, "pred_id": locked.get("pred_id")})
                continue
            if USE_V1:
                pred = predict_matchup(
                    g["home_team"],
                    g["away_team"],
                    elo=elo,
                    profiles=profiles,
                    game_date=g.get("game_date"),
                )
            else:
                assert state is not None
                pred = predict_matchup_v2(
                    g["home_team"],
                    g["away_team"],
                    state=state,
                    game_date=g.get("game_date"),
                )
            conn.execute(
                "UPDATE cfl_games SET rest_home=?, rest_away=? WHERE game_id=?",
                (pred.get("rest_home"), pred.get("rest_away"), g["game_id"]),
            )
            conn.execute("DELETE FROM cfl_predictions WHERE game_id=?", (g["game_id"],))
            pred_id = f"cp_{uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO cfl_predictions (
                  pred_id, game_id, created_at, model_name,
                  home_win_prob, away_win_prob, predicted_home_score, predicted_away_score,
                  model_spread, model_total, pick_ml, confidence, explanation
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    pred_id, g["game_id"], now, live_name,
                    pred["home_win_prob"], pred["away_win_prob"],
                    pred["predicted_home_score"], pred["predicted_away_score"],
                    pred["model_spread"], pred["model_total"],
                    pred["pick_ml"], pred["confidence"], pred["explanation"],
                ),
            )
            written += 1
            cards.append({**g, **pred, "pred_id": pred_id})
        conn.commit()
    return {
        **meta,
        "completed_for_elo": len(completed),
        "predictions": written,
        "cards": cards,
        "model_name": live_name,
    }


def _book_day_et(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except ValueError:
        return s[:10]


def _book_total_index() -> dict[tuple[str, str, str], dict[str, Any]]:
    """Match key (home, away, ET YYYY-MM-DD) → official row with any book line."""
    idx: dict[tuple[str, str, str], dict[str, Any]] = {}
    try:
        games = fetch_official_all_games(use_cache=True)
    except Exception:
        return idx
    for g in games:
        if (
            g.get("book_total") is None
            and g.get("book_spread") is None
            and g.get("book_home_moneyline") is None
        ):
            continue
        home = (g.get("home_team") or "").strip().lower()
        away = (g.get("away_team") or "").strip().lower()
        day = _book_day_et(g.get("game_date"))
        if home and away and day:
            idx[(home, away, day)] = g
    return idx


def attach_book_totals(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy live CFL.ca books onto cards. Does not fabricate missing lines."""
    if not cards:
        return cards
    idx = _book_total_index()
    if not idx:
        return cards
    for c in cards:
        key = (
            (c.get("home_team") or "").strip().lower(),
            (c.get("away_team") or "").strip().lower(),
            _book_day_et(c.get("game_date")),
        )
        g = idx.get(key)
        if not g:
            continue
        for field in (
            "book_total",
            "book_over_odds",
            "book_under_odds",
            "book_spread",
            "book_home_moneyline",
            "book_away_moneyline",
        ):
            if g.get(field) is not None and c.get(field) is None:
                c[field] = g.get(field)
    return cards


def list_pick_cards(*, days_back: int = 1, days_fwd: int = 21) -> list[dict[str, Any]]:
    if not DB_PATH.exists():
        ensure_predictions(refresh=True)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT g.*, p.home_win_prob, p.away_win_prob, p.predicted_home_score,
                   p.predicted_away_score, p.model_spread, p.model_total,
                   p.pick_ml, p.confidence, p.explanation, p.created_at AS pred_at,
                   p.model_name, p.pred_id
            FROM cfl_games g
            JOIN cfl_predictions p ON p.game_id = g.game_id
            ORDER BY g.game_date ASC
            """
        ).fetchall()
        cards = [dict(r) for r in rows]
    cards = [
        c for c in cards
        if (c.get("home_team") or "").strip().upper() not in ("", "TBD")
        and (c.get("away_team") or "").strip().upper() not in ("", "TBD")
    ]
    window = games_in_window(cards, days_back=days_back, days_fwd=days_fwd)
    # Prefer non-complete; if empty keep window
    liveish = [
        c for c in window
        if (c.get("status") or "").lower() not in {"complete", "final"}
    ]
    return attach_book_totals(liveish or window or cards[:12])


def list_graded_results(
    *,
    days: int | None = None,
    regular_season_only: bool = True,
) -> list[dict[str, Any]]:
    """Completed games with ML grade when a locked prediction exists.

    Default is the current CFL.ca regular season (Week N), not preseason.
    Preseason rows stay in cfl_games for Elo training; they must not pad or
    replace Season Performance. A rolling ``days`` window is ignored for
    regular-season results so Week 1 is never dropped as the season runs.
    """
    if not DB_PATH.exists():
        ensure_predictions(refresh=True)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT g.*,
                   p.home_win_prob, p.away_win_prob, p.predicted_home_score,
                   p.predicted_away_score, p.model_spread, p.model_total,
                   p.pick_ml, p.confidence, p.explanation, p.created_at AS pred_at,
                   p.model_name, p.pred_id
            FROM cfl_games g
            LEFT JOIN cfl_predictions p ON p.game_id = g.game_id
            WHERE lower(g.status) IN ('complete', 'final', 'closed')
              AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
            ORDER BY g.game_date DESC
            """
        ).fetchall()
    out: list[dict[str, Any]] = []
    et = __import__("zoneinfo").ZoneInfo("America/New_York")
    season_year = cfl_season_year()
    cutoff = None
    if days is not None and not regular_season_only:
        cutoff = datetime.now(et) - timedelta(days=days)
    for r in rows:
        d = dict(r)
        date_s = d.get("game_date")
        year = _event_year(date_s)
        if year is not None and year != season_year:
            continue
        if regular_season_only and not is_regular_season_round(d.get("round_name")):
            continue
        try:
            dt = datetime.fromisoformat(str(date_s).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if cutoff is not None and dt.astimezone(et) < cutoff:
                continue
        except ValueError:
            pass
        hs = int(d["home_score"])
        as_ = int(d["away_score"])
        pick = d.get("pick_ml")
        grade = None
        if pick and d.get("home_win_prob") is not None:
            if hs == as_:
                grade = "PUSH"
            else:
                winner = d["home_team"] if hs > as_ else d["away_team"]
                grade = "WIN" if pick == winner else "LOSS"
        else:
            # Honest finals — never invent 50% / −100 placeholders
            d["pick_ml"] = None
            d["home_win_prob"] = None
            d["away_win_prob"] = None
            d["explanation"] = "Final score only — no pre-game pick was locked."
            d["confidence"] = None
            grade = None
        d["grade"] = grade
        out.append(d)
    return out


def american_from_prob(p: float) -> int:
    p = min(max(float(p), 0.01), 0.99)
    if p >= 0.5:
        return int(round(-100 * p / (1.0 - p)))
    return int(round(100 * (1.0 - p) / p))
