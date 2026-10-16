"""Read-only access to existing PredictionLab data.

Predictions and final scores come from the main predictions database (the same
rows the picks and results pages use). Sportsbook prices come from the existing
book-odds fetcher. Event start time and live status come from the public event
summary for the game's event id.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("ladder_picks")

_ROOT = Path(__file__).resolve().parent.parent

# Two-way moneyline sports with event ids. Soccer is left out: its moneyline is
# three-way (a draw loses a side bet), so a two-way win probability does not fit.
SUPPORTED_SPORTS = ("NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAB", "NCAAW", "WNBA")

_SUMMARY_PATHS = {
    "NFL": "football/nfl",
    "NCAAF": "football/college-football",
    "NBA": "basketball/nba",
    "WNBA": "basketball/wnba",
    "NCAAB": "basketball/mens-college-basketball",
    "NCAAW": "basketball/womens-college-basketball",
    "MLB": "baseball/mlb",
    "NHL": "hockey/nhl",
}

# Component model columns in the published card. Exactly 50.0 is a placeholder.
_COMPONENT_KEYS = ("elo_prob", "xgb_prob", "glicko2_prob", "trueskill_prob")


def main_db_path() -> str:
    explicit = (os.environ.get("LADDER_MAIN_DB_PATH") or os.environ.get("DATABASE_PATH") or "").strip()
    if explicit:
        return explicit
    if os.path.isfile("/data/sports_predictions_original.db"):
        return "/data/sports_predictions_original.db"
    return str(_ROOT / "sports_predictions_original.db")


def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _pct(raw) -> float | None:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val != val:
        return None
    if val > 1.0:
        val /= 100.0
    if not 0.0 <= val <= 1.0:
        return None
    return val


def event_id(game_id: str) -> str | None:
    tail = str(game_id or "").rsplit("_", 1)[-1]
    return tail if tail.isdigit() else None


class PredictionSource:
    """Published (locked) predictions and graded history from the main database."""

    def __init__(self, path: str | None = None):
        self.path = path or main_db_path()

    def candidates(self, pick_date: str) -> list[dict]:
        marks = ",".join("?" for _ in SUPPORTED_SPORTS)
        sql = f"""
            SELECT sport, league, game_id, game_date, home_team_id, away_team_id,
                   win_probability, lock_card_json, locked, created_at,
                   lock_pl_spread, lock_xs_spread, lock_pl_total, lock_xs_total
            FROM predictions
            WHERE sport IN ({marks}) AND date(game_date) = ?
              AND lock_card_json IS NOT NULL AND COALESCE(locked, 0) = 1
        """
        with closing(_ro(self.path)) as conn:
            rows = conn.execute(sql, (*SUPPORTED_SPORTS, pick_date)).fetchall()
        out = []
        for row in rows:
            try:
                card = json.loads(row["lock_card_json"]) or {}
            except (TypeError, ValueError):
                card = {}
            if not isinstance(card, dict):
                card = {}
            out.append(
                {
                    "sport": row["sport"],
                    "league": row["league"],
                    "game_id": row["game_id"],
                    "game_date": str(row["game_date"])[:10],
                    "home_team": row["home_team_id"],
                    "away_team": row["away_team_id"],
                    "home_prob": _pct(card.get("ensemble_prob")) or _pct(row["win_probability"]),
                    "components": [_pct(card.get(k)) for k in _COMPONENT_KEYS],
                    "model": _model_values(row, card),
                    "card": card,
                }
            )
        return out

    def graded_history(self, since: str, until: str) -> list[dict]:
        """Every past pick of every type, graded against the pre-game book line.

        The book line is the last one stored before 16:00 UTC on game day (before
        the ladder's pick window closes), so no in-game line is used. Pushes are
        left out. Sorted by game date.
        """
        from . import markets

        marks = ",".join("?" for _ in SUPPORTED_SPORTS)
        with closing(_ro(self.path)) as conn:
            books: dict[tuple[str, str], sqlite3.Row] = {}
            for b in conn.execute(
                f"""SELECT sport, game_id, spread, total, home_moneyline, away_moneyline
                    FROM betting_lines
                    WHERE sport IN ({marks}) AND date(game_date) >= ? AND date(game_date) < ?
                      AND fetched_at < date(game_date) || ' 16:00:00'
                    ORDER BY fetched_at""",
                (*SUPPORTED_SPORTS, since, until),
            ):
                books[(b["sport"], b["game_id"])] = b
            rows = conn.execute(
                f"""SELECT p.sport, p.game_id, p.game_date, p.win_probability, p.lock_card_json,
                           p.lock_pl_spread, p.lock_xs_spread, p.lock_pl_total, p.lock_xs_total,
                           g.home_score, g.away_score
                    FROM predictions p
                    JOIN games g ON g.sport = p.sport AND g.game_id = p.game_id
                    WHERE p.sport IN ({marks}) AND date(p.game_date) >= ? AND date(p.game_date) < ?
                      AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
                      AND lower(COALESCE(g.status, '')) IN ('final', 'complete', 'completed', 'closed', 'post')
                    ORDER BY p.game_date, p.id""",
                (*SUPPORTED_SPORTS, since, until),
            ).fetchall()
        out: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (row["sport"], row["game_id"])
            if key in seen or key not in books:
                continue
            seen.add(key)
            try:
                card = json.loads(row["lock_card_json"] or "{}") or {}
            except (TypeError, ValueError):
                card = {}
            if not isinstance(card, dict):
                card = {}
            b = books[key]
            book = {"home_ml": b["home_moneyline"], "away_ml": b["away_moneyline"],
                    "spread": b["spread"], "total": b["total"]}
            hs, aw = int(row["home_score"]), int(row["away_score"])
            for o in markets.options(row["sport"], _model_values(row, card), book):
                result = markets.grade(o["market"], o["side"], o["line"], hs, aw)
                if result == "push":
                    continue
                out.append({
                    "sport": row["sport"], "date": str(row["game_date"])[:10], "market": o["market"],
                    "model": o["model"], "side_class": o["side_class"], "gap": o["gap"],
                    "price": o["price"], "win": result == "win",
                })
        return out

    def final_score(self, sport: str, game_id: str) -> dict | None:
        with closing(_ro(self.path)) as conn:
            row = conn.execute(
                "SELECT status, home_score, away_score FROM games WHERE sport = ? AND game_id = ?",
                (sport, game_id),
            ).fetchone()
        if not row:
            return None
        status = str(row["status"] or "").lower()
        if row["home_score"] is None or row["away_score"] is None:
            return {"state": status or "scheduled"}
        if status not in {"final", "complete", "completed", "closed", "post"}:
            return {"state": status or "scheduled"}
        return {"state": "final", "home_score": int(row["home_score"]), "away_score": int(row["away_score"])}

def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _model_values(row, card: dict) -> dict:
    """Our published numbers for one game. Spreads are the projected home margin."""
    def first(*vals):
        for v in vals:
            x = _num(v)
            if x is not None:
                return x
        return None

    return {
        "home_prob": _pct(card.get("ensemble_prob")) or _pct(row["win_probability"]),
        "pl_spread": first(row["lock_pl_spread"]),
        "xs_spread": first(row["lock_xs_spread"], card.get("xgb_spread")),
        "pl_total": first(row["lock_pl_total"], card.get("our_total")),
        "xs_total": first(row["lock_xs_total"], card.get("xgb_total")),
    }


def book_odds(cand: dict) -> dict | None:
    """Posted sportsbook lines and prices for one game, or None.

    Keys: home_ml, away_ml, spread (home line), spread_price_home, spread_price_away,
    total, over_price, under_price. A moneyline estimated from the spread is dropped.
    """
    try:
        from pl_book_odds_api import _ml_from_spread_fallback, build_pl_book_odds
    except Exception as exc:
        logger.warning("ladder: book odds module unavailable: %s", exc)
        return None
    try:
        row = build_pl_book_odds(
            cand["sport"], cand["game_id"], cand["home_team"], cand["away_team"],
            cand.get("game_date"), league_name=cand.get("league"),
        )
    except Exception as exc:
        logger.info("ladder: book odds fetch failed for %s: %s", cand.get("game_id"), exc)
        return None
    if not row:
        return None
    home, away = row.get("home_moneyline"), row.get("away_moneyline")
    spread = row.get("spread")
    # A moneyline filled in from the spread is an estimate, not a posted price.
    if spread is not None:
        try:
            if (home, away) == tuple(_ml_from_spread_fallback(float(spread))):
                home = away = None
        except Exception:
            pass
    return {
        "home_ml": home, "away_ml": away,
        "spread": spread, "spread_price_home": row.get("spread_price_home"),
        "spread_price_away": row.get("spread_price_away"),
        "total": row.get("total"), "over_price": row.get("total_over_price"),
        "under_price": row.get("total_under_price"),
    }


def event_status(sport: str, game_id: str, timeout: float = 6.0) -> dict | None:
    """{'start': aware UTC datetime|None, 'state': pre|in|post|postponed|canceled, scores...}."""
    path = _SUMMARY_PATHS.get(sport)
    eid = event_id(game_id)
    if not path or not eid:
        return None
    try:
        import requests

        resp = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/{path}/summary",
            params={"event": eid},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception as exc:
        logger.info("ladder: event status failed for %s: %s", game_id, exc)
        return None
    comps = ((data.get("header") or {}).get("competitions") or [])
    if not comps:
        return None
    comp = comps[0]
    start = None
    raw = comp.get("date")
    if raw:
        try:
            start = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
        except ValueError:
            start = None
    stype = ((comp.get("status") or {}).get("type") or {})
    name = str(stype.get("name") or "").upper()
    state = str(stype.get("state") or "").lower()
    if "POSTPONED" in name or "SUSPENDED" in name:
        state = "postponed"
    elif "CANCEL" in name or "FORFEIT" in name:
        state = "canceled"
    out = {"start": start, "state": state or "pre"}
    if state == "post" and stype.get("completed"):
        scores = {}
        for team in comp.get("competitors") or []:
            try:
                scores[team.get("homeAway")] = int(float(team.get("score")))
            except (TypeError, ValueError):
                pass
        if "home" in scores and "away" in scores:
            out.update(state="final", home_score=scores["home"], away_score=scores["away"])
    return out


def window(today: date, days: int) -> tuple[str, str]:
    return (today - timedelta(days=days)).isoformat(), today.isoformat()
