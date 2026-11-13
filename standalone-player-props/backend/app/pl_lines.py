"""PL line adapter: actual per-market history only, never hash/random example lines."""
from odds_engine.model.prop_math import VERSION, calculate, finite
from datetime import datetime
from zoneinfo import ZoneInfo
import time
import re
import hashlib
import json
import os
import threading

_HISTORY_CACHE = {}
_HISTORY_LOCK = threading.Lock()


def _remember_history(key, entry):
    with _HISTORY_LOCK:
        _HISTORY_CACHE[key] = entry
        while len(_HISTORY_CACHE) > 512:
            _HISTORY_CACHE.pop(next(iter(_HISTORY_CACHE)), None)


def _history_snapshot(key):
    from served_cache import cache_dir
    identity = hashlib.sha256(json.dumps(key).encode()).hexdigest()
    return cache_dir() / "props_player_history" / f"{identity}.json"


def hydrate_market_histories(league, players, markets, allowed, max_workers=8):
    """Load each candidate at bounded concurrency before category ranking."""
    from concurrent.futures import ThreadPoolExecutor

    def hydrate(player):
        for prop_type in markets:
            if not allowed(player, prop_type):
                continue
            existing = (player.get("stat_observations") or {}).get(prop_type) or []
            if player.get("history_verified") and len(existing) >= 10:
                continue
            stats = history_stats(league, player["player_id"], prop_type)
            if stats.get("observations"):
                player.setdefault("stat_observations", {})[prop_type] = stats["observations"]
                player.setdefault("stat_history", {})[prop_type] = stats["recent_form"]
                player["history_verified"] = True
        return player

    if not players:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(players)))) as pool:
        return list(pool.map(hydrate, players))


def _history(league, player_id, season=None):
    from .config import LEAGUE_CONFIG
    from odds_engine.model.player_history import read_history
    import requests
    cfg = LEAGUE_CONFIG.get(league) or {}
    if not cfg:
        return {}
    now = datetime.now(ZoneInfo("America/New_York"))
    key = (league, str(player_id), now.date().isoformat(), season)
    cached = _HISTORY_CACHE.get(key)
    if cached and time.time()-cached["ts"] < 21600:
        return cached["body"]
    snapshot = _history_snapshot(key)
    try:
        cached = json.loads(snapshot.read_text())
        if 0 <= now.timestamp()-cached["ts"] < 21600 and isinstance(cached["body"], dict):
            _remember_history(key, cached)
            return cached["body"]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    try:
        url = f"https://site.web.api.espn.com/apis/common/v3/sports/{cfg['espn_sport']}/{cfg['espn_league']}/athletes/{player_id}/gamelog"
        response = requests.get(url, params={"season": season} if season else None, timeout=8)
        response.raise_for_status()
        body = response.json()
    except Exception:
        body = {}
    _remember_history(key, {"ts": time.time(), "body": body})
    if body:
        try:
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            temporary = snapshot.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            temporary.write_text(json.dumps(_HISTORY_CACHE.get(key) or {"ts": time.time(), "body": body}))
            temporary.replace(snapshot)
        except OSError:
            pass
    return body


def history_stats(league, player_id, prop_type):
    from odds_engine.model.player_history import read_history
    now = datetime.now(ZoneInfo("America/New_York"))
    body = _history(league, player_id)
    stats = read_history(body, prop_type, now, minimum=1) or {}
    observations = {row["event_id"]: row for row in stats.get("observations") or []}
    if len(observations) < 10:
        season = body.get("season") or {}
        year = int(season.get("year") or 0) if isinstance(season, dict) else 0
        if not year:
            for item in body.get("seasonTypes") or []:
                match = re.search(r"(20\d{2})(?:-(\d{2,4}))?", str(item.get("displayName") or ""))
                if match:
                    year = int(match[1])
                    if match[2]:
                        year = int(match[2]) if len(match[2]) == 4 else year//100*100+int(match[2])
                    break
        year = year or now.year
        previous = read_history(_history(league, player_id, year-1), prop_type, now, minimum=1) or {}
        for row in previous.get("observations") or []:
            observations.setdefault(row["event_id"], row)
    ordered = sorted(observations.values(), key=lambda row: row["date"], reverse=True)[:60]
    if len(ordered) < 5:
        return {}
    return {"recent_form": [row["actual"] for row in ordered[:20]], "observations": ordered,
            "history_verified": True}


def lines_from_players(league, players, markets, allowed, endpoint="", post=None, market_lines=()):
    items, player_by_key, originals = [], {}, {}
    for player in players:
        for prop_type in markets:
            if not allowed(player, prop_type):
                continue
            history = (player.get("stat_history") or {}).get(prop_type)
            stats = {"recent_form": history} if player.get("history_verified") and history else {}
            if not stats and league != "NBA":
                stats = history_stats(league, player["player_id"], prop_type)
            if stats.get("observations"):
                player.setdefault("stat_observations", {})[prop_type] = stats["observations"]
                player.setdefault("stat_history", {})[prop_type] = stats["recent_form"]
            offers = [row for row in market_lines if str(row["player_id"]) == str(player["player_id"])
                      and row["prop_type"] == prop_type] or [None]
            for offer in offers:
                identity = str(len(items))
                item = {"request_id": identity, "player_id": str(player["player_id"]),
                        "player_name": player["name"], "team": player.get("team"),
                        "prop_type": prop_type, "player_stats": stats}
                if offer is not None:
                    item["real_line"] = offer["line"]
                    originals[identity] = offer
                items.append(item)
                player_by_key[identity] = player
    generated = {}
    by_request = {item["request_id"]: item for item in items}
    if endpoint and post and items:
        try:
            response = post(endpoint.rstrip("/") + "/player-props/batch",
                            json={"sport": league, "items": items}, timeout=10)
            response.raise_for_status()
            for row in response.json().get("props") or []:
                key = str(row.get("request_id"))
                original = originals.get(key)
                wanted = by_request.get(key)
                probs = row.get("probabilities") or {}
                values = [finite(probs.get(name)) for name in ("over", "under", "push")]
                if key in player_by_key and row.get("model_version") == VERSION and finite(row.get("line")) is not None \
                        and wanted and str(row.get("player_id")) == wanted["player_id"] \
                        and row.get("prop_type") == wanted["prop_type"] and finite(row.get("projection")) is not None \
                        and isinstance(row.get("model_forecasts"), list) and row["model_forecasts"] \
                        and all(value is not None and 0 <= value <= 1 for value in values) \
                        and abs(sum(values)-1) < 1e-6 \
                        and (original is None or float(original["line"]) == float(row["line"])):
                    generated[key] = row
        except Exception:
            # Same shared PL calculation works locally when the service is unavailable.
            pass
    out = []
    for item in items:
        key = item["request_id"]
        result = generated.get(key)
        if result is None:
            try:
                result = calculate(item["player_stats"], league, item["prop_type"], item.get("real_line"))
            except ValueError:
                continue
        player = player_by_key[key]
        game = player.get("next_game") or {}
        original = originals.get(key) or {}
        out.append({**original, "player_id": item["player_id"], "prop_type": item["prop_type"], "line": result["line"],
                    "line_for_calc": result["line"], "line_source": original.get("line_source", "pl_model"),
                    "model_version": VERSION, "projection": result.get("projection"),
                    "pl_line": result.get("fair_line"),
                    "model_forecasts": result.get("model_forecasts", []),
                    "probabilities": result.get("probabilities"), "strong_pick": result.get("strong_pick", False),
                    "input_hash": result.get("input_hash"), "sample": result.get("sample"),
                    "input_values": result.get("input_values"),
                    "odds_over": original.get("odds_over") if original else result.get("odds_over"),
                    "odds_under": original.get("odds_under") if original else result.get("odds_under"),
                    "event_id": original.get("event_id") or game.get("event_id"),
                    "event_start": original.get("event_start") or game.get("start_time")})
    return out
