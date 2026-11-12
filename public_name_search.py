"""Public player/team names only; never return prediction, price or prop fields."""
from functools import lru_cache
import json
import re
import sqlite3
import time
import unicodedata
from urllib.parse import quote, urlencode
from urllib.request import urlopen

INDIVIDUAL = {"TENNIS", "UFC", "GOLF"}


def normalized(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


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
        with urlopen(url, timeout=1.5) as response:
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


def find_names(query, local, slug_of, sport_order, provider=public_players):
    query = normalized(query)[:80]
    if len(query) < 2:
        return [], False
    names = list(local)
    partial = False
    if len(query) >= 3:
        extra, partial = provider(query, int(time.time() // 600))
        names.extend(extra)
    matches = {}
    for row in names:
        sport, label, kind = row["sport"], row["label"], row["kind"]
        key = normalized(label)
        if sport not in slug_of or query not in key:
            continue
        matches.setdefault((sport, key, kind), row)

    def rank(row):
        key = normalized(row["label"])
        words = key.split()
        relevance = (0 if key == query else 1 if query in words else
                     2 if any(word.startswith(query) for word in words) else 3)
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
    return results, partial
