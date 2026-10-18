"""Which leagues are in season, read from ESPN's league calendars.

ESPN's scoreboard lists each league's season calendar (game days) and the
current season type (pre / regular / post / off). That moves with the real
schedule, so a late Finals or an early opener needs no code change.

Pages never wait on ESPN: a background thread refreshes every few hours and
the answer is kept in `.cache/espn_season_status.json` across restarts. When
ESPN has no usable answer for a league, callers fall back to the fixed
calendar.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

LEAGUE_PATHS = {
    "NBA": "basketball/nba",
    "WNBA": "basketball/wnba",
    "NFL": "football/nfl",
    "NCAAF": "football/college-football",
    "MLB": "baseball/mlb",
    "NHL": "hockey/nhl",
    "NCAAB": "basketball/mens-college-basketball",
    "NCAAW": "basketball/womens-college-basketball",
    "CFL": "football/cfl",
}

REFRESH_EVERY_S = 3 * 3600
# A league whose ESPN calendar ended this long ago is stale (CFL still lists
# 2022), so the fixed calendar decides instead.
STALE_AFTER_DAYS = 200
_DISK = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "espn_season_status.json")

_lock = threading.Lock()
_state: dict = {"ts": 0.0, "leagues": {}, "running": False}


def _day(raw) -> str:
    return str(raw or "")[:10]


def parse_scoreboard(data: dict) -> dict | None:
    """Season start / end / type from one ESPN scoreboard payload."""
    leagues = (data or {}).get("leagues") or []
    if not leagues:
        return None
    league = leagues[0] or {}
    season = league.get("season") or {}
    stype = (season.get("type") or {}).get("type")
    try:
        stype = int(stype)
    except (TypeError, ValueError):
        stype = None
    days = []
    for item in league.get("calendar") or []:
        if isinstance(item, str):
            days.append(_day(item))
            continue
        if not isinstance(item, dict):
            continue
        if str(item.get("label") or "").strip().lower() == "off season":
            continue
        for entry in item.get("entries") or []:
            if entry.get("startDate"):
                days.append(_day(entry.get("startDate")))
            if entry.get("endDate"):
                days.append(_day(entry.get("endDate")))
    days = sorted(d for d in days if len(d) == 10)
    if not days:
        return None
    return {
        "start": days[0],
        "end": days[-1],
        "season_end": _day(season.get("endDate")),
        "type": stype,
    }


def league_in_season(info: dict | None, today: datetime) -> bool | None:
    """True / False from ESPN, or None when ESPN's answer is unusable."""
    if not info or not info.get("start") or not info.get("end"):
        return None
    t = today.strftime("%Y-%m-%d")
    try:
        end_dt = datetime.strptime(info["end"], "%Y-%m-%d")
    except ValueError:
        return None
    if (today - end_dt).days > STALE_AFTER_DAYS:
        return None
    if info.get("type") == 4:
        return False
    if info["start"] <= t <= info["end"]:
        return True
    # Playoff days are not always on the calendar yet; the season type is.
    if info.get("type") == 3 and info.get("season_end") and t <= info["season_end"]:
        return True
    return False


def league_window_end(sport: str, today: datetime) -> datetime | None:
    """Last day ESPN still has this league playing, when it is in season."""
    info = (_state.get("leagues") or {}).get(sport)
    if not league_in_season(info, today):
        return None
    end = info.get("end")
    if info.get("type") == 3 and info.get("season_end"):
        end = max(end, info["season_end"])
    try:
        return datetime.strptime(end, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _load_disk() -> None:
    try:
        with open(_DISK, encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict) and isinstance(saved.get("leagues"), dict):
            _state["leagues"] = saved["leagues"]
            _state["ts"] = float(saved.get("ts") or 0.0)
    except (OSError, ValueError):
        pass


def _fetch_all() -> None:
    import requests

    found = {}
    for sport, path in LEAGUE_PATHS.items():
        url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                continue
            info = parse_scoreboard(resp.json())
        except Exception as exc:
            logger.debug("[season] %s fetch failed: %s", sport, exc)
            continue
        if info:
            found[sport] = info
    if not found:
        return
    with _lock:
        merged = dict(_state.get("leagues") or {})
        merged.update(found)
        _state["leagues"] = merged
        _state["ts"] = time.time()
        try:
            os.makedirs(os.path.dirname(_DISK), exist_ok=True)
            tmp = _DISK + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"ts": _state["ts"], "leagues": merged}, fh)
            os.replace(tmp, _DISK)
        except OSError:
            pass


def _refresh_in_background() -> None:
    with _lock:
        if _state["running"] or time.time() - _state["ts"] < REFRESH_EVERY_S:
            return
        _state["running"] = True

    def _run():
        try:
            _fetch_all()
        finally:
            _state["running"] = False

    threading.Thread(target=_run, daemon=True, name="espn-season-status").start()


def espn_in_season(sport: str, today: datetime | None = None) -> bool | None:
    """ESPN's answer for one league, or None (caller uses the fixed calendar)."""
    if sport not in LEAGUE_PATHS:
        return None
    if not _state["leagues"]:
        _load_disk()
    _refresh_in_background()
    return league_in_season((_state.get("leagues") or {}).get(sport), today or datetime.now())


_load_disk()
