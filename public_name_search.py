"""Public player/team names only; never return prediction, price or prop fields."""
from functools import lru_cache
import json
import re
import sqlite3
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import urlopen

INDIVIDUAL = {"TENNIS", "UFC", "GOLF"}

# ESPN is optional enrichment. Never let it block the header search (or the
# single gunicorn worker) — local DB names must answer in well under a second.
_ESPN_TIMEOUT_SEC = 0.6

_ALIAS_FILES = (
    "ncaaf_team_espn_ids.json",
    "ncaa_basketball_team_ids.json",
    "soccer_team_espn_ids.json",
)


def normalized(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


@lru_cache(maxsize=1)
def _alias_id_map():
    """Normalized team alias → ESPN id (from on-disk college / soccer tables)."""
    root = Path(__file__).resolve().parent / "data"
    merged = {}
    for name in _ALIAS_FILES:
        path = root / name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for raw, espn_id in payload.items():
            key = normalized(raw)
            eid = str(espn_id or "").strip()
            if key and eid:
                merged[key] = eid
    return merged


@lru_cache(maxsize=512)
def _alias_expansions(query):
    """Full team names that share an ESPN id with a short abbreviation query."""
    query = normalized(query)
    if len(query) < 2 or " " in query:
        return ()
    eid = _alias_id_map().get(query)
    if not eid:
        return ()
    # Multi-word aliases only — skip shared nicknames like "panthers".
    return tuple(sorted({
        name for name, other in _alias_id_map().items()
        if other == eid and name != query and " " in name
    }))


def _label_matches(query, label):
    key = normalized(label)
    if not key:
        return False
    aliases = _alias_expansions(query)
    # Short abbreviation with known ESPN-id aliases (e.g. FIU): only full-name
    # aliases / whole-word hits — never mid-name substrings like "Fiumae".
    if aliases:
        if query in key.split():
            return True
        for alias in aliases:
            if alias == key or alias in key:
                return True
        return False
    return query in key


def local_entities(connection):
    found = {}
    queries = [
        ("SELECT DISTINCT sport,home_team_id,away_team_id FROM games", "team"),
        ("SELECT DISTINCT sport,home_team_id,away_team_id FROM predictions", "team"),
        ("SELECT DISTINCT league,player_name,NULL FROM player_prop_picks", "player"),
        ("SELECT DISTINCT league,player_name,NULL FROM player_prop_results", "player"),
    ]
    for query, kind in queries:
        try:
            rows = connection.execute(query)
            for sport, first, second in rows:
                sport = str(sport or "").upper()
                entity_kind = "player" if sport in INDIVIDUAL else kind
                for name in (first, second):
                    name = " ".join(str(name or "").split())
                    if name and not name.isdigit():
                        found[(sport, normalized(name), entity_kind)] = {
                            "label": name, "sport": sport, "kind": entity_kind, "_local": True,
                        }
        except sqlite3.Error:
            continue
    return list(found.values())


@lru_cache(maxsize=128)
def public_players(query, bucket):
    """Bounded name-only lookup for athletes absent from the saved props slate."""
    url = "https://site.web.api.espn.com/apis/common/v3/search?" + urlencode({
        "query": query, "limit": 50, "type": "player",
    })
    try:
        with urlopen(url, timeout=_ESPN_TIMEOUT_SEC) as response:
            payload = json.loads(response.read(2_000_000))
    except (OSError, ValueError, TimeoutError):
        return [], True
    if not isinstance(payload, dict) or not isinstance(payload.get("items", []), list):
        return [], True
    found = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        name = item.get("displayName") or item.get("name")
        league = item.get("defaultLeagueSlug") or item.get("league") or ""
        if isinstance(league, dict):
            league = league.get("slug") or league.get("abbreviation") or ""
        sport = str(league).upper()
        category = str(item.get("sport") or "").lower()
        if category in {"soccer", "tennis", "golf"}:
            sport = category.upper()
        elif category in {"mma", "ufc"}:
            sport = "UFC"
        if name and sport:
            found.append({"label": str(name), "sport": sport, "kind": "player"})
    return found, False


def _local_match_count(query, local, slug_of):
    count = 0
    for row in local:
        sport, label = row.get("sport"), row.get("label")
        if sport not in slug_of:
            continue
        if _label_matches(query, label):
            count += 1
            if count >= 8:
                return count
    return count


def find_names(query, local, slug_of, sport_order, provider=public_players):
    query = normalized(query)[:80]
    if len(query) < 2:
        return [], False
    names = list(local)
    aliases = _alias_expansions(query)
    # Local-first: only call ESPN when local names are thin. A hung ESPN lookup
    # used to freeze /api/web/search and show "Player directory unavailable".
    if len(query) >= 3 and _local_match_count(query, names, slug_of) < 5:
        try:
            extra, _espn_failed = provider(query, int(time.time() // 600))
            names.extend(extra)
        except Exception:
            pass
    matches = {}
    for row in names:
        sport, label, kind = row["sport"], row["label"], row["kind"]
        key = normalized(label)
        if sport not in slug_of or not _label_matches(query, label):
            continue
        matches.setdefault((sport, key, kind), row)

    def rank(row):
        key = normalized(row["label"])
        words = key.split()
        alias_hit = any(a == key or a in key for a in aliases)
        relevance = (
            0 if key == query else
            1 if query in words or alias_hit else
            2 if any(word.startswith(query) for word in words) else 3
        )
        return (0 if key == query else 1, 0 if row.get("_local") else 1, relevance,
                sport_order.index(row["sport"]) if row["sport"] in sport_order else 99,
                key, row["kind"])

    results = []
    for row in sorted(matches.values(), key=rank)[:20]:
        sport, label, kind = row["sport"], row["label"], row["kind"]
        if kind == "player" and sport not in INDIVIDUAL:
            href = "/player-props?" + urlencode({"league": sport, "player": label})
        else:
            href = "/" + slug_of[sport].replace("-picks", "-predictions") + "?team=" + quote(label)
        results.append({"label": label, "sport": sport, "kind": kind, "href": href})
    # Local directory already answered. ESPN is optional — never claim the
    # player directory is down just because enrichment timed out / 503'd.
    return results, False
