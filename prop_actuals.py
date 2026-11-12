"""Official actual recovery for saved selections; original application DB remains read-only."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from prop_forecast_store import _aware
from prop_settlement import PATHS, PARSER_VERSION, _actuals

ET = ZoneInfo("America/New_York")
SPORT_PATHS = {**PATHS, "CFL": "football/cfl", "SOCCER": "soccer/eng.1"}


def _name(value):
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    normalized = re.sub("[^a-z0-9]", "", normalized)
    return {"laclippers": "losangelesclippers"}.get(normalized, normalized)


def actuals_path(original_db):
    return Path(original_db).resolve().with_name("prop_actuals.sqlite")


def pick_fingerprint(row):
    fields = [row.get(key) for key in ("league", "pick_date", "player_name", "team",
                                      "prop_type", "pick", "line", "created_at")]
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _saved(original_db, day, league=None, end_day=None):
    with closing(sqlite3.connect(Path(original_db).resolve().as_uri()+"?mode=ro", uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_prop_picks'").fetchone():
            return []
        sql = "SELECT * FROM player_prop_picks WHERE pick_date BETWEEN ? AND ?"
        params = [day, end_day or day]
        if league:
            sql += " AND league=?"
            params.append(league)
        return [dict(row) for row in conn.execute(sql, params)]


def _final(competition):
    status = ((competition.get("status") or {}).get("type") or {})
    return status.get("completed") is True and "FINAL" in str(status.get("name") or "").upper()


def _event_day(event):
    stamp = _aware(event.get("date"))
    return stamp.astimezone(ET).date().isoformat() if stamp else None


def _event_teams(event):
    comp = (event.get("competitions") or [{}])[0]
    return {_name((team.get("team") or {}).get("displayName")) for team in comp.get("competitors") or []}


def _extract(payload, league):
    parsed = _actuals(payload, "NFL" if league == "CFL" else league)
    if league == "SOCCER":
        aliases = {"SH": "shots", "SOG": "shots_on_target", "G": "goals", "A": "assists"}
        for team in (payload.get("boxscore") or {}).get("players") or []:
            for group in team.get("statistics") or []:
                for row in group.get("athletes") or []:
                    identity = str((row.get("athlete") or {}).get("id") or "")
                    target = parsed.get(identity)
                    if not target:
                        continue
                    for label, raw in zip(group.get("labels") or [], row.get("stats") or []):
                        if label in aliases and raw not in (None, "", "--") and not isinstance(raw, bool):
                            try:
                                value = float(raw)
                                if value >= 0 and value.is_integer():
                                    target["stats"][aliases[label]] = value
                            except (ValueError, TypeError):
                                pass
    return parsed


def _athletes(payload):
    output = {}
    for team in (payload.get("boxscore") or {}).get("players") or []:
        team_name = _name((team.get("team") or {}).get("displayName"))
        for group in team.get("statistics") or []:
            for entry in group.get("athletes") or []:
                athlete = entry.get("athlete") or {}
                identity, name = str(athlete.get("id") or ""), _name(athlete.get("displayName"))
                if identity.isdigit() and name and team_name:
                    output[(team_name, name, identity)] = True
    return output


def recover(original_db, league, day, get=None, max_events=100):
    if datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") != day:
        raise ValueError("Invalid date")
    picks = _saved(original_db, day, league)
    if not picks:
        return {"saved": 0, "actuals": 0, "unavailable": 0}
    if league not in SPORT_PATHS:
        return {"saved": len(picks), "actuals": 0, "unavailable": len(picks),
                "reason": "Official player-stat settlement is not connected for this sport."}
    def fetch(url):
        import requests
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()
    get = get or fetch
    base = f"https://site.api.espn.com/apis/site/v2/sports/{SPORT_PATHS[league]}/"
    params = {"dates": day.replace("-", ""), "limit": 1000}
    if league in ("NCAAB", "NCAAW", "NCAAF"):
        params["groups"] = 50 if league in ("NCAAB", "NCAAW") else 80
    scoreboard = get(base+"scoreboard?"+urlencode(params))
    needed = {_name(row["team"]) for row in picks}
    events = {str(event["id"]): event for event in scoreboard.get("events") or []
              if str(event.get("id") or "").isdigit() and _event_day(event) == day
              and _final(event) and needed.intersection(_event_teams(event))}
    if len(events) > max_events:
        raise ValueError("Event recovery bound exceeded; narrow this batch")
    candidates = {}
    evidence = []
    for event_id, event in events.items():
        url = base+"summary?"+urlencode({"event": event_id})
        payload = get(url)
        header = payload.get("header") or {}
        competitions = header.get("competitions") or []
        if str(header.get("id") or "") != event_id or not competitions or not _final(competitions[0]):
            continue
        if _event_day(competitions[0]) != day:
            continue
        stats = _extract(payload, league)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        evidence.append(digest)
        for team, name, athlete_id in _athletes(payload):
            candidates.setdefault((team, name), []).append({
                "event_id": event_id, "event_start": competitions[0].get("date"),
                "player_id": athlete_id, "stats": stats.get(athlete_id) or {},
                "source_hash": digest, "source_url": url,
            })
    inserted, actual_count = [], 0
    for row in picks:
        matches = candidates.get((_name(row["team"]), _name(row["player_name"])), [])
        record = {"actual": None, "selection_result": "N/A", "event_id": None, "player_id": None,
                  "pregame_verified": False, "reason": None, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                  "parser_version": PARSER_VERSION,
                  "pick_fingerprint": pick_fingerprint(row), "actual_verified": False}
        if len(matches) != 1:
            record["reason"] = ("More than one exact final event/player match exists; the original event was not saved."
                                if matches else "No exact player/team match was found in a verified final box score for this date.")
            source_hash = hashlib.sha256(json.dumps(sorted(evidence)).encode()).hexdigest()
        else:
            match = matches[0]
            record.update({key: value for key, value in match.items() if key != "stats"})
            source_hash = match["source_hash"]
            start, created = _aware(match["event_start"]), _aware(row.get("created_at"))
            record["pregame_verified"] = bool(start and created and created < start)
            value = (match["stats"].get("stats") or {}).get(row["prop_type"])
            batting_comparison = (league == "MLB" and row["prop_type"] == "strikeouts" and value is None
                                  and "batter_strikeouts" in (match["stats"].get("stats") or {}))
            if batting_comparison:
                value = match["stats"]["stats"]["batter_strikeouts"]
            if match["stats"].get("did_not_play"):
                record.update(actual_verified=True, reason="Player did not participate; the market's void rule is not verified.")
                if row.get("line_source") == "pl_model":
                    record.update(selection_result="Void", reason="Player did not participate.")
            elif value is None:
                record["reason"] = "The official final box score does not provide the required player stat."
            else:
                value, line = float(value), float(row["line"])
                record.update(actual=value, actual_verified=True)
                if value == line:
                    record["selection_result"] = "Push"
                elif str(row["pick"]).upper() in ("OVER", "UNDER"):
                    won = value > line if str(row["pick"]).upper() == "OVER" else value < line
                    record["selection_result"] = "Win" if won else "Loss"
                else:
                    record["reason"] = "The original selection side is missing."
                if batting_comparison:
                    record.update(selection_result="N/A", actual_label="Batter strikeouts",
                                  reason="Official batting strikeouts are shown. The original strikeout market was not identified, so its market result is unverified.")
                actual_count += 1
        identity_hash = hashlib.sha256((PARSER_VERSION+source_hash).encode()).hexdigest()
        inserted.append((row["id"], league, day, record["pick_fingerprint"], int(record["actual_verified"]),
                         identity_hash, json.dumps(record, allow_nan=False)))
    path = actuals_path(original_db)
    with closing(sqlite3.connect(path, timeout=5)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS recovered_actuals(
            id INTEGER PRIMARY KEY AUTOINCREMENT, pick_id INTEGER NOT NULL, league TEXT NOT NULL,
            pick_date TEXT NOT NULL, fingerprint TEXT NOT NULL, verified INTEGER NOT NULL,
            source_hash TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(pick_id,fingerprint,source_hash))""")
        conn.execute("CREATE INDEX IF NOT EXISTS recovered_day ON recovered_actuals(pick_date,league)")
        conn.executemany("""INSERT OR IGNORE INTO recovered_actuals
            (pick_id,league,pick_date,fingerprint,verified,source_hash,payload) VALUES(?,?,?,?,?,?,?)""", inserted)
        conn.commit()
    return {"saved": len(picks), "actuals": actual_count, "unavailable": len(picks)-actual_count}


def read_actuals(original_db, day, league=None, end_day=None):
    path = actuals_path(original_db)
    if not path.exists():
        return {}
    originals = {row["id"]: pick_fingerprint(row) for row in _saved(original_db, day, league, end_day)}
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=5)) as conn:
        sql = """SELECT pick_id,fingerprint,payload FROM recovered_actuals
                 WHERE pick_date BETWEEN ? AND ?"""
        params = [day, end_day or day]
        if league:
            sql += " AND league=?"
            params.append(league)
        # Prefer verified evidence; transient missing responses never erase an official actual.
        sql += " ORDER BY verified,id"
        output = {}
        for pick_id, fingerprint, raw in conn.execute(sql, params):
            if originals.get(pick_id) == fingerprint:
                output[pick_id] = json.loads(raw)
        return output
