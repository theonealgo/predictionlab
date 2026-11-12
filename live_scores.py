"""Read-only scoreboard enrichment; external requests never run on the page thread."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import logging
import re
import threading
import time
import unicodedata
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
PATHS = {
    "NBA": "basketball/nba", "WNBA": "basketball/wnba",
    "NCAAB": "basketball/mens-college-basketball",
    "NCAAW": "basketball/womens-college-basketball",
    "NFL": "football/nfl", "NCAAF": "football/college-football",
    "CFL": "football/cfl", "NHL": "hockey/nhl", "MLB": "baseball/mlb",
    "TENNIS": "tennis/atp",
}
_ALIASES = {"laclippers": "losangelesclippers"}
_FRESH = 25
_STALE = 120
_EXPIRE = 600


def identity(name):
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    key = re.sub(r"[^a-z0-9]", "", text.lower())
    return _ALIASES.get(key, key)


def paths_for(item):
    sport = str(item.get("sport", "")).upper()
    if sport == "SOCCER":
        from soccer_league_catalog import soccer_espn_slug
        slug = soccer_espn_slug(item.get("group"))
        return ["soccer/" + slug] if slug else []
    if sport == "TENNIS":
        return ["tennis/atp", "tennis/wta"]
    return [PATHS[sport]] if sport in PATHS else []


def _day(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.astimezone(ET).date().isoformat() if stamp.tzinfo else None
    except (TypeError, ValueError):
        return None


def _score(value):
    # Scheduled placeholders are discarded by the caller; a real live zero is valid.
    if isinstance(value, dict):
        value = value.get("value")
    text = str(value if value is not None else "")
    return text if re.fullmatch(r"\d+(?:\.\d+)?", text) else None


def parse_scoreboard(payload, day):
    records = []

    def visit(node, event_id=None, date=None):
        if not isinstance(node, dict):
            return
        date = node.get("date") or date
        if "competitors" in node:
            opponents = node.get("competitors") or []
            if len(opponents) == 2 and _day(date) == day:
                status = node.get("status") or {}
                kind = status.get("type") or {}
                sides = []
                for opponent in opponents:
                    entity = opponent.get("team") or opponent.get("athlete") or {}
                    names = {identity(entity.get("displayName"))}
                    # Full athlete names are also safe; team mascot-only names are not.
                    if opponent.get("athlete"):
                        names.add(identity(entity.get("fullName")))
                    names.discard("")
                    sides.append({"names": names, "score": _score(opponent.get("score"))})
                if all(side["names"] for side in sides):
                    records.append({
                        "id": str(node.get("id") or event_id or ""),
                        "sides": sides, "state": kind.get("state"),
                        "kind": kind.get("name", ""),
                        "detail": kind.get("shortDetail") or kind.get("detail") or "",
                        "period": status.get("period"),
                        "clock": status.get("displayClock") or "",
                    })
        for key in ("events", "competitions", "groupings"):
            for child in node.get(key) or []:
                visit(child, node.get("id") or event_id, date)

    visit(payload)
    return records


def match_fixture(item, records):
    away, home = identity(item.get("away")), identity(item.get("home"))
    if not away or not home or away == home:
        return None, "Game identity unavailable"
    matches = {}
    for record in records:
        a, b = record["sides"]
        if away in a["names"] and home in b["names"]:
            sides = (a, b)
        elif away in b["names"] and home in a["names"]:
            sides = (b, a)
        else:
            continue
        if item.get("eventId") and str(item["eventId"]) != record["id"]:
            continue
        matches[record["id"]] = (record, sides)
    if len(matches) != 1:
        return None, "Multiple matching games" if matches else "No confirmed score for this game"
    return next(iter(matches.values())), ""


class ScoreCache:
    def __init__(self, fetch=None, clock=time.time):
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.entries = {}
        self.attempts = {}
        self.pending = set()
        self.lock = threading.Lock()
        self.running = False

    @staticmethod
    def _fetch(path, day):
        params = {"dates": day.replace("-", ""), "limit": 1000}
        if "college" in path:
            params["groups"] = 50 if "basketball" in path else 80
        response = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard",
            params=params, timeout=(2, 4),
        )
        response.raise_for_status()
        return response.json()

    def _refresh(self, key):
        path, day = key
        try:
            records = parse_scoreboard(self.fetch(path, day), day)
            with self.lock:
                self.entries[key] = (self.clock(), records)
        except Exception as exc:
            logging.getLogger(__name__).info("Live score refresh unavailable: %s", type(exc).__name__)

    def _drain(self):
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                while True:
                    with self.lock:
                        keys = list(self.pending)
                        self.pending.clear()
                        if not keys:
                            self.running = False
                            return
                    list(pool.map(self._refresh, keys))
        except Exception:
            with self.lock:
                self.running = False
            logging.getLogger(__name__).exception("Live score background refresh failed")

    def _queue(self, keys):
        now = self.clock()
        with self.lock:
            # Keep only today's and yesterday's small in-memory records.
            for key in list(self.attempts):
                if now - self.attempts[key] > 86400:
                    self.entries.pop(key, None)
                    self.attempts.pop(key, None)
            for key in keys:
                if now - self.attempts.get(key, float("-inf")) >= _FRESH:
                    self.attempts[key] = now
                    self.pending.add(key)
            if self.running or not self.pending:
                return
            self.running = True
        threading.Thread(target=self._drain, name="live-score-refresh", daemon=True).start()

    def enrich(self, items, day=None, refresh=True):
        day = day or datetime.now(ET).date().isoformat()
        keys = {(path, day) for item in items for path in paths_for(item)}
        if refresh:
            self._queue(keys)
        with self.lock:
            saved = dict(self.entries)
        now = self.clock()
        output = []
        for original in items:
            item = dict(original)
            item.update(live=False, awayScore=None, homeScore=None, scoreState="unavailable",
                        scoreReason="Score coverage unavailable", statusDetail="")
            paths = paths_for(item)
            records = []
            times = []
            for path in paths:
                entry = saved.get((path, day))
                if entry and now - entry[0] < _EXPIRE:
                    records.extend(entry[1])
                    times.append(entry[0])
            matched, reason = match_fixture(item, records)
            if matched:
                record, sides = matched
                state = record["state"]
                # Finished, postponed and canceled fixtures do not belong in this strip.
                if state == "post" or record["kind"] in (
                    "STATUS_CANCELED", "STATUS_POSTPONED", "STATUS_CANCELLED",
                ):
                    continue
                stale = now - min(times) > _STALE
                item.update(live=state == "in" and not stale,
                            scoreState="delayed" if stale else state or "unavailable",
                            statusDetail=record["detail"], period=record["period"],
                            clock=record["clock"], eventId=record["id"],
                            scoreUpdatedAt=int(min(times)),
                            scoreReason="Last score update was delayed" if stale else "")
                if state == "in":
                    item.update(awayScore=sides[0]["score"], homeScore=sides[1]["score"])
                    if any(side["score"] is None for side in sides):
                        item["scoreReason"] = "Current score unavailable"
                if str(item.get("sport", "")).upper() == "TENNIS":
                    item["scoreLabel"] = "Sets"
            elif paths:
                item["scoreReason"] = reason if times else "Score updates unavailable"
            output.append(item)
        return sorted(output, key=lambda item: not item["live"])


cache = ScoreCache()


def enrich_live_items(items):
    return cache.enrich(items)
