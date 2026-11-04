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
try:
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = timezone(timedelta(hours=-4))

# Every sport with published two-way picks. Soccer moneyline is three-way (a draw
# loses a side bet), so soccer is scanned for goal-line and total picks only (see
# markets.options). Golf has no head-to-head pick to place.
SUPPORTED_SPORTS = ("NFL", "NBA", "MLB", "NHL", "NCAAF", "NCAAB", "NCAAW", "WNBA",
                    "CFL", "SOCCER", "TENNIS", "UFC")

_SUMMARY_PATHS = {
    "NFL": "football/nfl",
    "NCAAF": "football/college-football",
    "NBA": "basketball/nba",
    "WNBA": "basketball/wnba",
    "NCAAB": "basketball/mens-college-basketball",
    "NCAAW": "basketball/womens-college-basketball",
    "MLB": "baseball/mlb",
    "NHL": "hockey/nhl",
    "CFL": "football/cfl",
    "UFC": "mma/ufc",
}


# Tennis and UFC finals and start times live in each sport's own database, keyed
# by the event number in our game id. "home" is the card's home player / fighter.
_OWN_DB = {
    "TENNIS": (_ROOT / "tennis" / "database" / "tennis.sqlite",
               "SELECT match_date AS start, status, winner, player_a AS a, player_b AS b "
               "FROM matches WHERE match_id = ?", "tennis-{}"),
    "UFC": (_ROOT / "ufc_pipeline" / "database" / "ufc_sandbox.db",
            "SELECT fight_date AS start, status, winner, home_fighter AS a, away_fighter AS b "
            "FROM ufc_fights WHERE fight_id = ?", "{}"),
}
# No pre-game book price is stored for these sports' past picks, so their record
# counts only picks we gave 60% or less, the -150 to +150 band.
UNPRICED_MAX_PROB = 0.60


def _own_db_event(sport: str, game_id: str) -> dict | None:
    spec = _OWN_DB.get(sport)
    eid = event_id(game_id)
    if not spec or not eid or not spec[0].is_file():
        return None
    try:
        with closing(_ro(str(spec[0]))) as conn:
            row = conn.execute(spec[1], (spec[2].format(eid),)).fetchone()
    except sqlite3.Error as exc:
        logger.info("ladder: %s event lookup failed for %s: %s", sport, game_id, exc)
        return None
    return dict(row) if row else None


def _ufc_model(fight_ids) -> dict[str, dict]:
    """{fight id: home_prob, home_ml, away_ml} from the last prediction made before the fight.

    The main database stores a 50% placeholder on UFC cards; the fight model's own
    numbers are in the UFC database.
    """
    ids = sorted({str(f) for f in fight_ids if f})
    path = _OWN_DB["UFC"][0]
    if not ids or not path.is_file():
        return {}
    marks = ",".join("?" for _ in ids)
    try:
        with closing(_ro(str(path))) as conn:
            rows = conn.execute(
                f"""SELECT p.fight_id, p.home_win_prob, p.home_ml, p.away_ml, p.books_count
                    FROM ufc_predictions p JOIN ufc_fights f ON f.fight_id = p.fight_id
                    WHERE p.fight_id IN ({marks}) AND p.created_at <= f.fight_date
                    ORDER BY p.created_at""",
                ids,
            ).fetchall()
    except sqlite3.Error as exc:
        logger.info("ladder: UFC model lookup failed: %s", exc)
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        priced = (r["books_count"] or 0) > 0
        out[str(r["fight_id"])] = {
            "home_prob": _pct(r["home_win_prob"]),
            "home_ml": r["home_ml"] if priced else None,
            "away_ml": r["away_ml"] if priced else None,
        }
    return out


def _own_db_rows(sport: str, since: str, until: str) -> list[dict]:
    """Tennis / UFC events dated since <= d < until (ET date) from the sport's own
    database, the one its picks and results pages read. home = player_a /
    home_fighter. pre_prob is the last prediction made before the start,
    last_prob the latest one."""
    path = _OWN_DB[sport][0]
    if not path.is_file():
        return []
    if sport == "TENNIS":
        sql = """SELECT m.match_id AS id, m.match_date AS start, m.status, m.winner,
                        m.player_a AS a, m.player_b AS b, m.tournament AS league,
                        (SELECT p.win_prob_a FROM predictions p WHERE p.match_id = m.match_id
                           AND p.created_at <= m.match_date ORDER BY p.created_at DESC LIMIT 1) AS pre_prob,
                        (SELECT p.win_prob_a FROM predictions p WHERE p.match_id = m.match_id
                           ORDER BY p.created_at DESC LIMIT 1) AS last_prob
                 FROM matches m
                 WHERE date(substr(m.match_date, 1, 10)) >= ? AND date(substr(m.match_date, 1, 10)) < ?"""
    else:
        sql = """SELECT f.fight_id AS id, f.fight_date AS start, f.status, f.winner,
                        f.home_fighter AS a, f.away_fighter AS b, '' AS league,
                        (SELECT p.home_win_prob FROM ufc_predictions p WHERE p.fight_id = f.fight_id
                           AND p.created_at <= f.fight_date ORDER BY p.created_at DESC LIMIT 1) AS pre_prob,
                        (SELECT p.home_win_prob FROM ufc_predictions p WHERE p.fight_id = f.fight_id
                           ORDER BY p.created_at DESC LIMIT 1) AS last_prob
                 FROM ufc_fights f
                 WHERE date(substr(f.fight_date, 1, 10)) >= date(?, '-1 day')
                   AND date(substr(f.fight_date, 1, 10)) < date(?, '+1 day')"""
    try:
        with closing(_ro(str(path))) as conn:
            rows = [dict(r) for r in conn.execute(sql, (since, until))]
    except sqlite3.Error as exc:
        logger.info("ladder: %s events lookup failed: %s", sport, exc)
        return []
    out = []
    for r in rows:
        start = _own_db_start(r["start"])
        if sport == "UFC":
            if start is None:
                continue
            r["date"] = start.astimezone(_ET).date().isoformat()
            if not since <= r["date"] < until:
                continue
        else:
            r["date"] = str(r["start"])[:10]
        num = str(r["id"]).rsplit("-", 1)[-1]
        r.update(game_id=f"{sport}_{num}", home_team_id=r["a"], away_team_id=r["b"])
        out.append(r)
    return out


def _game_keys(row, o: dict, home_score: int, away_score: int) -> dict:
    """Game details carried on a graded pick (for display; the record ignores them)."""
    from . import markets

    return {
        "game_id": row["game_id"], "home_team": row["home_team_id"], "away_team": row["away_team_id"],
        "side": o["side"], "line": o["line"],
        "label": markets.label(o, row["home_team_id"], row["away_team_id"]),
        "home_score": home_score, "away_score": away_score,
    }


def _own_db_start(raw) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _winner_score(row: dict, home_team: str | None) -> tuple[int, int] | None:
    """(home, away) as 1-0 / 0-1 from the winner's name."""
    win = str(row.get("winner") or "").strip().lower()
    home = str(home_team or "").strip().lower()
    names = {str(row.get("a") or "").strip().lower(), str(row.get("b") or "").strip().lower()}
    if not win or win not in names or home not in names:
        return None
    return (1, 0) if win == home else (0, 1)


def _summary_path(sport: str, game_id: str) -> str | None:
    if sport == "SOCCER":
        parts = str(game_id or "").split("_")
        return f"soccer/{parts[1]}" if len(parts) >= 3 and parts[1] else None
    return _SUMMARY_PATHS.get(sport)

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
        with closing(_ro(self.path)) as conn:
            locks = _lock_cols(conn, "")
            sql = f"""
                SELECT sport, league, game_id, game_date, home_team_id, away_team_id,
                       win_probability, lock_card_json, locked, created_at, {locks}
                FROM predictions
                WHERE sport IN ({marks}) AND date(game_date) = ?
                  AND lock_card_json IS NOT NULL AND COALESCE(locked, 0) = 1
            """
            rows = conn.execute(sql, (*SUPPORTED_SPORTS, pick_date)).fetchall()
        out = []
        for row in rows:
            if row["sport"] in _OWN_DB:
                continue
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
        nxt = (date.fromisoformat(pick_date) + timedelta(days=1)).isoformat()
        for sport in _OWN_DB:
            if sport not in SUPPORTED_SPORTS:
                continue
            for r in _own_db_rows(sport, pick_date, nxt):
                hp = _pct(r["last_prob"])
                out.append({
                    "sport": sport, "league": r["league"] or "", "game_id": r["game_id"],
                    "game_date": r["date"], "home_team": r["a"], "away_team": r["b"],
                    "home_prob": hp, "components": [],
                    "model": {"home_prob": hp, "pl_spread": None, "xs_spread": None,
                              "pl_total": None, "xs_total": None},
                    "card": {},
                })
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
            books: dict = {}
            bl = _table_cols(conn, "betting_lines")
            ml = ("home_moneyline, away_moneyline" if {"home_moneyline", "away_moneyline"} <= bl
                  else "NULL AS home_moneyline, NULL AS away_moneyline")
            if {"sport", "game_date", "fetched_at"} <= bl:
                for b in conn.execute(
                    f"""SELECT sport, game_id, spread, total, {ml}
                        FROM betting_lines
                        WHERE sport IN ({marks}) AND date(game_date) >= ? AND date(game_date) < ?
                          AND fetched_at < date(game_date) || ' 16:00:00'
                        ORDER BY fetched_at""",
                    (*SUPPORTED_SPORTS, since, until),
                ):
                    books[(b["sport"], b["game_id"])] = b
            elif "game_id" in bl:
                # Short table: one row per game, the first line stored is kept.
                for b in conn.execute(f"SELECT game_id, spread, total, {ml} FROM betting_lines ORDER BY id"):
                    books.setdefault(b["game_id"], b)
            # betting_lines keeps one row per game, rewritten on every fetch, so most
            # rows are stamped after the game and dropped above. price_snapshots only
            # saves rows before the start: its last row is the closing pre-game line.
            if {"captured_at", "spread_price_home", "over_price"} <= _table_cols(conn, "price_snapshots"):
                for s in conn.execute(
                    f"""SELECT sport, game_id, spread, total, spread_price_home, spread_price_away,
                               over_price, under_price, ml_estimated,
                               home_ml AS home_moneyline, away_ml AS away_moneyline
                        FROM price_snapshots
                        WHERE sport IN ({marks}) AND date(game_date) >= ? AND date(game_date) < ?
                        ORDER BY captured_at""",
                    (*SUPPORTED_SPORTS, since, until),
                ):
                    books[(s["sport"], s["game_id"])] = s
            locks = _lock_cols(conn, "p.")
            rows = conn.execute(
                f"""SELECT p.sport, p.game_id, p.game_date, p.win_probability, p.lock_card_json,
                           p.home_team_id, p.away_team_id, {locks}, g.home_score, g.away_score
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
            b = books.get(key) or books.get(row["game_id"])
            if key in seen or b is None:
                continue
            seen.add(key)
            try:
                card = json.loads(row["lock_card_json"] or "{}") or {}
            except (TypeError, ValueError):
                card = {}
            if not isinstance(card, dict):
                card = {}
            keys = b.keys()
            estimated = "ml_estimated" in keys and b["ml_estimated"]
            book = {"home_ml": None if estimated else b["home_moneyline"],
                    "away_ml": None if estimated else b["away_moneyline"],
                    "spread": b["spread"], "total": b["total"]}
            for k in ("spread_price_home", "spread_price_away", "over_price", "under_price"):
                if k in keys:
                    book[k] = b[k]
            hs, aw = int(row["home_score"]), int(row["away_score"])
            for o in markets.options(row["sport"], _model_values(row, card), book):
                result = markets.grade(o["market"], o["side"], o["line"], hs, aw)
                if result == "push":
                    continue
                out.append({
                    "sport": row["sport"], "date": str(row["game_date"])[:10], "market": o["market"],
                    "model": o["model"], "side_class": o["side_class"], "gap": o["gap"],
                    "price": o["price"], "win": result == "win",
                    **_game_keys(row, o, hs, aw),
                })
        out.extend(self._own_db_history(since, until))
        out.sort(key=lambda r: r["date"])
        return out

    def _own_db_history(self, since: str, until: str) -> list[dict]:
        """Tennis / UFC moneyline picks graded from each sport's own finals."""
        out: list[dict] = []
        for sport in _OWN_DB:
            if sport not in SUPPORTED_SPORTS:
                continue
            rows = [r for r in _own_db_rows(sport, since, until)
                    if r["winner"] and str(r["status"] or "").lower() in ("final", "complete")]
            ufc = _ufc_model(event_id(r["game_id"]) for r in rows) if sport == "UFC" else {}
            for row in rows:
                hp = _pct(row["pre_prob"])
                score = _winner_score(row, row["a"])
                if hp is None or not score:
                    continue
                out.extend(self._own_db_graded(sport, row, hp, score, ufc.get(event_id(row["game_id"]) or "")))
        return out

    def _own_db_graded(self, sport: str, row: dict, hp: float, score, fm: dict | None) -> list[dict]:
        from . import markets

        out: list[dict] = []
        model = {"home_prob": hp}
        book = {"home_ml": fm["home_ml"], "away_ml": fm["away_ml"]} if fm else {}
        for o in markets.options(sport, model, book):
            if o["market"] != "moneyline":
                continue
            unpriced = o["price"] is None
            if unpriced and o["gap"] > UNPRICED_MAX_PROB + 1e-9:
                continue
            result = markets.grade("moneyline", o["side"], None, *score)
            out.append({
                "sport": sport, "date": row["date"], "market": "moneyline",
                "model": o["model"], "side_class": o["side_class"], "gap": o["gap"],
                "price": o["price"], "unpriced": unpriced, "win": result == "win",
                **_game_keys(row, o, *score),
            })
        return out

    def final_score(self, sport: str, game_id: str) -> dict | None:
        if sport in _OWN_DB:
            fin = _own_db_event(sport, game_id)
            if not fin:
                return None
            if str(fin.get("status") or "").lower() not in ("final", "complete") or not fin.get("winner"):
                return {"state": "scheduled"}
            score = _winner_score(fin, fin.get("a"))
            if not score:
                return None
            return {"state": "final", "home_score": score[0], "away_score": score[1]}
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

_LOCK_COLS = ("lock_pl_spread", "lock_xs_spread", "lock_pl_total", "lock_xs_total")


def _table_cols(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")}


def _lock_cols(conn, prefix: str) -> str:
    """The lock_* columns, as NULL when this database does not have them."""
    have = _table_cols(conn, "predictions")
    return ", ".join(f"{prefix}{c}" if c in have else f"NULL AS {c}" for c in _LOCK_COLS)


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
    if sport in _OWN_DB:
        row = _own_db_event(sport, game_id)
        if not row:
            return None
        status = str(row.get("status") or "").lower()
        state = {"scheduled": "pre", "upcoming": "pre", "live": "in", "final": "post",
                 "complete": "post"}.get(status, status or "pre")
        if "postpon" in status or "cancel" in status:
            state = "postponed" if "postpon" in status else "canceled"
        return {"start": _own_db_start(row.get("start")), "state": state}
    path = _summary_path(sport, game_id)
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
