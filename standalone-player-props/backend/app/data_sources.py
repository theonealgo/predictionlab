import math
import os
import random
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from .config import LEAGUE_CONFIG, ODDS_API_BASE, ODDS_API_KEY, ODDS_ENGINE_URL

# Realistic "minutes" (or ice-time for hockey) ranges per sport.
# NHL skaters average 12-22 min of ice time; using NBA values (24-40) was the
# root cause of absurd projections like "9 assists in a game".
_LEAGUE_MINUTES_RANGE = {
    "NBA":    (24.0, 40.0),
    "WNBA":   (18.0, 36.0),
    "NCAAB":  (20.0, 40.0),
    "NCAAW":  (18.0, 38.0),
    "NHL":    (12.0, 22.0),   # ice time for skaters
    "MLB":    (1.0,  1.0),    # plate appearances
    "NFL":    (20.0, 65.0),   # snaps
    "NCAAF":  (18.0, 60.0),
    "SOCCER": (60.0, 90.0),   # match minutes
    "TENNIS": (60.0, 180.0),  # match minutes (varies widely)
    "UFC":    (1.0,  25.0),   # total fight minutes (5 rounds × 5 min max)
    "GOLF":   (18.0, 18.0),   # one round = 18 holes
}

_LEAGUE_PROP_TYPES = {
    "NBA":   ["points", "rebounds", "assists", "threes"],
    "WNBA":  ["points", "rebounds", "assists", "threes"],
    "NCAAB": ["points", "rebounds", "assists", "threes"],
    "NCAAW": ["points", "rebounds", "assists", "threes"],
    "NHL":   ["shots_on_goal", "points", "assists", "goals"],
    # MLB split by role — position-aware selection preferred
    "MLB":         ["hits", "runs", "rbis", "home_runs"],  # batter fallback
    "MLB_PITCHER": ["strikeouts", "earned_runs", "hits_allowed", "walks", "outs"],
    "MLB_BATTER":  ["hits", "runs", "rbis", "home_runs",
                    "singles", "doubles", "total_bases", "stolen_bases"],
    # NFL split by role
    "NFL":         ["rushing_yards", "receiving_yards", "receptions"],
    "NFL_QB":      ["passing_yards"],
    "NFL_SKILL":   ["rushing_yards", "receiving_yards", "receptions"],
    "NCAAF":       ["rushing_yards", "receiving_yards", "receptions"],
    "NCAAF_QB":    ["passing_yards"],
    "NCAAF_SKILL": ["rushing_yards", "receiving_yards", "receptions"],
    "SOCCER":      ["shots", "shots_on_target", "goals", "assists"],
    # New individual sports
    "TENNIS": ["aces", "games", "double_faults"],
    "UFC":    ["significant_strikes", "takedowns"],
    "GOLF":   ["birdies", "bogeys"],
}

# MLB pitcher position abbreviations from ESPN API
_MLB_PITCHER_POS = {"SP", "RP", "CP", "P", "LHP", "RHP"}
# NFL QB position abbreviations
_NFL_QB_POS      = {"QB"}
# NHL goalie positions (excluded from props — no shots/goals/assists)
_NHL_GOALIE_POS  = {"G", "GK"}

_PROP_LINE_RANGES = {
    # Basketball
    "points":               (9.5,   30.5),
    "rebounds":             (4.5,   12.5),
    "assists":              (3.5,   10.5),
    "threes":               (0.5,    3.5),
    # Hockey base (overridden per sport below)
    "shots_on_goal":        (2.5,    3.5),
    # NOTE: these are PROJECTION ranges (real expected per-game values).
    # The displayed LINE is set separately by _DISCRETE_MARKET_LINES (0.5).
    "goals":                (0.05,   0.70),  # expected goals/game; line stays 0.5
    # Baseball
    "hits":                 (0.5,    2.5),
    "strikeouts":           (4.5,    8.5),   # pitcher Ks
    "runs":                 (0.3,    1.4),
    "rbis":                 (0.3,    1.4),
    "home_runs":            (0.05,   0.55),  # expected HR/game; line stays 0.5
    # Football
    "passing_yards":        (225.5, 265.5),
    "rushing_yards":        (50.5,   85.5),
    "receiving_yards":      (45.5,   75.5),
    "receptions":           (3.5,    7.5),
    # Soccer
    "shots":                (1.5,    3.5),
    "shots_on_target":      (0.5,    1.5),
    # Tennis — per match
    "aces":                 (4.5,   12.5),   # big servers 8.5-12.5, others 4.5-6.5
    "games":                (8.5,   22.5),   # total games won in the match
    "double_faults":        (2.5,    4.5),
    # UFC — per fight (non-main-event fighters on lower end)
    "significant_strikes":  (25.5,  75.5),
    "takedowns":            (0.5,    2.5),
    # Golf — per round
    "birdies":              (3.5,    5.5),   # top PGA players average 4-5 birdies/round
    "bogeys":               (1.5,    3.5),
}

# Per-sport overrides — NHL and Soccer share key names with NBA but need
# completely different ranges.
_SPORT_PROP_LINE_RANGES: dict[str, dict[str, tuple]] = {
    "NHL": {
        # These are PROJECTION ranges (real expected per-game values).
        # Displayed line is pinned to 0.5 (anytime) via _DISCRETE_MARKET_LINES.
        "points":        (0.20, 1.40),   # expected points/game
        "assists":       (0.15, 0.95),   # expected assists/game
        "goals":         (0.05, 0.70),   # expected goals/game
        "shots_on_goal": (2.5, 3.5),     # volume stat — real O/U line
    },
    "SOCCER": {
        "goals":           (0.05, 0.65),
        "assists":         (0.05, 0.55),
        "shots":           (1.5, 3.5),
        "shots_on_target": (0.5, 1.5),
    },
}

# Discrete market lines — for props where sportsbooks use fixed step values
# (e.g. NHL points is either 0.5 OR 1.5, never 1.0).
# Format: {league: {prop_type: [list_of_valid_lines]}}
_DISCRETE_MARKET_LINES: dict[str, dict[str, list]] = {
    "NHL": {
        # OVER-only milestone props pin to the anytime 0.5 line (no UNDER side)
        "points":        [0.5],
        "assists":       [0.5],
        "goals":         [0.5],
        "shots_on_goal": [2.5, 3.5],   # volume stat — real over/under
    },
    "SOCCER": {
        "goals":           [0.5],
        "assists":         [0.5],
        "shots_on_target": [0.5, 1.5],
    },
    "MLB": {
        # OVER-only milestone props pin to anytime 0.5 (no UNDER side);
        # pitcher strikeouts stay over/under (handled by range, not here).
        "hits":      [0.5],
        "runs":      [0.5],
        "rbis":      [0.5],
        "home_runs": [0.5],
    },
    "GOLF": {
        # Most popular golf prop lines — round birdies O/U
        "birdies": [3.5, 4.5, 5.5],
        "bogeys":  [1.5, 2.5, 3.5],
    },
    "UFC": {
        "takedowns": [0.5, 1.5, 2.5],
    },
}


def _pick_market_line(league: str, prop_type: str, projection: float) -> float:
    """Return the sportsbook-standard line closest to the projection."""
    options = (_DISCRETE_MARKET_LINES.get(league) or {}).get(prop_type)
    if not options:
        return None  # caller should use continuous range
    if len(options) == 1:
        return options[0]
    # Pick the option that makes the over/under most interesting (closest to proj)
    return min(options, key=lambda x: abs(x - projection))


# ── ESPN Gamelog API configuration ────────────────────────────────────────
# Used to fetch per-game stats for hit-rate calculation (L-5, L-10, Season).
# Maps league + position_type → ESPN URL template + stat label mappings.
_GAMELOG_ESPN: dict[str, dict] = {
    "NHL": {
        "url": "https://site.web.api.espn.com/apis/common/v3/sports/hockey/nhl/athletes/{player_id}/gamelog",
        "stat_map": {
            "goals":         ["G"],
            "assists":       ["A"],
            "points":        ["PTS", "P"],
            "shots_on_goal": ["S", "SOG", "SHOTS"],
        },
    },
    "MLB_BATTER": {
        "url": "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/athletes/{player_id}/gamelog",
        "stat_map": {
            "hits":          ["H"],
            "runs":          ["R"],
            "rbis":          ["RBI"],
            "home_runs":     ["HR"],
            "doubles":       ["2B"],
            "triples":       ["3B"],   # internal: used to derive singles / total_bases
            "walks":         ["BB"],
            "stolen_bases":  ["SB"],
        },
    },
    "MLB_PITCHER": {
        "url": "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/athletes/{player_id}/gamelog",
        "stat_map": {
            "strikeouts":    ["K", "SO"],
            "earned_runs":   ["ER"],
            "hits_allowed":  ["H"],
            "walks":         ["BB"],
            "innings":       ["IP"],   # internal: used to derive outs
        },
    },
}

_GAMELOG_CACHE: Dict[str, Dict] = {}
_GAMELOG_TTL = 4 * 3600  # 4-hour cache


def _fetch_player_gamelog(player_id: str, gamelog_key: str) -> Dict[str, list]:
    """Fetch up to last 15 per-game stat values for a player.
    Returns {prop_type: [v1, v2, ...]} most-recent-first.
    """
    cache_key = f"{gamelog_key}:{player_id}"
    now = time.time()
    cached = _GAMELOG_CACHE.get(cache_key)
    if cached and (now - cached["ts"]) < _GAMELOG_TTL:
        return cached["payload"]

    cfg = _GAMELOG_ESPN.get(gamelog_key, {})
    url_tpl = cfg.get("url", "")
    stat_map = cfg.get("stat_map", {})
    if not url_tpl or not stat_map:
        return {}

    payload: Dict[str, list] = {}
    try:
        url = url_tpl.format(player_id=player_id)
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        body = resp.json()

        labels = [str(l).upper() for l in (body.get("labels") or [])]
        label_idx = {lbl: i for i, lbl in enumerate(labels)}

        # Collect events from regular season
        events: list[list] = []
        seasons = body.get("seasonTypes") or []
        regular = next(
            (s for s in seasons
             if "regular" in (s.get("displayName") or "").lower()), None)
        if regular is None and seasons:
            regular = seasons[0]
        for cat in ((regular or {}).get("categories") or []):
            for ev in (cat.get("events") or []):
                stats = ev.get("stats")
                if stats:
                    events.append(stats)

        if not events:
            _GAMELOG_CACHE[cache_key] = {"ts": now, "payload": {}}
            return {}

        for prop_type, label_options in stat_map.items():
            idx = next(
                (label_idx[l.upper()] for l in label_options
                 if l.upper() in label_idx), None)
            if idx is None:
                continue
            vals = []
            for stats in events[:15]:
                if idx < len(stats):
                    try:
                        v = float(str(stats[idx]).replace(",", "") or 0)
                        vals.append(round(v, 1))
                    except (ValueError, TypeError):
                        pass
            if vals:
                payload[prop_type] = vals

        # ── Derived per-game stats (computed from REAL components, never faked) ──
        if gamelog_key == "MLB_BATTER":
            h  = payload.get("hits"); d = payload.get("doubles")
            t  = payload.get("triples"); hr = payload.get("home_runs")
            if h and d and t is not None and hr is not None:
                n = min(len(h), len(d), len(t), len(hr))
                payload["singles"]     = [round(max(0.0, h[i] - d[i] - t[i] - hr[i]), 1) for i in range(n)]
                payload["total_bases"] = [round(h[i] + d[i] + 2 * t[i] + 3 * hr[i], 1) for i in range(n)]
            payload.pop("triples", None)  # internal helper, not a shipped prop
        elif gamelog_key == "MLB_PITCHER":
            ip = payload.get("innings")
            if ip:
                def _ip_to_outs(v: float) -> float:
                    whole = int(v)
                    frac = min(round((v - whole) * 10), 2)  # .1=1 out, .2=2 outs
                    return float(whole * 3 + frac)
                payload["outs"] = [_ip_to_outs(v) for v in ip]
            payload.pop("innings", None)  # internal helper, not a shipped prop

    except Exception:
        pass

    _GAMELOG_CACHE[cache_key] = {"ts": now, "payload": payload}
    return payload

_NBA_CONSENSUS_TOP100 = [
    "Nikola Jokic","Shai Gilgeous-Alexander","Luka Doncic","Giannis Antetokounmpo","Victor Wembanyama","Anthony Edwards","Stephen Curry","LeBron James","Kevin Durant","Jayson Tatum",
    "Jalen Brunson","Anthony Davis","Donovan Mitchell","Devin Booker","Paolo Banchero","Jimmy Butler","Jaylen Brown","Kawhi Leonard","Tyrese Haliburton","De'Aaron Fox",
    "Damian Lillard","Ja Morant","Zion Williamson","Cade Cunningham","Jalen Williams","Evan Mobley","Alperen Sengun","Trae Young","Pascal Siakam","Jamal Murray",
    "LaMelo Ball","Brandon Ingram","Jrue Holiday","Kristaps Porzingis","Desmond Bane","Tyrese Maxey","Darius Garland","Domantas Sabonis","Bam Adebayo","Karl-Anthony Towns",
    "Mikal Bridges","OG Anunoby","Jaren Jackson Jr.","Scottie Barnes","Fred VanVleet","Aaron Gordon","Kyrie Irving","James Harden","Klay Thompson","DeMar DeRozan",
    "Julius Randle","Chet Holmgren","Austin Reaves","Naz Reid","Rudy Gobert","Myles Turner","Jarrett Allen","Walker Kessler","Jabari Smith Jr.","Bennedict Mathurin",
    "Immanuel Quickley","Herb Jones","CJ McCollum","Zach LaVine","Anfernee Simons","Josh Giddey","Cam Thomas","Jalen Green","Franz Wagner","Tyler Herro",
    "Derrick White","Brook Lopez","Michael Porter Jr.","Aaron Nesmith","Kyle Kuzma","RJ Barrett","Keegan Murray","Khris Middleton","Dejounte Murray","Amen Thompson",
    "Ausar Thompson","Andrew Wiggins","Buddy Hield","Bogdan Bogdanovic","Malik Monk","Tobias Harris","Jakob Poeltl","Nic Claxton","Jalen Johnson","Jonathan Kuminga",
    "Jaden McDaniels","Alex Caruso","Clint Capela","Deni Avdija","Norman Powell","Jordan Poole","Collin Sexton","Tyus Jones","Onyeka Okongwu","Bobby Portis",
]


def _norm_name(v: str) -> str:
    if not v:
        return ""
    s = unicodedata.normalize("NFKD", v)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace(".", "").replace("'", "").replace("-", " ")
    s = " ".join(s.split())
    return s


_NBA_CONSENSUS_RANK = {_norm_name(name): i + 1 for i, name in enumerate(_NBA_CONSENSUS_TOP100)}

_PLAYER_METRICS_CACHE: Dict[str, Dict] = {}
_PLAYER_METRICS_TTL = 6 * 3600


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _to_half_step(v: float) -> float:
    return round(round(float(v) * 2.0) / 2.0, 1)


def _parse_attempts(value: str) -> float:
    """Attempts side of a 'made-attempts' stat (e.g. '3-8' -> 8). Used for usage."""
    if not value or "-" not in value:
        return 0.0
    try:
        return float(value.split("-")[1])
    except Exception:
        return 0.0


def _parse_made(value: str) -> float:
    """Made side of a 'made-attempts' stat (e.g. '3-8' -> 3). Used for 3PT-made."""
    if value is None:
        return 0.0
    try:
        if "-" in str(value):
            return float(str(value).split("-")[0])
        return float(value)
    except Exception:
        return 0.0


def _num(value: str) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _espn_scoreboard_url_for(cfg: dict) -> str:
    return (
        f"https://site.api.espn.com/apis/site/v2/sports/"
        f"{cfg['espn_sport']}/{cfg['espn_league']}/scoreboard"
    )


def _league_espn_sources(league: str) -> List[dict]:
    """Return one or more ESPN scoreboard configs for a league."""
    cfg = LEAGUE_CONFIG.get(league, {})
    sources = cfg.get("espn_sources") or []
    if sources:
        return sources
    return [{
        "espn_sport": cfg.get("espn_sport", ""),
        "espn_league": cfg.get("espn_league", ""),
        "individual": cfg.get("individual", False),
        "tournament": cfg.get("tournament", False),
        "tennis_groupings": cfg.get("tennis_groupings", False),
    }]


def _schedule_rows_from_scoreboard(body: dict, *, is_individual: bool, is_tournament: bool,
                                   tennis_groupings: bool) -> List[Dict]:
    events = body.get("events", [])
    rows: List[Dict] = []

    for ev in events:
        competitions = ev.get("competitions") or []

        if is_individual and not is_tournament and tennis_groupings and not competitions:
            for grp in (ev.get("groupings") or []):
                competitions.extend(grp.get("competitions") or [])

        if is_tournament:
            comp = competitions[0] if competitions else {}
            field = comp.get("competitors") or []
            for i in range(0, len(field) - 1, 2):
                p1, p2 = field[i], field[i + 1]
                rows.append({
                    "event_id":    ev.get("id", "") + f"_pair{i}",
                    "start_time":  ev.get("date"),
                    "home_team":   _competitor_name(p1),
                    "away_team":   _competitor_name(p2),
                    "home_team_id": _competitor_id(p1),
                    "away_team_id": _competitor_id(p2),
                })
            break

        for comp in competitions:
            teams = comp.get("competitors") or []
            if len(teams) < 2:
                continue
            if is_individual:
                p1, p2 = teams[0], teams[1]
                home_name = _competitor_name(p1)
                away_name = _competitor_name(p2)
                home_id   = _competitor_id(p1)
                away_id   = _competitor_id(p2)
            else:
                home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
                away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
                home_name = (home.get("team") or {}).get("displayName", "Home")
                away_name = (away.get("team") or {}).get("displayName", "Away")
                home_id   = str((home.get("team") or {}).get("id", ""))
                away_id   = str((away.get("team") or {}).get("id", ""))

            rows.append({
                "event_id":    comp.get("id", ev.get("id", "")),
                "start_time":  ev.get("date"),
                "home_team":   home_name,
                "away_team":   away_name,
                "home_team_id": home_id,
                "away_team_id": away_id,
            })
    return rows


def _fetch_scoreboard_body(url: str, ds: str) -> dict:
    resp = requests.get(url, params={"dates": ds}, timeout=12)
    resp.raise_for_status()
    return resp.json()


def _dedupe_schedule_rows(rows: List[Dict]) -> List[Dict]:
    seen: set[str] = set()
    out: List[Dict] = []
    for row in rows:
        key = "|".join([
            str(row.get("event_id", "")),
            str(row.get("home_team", "")).lower(),
            str(row.get("away_team", "")).lower(),
        ])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _rows_for_league_date(league: str, ds: str) -> List[Dict]:
    rows: List[Dict] = []
    for src in _league_espn_sources(league):
        url = _espn_scoreboard_url_for(src)
        is_individual = src.get("individual", LEAGUE_CONFIG.get(league, {}).get("individual", False))
        is_tournament = src.get("tournament", LEAGUE_CONFIG.get(league, {}).get("tournament", False))
        tennis_groupings = src.get(
            "tennis_groupings",
            LEAGUE_CONFIG.get(league, {}).get("tennis_groupings", False),
        )
        try:
            body = _fetch_scoreboard_body(url, ds)
        except Exception:
            continue
        rows.extend(_schedule_rows_from_scoreboard(
            body,
            is_individual=is_individual,
            is_tournament=is_tournament,
            tennis_groupings=tennis_groupings,
        ))
    return _dedupe_schedule_rows(rows)


def _ufc_sig_strikes(competitor: dict) -> Optional[float]:
    lines = competitor.get("linescores") or []
    if not lines:
        return None
    try:
        total = float(lines[0].get("value"))
        if total > 0:
            return total
    except (TypeError, ValueError):
        pass
    inner = lines[0].get("linescores") or []
    if inner:
        try:
            return float(sum(float(x.get("value", 0) or 0) for x in inner))
        except (TypeError, ValueError):
            pass
    return None


def _tennis_games_won(competitor: dict) -> Optional[float]:
    """Games won per set (linescore values) summed for the finished match."""
    lines = competitor.get("linescores") or []
    if not lines:
        return None
    total = 0.0
    for ls in lines:
        try:
            total += float(ls.get("value", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total if total > 0 else None


_UFC_CORE_STATS_CACHE: Dict[str, Dict] = {}
_UFC_CORE_STATS_TTL = 3600


def _ufc_core_fight_stats(event_id: str, comp_id: str, competitor_id: str) -> Dict[str, float]:
    """Fetch sig. strikes + takedowns from ESPN core when scoreboard linescores are missing."""
    if not event_id or not comp_id or not competitor_id:
        return {}
    cache_key = f"{event_id}:{comp_id}:{competitor_id}"
    now = time.time()
    cached = _UFC_CORE_STATS_CACHE.get(cache_key)
    if cached and (now - cached["ts"]) < _UFC_CORE_STATS_TTL:
        return cached["payload"]

    out: Dict[str, float] = {}
    url = (
        "https://sports.core.api.espn.com/v2/sports/mma/leagues/ufc/events/"
        f"{event_id}/competitions/{comp_id}/competitors/{competitor_id}/statistics"
        "?lang=en&region=us"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        splits = resp.json().get("splits") or {}
        categories = splits.get("categories") if isinstance(splits, dict) else []
        stat_map: Dict[str, float] = {}
        for cat in categories or []:
            for st in cat.get("stats") or []:
                name = str(st.get("name") or "")
                try:
                    stat_map[name] = float(st.get("value"))
                except (TypeError, ValueError):
                    continue
        if "sigStrikesLanded" in stat_map:
            out["significant_strikes"] = stat_map["sigStrikesLanded"]
        if "takedownsLanded" in stat_map:
            out["takedowns"] = stat_map["takedownsLanded"]
    except Exception:
        pass

    _UFC_CORE_STATS_CACHE[cache_key] = {"ts": now, "payload": out}
    return out


def _birdies_bogeys_from_holes(holes: list) -> Dict[str, float]:
    birdies = bogeys = 0
    for hole in holes:
        rel = (hole.get("scoreType") or {}).get("displayValue", "")
        if rel == "-1":
            birdies += 1
        elif rel == "+1":
            bogeys += 1
    if not holes:
        return {}
    return {"birdies": float(birdies), "bogeys": float(bogeys)}


def _golf_round_stats(competitor: dict, *, round_period: Optional[int] = None) -> Dict[str, float]:
    """Extract birdies/bogeys from ESPN round statistics or hole scoreTypes."""
    lines = competitor.get("linescores") or []
    if not lines:
        return {}
    if round_period is not None:
        rounds = [ls for ls in lines if ls.get("period") == round_period]
    else:
        rounds = sorted(lines, key=lambda ls: int(ls.get("period") or 0), reverse=True)
    for ls in rounds:
        cats = ((ls.get("statistics") or {}).get("categories") or [])
        if cats:
            stats = cats[0].get("stats") or []
            out: Dict[str, float] = {}
            if stats and stats[0].get("value") is not None:
                out["birdies"] = float(stats[0]["value"])
            if len(stats) >= 2 and stats[1].get("value") is not None:
                out["bogeys"] = float(stats[1]["value"])
            if out:
                return out
        holes = ls.get("linescores") or []
        if holes and any(h.get("scoreType") for h in holes):
            return _birdies_bogeys_from_holes(holes)
    return {}


def _is_final_competition(comp: dict) -> bool:
    return (comp.get("status") or {}).get("type", {}).get("name") == "STATUS_FINAL"


def fetch_individual_prop_actuals(league: str, target_date) -> Dict[str, Dict[str, float]]:
    """Map lowercased athlete name -> {prop_type: actual} for finished events on target_date."""
    key = league.upper()
    if key not in ("TENNIS", "UFC", "GOLF"):
        return {}
    ds = target_date.strftime("%Y%m%d")
    actuals: Dict[str, Dict[str, float]] = {}

    for src in _league_espn_sources(key):
        url = _espn_scoreboard_url_for(src)
        is_tournament = src.get("tournament", LEAGUE_CONFIG.get(key, {}).get("tournament", False))
        tennis_groupings = src.get(
            "tennis_groupings",
            LEAGUE_CONFIG.get(key, {}).get("tennis_groupings", False),
        )
        try:
            body = _fetch_scoreboard_body(url, ds)
        except Exception:
            continue

        for ev in body.get("events", []):
            competitions = list(ev.get("competitions") or [])
            if key == "TENNIS" and tennis_groupings and not competitions:
                for grp in ev.get("groupings") or []:
                    competitions.extend(grp.get("competitions") or [])

            if key == "GOLF" and is_tournament:
                comp = competitions[0] if competitions else {}
                for c in comp.get("competitors") or []:
                    name = _competitor_name(c).lower()
                    if not name or name == "unknown":
                        continue
                    stats = _golf_round_stats(c)
                    if stats:
                        bucket = actuals.setdefault(name, {})
                        bucket.update(stats)
                continue

            for comp in competitions:
                if not _is_final_competition(comp):
                    continue
                for c in comp.get("competitors") or []:
                    name = _competitor_name(c).lower()
                    if not name or name == "unknown":
                        continue
                    bucket = actuals.setdefault(name, {})
                    if key == "UFC":
                        sig = _ufc_sig_strikes(c)
                        if sig is not None:
                            bucket["significant_strikes"] = sig
                        core_stats = _ufc_core_fight_stats(
                            str(ev.get("id", "")),
                            str(comp.get("id", "")),
                            str(c.get("id", "")),
                        )
                        bucket.update(core_stats)
                    elif key == "TENNIS":
                        games = _tennis_games_won(c)
                        if games is not None:
                            bucket["games"] = games

    return actuals


def _competitor_name(c: dict) -> str:
    """Extract display name from a competitor dict (team or individual athlete)."""
    return (c.get("athlete") or c.get("team") or {}).get("displayName", "") \
        or c.get("displayName", "") or "Unknown"


def _competitor_id(c: dict) -> str:
    """Extract unique ID from a competitor dict."""
    team_id = (c.get("team") or {}).get("id", "")
    return str(team_id or c.get("id", ""))


def _position_abbr(obj: dict) -> str:
    """Safely extract a position abbreviation from an ESPN athlete/group dict.
    ESPN's `position` field is sometimes a dict ({'abbreviation': 'RP'}) and
    sometimes a plain string ('pitcher') — handle both without crashing."""
    if not isinstance(obj, dict):
        return ""
    pos = obj.get("position")
    if isinstance(pos, dict):
        return (pos.get("abbreviation") or pos.get("name") or "").strip()
    if isinstance(pos, str):
        return pos.strip()
    return ""


def fetch_schedule_and_teams(league: str, target_date=None) -> List[Dict]:
    cfg = LEAGUE_CONFIG.get(league, {})
    primary_src = _league_espn_sources(league)[0]
    primary_url = _espn_scoreboard_url_for(primary_src)

    try:
        now_et = datetime.now(ZoneInfo("America/New_York"))
        use_date = target_date or now_et.date()
        rows = _rows_for_league_date(league, use_date.strftime("%Y%m%d"))
        if rows:
            return rows
        for d in range(1, 8):
            probe = (use_date + timedelta(days=d)).strftime("%Y%m%d")
            rows = _rows_for_league_date(league, probe)
            if rows:
                return rows
        # Use scoreboard calendar to auto-activate when the next season date arrives.
        resp = requests.get(primary_url, timeout=12)
        resp.raise_for_status()
        body = resp.json()
        cal = ((body.get("leagues") or [{}])[0].get("calendar") or [])
        candidate_days = []
        for entry in cal:
            if isinstance(entry, str):
                try:
                    candidate_days.append(datetime.fromisoformat(entry.replace("Z", "+00:00")).date())
                except Exception:
                    continue
            elif isinstance(entry, dict):
                for key in ("startDate", "date"):
                    if entry.get(key):
                        try:
                            candidate_days.append(datetime.fromisoformat(str(entry[key]).replace("Z", "+00:00")).date())
                            break
                        except Exception:
                            continue
        upcoming = sorted({d for d in candidate_days if d >= use_date})
        for d in upcoming[:6]:
            rows = _rows_for_league_date(league, d.strftime("%Y%m%d"))
            if rows:
                return rows
        # Last resort: parse whatever events are on the default scoreboard response.
        is_individual = cfg.get("individual", False)
        is_tournament = cfg.get("tournament", False)
        tennis_groupings = cfg.get("tennis_groupings", False)
        rows = _schedule_rows_from_scoreboard(
            body,
            is_individual=is_individual,
            is_tournament=is_tournament,
            tennis_groupings=tennis_groupings,
        )
        if rows:
            return rows
        return []
    except Exception:
        pass
    # Fallback synthetic schedule so app remains usable in dev
    return [
        {
            "event_id": f"{league}-{i}",
            "start_time": datetime.utcnow().isoformat(),
            "home_team": f"{league} Team {2*i+1}",
            "away_team": f"{league} Team {2*i+2}",
        }
        for i in range(12)
    ]


def _build_individual_sport_players(league: str, schedule_rows: List[Dict]) -> List[Dict]:
    """For individual sports (Tennis, UFC, Golf): extract athletes directly from
    schedule rows rather than fetching team rosters. Each athlete is a 'player'."""
    _min_lo, _min_hi = _LEAGUE_MINUTES_RANGE.get(league, (20.0, 40.0))
    seen: set[str] = set()
    players: List[Dict] = []
    idx = 1

    for row in schedule_rows:
        for side in ("home_team", "away_team"):
            name   = row.get(side, "")
            pid    = row.get(f"{side}_id", "")
            if not name or name in seen:
                continue
            seen.add(name)
            final_id = pid if pid else f"{league}-{idx}"
            players.append({
                "player_id":         final_id,
                "name":              name,
                "player_name":       name,
                "team":              name,          # individual athletes ARE their own team
                "league":            league,
                "position_type":     "",
                "projected_minutes": round(random.uniform(_min_lo, _min_hi), 1),
                "usage_score":       round(random.uniform(0.5, 1.0), 3),
                "prop_frequency":    round(random.uniform(0.6, 1.0), 3),
                "top50_score":       round(random.uniform(40.0, 80.0), 2),
            })
            idx += 1

    return players[:50]


def build_top_players(league: str, schedule_rows: List[Dict]) -> List[Dict]:
    cfg = LEAGUE_CONFIG.get(league, {})

    # Individual sports (Tennis, UFC, Golf): no team rosters — use schedule directly
    if cfg.get("individual"):
        return _build_individual_sport_players(league, schedule_rows)

    espn_sport = cfg.get("espn_sport", "")
    espn_league = cfg.get("espn_league", "")
    players = []
    seen_player_ids = set()
    seen_name_team = set()
    idx = 1

    def _add_player(player_id: str, name: str, team: str,
                    position_abbr: str = ""):
        nonlocal idx
        if not name or not team:
            return
        key_id = (player_id or "").strip()
        key_name = (name.strip().lower(), team.strip().lower())
        if key_id and key_id in seen_player_ids:
            return
        if key_name in seen_name_team:
            return

        pos = (position_abbr or "").strip().upper()

        # Exclude NHL goalies — no skater props apply to them
        if league == "NHL" and pos in _NHL_GOALIE_POS:
            return

        # Determine position type for position-aware prop selection
        if league == "MLB":
            position_type = "pitcher" if pos in _MLB_PITCHER_POS else "batter"
        elif league in ("NFL", "NCAAF"):
            position_type = "qb" if pos in _NFL_QB_POS else "skill"
        else:
            position_type = ""

        _min_lo, _min_hi = _LEAGUE_MINUTES_RANGE.get(league, (24.0, 40.0))
        projected_minutes = random.uniform(_min_lo, _min_hi)
        usage = random.uniform(0.35, 1.0)
        prop_frequency = random.uniform(0.4, 1.0)
        score = projected_minutes * 0.45 + usage * 30 + prop_frequency * 25
        final_id = key_id if key_id else f"{league}-{idx}"
        players.append(
            {
                "player_id":       final_id,
                "name":            name,
                "team":            team,
                "league":          league,
                "position_abbr":   pos,
                "position_type":   position_type,
                "projected_minutes": round(projected_minutes, 1),
                "usage_score":     round(usage, 3),
                "prop_frequency":  round(prop_frequency, 3),
                "top50_score":     round(score, 2),
            }
        )
        seen_player_ids.add(final_id)
        seen_name_team.add(key_name)
        idx += 1

    def _fetch_roster(team_id: str, team_name: str):
        if not (team_id and espn_sport and espn_league):
            return
        url = (
            f"https://site.api.espn.com/apis/site/v2/sports/"
            f"{espn_sport}/{espn_league}/teams/{team_id}/roster"
        )
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            payload = resp.json()

            # athletesByPosition groups carry explicit position info — prefer this
            for group in payload.get("athletesByPosition") or []:
                group_pos = _position_abbr(group)
                for a in group.get("athletes") or []:
                    if not isinstance(a, dict):
                        continue
                    full_name = (a.get("fullName") or a.get("displayName") or "").strip()
                    a_id = str(a.get("id") or "")
                    # Individual athlete may override with own position
                    _add_player(a_id, full_name, team_name,
                                _position_abbr(a) or group_pos)

            # Fallback: flat athletes list (some leagues / ESPN endpoints)
            for a in payload.get("athletes") or []:
                if not isinstance(a, dict):
                    continue
                # MLB roster shape returns grouped entries inside athletes:
                # [{position: 'pitcher', items: [{id, fullName, position:{...}}]}]
                if isinstance(a.get("items"), list):
                    group_pos = _position_abbr(a)
                    for it in a.get("items") or []:
                        if not isinstance(it, dict):
                            continue
                        full_name = (it.get("fullName") or it.get("displayName") or "").strip()
                        a_id = str(it.get("id") or "")
                        _add_player(a_id, full_name, team_name,
                                    _position_abbr(it) or group_pos)
                    continue
                full_name = (a.get("fullName") or a.get("displayName") or "").strip()
                a_id = str(a.get("id") or "")
                _add_player(a_id, full_name, team_name, _position_abbr(a))
        except Exception:
            return

    for game in schedule_rows[:25]:
        _fetch_roster(game.get("home_team_id", ""), game["home_team"])
        _fetch_roster(game.get("away_team_id", ""), game["away_team"])
    players.sort(key=lambda x: x["top50_score"], reverse=True)
    top_players = players[:50] if players else []

    # Fetch per-game gamelogs for hit-rate calculation (NHL + MLB).
    # Run in parallel to keep latency acceptable.
    gamelog_leagues = {"NHL", "MLB"}
    if league in gamelog_leagues and top_players:
        def _get_gamelog(p: Dict) -> tuple[str, Dict]:
            pid = p.get("player_id", "")
            pos = p.get("position_type", "")
            if league == "MLB":
                key = "MLB_PITCHER" if pos == "pitcher" else "MLB_BATTER"
            else:
                key = league  # "NHL"
            return pid, _fetch_player_gamelog(pid, key)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_get_gamelog, p): p for p in top_players}
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    _, stats = fut.result()
                    if stats:
                        p["game_stats_last15"] = stats
                except Exception:
                    pass

    if top_players:
        return top_players
    # Fallback: synthesize a stable top-player pool from scheduled teams so
    # props do not render blank when roster endpoints are temporarily empty.
    teams = []
    seen = set()
    for g in schedule_rows[:25]:
        for side in ("home_team", "away_team"):
            t = (g.get(side) or "").strip()
            if not t:
                continue
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            teams.append(t)
    if not teams:
        teams = [f"{league} Team {i+1}" for i in range(12)]
    synthetic = []
    idx = 1
    for t in teams:
        for n in range(1, 5):
            _smin_lo, _smin_hi = _LEAGUE_MINUTES_RANGE.get(league, (20.0, 38.0))
            projected_minutes = random.uniform(_smin_lo, _smin_hi)
            usage = random.uniform(0.25, 0.95)
            prop_frequency = random.uniform(0.45, 1.0)
            score = projected_minutes * 0.45 + usage * 30 + prop_frequency * 25
            synthetic.append(
                {
                    "player_id": f"{league}-fallback-{idx}",
                    "name": f"{t} Player {n}",
                    "team": t,
                    "league": league,
                    "projected_minutes": round(projected_minutes, 1),
                    "usage_score": round(usage, 3),
                    "prop_frequency": round(prop_frequency, 3),
                    "top50_score": round(score, 2),
                }
            )
            idx += 1
            if len(synthetic) >= 50:
                return synthetic
    return synthetic


def _player_log_path() -> str:
    primary = "/logs/player_filter.log"
    try:
        os.makedirs("/logs", exist_ok=True)
        return primary
    except Exception:
        fallback = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "player_filter.log")
        os.makedirs(os.path.dirname(fallback), exist_ok=True)
        return fallback


def _append_filter_log(lines: List[str]):
    if not lines:
        return
    p = _player_log_path()
    ts = datetime.utcnow().isoformat()
    with open(p, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(f"{ts} {line}\n")


def _fetch_nba_player_metrics(player_id: str, athlete: Dict | None = None) -> Dict:
    now = time.time()
    cached = _PLAYER_METRICS_CACHE.get(player_id)
    if cached and (now - cached["ts"]) < _PLAYER_METRICS_TTL:
        return cached["payload"]

    url = f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{player_id}/gamelog"
    payload = {"avg_minutes": 0.0, "usage_rate": 0.05, "last_10_games_minutes": [], "avg_points": 0.0, "insufficient_data": True}
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        labels = body.get("labels") or []
        idx = {name: i for i, name in enumerate(labels)}
        seasons = body.get("seasonTypes") or []
        regular = next((s for s in seasons if "regular season" in (s.get("displayName") or "").lower()), None)
        if regular is None and seasons:
            regular = seasons[0]
        events = []
        for cat in ((regular or {}).get("categories") or []):
            for ev in cat.get("events") or []:
                if ev.get("stats"):
                    events.append(ev)
        mins, usage_raw = [], []
        pts_vals, reb_vals, ast_vals, thr_vals = [], [], [], []
        for ev in events:
            stats = ev.get("stats") or []
            if not stats:
                continue
            m = _num(stats[idx["MIN"]]) if "MIN" in idx and idx["MIN"] < len(stats) else 0.0
            fg = _parse_attempts(stats[idx["FG"]]) if "FG" in idx and idx["FG"] < len(stats) else 0.0
            ft = _parse_attempts(stats[idx["FT"]]) if "FT" in idx and idx["FT"] < len(stats) else 0.0
            to = _num(stats[idx["TO"]]) if "TO" in idx and idx["TO"] < len(stats) else 0.0
            pts = _num(stats[idx["PTS"]]) if "PTS" in idx and idx["PTS"] < len(stats) else 0.0
            reb = _num(stats[idx["REB"]]) if "REB" in idx and idx["REB"] < len(stats) else 0.0
            ast = _num(stats[idx["AST"]]) if "AST" in idx and idx["AST"] < len(stats) else 0.0
            thr = _parse_made(stats[idx["3PT"]]) if "3PT" in idx and idx["3PT"] < len(stats) else 0.0  # MADE threes, not attempts
            if m > 0:
                mins.append(m)
                usage_raw.append((fg + 0.44 * ft + to) / max(m, 1.0))
                pts_vals.append(pts)
                reb_vals.append(reb)
                ast_vals.append(ast)
                thr_vals.append(thr)
        if len(mins) >= 5:
            last5_m = mins[:5]
            last10_m = mins[:10]
            avg_last5_m = sum(last5_m) / len(last5_m)
            avg_last10_m = sum(last10_m) / len(last10_m)
            projected_minutes = (avg_last5_m * 0.7) + (avg_last10_m * 0.3)
            avg_min = sum(mins) / len(mins)
            u = (sum(usage_raw) / len(usage_raw)) / 2.0
            usage_rate = _clamp(u, 0.05, 0.38)
            def _avg(arr):
                return (sum(arr) / len(arr)) if arr else 0.0
            def _weighted(arr):
                a5 = _avg(arr[:5])
                a10 = _avg(arr[:10])
                return (a5 * 0.7) + (a10 * 0.3)
            payload = {
                "avg_minutes": round(avg_min, 2),
                "projected_minutes": round(projected_minutes, 2),
                "usage_rate": round(usage_rate, 3),
                "last_10_games_minutes": [round(v, 1) for v in mins[:10]],
                "avg_points": round(_avg(pts_vals), 2),
                "stats_last5": {
                    "points": round(_avg(pts_vals[:5]), 2),
                    "rebounds": round(_avg(reb_vals[:5]), 2),
                    "assists": round(_avg(ast_vals[:5]), 2),
                    "threes": round(_avg(thr_vals[:5]), 2),
                },
                "stats_last10": {
                    "points": round(_avg(pts_vals[:10]), 2),
                    "rebounds": round(_avg(reb_vals[:10]), 2),
                    "assists": round(_avg(ast_vals[:10]), 2),
                    "threes": round(_avg(thr_vals[:10]), 2),
                },
                "stats_weighted": {
                    "points": round(_weighted(pts_vals), 2),
                    "rebounds": round(_weighted(reb_vals), 2),
                    "assists": round(_weighted(ast_vals), 2),
                    "threes": round(_weighted(thr_vals), 2),
                },
                "game_stats_last15": {
                    "points":   [round(v, 1) for v in pts_vals[:15]],
                    "rebounds": [round(v, 1) for v in reb_vals[:15]],
                    "assists":  [round(v, 1) for v in ast_vals[:15]],
                    "threes":   [round(v, 1) for v in thr_vals[:15]],
                },
                "insufficient_data": False,
            }
    except Exception:
        pass
    _PLAYER_METRICS_CACHE[player_id] = {"ts": now, "payload": payload}
    return payload


def build_validated_nba_player_pool(schedule_rows: List[Dict]) -> Dict:
    players: List[Dict] = []
    excluded: List[Dict] = []
    roster_names = set()
    roster_team_pairs = set()
    team_ids = set()
    next_game_by_team = {}
    for g in schedule_rows[:15]:
        if g.get("home_team_id"):
            team_ids.add((g["home_team_id"], g["home_team"]))
            next_game_by_team[g["home_team_id"]] = {
                "opponent": g.get("away_team", ""),
                "start_time": g.get("start_time", ""),
                "venue": "home",
            }
        if g.get("away_team_id"):
            team_ids.add((g["away_team_id"], g["away_team"]))
            next_game_by_team[g["away_team_id"]] = {
                "opponent": g.get("home_team", ""),
                "start_time": g.get("start_time", ""),
                "venue": "away",
            }

    log_lines = []
    for team_id, team_name in team_ids:
        roster_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/roster"
        try:
            roster_resp = requests.get(roster_url, timeout=5)
            roster_resp.raise_for_status()
            roster = roster_resp.json()
            athletes = roster.get("athletes") or []
            for a in athletes:
                player_id = str(a.get("id") or "")
                player_name = (a.get("fullName") or a.get("displayName") or "").strip()
                if not player_id or not player_name:
                    continue
                player_key = _norm_name(player_name)
                roster_names.add(player_name.lower())
                roster_team_pairs.add((player_name.lower(), team_id))
                status = (a.get("status") or {}).get("type", "").lower()
                contracts = a.get("contracts") or []
                injuries = a.get("injuries") or []

                reasons = []
                if status and status != "active":
                    reasons.append("inactive_status")
                if player_key not in _NBA_CONSENSUS_RANK:
                    reasons.append("not_consensus_top100")
                if "g league" in player_name.lower() or "g-league" in player_name.lower():
                    reasons.append("g_league_flag")
                if injuries:
                    injury_blob = " ".join(str(x).lower() for x in injuries)
                    if any(flag in injury_blob for flag in ("out", "doubtful", "questionable", "injured", "inactive")):
                        reasons.append("injury_or_not_available")
                # Fast path: skip expensive gamelog calls for obvious excludes.
                if any(r in reasons for r in ("not_consensus_top100", "inactive_status", "injury_or_not_available", "g_league_flag")):
                    excluded.append({"player_id": player_id, "name": player_name, "team_id": team_id, "reasons": reasons})
                    log_lines.append(f"DROP {player_name} ({team_name}) -> {','.join(reasons)}")
                    continue

                # Keep request-time latency low: by default we avoid per-player live calls.
                metrics = _fetch_nba_player_metrics(player_id, athlete=a)
                if metrics.get("insufficient_data", True):
                    reasons.append("insufficient_data")
                avg_minutes = _clamp(metrics["avg_minutes"], 5.0, 40.0)
                usage_rate = _clamp(metrics["usage_rate"], 0.05, 0.38)
                last_10 = metrics["last_10_games_minutes"]
                if avg_minutes < 10.0:
                    reasons.append("below_10_mpg")
                if not last_10:
                    reasons.append("missing_last_10_minutes")
                two_way = any("two-way" in str(c).lower() for c in contracts)
                if two_way and avg_minutes <= 15.0:
                    reasons.append("two_way_below_15_mpg")

                if reasons:
                    excluded.append({"player_id": player_id, "name": player_name, "team_id": team_id, "reasons": reasons})
                    log_lines.append(f"DROP {player_name} ({team_name}) -> {','.join(reasons)}")
                    continue

                role = "starter" if avg_minutes >= 24.0 else "bench"
                points_avg = metrics.get("avg_points", 0.0)
                superstar = usage_rate >= 0.30 and avg_minutes >= 32.0 and points_avg >= 24.0
                players.append(
                    {
                        "player_id": player_id,
                        "name": player_name,
                        "team": team_name,
                        "team_id": team_id,
                        "league": "NBA",
                        "projected_minutes": round(avg_minutes, 1),
                        "projected_minutes_weighted": round(_clamp(metrics.get("projected_minutes", avg_minutes), 5.0, 40.0), 1),
                        "avg_minutes": round(avg_minutes, 1),
                        "usage_score": round(usage_rate, 3),
                        "usage_rate": round(usage_rate, 3),
                        "last_10_games_minutes": last_10,
                        "stats_last5": metrics.get("stats_last5", {}),
                        "stats_last10": metrics.get("stats_last10", {}),
                        "stats_weighted": metrics.get("stats_weighted", {}),
                        "game_stats_last15": metrics.get("game_stats_last15", {}),
                        "prop_frequency": round(_clamp(len(last_10) / 10.0, 0.4, 1.0), 3),
                        "top50_score": round((avg_minutes * 0.5) + (usage_rate * 100.0 * 0.5), 2),
                        "role": role,
                        "is_superstar": superstar,
                        "consensus_rank": _NBA_CONSENSUS_RANK.get(player_key, 999),
                        "consensus_tier": (
                            "top_10" if _NBA_CONSENSUS_RANK.get(player_key, 999) <= 10
                            else "superstar" if _NBA_CONSENSUS_RANK.get(player_key, 999) <= 25
                            else "all_star" if _NBA_CONSENSUS_RANK.get(player_key, 999) <= 50
                            else "starter" if _NBA_CONSENSUS_RANK.get(player_key, 999) <= 75
                            else "elite_role"
                        ),
                        "is_available": True,
                        "next_game": next_game_by_team.get(team_id, {}),
                    }
                )
        except Exception as e:
            log_lines.append(f"WARN roster_fetch_failed team={team_name} id={team_id} err={e}")

    # Sanity: ensure players are from active roster dataset and valid team pair
    cleaned = []
    for p in players:
        name_key = p["name"].lower()
        if name_key not in roster_names:
            excluded.append({"player_id": p["player_id"], "name": p["name"], "team_id": p["team_id"], "reasons": ["name_not_in_active_roster"]})
            log_lines.append(f"DROP {p['name']} ({p['team']}) -> name_not_in_active_roster")
            continue
        if (name_key, p["team_id"]) not in roster_team_pairs:
            excluded.append({"player_id": p["player_id"], "name": p["name"], "team_id": p["team_id"], "reasons": ["team_mismatch"]})
            log_lines.append(f"DROP {p['name']} ({p['team']}) -> team_mismatch")
            continue
        cleaned.append(p)
    cleaned.sort(key=lambda x: (x.get("consensus_rank", 999), -x["top50_score"]))
    _append_filter_log(log_lines)
    return {"players": cleaned[:100], "excluded": excluded}


def _synthetic_prop_lines(league: str, players: List[Dict]) -> List[Dict]:
    """One synthetic line per player for local/dev when odds API is unused or unavailable."""
    lines = []
    prop_types = _LEAGUE_PROP_TYPES.get(league, ["points", "rebounds", "assists"])
    for p in players:
        prop_type = random.choice(prop_types)
        low, high = _PROP_LINE_RANGES.get(prop_type, (5.5, 25.5))
        _line = _to_half_step(random.uniform(low, high))
        lines.append(
            {
                "player_id": p["player_id"],
                "prop_type": prop_type,
                "line": _line,
                "line_for_calc": _line,
                "line_source": "synthetic",
                "odds_over": random.choice([-130, -120, -110, 100, 110]),
                "odds_under": random.choice([-130, -120, -110, 100, 110]),
            }
        )
    return lines


def _nearest_half(p: float) -> float:
    """Nearest X.5 line to the projection (how books set most player lines)."""
    return math.floor(max(0.0, p)) + 0.5


def _ladder_line(prop_type: str, projection: float) -> float:
    p = float(projection)
    if prop_type == "points":
        # Real points lines are X.5 near the projection — NOT multiples of 5.
        return round(_nearest_half(p), 1)
    if prop_type in ("rebounds", "assists"):
        # No artificial +1.5 offset (that biased every pick to UNDER).
        return round(_nearest_half(p), 1)
    if prop_type == "threes":
        return round(max(0.5, min(5.5, _nearest_half(p))), 1)
    return round(_nearest_half(p), 1)


# Combined ("parlay") basketball props — projection = sum of the components.
_NBA_COMBINED_PROPS = {
    "pts_reb":     ("points", "rebounds"),
    "pts_ast":     ("points", "assists"),
    "reb_ast":     ("rebounds", "assists"),
    "pts_reb_ast": ("points", "rebounds", "assists"),
}


def _internal_nba_prop_lines(players: List[Dict]) -> List[Dict]:
    out = []
    for p in players:
        weighted = p.get("stats_weighted") or {}
        glog = p.get("game_stats_last15") or {}

        def _wv(stat):
            return float(weighted.get(stat, 0.0) or 0.0)

        # ── Individual props (every stat the player actually produces) ──
        for prop_type in ("points", "rebounds", "assists", "threes"):
            projection = _wv(prop_type)
            if projection <= 0.0:
                continue
            line = _ladder_line(prop_type, projection)
            out.append({
                "player_id": p["player_id"], "prop_type": prop_type,
                "line": line, "line_for_calc": line, "line_source": "internal_odds_api",
                "projection": round(projection, 2),
                "odds_over": random.choice([-130, -120, -110, 100, 110]),
                "odds_under": random.choice([-130, -120, -110, 100, 110]),
            })

        # ── Combined props (Pts+Reb, Pts+Ast, Reb+Ast, Pts+Reb+Ast) ──
        for combo, parts in _NBA_COMBINED_PROPS.items():
            projection = sum(_wv(s) for s in parts)
            if projection <= 0.0:
                continue
            # Per-game combined values so L-5/L-10/Season hit rates work.
            arrays = [glog.get(s) or [] for s in parts]
            if all(arrays):
                n = min(len(a) for a in arrays)
                glog[combo] = [round(sum(a[i] for a in arrays), 1) for i in range(n)]
            line = _ladder_line("points", projection)  # combined lines use the points ladder
            out.append({
                "player_id": p["player_id"], "prop_type": combo,
                "line": line, "line_for_calc": line, "line_source": "internal_odds_api",
                "projection": round(projection, 2),
                "odds_over": random.choice([-130, -120, -110, 100, 110]),
                "odds_under": random.choice([-130, -120, -110, 100, 110]),
            })
        p["game_stats_last15"] = glog
    return out


def _mlb_combined_hrr(p: Dict) -> Dict | None:
    """Build a combined Hits+Runs+RBIs (H+R+RBI) prop for an MLB batter from its
    per-game gamelog. Returns None when component gamelogs are missing so we
    never invent a projection. The line is the nearest X.5 to our own projection
    so OVER/UNDER splits naturally (a real over/under, not a milestone)."""
    glog = p.get("game_stats_last15") or {}
    parts = ("hits", "runs", "rbis")
    arrays = [glog.get(s) or [] for s in parts]
    if not all(arrays):
        return None
    n = min(len(a) for a in arrays)
    if n < 3:
        return None
    combined = [round(sum(a[i] for a in arrays), 1) for i in range(n)]
    glog["h_r_rbi"] = combined
    p["game_stats_last15"] = glog
    projection = sum(combined) / len(combined)
    if projection <= 0.0:
        return None
    line = math.floor(projection) + 0.5
    return {
        "player_id": p["player_id"],
        "prop_type": "h_r_rbi",
        "line": line,
        "line_for_calc": line,
        "line_source": "internal_odds_api",
        "projection": round(projection, 2),
        "odds_over": random.choice([-130, -120, -110, 100, 110]),
        "odds_under": random.choice([-130, -120, -110, 100, 110]),
    }


# Milestone (anytime / 1+, over-only) MLB prop types keep a fixed 0.5 line to
# preserve existing behaviour. Every other MLB prop is a real over/under whose
# line is the nearest X.5 to the player's actual recent-game average.
_MLB_MILESTONE_TYPES = frozenset({"hits", "runs", "rbis", "home_runs"})


def _arr_mean(vals) -> float | None:
    vals = [v for v in (vals or []) if v is not None]
    return (sum(vals) / len(vals)) if vals else None


def _internal_mlb_prop_lines(players: List[Dict]) -> List[Dict]:
    """Generate MLB props for every applicable type per player, using REAL
    per-game gamelog averages as the projection. A prop is emitted only when the
    player actually has game-log data for that stat — never invented. Batters
    also get the combined H+R+RBI prop."""
    out: List[Dict] = []
    for p in players:
        pos_type = p.get("position_type", "")
        glog = p.get("game_stats_last15") or {}
        types = (_LEAGUE_PROP_TYPES["MLB_PITCHER"] if pos_type == "pitcher"
                 else _LEAGUE_PROP_TYPES["MLB_BATTER"])
        for prop_type in types:
            proj = _arr_mean(glog.get(prop_type))
            if proj is None:
                continue  # no real data for this stat -> skip, never fabricate
            line = 0.5 if prop_type in _MLB_MILESTONE_TYPES else math.floor(max(0.0, proj)) + 0.5
            out.append({
                "player_id": p["player_id"],
                "prop_type": prop_type,
                "line": line,
                "line_for_calc": line,
                "line_source": "internal_odds_api",
                "projection": round(float(proj), 2),
                "odds_over": random.choice([-130, -120, -110, 100, 110]),
                "odds_under": random.choice([-130, -120, -110, 100, 110]),
            })
        if pos_type != "pitcher":
            combo = _mlb_combined_hrr(p)
            if combo:
                out.append(combo)
    return out


def _internal_generic_prop_lines(league: str, players: List[Dict]) -> List[Dict]:
    out = []
    default_prop_types = _LEAGUE_PROP_TYPES.get(league, ["points"])
    for p in players:
        # Position-aware prop type selection
        pos_type = p.get("position_type", "")
        if league == "MLB":
            if pos_type == "pitcher":
                prop_types = _LEAGUE_PROP_TYPES.get("MLB_PITCHER", ["strikeouts"])
            else:
                prop_types = _LEAGUE_PROP_TYPES.get("MLB_BATTER",
                                                     ["hits", "runs", "rbis", "home_runs"])
        elif league in ("NFL", "NCAAF"):
            qt = "NFL_QB" if league == "NFL" else "NCAAF_QB"
            sk = "NFL_SKILL" if league == "NFL" else "NCAAF_SKILL"
            if pos_type == "qb":
                prop_types = _LEAGUE_PROP_TYPES.get(qt, ["passing_yards"])
            else:
                prop_types = _LEAGUE_PROP_TYPES.get(sk,
                                                     ["rushing_yards", "receiving_yards",
                                                      "receptions"])
        else:
            prop_types = default_prop_types

        prop_type = random.choice(prop_types)
        # Sport-specific range takes priority over the shared default
        low, high = (_SPORT_PROP_LINE_RANGES.get(league, {}).get(prop_type)
                     or _PROP_LINE_RANGES.get(prop_type, (1.5, 20.5)))
        minutes = _clamp(float(p.get("projected_minutes", 24.0) or 24.0), 8.0, 42.0)
        usage = _clamp(float(p.get("usage_score", 0.25) or 0.25), 0.05, 1.0)
        # Normalize to 0-1 so every prop type scales inside its own realistic band.
        level = _clamp((minutes / 42.0) * 0.35 + usage * 0.65, 0.05, 0.98)
        projection = low + (high - low) * level
        projection += random.uniform(-(high - low) * 0.08, (high - low) * 0.08)
        projection = _clamp(projection, low, high)
        # Use discrete market line if defined (e.g. NHL points = 0.5 or 1.5 only)
        snapped = _pick_market_line(league, prop_type, projection)
        if snapped is not None:
            line = snapped
        else:
            line = _to_half_step(_clamp(projection + random.uniform(-0.9, 0.9), low, high))
        out.append(
            {
                "player_id": p["player_id"],
                "prop_type": prop_type,
                "line": line,
                "line_for_calc": line,
                "line_source": "internal_odds_api",
                "projection": round(float(projection), 2),  # real per-player number (line stays book-standard)
                "odds_over": random.choice([-130, -120, -110, 100, 110]),
                "odds_under": random.choice([-130, -120, -110, 100, 110]),
            }
        )
        # MLB batters also carry a combined H+R+RBI prop (real O/U from gamelog).
        if league == "MLB" and pos_type != "pitcher":
            combo = _mlb_combined_hrr(p)
            if combo:
                out.append(combo)
    return out


def _position_prop_type(league: str, player: Dict) -> str:
    """Return a position-appropriate prop type for a player."""
    pos_type = player.get("position_type", "")
    if league == "MLB":
        types = (_LEAGUE_PROP_TYPES["MLB_PITCHER"] if pos_type == "pitcher"
                 else _LEAGUE_PROP_TYPES["MLB_BATTER"])
    elif league == "NFL":
        types = (_LEAGUE_PROP_TYPES["NFL_QB"] if pos_type == "qb"
                 else _LEAGUE_PROP_TYPES["NFL_SKILL"])
    elif league == "NCAAF":
        types = (_LEAGUE_PROP_TYPES["NCAAF_QB"] if pos_type == "qb"
                 else _LEAGUE_PROP_TYPES["NCAAF_SKILL"])
    else:
        types = _LEAGUE_PROP_TYPES.get(league, ["points"])
    return random.choice(types)


def fetch_prop_lines(league: str, players: List[Dict]) -> List[Dict]:
    if not players:
        return []
    # Fast NBA path: use already-fetched gamelog-based weighted projections
    # so /props doesn't stall on another full network fan-out.
    if league == "NBA":
        fast = _internal_nba_prop_lines(players)
        if fast:
            return fast
    # Fast MLB path: full per-type props from the already-fetched gamelogs
    # (real averages). Falls through to the generic path only if no player has
    # gamelog data (e.g. offseason / API outage).
    if league == "MLB":
        fast = _internal_mlb_prop_lines(players)
        if fast:
            return fast
    if ODDS_ENGINE_URL:
        try:
            payload = {
                "sport": league,
                "items": [
                    {
                        "player_id": p.get("player_id"),
                        "player_name": p.get("name"),
                        "team": p.get("team"),
                        "prop_type": _position_prop_type(league, p),
                    }
                    for p in players
                ],
            }
            resp = requests.post(f"{ODDS_ENGINE_URL}/player-props/batch", json=payload, timeout=8)
            resp.raise_for_status()
            body = resp.json()
            items = body.get("props") or []
            by_key = {(str(x.get("player_id")), str(x.get("prop_type"))): x for x in items}
            out = []
            for src in payload["items"]:
                k = (str(src["player_id"]), str(src["prop_type"]))
                row = by_key.get(k)
                if not row:
                    continue
                out.append(
                    {
                        "player_id": src["player_id"],
                        "prop_type": src["prop_type"],
                        "line": _to_half_step(row.get("line")) if row.get("line") is not None else None,
                        "line_for_calc": _to_half_step(row.get("line")) if row.get("line") is not None else None,
                        "line_source": row.get("line_source", "internal_odds_api"),
                        "odds_over": row.get("odds_over", -110),
                        "odds_under": row.get("odds_under", -110),
                    }
                )
            if out:
                return out
        except Exception:
            pass
    internal = _internal_generic_prop_lines(league, players)
    if internal:
        return internal
    if not ODDS_API_KEY:
        return _synthetic_prop_lines(league, players)
    # Odds key set: probe API once; real market parsing is not wired yet — always fall back to synthetic.
    try:
        resp = requests.get(f"{ODDS_API_BASE}/sports", params={"apiKey": ODDS_API_KEY}, timeout=10)
        resp.raise_for_status()
    except Exception:
        pass
    return _synthetic_prop_lines(league, players)


def implied_prob(american_odds: float) -> float:
    if american_odds < 0:
        return abs(american_odds) / (abs(american_odds) + 100.0)
    return 100.0 / (american_odds + 100.0)


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def poisson_cdf(k: int, lam: float) -> float:
    term = math.exp(-lam)
    c = term
    for i in range(1, max(k + 1, 1)):
        term *= lam / i
        c += term
    return c
