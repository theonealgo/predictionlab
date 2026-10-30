"""Sportsbook price history for games that have not started.

Every time book odds are fetched for an upcoming game, the price is saved as a
new row when it differs from that game's last saved row. The first row is the
opening price; the last row captured before kickoff is the closing price.
Nothing is overwritten. Moneylines the odds module estimated from the spread
are saved with ml_estimated = 1 (not a posted book price).

sweep() fetches prices for games in the next two days so every game gets a
history even when nobody opens its page. Set PRICE_SNAPSHOTS=0 to turn both off.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
SWEEP_EVERY = 2 * 3600
SWEEP_DAYS = 2
SWEEP_MAX_GAMES = 250
SWEEP_PAUSE = 0.5
SWEEP_SPORTS = ("NFL", "NBA", "NHL", "MLB", "NCAAF", "NCAAB", "NCAAW", "WNBA", "SOCCER")
PRICE_FIELDS = (
    "home_ml", "away_ml", "ml_estimated", "spread", "spread_price_home", "spread_price_away",
    "total", "over_price", "under_price",
)

_db_path = None
_ready = False
_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("PRICE_SNAPSHOTS", "1") != "0"


def init(db_path: str) -> None:
    global _db_path
    _db_path = db_path


def _connect():
    conn = sqlite3.connect(_db_path, timeout=8)
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def _ensure(conn) -> None:
    global _ready
    if _ready:
        return
    conn.execute(
        "CREATE TABLE IF NOT EXISTS price_snapshots ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, sport TEXT, game_id TEXT, game_date TEXT, "
        "home_team TEXT, away_team TEXT, home_ml INTEGER, away_ml INTEGER, "
        "ml_estimated INTEGER, spread REAL, spread_price_home INTEGER, "
        "spread_price_away INTEGER, total REAL, over_price INTEGER, under_price INTEGER, "
        "provider TEXT, captured_at TEXT)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_snapshots_game "
        "ON price_snapshots (sport, game_id, captured_at)"
    )
    _ready = True


def _int(v):
    try:
        return None if v is None else int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _not_started(game: dict) -> bool:
    if game.get("home_score") is not None or game.get("actual_home_score") is not None:
        return False
    day = str(game.get("date") or game.get("game_date") or "")[:10]
    try:
        return datetime.strptime(day, "%Y-%m-%d").date() >= datetime.now(ET).date()
    except ValueError:
        return False


def record(sport: str, game: dict, row: dict) -> None:
    """Save `row` (a build_pl_book_odds result) for an upcoming `game`."""
    if not enabled() or not _db_path or not row or not game.get("game_id"):
        return
    if not _not_started(game):
        return
    price = {
        "home_ml": _int(row.get("home_moneyline")),
        "away_ml": _int(row.get("away_moneyline")),
        "ml_estimated": 1 if row.get("ml_from_spread") else 0,
        "spread": _float(row.get("spread")),
        "spread_price_home": _int(row.get("spread_price_home")),
        "spread_price_away": _int(row.get("spread_price_away")),
        "total": _float(row.get("total")),
        "over_price": _int(row.get("total_over_price")),
        "under_price": _int(row.get("total_under_price")),
    }
    if price["home_ml"] is None and price["spread"] is None and price["total"] is None:
        return
    gid = str(game["game_id"])
    try:
        with _lock:
            conn = _connect()
            try:
                _ensure(conn)
                last = conn.execute(
                    f"SELECT {', '.join(PRICE_FIELDS)} FROM price_snapshots "
                    "WHERE sport = ? AND game_id = ? ORDER BY id DESC LIMIT 1",
                    (sport, gid),
                ).fetchone()
                if last is not None and tuple(last) == tuple(price[f] for f in PRICE_FIELDS):
                    return
                conn.execute(
                    "INSERT INTO price_snapshots (sport, game_id, game_date, home_team, away_team, "
                    f"{', '.join(PRICE_FIELDS)}, provider, captured_at) "
                    f"VALUES (?, ?, ?, ?, ?, {', '.join('?' * len(PRICE_FIELDS))}, ?, ?)",
                    (
                        sport, gid,
                        str(game.get("date") or game.get("game_date") or "")[:10],
                        game.get("home") or game.get("home_team_id") or row.get("home_team"),
                        game.get("away") or game.get("away_team_id") or row.get("away_team"),
                        *(price[f] for f in PRICE_FIELDS),
                        row.get("provider"),
                        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:
        logger.debug("[price-snapshots] %s %s: %s", sport, gid, exc)


def _upcoming(conn, sport: str) -> list[dict]:
    today = datetime.now(ET).date()
    last = today + timedelta(days=SWEEP_DAYS)
    rows = conn.execute(
        "SELECT game_id, game_date, home_team_id, away_team_id, league, actual_home_score "
        "FROM predictions WHERE sport = ? AND game_date BETWEEN ? AND ? "
        "AND actual_home_score IS NULL",
        (sport, today.isoformat(), last.isoformat() + "~"),
    ).fetchall()
    seen, out = set(), []
    for gid, gd, h, a, league, score in rows:
        if not gid or gid in seen:
            continue
        seen.add(gid)
        out.append({"game_id": gid, "game_date": gd, "home_team_id": h,
                    "away_team_id": a, "league": league, "actual_home_score": score})
    return out


def sweep() -> int:
    """Fetch and save prices for upcoming games. Returns rows fetched."""
    if not enabled() or not _db_path:
        return 0
    from pl_book_odds_api import build_pl_book_odds

    conn = _connect()
    try:
        games = [(s, g) for s in SWEEP_SPORTS for g in _upcoming(conn, s)]
    finally:
        conn.close()
    fetched = 0
    for sport, g in games[:SWEEP_MAX_GAMES]:
        try:
            row = build_pl_book_odds(
                sport, g["game_id"], g["home_team_id"], g["away_team_id"],
                g["game_date"], league_name=g.get("league"),
            )
        except Exception as exc:
            logger.debug("[price-snapshots] fetch %s %s: %s", sport, g["game_id"], exc)
            row = None
        if row:
            record(sport, g, row)
            fetched += 1
        time.sleep(SWEEP_PAUSE)
    return fetched


def _loop() -> None:
    time.sleep(300)
    while True:
        try:
            n = sweep()
            logger.info("[price-snapshots] sweep saved prices for %s games", n)
        except Exception as exc:
            logger.debug("[price-snapshots] sweep failed: %s", exc)
        time.sleep(SWEEP_EVERY)


def start(db_path: str) -> None:
    init(db_path)
    if enabled():
        threading.Thread(target=_loop, daemon=True, name="price-snapshots").start()
