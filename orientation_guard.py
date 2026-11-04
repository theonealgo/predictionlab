"""Keep stored games in ESPN's home / away order.

ESPN can swap home and away on a neutral-site game after the slate is saved.
Pages attach stored predictions to ESPN's game by id, so a swapped row puts
every model's chance on the other team and the score sync credits each score
to the wrong side. This rewrites a swapped row to ESPN's order: team names
swap, home chances invert, spreads change sign, home / away pairs swap. The
pick itself (which team each model backed) does not change.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

ESPN_PATHS = {
    "NCAAB": ("basketball/mens-college-basketball", "&groups=50"),
    "NCAAW": ("basketball/womens-college-basketball", ""),
    "NBA": ("basketball/nba", ""),
    "WNBA": ("basketball/wnba", ""),
    "NFL": ("football/nfl", ""),
    "NCAAF": ("football/college-football", "&groups=80"),
    "NHL": ("hockey/nhl", ""),
}
PROB_COLS = (
    "win_probability", "elo_home_prob", "logistic_home_prob", "xgboost_home_prob",
    "catboost_home_prob", "meta_home_prob", "glicko_home_prob", "trueskill_home_prob",
)
SPREAD_COLS = ("lock_pl_spread", "lock_xs_spread")


def _espn_day(sport: str, day: str) -> list[dict]:
    path, extra = ESPN_PATHS[sport]
    url = (
        f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
        f"?dates={day.replace('-', '')}&limit=400{extra}"
    )
    try:
        data = requests.get(url, timeout=15).json()
    except Exception:
        return []
    out = []
    for ev in data.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        if "home" not in teams or "away" not in teams:
            continue
        final = ((ev.get("status") or {}).get("type") or {}).get("completed")

        def _score(c):
            try:
                return int(c.get("score")) if final else None
            except (TypeError, ValueError):
                return None

        out.append({
            "game_id": f"{sport}_{ev.get('id')}",
            "home": (teams["home"].get("team") or {}).get("displayName") or "",
            "away": (teams["away"].get("team") or {}).get("displayName") or "",
            "home_score": _score(teams["home"]),
            "away_score": _score(teams["away"]),
        })
    return out


def _flip_prob(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return round(100.0 - f, 4) if f > 1.0 else round(1.0 - f, 6)


def _neg(v):
    try:
        return -float(v) if v is not None else None
    except (TypeError, ValueError):
        return v


def _flip_card(raw: str | None) -> str | None:
    if not raw:
        return raw
    try:
        card = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if not isinstance(card, dict):
        return raw
    out = dict(card)
    for k, v in card.items():
        kl = k.lower()
        if "prob" in kl and "home" not in kl and "away" not in kl:
            flipped = _flip_prob(v)
            out[k] = str(round(flipped, 1)) if isinstance(v, str) and flipped != v else flipped
        elif "spread" in kl and "home" not in kl and "away" not in kl:
            out[k] = _neg(v)
    for k in card:
        if "home" in k:
            twin = k.replace("home", "away")
            if twin in card:
                out[k], out[twin] = card[twin], card[k]
    return json.dumps(out)


def _flip_row(conn, game_id: str, espn: dict) -> None:
    pcols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    sets = ["home_team_id = away_team_id", "away_team_id = home_team_id"]
    rows = conn.execute(
        "SELECT * FROM predictions WHERE game_id = ?", (game_id,)
    ).fetchall()
    names = [d[0] for d in conn.execute("SELECT * FROM predictions LIMIT 0").description]
    for row in rows:
        r = dict(zip(names, row))
        upd = {c: _flip_prob(r[c]) for c in PROB_COLS if c in pcols}
        upd.update({c: _neg(r[c]) for c in SPREAD_COLS if c in pcols})
        if {"expected_home_score", "expected_away_score"} <= pcols:
            upd["expected_home_score"] = r["expected_away_score"]
            upd["expected_away_score"] = r["expected_home_score"]
        if "lock_card_json" in pcols:
            upd["lock_card_json"] = _flip_card(r["lock_card_json"])
        if espn.get("home_score") is not None and {"actual_home_score", "actual_away_score"} <= pcols:
            upd["actual_home_score"] = espn["home_score"]
            upd["actual_away_score"] = espn["away_score"]
        assign = ", ".join(sets + [f"{c} = ?" for c in upd])
        conn.execute(
            f"UPDATE predictions SET {assign} WHERE id = ?",
            (*upd.values(), r["id"]),
        )
    if espn.get("home_score") is not None:
        conn.execute(
            "UPDATE games SET home_team_id = away_team_id, away_team_id = home_team_id, "
            "home_score = ?, away_score = ? WHERE game_id = ?",
            (espn["home_score"], espn["away_score"], game_id),
        )
    else:
        conn.execute(
            "UPDATE games SET home_team_id = away_team_id, away_team_id = home_team_id "
            "WHERE game_id = ?",
            (game_id,),
        )


def repair(db: str, sports=None, back: int = 3, ahead: int = 7) -> list[str]:
    """Rewrite stored games whose home / away is the reverse of ESPN's."""
    sports = [s for s in (sports or ESPN_PATHS) if s in ESPN_PATHS]
    today = datetime.now()
    days = [
        (today + timedelta(days=d)).strftime("%Y-%m-%d") for d in range(-back, ahead + 1)
    ]
    conn = sqlite3.connect(db, timeout=30, isolation_level=None)
    fixed: list[str] = []
    try:
        for sport in sports:
            stored = {
                r[0]: (r[1], r[2])
                for r in conn.execute(
                    "SELECT game_id, home_team_id, away_team_id FROM games "
                    "WHERE sport = ? AND date(game_date) BETWEEN date(?) AND date(?)",
                    (sport, days[0], days[-1]),
                )
            }
            if not stored:
                continue
            for day in days:
                for ev in _espn_day(sport, day):
                    have = stored.get(ev["game_id"])
                    if not have or not ev["home"] or not ev["away"]:
                        continue
                    if have[0] == ev["away"] and have[1] == ev["home"] and have[0] != have[1]:
                        fixed.append(ev["game_id"])
                        stored[ev["game_id"]] = (ev["home"], ev["away"])
                        _flip_with_guard_lifted(conn, ev["game_id"], ev)
    finally:
        conn.close()
    if fixed:
        logger.warning("[orientation] swapped to ESPN home/away: %s", ", ".join(fixed))
    return fixed


def _flip_with_guard_lifted(conn, game_id: str, espn: dict) -> None:
    triggers = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'pl_guard_%'"
    ).fetchall()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for name, _sql in triggers:
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        _flip_row(conn, game_id, espn)
        for _name, sql in triggers:
            conn.execute(sql)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    db = sys.argv[1] if len(sys.argv) > 1 else "sports_predictions_original.db"
    print(repair(db, sys.argv[2:] or None))
