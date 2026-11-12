"""Read-only availability metadata: counts and public game names, never player prices or picks."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import unicodedata
from zoneinfo import ZoneInfo

from sports_props_hub import LEAGUES, historical_catalog, preview

ET = ZoneInfo("America/New_York")


def _name(value):
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()
    value = re.sub("[^a-z0-9]", "", value)
    return {"laclippers": "losangelesclippers"}.get(value, value)


def game_catalog(database, day):
    result = []
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro", uri=True, timeout=3)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "games" in tables:
            result = [{"league": league, "id": identity, "home": home, "away": away}
                      for league, identity, home, away in conn.execute(
                          "SELECT sport,game_id,home_team_id,away_team_id FROM games WHERE substr(game_date,1,10)=?",
                          (day,)) if league in LEAGUES]
    return result


def bind_games(section, catalog, entry=None):
    league = section["league"]
    known = {str(game["id"]): game for game in catalog if game["league"] == league}
    for game in ((entry or {}).get("payload") or {}).get("schedule") or []:
        try:
            stamp = datetime.fromisoformat(str(game["start_time"]).replace("Z", "+00:00"))
            if not stamp.tzinfo or stamp.astimezone(ET).date().isoformat() != section["date"]:
                continue
            eid = str(game["event_id"])
            if eid.isdigit():
                known.setdefault(f"{league}_{eid}", {"id": f"{league}_{eid}", "league": league,
                                                   "home": game["home_team"], "away": game["away_team"]})
        except (KeyError, ValueError, TypeError):
            continue
    for row in section.get("items") or []:
        eid = str(row.get("event_id") or "")
        direct = [game for identity, game in known.items()
                  if identity == eid or identity == f"{league}_{eid}"]
        candidates = direct
        if not direct and not eid:
            candidates = [game for game in known.values()
                          if _name(row.get("team")) in {_name(game["home"]), _name(game["away"])}
                          and (not row.get("opponent") or _name(row["opponent"]) in
                               {_name(game["home"]), _name(game["away"])})]
        # Ambiguous doubleheaders are not assigned to an arbitrary game.
        if len(candidates) == 1:
            game = candidates[0]
            row["game_id"] = str(game["id"])
            row["game_label"] = f"{game['away']} @ {game['home']}"
    return section


def navigation(database, day, entries):
    datetime.strptime(day, "%Y-%m-%d")
    catalog = game_catalog(database, day)
    if day < datetime.now(ET).date().isoformat():
        sections = historical_catalog(database, day, limit=None)["sections"]
    else:
        sections = [preview(league, entries.get(league), day=day, limit=None) for league in LEAGUES]
    output = []
    for section in sections:
        bind_games(section, catalog, entries.get(section["league"]))
        grouped = {}
        unresolved = 0
        for row in section.get("items") or []:
            if not row.get("game_id"):
                unresolved += 1
                continue
            game = grouped.setdefault(row["game_id"], {"id": row["game_id"], "name": row["game_label"], "count": 0})
            game["count"] += 1
        output.append({"league": section["league"], "count": section.get("total"),
                       "kind": section.get("count_kind", "available"), "games": list(grouped.values()),
                       "status": section.get("status"), "reason": section.get("reason"),
                       "unresolved_game_count": unresolved})
    return {"date": day, "sports": output,
            "known_total": sum(item["count"] for item in output if item["count"] is not None),
            "complete": all(item["count"] is not None for item in output)}
