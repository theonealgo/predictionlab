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


def _schedule_fixture_count(entry, day):
    """Games on the props snapshot schedule for this calendar day (ET)."""
    count = 0
    for game in ((entry or {}).get("payload") or {}).get("schedule") or []:
        try:
            stamp = datetime.fromisoformat(str(game.get("start_time") or "").replace("Z", "+00:00"))
            if not stamp.tzinfo:
                continue
            if stamp.astimezone(ET).date().isoformat() == day:
                count += 1
        except (ValueError, TypeError):
            continue
    return count


def _fixed_in_season(league, day):
    """Same fixed window the homepage uses when ESPN's calendar is unusable (CFL→2022)."""
    try:
        from NHL77FINAL import get_season_phase
        today = datetime.strptime(day, "%Y-%m-%d")
        phase = get_season_phase(league, today=today)[0]
        return phase != "offseason"
    except Exception:
        return None


def _in_season_flags(day):
    """ESPN when fresh; otherwise the same fixed calendar as the homepage sports list."""
    flags = {}
    try:
        import season_status
        today = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=ET)
        for league in LEAGUES:
            if league in getattr(season_status, "LEAGUE_PATHS", {}):
                espn = season_status.espn_in_season(league, today)
                flags[league] = espn if espn is not None else _fixed_in_season(league, day)
            else:
                # Soccer / UFC / tennis / golf: not on the ESPN season map.
                flags[league] = _fixed_in_season(league, day)
                if flags[league] is None:
                    flags[league] = True
    except Exception:
        for league in LEAGUES:
            flags[league] = _fixed_in_season(league, day)
    return flags


def _games_word(n):
    return "game" if n == 1 else "games"


def _nav_note(status, count, fixtures, in_season):
    """Short left-rail copy — never leave a blank sport with no explanation."""
    if in_season is False:
        return "Off season"
    if isinstance(count, int) and count > 0:
        if fixtures > 0:
            return f"{count} props · {fixtures} {_games_word(fixtures)}"
        return f"{count} props"
    if status == "no_fixtures":
        return "No games today"
    if status == "slate_started":
        if fixtures > 0:
            return f"{fixtures} {_games_word(fixtures)} · started"
        return "Games started"
    if status == "no_verified_props":
        if fixtures > 0:
            return f"{fixtures} {_games_word(fixtures)} · no props yet"
        return "No props yet"
    if status == "unsupported":
        return "Props not offered"
    if status in ("building", "not_loaded") or count is None:
        return "Loading…"
    if fixtures > 0:
        return f"{fixtures} {_games_word(fixtures)}"
    return "No games today"


def navigation(database, day, entries):
    datetime.strptime(day, "%Y-%m-%d")
    catalog = game_catalog(database, day)
    catalog_counts = defaultdict(int)
    for game in catalog:
        catalog_counts[game["league"]] += 1
    if day < datetime.now(ET).date().isoformat():
        sections = historical_catalog(database, day, limit=None)["sections"]
    else:
        sections = [preview(league, entries.get(league), day=day, limit=None) for league in LEAGUES]
    season = _in_season_flags(day)
    output = []
    for section in sections:
        league = section["league"]
        entry = entries.get(league)
        bind_games(section, catalog, entry)
        grouped = {}
        unresolved = 0
        for row in section.get("items") or []:
            if not row.get("game_id"):
                unresolved += 1
                continue
            game = grouped.setdefault(row["game_id"], {"id": row["game_id"], "name": row["game_label"], "count": 0})
            game["count"] += 1
        fixtures = max(catalog_counts.get(league, 0), _schedule_fixture_count(entry, day), len(grouped))
        in_season = season.get(league)
        status = section.get("status")
        count = section.get("total")
        # Off-season calendar beats a stale no_fixtures snapshot.
        if in_season is False and status in (None, "no_fixtures", "not_loaded"):
            status = "off_season"
        note = _nav_note(status, count, fixtures, in_season)
        output.append({
            "league": league,
            "count": count,
            "kind": section.get("count_kind", "available"),
            "games": list(grouped.values()),
            "fixture_count": fixtures,
            "status": status,
            "reason": section.get("reason"),
            "note": note,
            "in_season": in_season,
            "unresolved_game_count": unresolved,
        })
    return {"date": day, "sports": output,
            "known_total": sum(item["count"] for item in output if item["count"] is not None),
            "complete": all(item["count"] is not None for item in output)}
