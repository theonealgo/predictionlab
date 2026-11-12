"""Bounded, date-aware prop publication queue; public metadata reads never start work."""
from datetime import datetime, timedelta, timezone
import itertools
import json
import logging
from pathlib import Path
from queue import PriorityQueue, Empty
import threading
import sqlite3
import time
from zoneinfo import ZoneInfo

from odds_engine.model.prop_math import VERSION
from sports_props_hub import LEAGUES, preview
from prop_models import VERSION as MODEL_VERSION

ET = ZoneInfo("America/New_York")
TTL = 600
UNSUPPORTED = {
    "TENNIS": "Verified player-stat prop markets and settlement coverage are not connected for tennis.",
    "GOLF": "Verified round-specific player prop markets and settlement coverage are not connected for golf.",
    "UFC": "Verified fighter prop markets and settlement coverage are not connected for UFC.",
}
_CACHE, _STATE, _ACTIVE = {}, {}, {}
_QUEUE, _COUNTER, _LOCK = PriorityQueue(), itertools.count(), threading.RLock()
_THREAD = None
_DAILY = set()
logger = logging.getLogger(__name__)


def start_daily(site):
    identity = str(Path(site.DATABASE).resolve())
    with _LOCK:
        if identity in _DAILY:
            return
        _DAILY.add(identity)
    def daily():
        last_day = None
        time.sleep(5)
        while True:
            now = datetime.now(ET)
            day = now.date().isoformat()
            try:
                if now.hour >= 9 and day != last_day:
                    ensure(site, day)
                    last_day = day
            except Exception:
                logger.exception("Daily prop publication could not be queued")
            time.sleep(60)
    threading.Thread(target=daily, daemon=True, name="props-daily-publication").start()


def _key(site, league, day):
    return (str(Path(site.DATABASE).resolve()), league, day)


def _path(site, league, day):
    directory = Path(getattr(site, "_PROPS_DISK_DIR", Path(site.DATABASE).resolve().parent / ".cache/props"))
    return directory / day / f"{league}.json"


def entries(site, day):
    """Read available caches only. No workers, network requests or writes."""
    output = {}
    for league in LEAGUES:
        key = _key(site, league, day)
        with _LOCK:
            entry = _CACHE.get(key)
        if entry is None:
            try:
                entry = json.loads(_path(site, league, day).read_text())
            except (OSError, ValueError):
                entry = None
        if entry is None:
            engine = getattr(site, "_PROPS_ENGINE_MODULE", None)
            candidate = (getattr(engine, "_CACHE", {}) or {}).get(league)
            if not candidate:
                candidate = site._props_disk_load(league)
            if ((candidate or {}).get("payload") or {}).get("schema_version") == VERSION:
                if preview(league, candidate, limit=0, day=day)["total"] is not None:
                    entry = candidate
        if entry is None and league in UNSUPPORTED:
            entry = {"ts": time.time(), "payload": {"requested_date": day,
                     "availability": "unsupported", "availability_reason": UNSUPPORTED[league]}}
        output[league] = entry
    return output


def building(site, league, day):
    with _LOCK:
        return _STATE.get(_key(site, league, day), {}).get("status") in ("queued", "building")


def _enqueue(site, league, day, priority=10):
    if league in UNSUPPORTED:
        return
    key = _key(site, league, day)
    with _LOCK:
        state = _STATE.get(key) or {}
        if state.get("status") in ("queued", "building"):
            return
        if state.get("status") == "unavailable" and time.time()-state.get("ts", 0) < TTL:
            return
        _STATE[key] = {"status": "queued", "ts": time.time()}
        _QUEUE.put((priority, next(_COUNTER), site, league, day))


def ensure(site, day, selected=None):
    """Queue a day's leagues once; a single worker bounds expensive refreshes."""
    global _THREAD
    if day < datetime.now(ET).date().isoformat():
        return
    if day > (datetime.now(ET).date()+timedelta(days=7)).isoformat():
        return
    with _LOCK:
        _ACTIVE[(str(Path(site.DATABASE).resolve()), day)] = (site, time.time())
    cached = entries(site, day)
    order = ([selected] if selected else []) + [league for league in LEAGUES if league != selected]
    for league in order:
        entry = cached.get(league)
        stamp = float((entry or {}).get("ts") or 0)
        if not entry or time.time()-stamp >= TTL or ((entry.get("payload") or {}).get("model_registry_version") != MODEL_VERSION):
            _enqueue(site, league, day, 0 if league == selected else 10)
    with _LOCK:
        if _THREAD is None or not _THREAD.is_alive():
            _THREAD = threading.Thread(target=_loop, daemon=True, name="props-publication")
            _THREAD.start()
    from prop_forecast_store import ledger_path
    if ledger_path(site.DATABASE).exists():
        from prop_settlement import start_worker
        start_worker(site.DATABASE)


def build_one(site, league, day):
    """Publish a verified current/future slate, or an explicit availability state."""
    engine, _config = site._load_props_modules()
    schedule = engine.fetch_schedule_and_teams(league, datetime.strptime(day, "%Y-%m-%d").date())
    from prop_forecast_store import _aware
    upcoming = [game for game in schedule if _aware(game.get("start_time")) and
                _aware(game["start_time"]) > datetime.now(timezone.utc)]
    if upcoming:
        payload = engine._build_league_payload(league, schedule_override=upcoming, allow_live_lines=True)
        published_at = datetime.now(timezone.utc)
        payload["props"] = [row for row in payload.get("props") or []
                            if _aware(row.get("event_start")) and _aware(row["event_start"]) > published_at]
        available = bool(payload.get("props"))
        payload.update(availability="ready" if available else "no_verified_props",
                       availability_reason=None if available else
                       "Fixtures are scheduled, but eligible player histories or verified prop lines are unavailable.")
    elif schedule:
        payload = {"schema_version": VERSION, "schedule": schedule, "players": [], "props": [],
                   "availability": "slate_started",
                   "availability_reason": "The games for this date have started. View their saved selections and outcomes in Results."}
    else:
        payload = {"schema_version": VERSION, "schedule": [], "players": [], "props": [],
                   "availability": "no_fixtures", "availability_reason": "No fixtures are scheduled for this date."}
        from contextlib import closing
        try:
            with closing(sqlite3.connect(Path(site.DATABASE).resolve().as_uri()+"?mode=ro", uri=True, timeout=3)) as conn:
                next_day = conn.execute(
                    "SELECT MIN(substr(game_date,1,10)) FROM games WHERE sport=? AND substr(game_date,1,10)>? "
                    "AND substr(game_date,1,10)<=?",
                    (league, day, (datetime.strptime(day, "%Y-%m-%d")+timedelta(days=7)).date().isoformat())).fetchone()[0]
                payload["next_date"] = next_day
        except sqlite3.Error:
            pass
    payload["requested_date"] = day
    payload["model_registry_version"] = MODEL_VERSION
    entry = {"ts": time.time(), "payload": payload}
    from prop_forecast_store import capture
    from prop_models import persist_training
    from props_navigation import game_catalog, bind_games
    section = preview(league, entry, limit=None, day=day)
    bind_games(section, game_catalog(site.DATABASE, day), entry)
    persist_training(site.DATABASE, payload.get("model_validation") or {})
    # Publication captures the whole slate, not merely the three-row hub preview.
    capture(site.DATABASE, [section])
    target = _path(site, league, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(entry, allow_nan=False))
    temporary.replace(target)
    return entry


def _loop():
    while True:
        try:
            _priority, _sequence, site, league, day = _QUEUE.get(timeout=60)
        except Empty:
            with _LOCK:
                active = list(_ACTIVE.items())
            for (_database, day), (site, seen) in active:
                if time.time()-seen < 3*3600 and day >= datetime.now(ET).date().isoformat():
                    for league, entry in entries(site, day).items():
                        if not entry or time.time()-float(entry.get("ts") or 0) >= TTL:
                            _enqueue(site, league, day)
            continue
        key = _key(site, league, day)
        previous = entries(site, day).get(league)
        with _LOCK:
            _STATE[key] = {"status": "building", "ts": time.time()}
        try:
            entry = build_one(site, league, day)
        except Exception:
            logger.exception("Prop publication unavailable for %s %s", league, day)
            if previous and preview(league, previous, limit=0, day=day)["total"] is not None:
                entry = {**previous, "payload": {**previous["payload"],
                         "refresh_notice": "The latest refresh is unavailable. Showing the last verified props for this date."}}
            else:
                entry = {"ts": time.time(), "payload": {"requested_date": day,
                         "availability": "unavailable", "availability_reason":
                         "The current fixture or player-stat service could not be verified. Please retry later."}}
        with _LOCK:
            _CACHE[key] = entry
            _STATE[key] = {"status": "unavailable" if entry["payload"].get("refresh_notice") else entry["payload"].get("availability"),
                           "ts": time.time()}
        _QUEUE.task_done()
