"""Official final-stat settlement for the separate prospective PL forecast ledger."""
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen

from prop_forecast_store import ledger_path, _aware

PATHS = {"NBA": "basketball/nba", "WNBA": "basketball/wnba",
         "NCAAB": "basketball/mens-college-basketball", "NCAAW": "basketball/womens-college-basketball",
         "NFL": "football/nfl", "NCAAF": "football/college-football", "CFL": "football/cfl",
         "MLB": "baseball/mlb", "NHL": "hockey/nhl"}
_WORKERS, _LOCK = set(), threading.Lock()
logger = logging.getLogger(__name__)
PARSER_VERSION = "official-player-stats-v3"


def _actuals(payload, league):
    stats = {}
    labels_by_prop = {
        "points": ("PTS",), "rebounds": ("REB",), "assists": ("AST",), "threes": ("3PT",),
        "hits": ("H",), "runs": ("R",), "rbis": ("RBI",), "home_runs": ("HR",),
        "walks": ("BB",), "strikeouts": ("K", "SO"), "shots_on_goal": ("SOG", "S"), "goals": ("G",),
    }
    if league == "NHL":
        labels_by_prop.update(points=("PTS", "P"), assists=("A",))
    for team in (payload.get("boxscore") or {}).get("players") or []:
        for group in team.get("statistics") or []:
            labels = group.get("labels") or []
            category = str(group.get("name") or group.get("type") or group.get("displayName") or "").lower()
            mapping = labels_by_prop
            if league in ("NFL", "NCAAF", "CFL"):
                mapping = {"passing": {"passing_yards": ("YDS",)},
                           "rushing": {"rushing_yards": ("YDS",)},
                           "receiving": {"receiving_yards": ("YDS",), "receptions": ("REC",)}}.get(category, {})
            elif league == "MLB":
                mapping = {"strikeouts": ("K", "SO")} if category == "pitching" else {
                    key: value for key, value in labels_by_prop.items()
                    if key in ("hits", "runs", "rbis", "home_runs", "walks")} if category == "batting" else {}
                if category == "batting":
                    mapping = {**mapping, "batter_strikeouts": ("K", "SO")}
            for athlete in group.get("athletes") or []:
                identity = str((athlete.get("athlete") or {}).get("id") or "")
                if not identity:
                    continue
                target = stats.setdefault(identity, {"did_not_play": False, "stats": {}})
                if athlete.get("didNotPlay") is True:
                    target["did_not_play"] = True
                    continue
                for prop, candidates in mapping.items():
                    for candidate in candidates:
                        if candidate not in labels:
                            continue
                        index = labels.index(candidate)
                        values = athlete.get("stats") or []
                        if index >= len(values) or values[index] in (None, "", "--") or isinstance(values[index], bool):
                            continue
                        try:
                            if prop == "threes":
                                from odds_engine.model.gamelog import makes
                                value = makes(values[index])
                            else:
                                value = float(values[index])
                            signed = prop in ("passing_yards", "rushing_yards", "receiving_yards")
                            if value is not None and math.isfinite(value) and (signed or value >= 0):
                                target["stats"][prop] = value
                        except (TypeError, ValueError):
                            continue
    if league == "NHL":
        for player in stats.values():
            values = player["stats"]
            if "points" not in values and "goals" in values and "assists" in values:
                values["points"] = values["goals"]+values["assists"]
    return stats


def settle_event(original_db, league, event_id, payload):
    header = payload.get("header") or {}
    if str(header.get("id") or "") != str(event_id):
        raise ValueError("Official event identity does not match the saved event")
    competitions = header.get("competitions") or []
    status = ((competitions[0].get("status") or {}).get("type") or {}) if competitions else {}
    if not status.get("completed") or "FINAL" not in str(status.get("name") or "").upper():
        return 0
    actuals = _actuals(payload, league)
    source_hash = hashlib.sha256((PARSER_VERSION+json.dumps(payload, sort_keys=True)).encode()).hexdigest()
    path = ledger_path(original_db)
    if not path.exists():
        return 0
    outcomes = []
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        for identity, raw in conn.execute("SELECT id,payload FROM forecasts WHERE league=?", (league,)):
            forecast = json.loads(raw)
            if str(forecast["event_id"]) != str(event_id):
                continue
            player = actuals.get(str(forecast["player_id"])) or {}
            actual = (player.get("stats") or {}).get(forecast["prop_type"])
            outcome = {"actual": actual, "result": "N/A", "reason": None,
                       "parser_version": PARSER_VERSION,
                       "settled_at": datetime.now(timezone.utc).isoformat()}
            if player.get("did_not_play"):
                if forecast.get("line_type") == "pl":
                    outcome.update(result="Void", reason="Player did not participate.")
                else:
                    outcome["reason"] = "No participation; the market's void rule is not verified."
            elif actual is None:
                outcome["reason"] = "The official final box score does not provide the required player stat."
            elif actual == forecast["line"]:
                outcome["result"] = "Push"
            else:
                won = actual > forecast["line"] if forecast["pick"] == "OVER" else actual < forecast["line"]
                outcome["result"] = "Win" if won else "Loss"
            outcomes.append((identity, json.dumps(outcome, allow_nan=False), source_hash))
        conn.executemany("INSERT OR IGNORE INTO settlements(forecast_id,payload,source_hash) VALUES(?,?,?)", outcomes)
        conn.commit()
    return len(outcomes)


def settle_pending(original_db, get=None):
    path = ledger_path(original_db)
    if not path.exists():
        return 0
    now = datetime.now(timezone.utc)
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=3)) as conn:
        candidates = conn.execute(
            """SELECT f.payload,s.payload FROM forecasts f LEFT JOIN settlements s
                 ON s.id=(SELECT MAX(s2.id) FROM settlements s2 WHERE s2.forecast_id=f.id)
               WHERE f.pick_date>=?""", ((now-timedelta(days=3)).date().isoformat(),)).fetchall()
    pending = []
    for raw, outcome in candidates:
        forecast = json.loads(raw)
        status = json.loads(outcome).get("result") if outcome else None
        if status is None or (status in ("Pending", "N/A") and
                              now-_aware(forecast["event_start"]) < timedelta(days=2)):
            pending.append(forecast)
    events = {(row["league"], str(row["event_id"])) for row in pending
              if row["league"] in PATHS and _aware(row["event_start"]) < datetime.now(timezone.utc)}
    def fetch(url):
        with urlopen(url, timeout=12) as response:
            return json.load(response)
    get = get or fetch
    settled = 0
    for league, identity in sorted(events)[:20]:
        if not identity.isdigit():
            continue
        payload = get(f"https://site.api.espn.com/apis/site/v2/sports/{PATHS[league]}/summary?event={identity}")
        settled += settle_event(original_db, league, identity, payload)
        time.sleep(1)
    return settled


def start_worker(original_db):
    identity = str(ledger_path(original_db))
    with _LOCK:
        if identity in _WORKERS:
            return
        _WORKERS.add(identity)
    def loop():
        delay = 300
        while True:
            try:
                settle_pending(original_db)
                delay = 300
            except HTTPError as error:
                delay = 3600 if error.code in (403, 429) else 300
                logger.warning("Prop final-stat source unavailable; forecasts remain pending.")
            except Exception:
                logger.exception("Prop settlement unavailable; original ledger unchanged")
            time.sleep(delay)
    threading.Thread(target=loop, daemon=True, name="prop-forecast-settlement").start()
