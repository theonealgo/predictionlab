import math
import os
import random
import re
import statistics
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from .config import (
    CACHE_TTL_SECONDS,
    ESPN_EVENTS_CAP,
    ESPN_PROP_PROVIDER_ID,
    LEAGUE_CONFIG,
    ODDS_API_BASE,
    ODDS_API_KEY,
    ODDS_ENGINE_URL,
    ODDS_EVENTS_CAP,
    REAL_LINES_TTL,
    TOP_PLAYER_POOL,
)

# Realistic "minutes" (or ice-time for hockey) ranges per sport.
# NHL skaters average 12-22 min of ice time; using NBA values (24-40) was the
# root cause of absurd projections like "9 assists in a game".
_LEAGUE_MINUTES_RANGE = {
    "NBA":   (24.0, 40.0),
    "WNBA":  (18.0, 36.0),
    "NCAAB": (20.0, 40.0),
    "NCAAW": (18.0, 38.0),
    "NHL":   (12.0, 22.0),   # ice time for skaters
    "MLB":   (1.0,  1.0),    # plate appearances, not meaningful as "minutes"
    "NFL":   (20.0, 65.0),   # snaps converted to minute-equivalent
    "NCAAF": (18.0, 60.0),
    "SOCCER":(60.0, 90.0),   # match minutes
}

_LEAGUE_PROP_TYPES = {
    "NBA": ["points", "rebounds", "assists", "threes"],
    "WNBA": ["points", "rebounds", "assists", "threes"],
    "NCAAB": ["points", "rebounds", "assists", "threes"],
    "NCAAW": ["points", "rebounds", "assists", "threes"],
    "NHL": ["shots_on_goal", "points", "assists", "goals"],
    "MLB": ["hits", "strikeouts", "runs", "rbis", "home_runs"],
    "NFL": ["passing_yards", "rushing_yards", "receiving_yards", "receptions"],
    "NCAAF": ["passing_yards", "rushing_yards", "receiving_yards", "receptions"],
    "SOCCER": ["shots", "shots_on_target", "goals", "assists"],
}

_PROP_LINE_RANGES = {
    "points": (8.5, 29.5),
    "rebounds": (3.5, 12.5),
    "assists": (2.5, 10.5),
    "threes": (0.5, 4.5),
    "shots_on_goal": (1.5, 4.5),
    "goals": (0.5, 1.5),
    "hits": (0.5, 2.5),
    "strikeouts": (2.5, 8.5),
    "runs": (0.5, 1.5),
    "rbis": (0.5, 1.5),
    "home_runs": (0.5, 1.5),
    "passing_yards": (185.5, 315.5),
    "rushing_yards": (35.5, 95.5),
    "receiving_yards": (35.5, 105.5),
    "receptions": (2.5, 8.5),
    "shots": (1.5, 4.5),
    "shots_on_target": (0.5, 2.5),
}

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

# ── The Odds API (real sportsbook player props) ───────────────────────────
# League -> The Odds API sport key.
_ODDS_API_SPORT_KEYS = {
    "NBA": "basketball_nba",
    "WNBA": "basketball_wnba",
    "NCAAB": "basketball_ncaab",
    "NCAAW": "basketball_ncaaw",
    "NHL": "icehockey_nhl",
    "MLB": "baseball_mlb",
    "NFL": "americanfootball_nfl",
    "NCAAF": "americanfootball_ncaaf",
    "SOCCER": "soccer_epl",
}
# League -> {internal prop_type: The Odds API market key}.
_ODDS_API_MARKETS = {
    "NBA":    {"points": "player_points", "rebounds": "player_rebounds", "assists": "player_assists", "threes": "player_threes"},
    "WNBA":   {"points": "player_points", "rebounds": "player_rebounds", "assists": "player_assists", "threes": "player_threes"},
    "NCAAB":  {"points": "player_points", "rebounds": "player_rebounds", "assists": "player_assists", "threes": "player_threes"},
    "NCAAW":  {"points": "player_points", "rebounds": "player_rebounds", "assists": "player_assists", "threes": "player_threes"},
    "NHL":    {"points": "player_points", "assists": "player_assists", "goals": "player_goals", "shots_on_goal": "player_shots_on_goal"},
    "MLB":    {"hits": "batter_hits", "runs": "batter_runs_scored", "rbis": "batter_rbis", "home_runs": "batter_home_runs", "strikeouts": "pitcher_strikeouts", "walks": "batter_walks"},
    "NFL":    {"passing_yards": "player_pass_yds", "rushing_yards": "player_rush_yds", "receiving_yards": "player_reception_yds", "receptions": "player_receptions"},
    "NCAAF":  {"passing_yards": "player_pass_yds", "rushing_yards": "player_rush_yds", "receiving_yards": "player_reception_yds", "receptions": "player_receptions"},
    "SOCCER": {"goals": "player_goals", "assists": "player_assists", "shots": "player_shots", "shots_on_target": "player_shots_on_target"},
}
# league -> {"ts": epoch, "rows": [raw consensus rows]} to protect Odds API quota.
_REAL_LINES_CACHE: Dict[str, Dict] = {}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _to_half_step(v: float) -> float:
    return round(round(float(v) * 2.0) / 2.0, 1)


def _parse_attempts(value: str) -> float:
    if not value or "-" not in value:
        return 0.0
    try:
        return float(value.split("-")[1])
    except Exception:
        return 0.0


def _num(value: str) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _espn_scoreboard_url(league: str) -> str:
    cfg = LEAGUE_CONFIG[league]
    return f"https://site.api.espn.com/apis/site/v2/sports/{cfg['espn_sport']}/{cfg['espn_league']}/scoreboard"


def fetch_schedule_and_teams(league: str, target_date=None) -> List[Dict]:
    url = _espn_scoreboard_url(league)
    def _rows_for(ds: str) -> List[Dict]:
        resp = requests.get(url, params={"dates": ds}, timeout=12)
        resp.raise_for_status()
        events = resp.json().get("events", [])
        rows = []
        for ev in events:
            comp = (ev.get("competitions") or [{}])[0]
            teams = comp.get("competitors") or []
            if len(teams) < 2:
                continue
            home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
            away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
            rows.append(
                {
                    "event_id": ev.get("id", ""),
                    "start_time": ev.get("date"),
                    "home_team": home.get("team", {}).get("displayName", "Home"),
                    "away_team": away.get("team", {}).get("displayName", "Away"),
                    "home_team_id": str(home.get("team", {}).get("id", "")),
                    "away_team_id": str(away.get("team", {}).get("id", "")),
                }
            )
        return rows
    try:
        now_et = datetime.now(ZoneInfo("America/New_York"))
        use_date = target_date or now_et.date()
        rows = _rows_for(use_date.strftime("%Y%m%d"))
        if rows:
            return rows
        for d in range(1, 8):
            probe = (use_date + timedelta(days=d)).strftime("%Y%m%d")
            rows = _rows_for(probe)
            if rows:
                return rows
        # Use scoreboard calendar to auto-activate when the next season date arrives.
        # This keeps each sport "ready" without manual date updates.
        resp = requests.get(url, timeout=12)
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
            rows = _rows_for(d.strftime("%Y%m%d"))
            if rows:
                return rows
        events = body.get("events", [])
        rows = []
        for ev in events:
            comp = (ev.get("competitions") or [{}])[0]
            teams = comp.get("competitors") or []
            if len(teams) < 2:
                continue
            home = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
            away = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
            rows.append(
                {
                    "event_id": ev.get("id", ""),
                    "start_time": ev.get("date"),
                    "home_team": home.get("team", {}).get("displayName", "Home"),
                    "away_team": away.get("team", {}).get("displayName", "Away"),
                    "home_team_id": str(home.get("team", {}).get("id", "")),
                    "away_team_id": str(away.get("team", {}).get("id", "")),
                }
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


def build_top_players(league: str, schedule_rows: List[Dict]) -> List[Dict]:
    cfg = LEAGUE_CONFIG.get(league, {})
    espn_sport = cfg.get("espn_sport", "")
    espn_league = cfg.get("espn_league", "")
    players = []
    seen_player_ids = set()
    seen_name_team = set()
    idx = 1

    def _add_player(player_id: str, name: str, team: str):
        nonlocal idx
        if not name or not team:
            return
        key_id = (player_id or "").strip()
        key_name = (name.strip().lower(), team.strip().lower())
        if key_id and key_id in seen_player_ids:
            return
        if key_name in seen_name_team:
            return
        _min_lo, _min_hi = _LEAGUE_MINUTES_RANGE.get(league, (24.0, 40.0))
        projected_minutes = random.uniform(_min_lo, _min_hi)
        usage = random.uniform(0.35, 1.0)
        prop_frequency = random.uniform(0.4, 1.0)
        score = projected_minutes * 0.45 + usage * 30 + prop_frequency * 25
        final_id = key_id if key_id else f"{league}-{idx}"
        players.append(
            {
                "player_id": final_id,
                "name": name,
                "team": team,
                "league": league,
                "projected_minutes": round(projected_minutes, 1),
                "usage_score": round(usage, 3),
                "prop_frequency": round(prop_frequency, 3),
                "top50_score": round(score, 2),
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
            for a in payload.get("athletes") or []:
                # MLB roster shape often returns grouped entries:
                # athletes: [{position: {...}, items: [{id, fullName, ...}, ...]}]
                if isinstance(a, dict) and isinstance(a.get("items"), list):
                    for it in a.get("items") or []:
                        full_name = (it.get("fullName") or it.get("displayName") or "").strip()
                        a_id = str(it.get("id") or "")
                        _add_player(a_id, full_name, team_name)
                    continue
                full_name = (a.get("fullName") or a.get("displayName") or "").strip()
                a_id = str(a.get("id") or "")
                _add_player(a_id, full_name, team_name)
            for group in payload.get("athletesByPosition") or []:
                for a in group.get("athletes") or []:
                    full_name = (a.get("fullName") or a.get("displayName") or "").strip()
                    a_id = str(a.get("id") or "")
                    _add_player(a_id, full_name, team_name)
        except Exception:
            return

    for game in schedule_rows[:25]:
        _fetch_roster(game.get("home_team_id", ""), game["home_team"])
        _fetch_roster(game.get("away_team_id", ""), game["away_team"])
    players.sort(key=lambda x: x["top50_score"], reverse=True)
    if players:
        return players[:TOP_PLAYER_POOL]
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
            if len(synthetic) >= TOP_PLAYER_POOL:
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
            thr = _parse_attempts(stats[idx["3PT"]]) if "3PT" in idx and idx["3PT"] < len(stats) else 0.0
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
            next_game_by_team[g["home_team_id"]] = {"opponent": g.get("away_team", ""), "start_time": g.get("start_time", "")}
        if g.get("away_team_id"):
            team_ids.add((g["away_team_id"], g["away_team"]))
            next_game_by_team[g["away_team_id"]] = {"opponent": g.get("home_team", ""), "start_time": g.get("start_time", "")}

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


def _ladder_line(prop_type: str, projection: float) -> float:
    p = float(projection)
    if prop_type == "points":
        return float(int(round(p / 5.0) * 5))
    if prop_type in ("rebounds", "assists"):
        return round((math.floor(max(0.0, p) / 2.0) * 2.0) + 1.5, 1)
    if prop_type == "threes":
        return round(max(0.5, min(6.5, math.floor(max(0.0, p)) + 0.5)), 1)
    return round(p, 1)


def _internal_nba_prop_lines(players: List[Dict]) -> List[Dict]:
    out = []
    for p in players:
        weighted = p.get("stats_weighted") or {}
        available = [pt for pt in ("points", "rebounds", "assists", "threes") if float(weighted.get(pt, 0.0) or 0.0) > 0.0]
        if not available:
            continue
        prop_type = random.choice(available)
        projection = float(weighted.get(prop_type, 0.0) or 0.0)
        if projection <= 0.0:
            continue
        line = _ladder_line(prop_type, projection)
        out.append(
            {
                "player_id": p["player_id"],
                "prop_type": prop_type,
                "line": line,
                "line_for_calc": line,
                "line_source": "internal_odds_api",
                "projection": _to_half_step(projection),
                "odds_over": random.choice([-130, -120, -110, 100, 110]),
                "odds_under": random.choice([-130, -120, -110, 100, 110]),
            }
        )
    return out


def _internal_generic_prop_lines(league: str, players: List[Dict]) -> List[Dict]:
    out = []
    prop_types = _LEAGUE_PROP_TYPES.get(league, ["points"])
    for p in players:
        prop_type = random.choice(prop_types)
        low, high = _PROP_LINE_RANGES.get(prop_type, (1.5, 20.5))
        minutes = _clamp(float(p.get("projected_minutes", 24.0) or 24.0), 8.0, 42.0)
        usage = _clamp(float(p.get("usage_score", 0.25) or 0.25), 0.05, 1.0)
        # Normalize to 0-1 so every prop type scales inside its own realistic band.
        level = _clamp((minutes / 42.0) * 0.35 + usage * 0.65, 0.05, 0.98)
        projection = low + (high - low) * level
        projection += random.uniform(-(high - low) * 0.08, (high - low) * 0.08)
        projection = _clamp(projection, low, high)
        # Build a market line near projection with directional noise so picks aren't uniform.
        line = _to_half_step(_clamp(projection + random.uniform(-0.9, 0.9), low, high))
        out.append(
            {
                "player_id": p["player_id"],
                "prop_type": prop_type,
                "line": line,
                "line_for_calc": line,
                "line_source": "internal_odds_api",
                "projection": _to_half_step(projection),
                "odds_over": random.choice([-130, -120, -110, 100, 110]),
                "odds_under": random.choice([-130, -120, -110, 100, 110]),
            }
        )
    return out


def _resolve_odds_api_key() -> str:
    """Odds API key from env, falling back to the committed project key so real
    lines work locally and on Render without extra setup."""
    if ODDS_API_KEY:
        return ODDS_API_KEY
    try:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from theodds_api import TheOddsAPI
        return (TheOddsAPI().api_key or "").strip()
    except Exception:
        return ""


def _closest_price(entries: List, consensus: float) -> int:
    """Representative American price: the book price whose point is closest to
    the consensus line (keeps a coherent number instead of averaging odds)."""
    cand = [(pt, pr) for (pt, pr) in entries if pr is not None]
    if not cand:
        return -110
    cand.sort(key=lambda t: abs((t[0] if t[0] is not None else consensus) - consensus))
    return int(round(cand[0][1]))


def _match_real_rows(raw_rows: List[Dict], players: List[Dict]) -> List[Dict]:
    """Attach real ESPN/DraftKings lines to players in our pool.

    Raw rows carry the ESPN athlete id in `player_id` (the preferred match key,
    since our pool ids are ESPN athlete ids) and an optional `norm_name`
    fallback. Emits internal line dicts tagged line_source='espn_props'.
    """
    by_id: Dict[str, Dict] = {}
    by_name: Dict[str, Dict] = {}
    for p in players:
        pid = str(p.get("player_id", "")).strip()
        if pid:
            by_id.setdefault(pid, p)
        by_name.setdefault(_norm_name(p.get("name", "")), p)
    out = []
    for row in raw_rows:
        p = None
        rid = str(row.get("player_id", "")).strip()
        if rid and rid in by_id:
            p = by_id[rid]
        if p is None:
            nm = row.get("norm_name")
            if nm:
                p = by_name.get(nm)
        if not p:
            continue
        line = row["line"]
        out.append(
            {
                "player_id": p["player_id"],
                "prop_type": row["prop_type"],
                "line": line,
                "line_for_calc": line,
                "line_source": "espn_props",
                "odds_over": row["odds_over"],
                "odds_under": row["odds_under"],
            }
        )
    return out


def _american_to_int(v) -> Optional[int]:
    """Normalize an ESPN american-odds value (number, '+150', 'EVEN') to int."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().upper()
        if s in ("EVEN", "EV", "PK"):
            return 100
        try:
            return int(round(float(s)))
        except Exception:
            return None
    try:
        return int(round(float(v)))
    except Exception:
        return None


def _espn_to_internal_prop(league: str, name: str) -> Optional[str]:
    """Map an ESPN propBet type name (e.g. 'Total Strikeouts', 'Points') to our
    internal prop_type, restricted to the types we grade for `league`.

    Returns None for unrecognized names, combined/alt markets ('+'), pitcher
    'allowed' variants, or anything not in the league's graded set. Keyword
    order guards against substring collisions (e.g. 'three point' contains
    'point'), so more specific types are tested first.
    """
    if not name:
        return None
    n = name.lower()
    if "+" in n or "allowed" in n:  # combos / pitcher 'hits allowed' etc.
        return None
    allowed = set(_LEAGUE_PROP_TYPES.get(league, []))
    if not allowed:
        return None
    checks = [
        ("strikeouts", ("strikeout",)),
        ("home_runs", ("home run",)),
        ("rbis", ("rbi", "runs batted in")),
        ("shots_on_goal", ("shots on goal", "shot on goal")),
        ("threes", ("three point", "3 point", "3-point", "3pt", "threes")),
        ("rebounds", ("rebound",)),
        ("assists", ("assist",)),
        ("hits", ("hits",)),
        ("runs", ("runs", "run ")),
        ("goals", ("goal",)),
        ("points", ("points", "point")),
    ]
    for internal, keywords in checks:
        if internal not in allowed:
            continue
        if any(kw in n for kw in keywords):
            return internal
    return None


def _espn_pick_provider(odds_base_url: str) -> Optional[str]:
    """Return the odds provider id to page prop bets from at a competition's
    /odds endpoint. Prefers DraftKings (ESPN_PROP_PROVIDER_ID); otherwise the
    first provider that exposes a numeric id."""
    try:
        r = requests.get(odds_base_url, params={"lang": "en", "region": "us"}, timeout=10)
        if r.status_code != 200:
            return None
        items = (r.json() or {}).get("items") or []
    except Exception:
        return None
    ids = []
    for it in items:
        ref = it.get("$ref") or ""
        m = re.search(r"/odds/(\d+)", ref)
        if m:
            ids.append(m.group(1))
        else:
            prov = it.get("provider") or {}
            pid = str(prov.get("id") or "").strip()
            if pid:
                ids.append(pid)
    if not ids:
        return None
    if ESPN_PROP_PROVIDER_ID in ids:
        return ESPN_PROP_PROVIDER_ID
    return ids[0]


def _espn_fetch_prop_rows(league: str, espn_sport: str, espn_league: str) -> List[Dict]:
    """Fetch real DraftKings player-prop lines from ESPN's free core API.

    Returns raw rows shaped for _match_real_rows: {player_id (ESPN athlete id),
    norm_name (None), prop_type, line, odds_over, odds_under}. One row per
    (athlete, prop_type). Each (athlete, prop_type) appears twice in the feed
    (Over then Under) with the same target; we keep the shared line and pair
    the two prices in feed order.
    """
    sb_url = f"https://site.api.espn.com/apis/site/v2/sports/{espn_sport}/{espn_league}/scoreboard"
    try:
        now_et = datetime.now(ZoneInfo("America/New_York"))
        r = requests.get(sb_url, params={"dates": now_et.strftime("%Y%m%d")}, timeout=12)
        r.raise_for_status()
        events = (r.json() or {}).get("events") or []
    except Exception:
        return []

    def _not_final(ev) -> bool:
        st = ((((ev.get("status") or {}).get("type")) or {}).get("state") or "").lower()
        return st != "post"

    ev_ids = [str(e.get("id")) for e in events if e.get("id") and _not_final(e)]
    if not ev_ids:
        ev_ids = [str(e.get("id")) for e in events if e.get("id")]
    ev_ids = ev_ids[:ESPN_EVENTS_CAP]
    if not ev_ids:
        return []

    core = "https://sports.core.api.espn.com/v2/sports"
    # (athlete_id, prop_type) -> {"line": float, "prices": [int, ...]}
    agg: Dict = {}
    for eid in ev_ids:
        # In these leagues the competition id equals the event id.
        comp_base = f"{core}/{espn_sport}/leagues/{espn_league}/events/{eid}/competitions/{eid}"
        pid = _espn_pick_provider(f"{comp_base}/odds")
        if not pid:
            continue
        page = 1
        page_count = 1
        while page <= page_count and page <= 12:
            url = f"{comp_base}/odds/{pid}/propBets"
            try:
                r = requests.get(
                    url,
                    params={"limit": 100, "page": page, "lang": "en", "region": "us"},
                    timeout=12,
                )
                if r.status_code != 200:
                    break
                body = r.json() or {}
            except Exception:
                break
            try:
                page_count = int(body.get("pageCount") or 1)
            except Exception:
                page_count = 1
            for it in body.get("items") or []:
                aref = ((it.get("athlete") or {}).get("$ref")) or ""
                m = re.search(r"/athletes/(\d+)", aref)
                if not m:
                    continue
                athlete_id = m.group(1)
                type_name = ((it.get("type") or {}).get("name")) or it.get("name") or ""
                prop_type = _espn_to_internal_prop(league, type_name)
                if not prop_type:
                    continue
                target = ((it.get("current") or {}).get("target") or {}).get("value")
                if target is None:
                    continue
                try:
                    target_f = float(target)
                except Exception:
                    continue
                price_int = _american_to_int(((it.get("odds") or {}).get("american") or {}).get("value"))
                key = (athlete_id, prop_type)
                slot = agg.get(key)
                if slot is None:
                    agg[key] = {"line": target_f, "prices": ([price_int] if price_int is not None else [])}
                elif abs(target_f - slot["line"]) < 1e-6 and price_int is not None and len(slot["prices"]) < 2:
                    slot["prices"].append(price_int)
            page += 1

    rows = []
    for (athlete_id, prop_type), slot in agg.items():
        prices = slot["prices"]
        odds_over = prices[0] if len(prices) >= 1 else -110
        odds_under = prices[1] if len(prices) >= 2 else -110
        rows.append(
            {
                "player_id": athlete_id,
                "norm_name": None,
                "prop_type": prop_type,
                "line": _to_half_step(slot["line"]),
                "odds_over": odds_over,
                "odds_under": odds_under,
            }
        )
    return rows


def fetch_real_prop_lines(league: str, players: List[Dict]) -> Optional[List[Dict]]:
    """Real DraftKings player-prop lines for `league` from ESPN's free core API.

    Returns line dicts (line_source='espn_props') for players present in our
    pool, or None when unavailable (off-season, no scheduled games, no player
    props for the slate, or any network error) so the caller can fall back to
    simulated lines cleanly. Uses the same free ESPN feed the rest of the app
    already relies on — no paid API key and no monthly request cap.
    """
    if not players:
        return None
    cfg = LEAGUE_CONFIG.get(league, {})
    espn_sport = cfg.get("espn_sport", "")
    espn_league = cfg.get("espn_league", "")
    if not (espn_sport and espn_league):
        return None

    now = time.time()
    cached = _REAL_LINES_CACHE.get(league)
    if cached and (now - cached["ts"]) < REAL_LINES_TTL:
        return _match_real_rows(cached["rows"], players) or None

    try:
        raw_rows = _espn_fetch_prop_rows(league, espn_sport, espn_league)
    except Exception:
        raw_rows = []
    # Cache even an empty result so we don't repeatedly hammer ESPN when a
    # league currently has no player props (off-season / uncovered slate).
    _REAL_LINES_CACHE[league] = {"ts": now, "rows": raw_rows}
    if not raw_rows:
        return None
    return _match_real_rows(raw_rows, players) or None


def fetch_prop_lines(league: str, players: List[Dict], use_real: bool = True) -> List[Dict]:
    if not players:
        return []
    # 1) Real DraftKings lines (ESPN free core API) — preferred for every league
    #    when building a live slate. Historical/grading rebuilds pass
    #    use_real=False (ESPN prop feeds only cover upcoming/live games).
    if use_real:
        real = fetch_real_prop_lines(league, players)
        if real:
            return real
    # 2) Fast NBA path: already-fetched gamelog-based weighted projections
    #    (dev / no-odds fallback so /props doesn't stall on another fan-out).
    if league == "NBA":
        fast = _internal_nba_prop_lines(players)
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
                        "prop_type": random.choice(_LEAGUE_PROP_TYPES.get(league, ["points"])),
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
