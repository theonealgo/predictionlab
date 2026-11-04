"""Read-only feed for the PredictionLab video app (ClipForge).

GET /api/app/<sport>?date=YYYY-MM-DD   (date defaults to today, Eastern time)
Header X-PL-App-Key: <PL_APP_API_KEY>

Returns every game we published for that date: our pick and its Sharp Consensus
win %, each model's win % for the picked team, projected scores, and the final
score with whether the pick won once the game is over, plus the day's record.
Only values saved before the game are used. The key is the app's own access;
there is no visitor login here, and without PL_APP_API_KEY set the feed is off.
"""
from __future__ import annotations

import hmac
import json
import os
from contextlib import closing
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

bp = Blueprint("app_api", __name__)
ET = ZoneInfo("America/New_York")
_FINAL = {"final", "complete", "completed", "closed", "post"}
_MODELS = {"grinder2": "glicko2_prob", "takedown": "trueskill_prob", "edge": "elo_prob", "xsharp": "xgb_prob"}


def _pct(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return round(x * 100 if x <= 1.0 else x, 1)


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _score(home, away) -> dict | None:
    h, a = _num(home), _num(away)
    if h is None or a is None:
        return None
    return {"home": round(h, 1), "away": round(a, 1)}


def _authorized() -> bool:
    key = (os.environ.get("PL_APP_API_KEY") or "").strip()
    given = (request.headers.get("X-PL-App-Key") or "").strip()
    return bool(key) and hmac.compare_digest(key, given)


def _game(row) -> dict:
    try:
        card = json.loads(row["lock_card_json"] or "{}") or {}
    except (TypeError, ValueError):
        card = {}
    if not isinstance(card, dict):
        card = {}
    home_p = _pct(card.get("ensemble_prob")) or _pct(row["win_probability"])
    pick_home = None if home_p is None or home_p == 50.0 else home_p > 50.0
    models = {}
    for name, key in _MODELS.items():
        p = _pct(card.get(key))
        models[name] = None if p is None or pick_home is None else (p if pick_home else round(100 - p, 1))
    pl = _score(card.get("our_home_pts"), card.get("our_away_pts"))
    if pl is None:
        sp, tot = _num(row["lock_pl_spread"]), _num(row["lock_pl_total"])
        if sp is None:
            sp = _num(card.get("our_spread"))
        if tot is None:
            tot = _num(card.get("our_total"))
        if sp is not None and tot is not None:
            pl = _score((tot + sp) / 2, (tot - sp) / 2)
    final = None
    if str(row["status"] or "").lower() in _FINAL:
        final = _score(row["home_score"], row["away_score"])
    correct = None
    if final and pick_home is not None and final["home"] != final["away"]:
        correct = (final["home"] > final["away"]) == pick_home
    return {
        "gameId": row["game_id"],
        "date": str(row["game_date"])[:10],
        "homeTeam": row["home_team_id"],
        "awayTeam": row["away_team_id"],
        "pick": None if pick_home is None else (row["home_team_id"] if pick_home else row["away_team_id"]),
        "pickIsHome": pick_home,
        "winPercent": None if home_p is None else (home_p if pick_home is not False else round(100 - home_p, 1)),
        "models": models,
        "projected": {
            "predictionLab": pl,
            "xsharp": _score(card.get("xgb_home_score"), card.get("xgb_away_score")),
        },
        "final": final,
        "correct": correct,
    }


@bp.route("/api/app/<sport>")
def app_feed(sport: str):
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    raw = (request.args.get("date") or "").strip()
    try:
        day = date.fromisoformat(raw) if raw else datetime.now(ET).date()
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    from ladder_picks import sources

    sport_u = sport.strip().upper()
    with closing(sources._ro(sources.main_db_path())) as conn:
        locks = sources._lock_cols(conn, "p.")
        rows = conn.execute(
            f"""SELECT p.game_id, p.game_date, p.home_team_id, p.away_team_id, p.win_probability,
                       p.lock_card_json, {locks}, g.home_score, g.away_score, g.status
                FROM predictions p
                LEFT JOIN games g ON g.sport = p.sport AND g.game_id = p.game_id
                WHERE p.sport = ? AND date(p.game_date) = ?
                ORDER BY p.id DESC""",
            (sport_u, day.isoformat()),
        ).fetchall()
    games, seen = [], set()
    for row in rows:
        if row["game_id"] in seen:
            continue
        seen.add(row["game_id"])
        games.append(_game(row))
    games.reverse()
    wins = sum(1 for g in games if g["correct"] is True)
    losses = sum(1 for g in games if g["correct"] is False)
    resp = jsonify({"sport": sport_u, "date": day.isoformat(), "games": games,
                    "record": {"wins": wins, "losses": losses, "text": f"{wins}-{losses}"}})
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


def init_app(app) -> None:
    app.register_blueprint(bp)
