#!/usr/bin/env python3
"""
underdogs.bet - Multi-Sport Prediction Platform
==================================================
Complete platform with Dashboard, Predictions, and Results pages for all sports.
5-Model System: Glicko-2, TrueSkill, Elo, XGBoost, Ensemble
"""

from flask import Flask, render_template_string, request, jsonify, redirect, url_for
from flask_cors import CORS
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
import nfl_data_py as nfl
from nhlschedules import get_nhl_2025_schedule
import requests
from nba_sportsdata_api import NBASportsDataAPI
from nhl_api import NHLAPI
from value_predictor import ValuePredictor
from ats_system import ATSSystem

# V2 PREDICTION SYSTEM - Upgraded architecture
import os as _os_v2
_V2_BASE = _os_v2.path.dirname(_os_v2.path.abspath(__file__))
try:
    from prediction_system_v2 import AdvancedPredictor
    V2_PREDICTORS = {}
    # Load trained models for supported sports
    for sport in ['NHL', 'NFL', 'NBA', 'MLB', 'NCAAF', 'NCAAB']:
        try:
            _model_path = _os_v2.path.join(_V2_BASE, 'models', f'{sport}_v2')
            V2_PREDICTORS[sport] = AdvancedPredictor.load(sport, _model_path)
            print(f"✅ Loaded {sport} v2 predictor (Glicko-2 + Ensemble + Calibration)")
        except Exception as e:
            print(f"⚠️ {sport} v2 model not found at {_model_path}: {e}")
    HAS_V2_SYSTEM = len(V2_PREDICTORS) > 0
except ImportError as e:
    print(f"⚠️ V2 prediction system not available: {e}")
    V2_PREDICTORS = {}
    HAS_V2_SYSTEM = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

import time as _time
import copy as _copy

# ── Module-level HTTP request cache (15-min TTL) ──────────────────────────────
_API_CACHE: dict = {}
_API_TTL = 900  # seconds
_PREDICTIONS_CACHE: dict = {}
_PREDICTIONS_TTL_BY_SPORT = {
    'NHL': 180,
    'NBA': 180,
    'NCAAB': 180,
    'MLB': 240,
    'NFL': 300,
    'NCAAF': 300,
    'WNBA': 240,
}
_SPORT_RESULTS_CACHE: dict = {}
_SPORT_RESULTS_TTL_BY_SPORT = {
    'NHL': 300,
    'NBA': 240,
    'NCAAB': 240,
    'MLB': 300,
    'NCAAF': 300,
    'NFL': 300,
    'WNBA': 300,
}


def _cached_get(url: str, timeout: int = 10):
    """requests.get with 15-minute in-process cache."""
    now = _time.time()
    entry = _API_CACHE.get(url)
    if entry and (now - entry['ts']) < _API_TTL:
        return entry['data']
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        _API_CACHE[url] = {'data': data, 'ts': now}
        return data
    except Exception as exc:
        raise exc

CORE_API_SPORT_PATHS = {
    'NBA': ('basketball', 'nba'),
    'NHL': ('hockey', 'nhl'),
    'NFL': ('football', 'nfl'),
    'MLB': ('baseball', 'mlb'),
    'WNBA': ('basketball', 'wnba'),
    'NCAAB': ('basketball', 'mens-college-basketball'),
    'NCAAF': ('football', 'college-football'),
}


def _normalize_team_key(team_name: str) -> str:
    """Normalize team names for resilient cross-source matching."""
    if not team_name:
        return ''
    import re
    import unicodedata
    txt = unicodedata.normalize('NFKD', str(team_name))
    txt = txt.encode('ascii', 'ignore').decode('ascii')
    txt = txt.lower().replace('&', 'and')
    txt = re.sub(r'[^a-z0-9]+', '', txt)
    alias_map = {
        'utahhockeyclub': 'utahmammoth',
    }
    txt = alias_map.get(txt, txt)
    return txt


def _resolve_espn_event_id_by_matchup(sport: str, game_date: str, home_team: str, away_team: str):
    """
    Resolve ESPN event ID by matching date + teams from scoreboard API.
    Needed for sports where local game IDs are not ESPN event IDs (notably NHL).
    """
    sport_path = CORE_API_SPORT_PATHS.get(sport)
    if not sport_path:
        return None

    parsed = parse_date(str(game_date)) if game_date else None
    if not parsed:
        return None

    home_key = _normalize_team_key(home_team)
    away_key = _normalize_team_key(away_team)
    if not home_key or not away_key:
        return None

    sport_slug, league_slug = sport_path
    day_offsets = [0, -1, 1]

    for day_offset in day_offsets:
        check_dt = parsed + timedelta(days=day_offset)
        date_str = check_dt.strftime('%Y%m%d')
        scoreboard_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/{sport_slug}/"
            f"{league_slug}/scoreboard?dates={date_str}"
        )
        if sport == 'NCAAB':
            scoreboard_url += '&groups=50&limit=357'

        try:
            data = _cached_get(scoreboard_url, timeout=8)
            events = data.get('events', []) if isinstance(data, dict) else []
        except Exception:
            continue

        for event in events:
            competition = event.get('competitions', [{}])[0]
            competitors = competition.get('competitors', [])
            if len(competitors) != 2:
                continue

            home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
            away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
            if not home or not away:
                continue

            def _team_keys(competitor):
                team = competitor.get('team', {})
                vals = {
                    team.get('displayName'),
                    team.get('shortDisplayName'),
                    team.get('name'),
                    team.get('nickname'),
                    team.get('location'),
                    team.get('abbreviation'),
                }
                keys = {_normalize_team_key(v) for v in vals if v}
                return {k for k in keys if k}

            home_keys = _team_keys(home)
            away_keys = _team_keys(away)
            if home_key in home_keys and away_key in away_keys:
                event_id = str(event.get('id') or '').strip()
                if event_id:
                    return event_id

    return None


def _fetch_live_market_line(
    sport: str,
    game_id: str,
    game_date: str = None,
    home_team: str = None,
    away_team: str = None
):
    """
    Fetch market spread/total for a game from ESPN Core API.
    Returns {'spread': float|None, 'total': float|None, 'source': str} or None.
    """
    sport_path = CORE_API_SPORT_PATHS.get(sport)
    if not sport_path or not game_id:
        return None
    event_candidates = []
    raw_event_id = str(game_id).split('_')[-1]
    if raw_event_id:
        event_candidates.append(raw_event_id)

    # NHL uses local game IDs (e.g., NHL_2025021109) that don't map to ESPN events.
    # For those, resolve event ID via date + teams on ESPN scoreboard first.
    needs_matchup_lookup = (
        sport == 'NHL'
        and game_date
        and home_team
        and away_team
        and (not raw_event_id.startswith('401'))
    )
    mapped_event_id = (
        _resolve_espn_event_id_by_matchup(sport, game_date, home_team, away_team)
        if needs_matchup_lookup else None
    )
    if mapped_event_id:
        event_candidates = [mapped_event_id] + [eid for eid in event_candidates if eid != mapped_event_id]

    if not event_candidates:
        return None
        return None

    sport_slug, league_slug = sport_path
    for event_id in event_candidates:
        odds_url = (
            f"http://sports.core.api.espn.com/v2/sports/{sport_slug}/leagues/{league_slug}/"
            f"events/{event_id}/competitions/{event_id}/odds"
        )

        try:
            odds_data = _cached_get(odds_url, timeout=8)
            items = odds_data.get('items', []) if isinstance(odds_data, dict) else []
            if not items:
                continue

            chosen = None
            for item in items:
                if item.get('spread') is not None or item.get('overUnder') is not None:
                    chosen = item
                    break
            if chosen is None:
                chosen = items[0]

            def _to_num(v):
                try:
                    return float(v) if v is not None else None
                except Exception:
                    return None

            spread_val = _to_num(chosen.get('spread'))
            total_val = _to_num(chosen.get('overUnder'))
            if spread_val is None and total_val is None:
                continue

            return {
                'spread': spread_val,
                'total': total_val,
                'source': (
                    'ESPN Core API (matchup fallback)'
                    if mapped_event_id and str(event_id) == str(mapped_event_id)
                    else 'ESPN Core API (live fallback)'
                ),
            }
        except Exception:
            continue

    return None

app = Flask(__name__)
CORS(app, origins=[
    'https://underdogs.bet',
    'https://www.underdogs.bet',
    'http://localhost:3000',
    'http://localhost:5000',
])

@app.context_processor
def inject_globals():
    """Make stripe_donation_url available in every template automatically."""
    return {'stripe_donation_url': STRIPE_DONATION_URL}

@app.after_request
def add_header(response):
    """Add headers to allow iframe embedding from underdogs.bet"""
    response.headers['X-Frame-Options'] = 'ALLOWALL'
    response.headers['Content-Security-Policy'] = (
        "frame-ancestors 'self' https://underdogs.bet https://www.underdogs.bet "
        "http://localhost:3000"
    )
    return response

import os as _os
_DATA_DIR = '/data' if _os.path.isdir('/data') else '.'
DATABASE = _os.path.join(_DATA_DIR, 'sports_predictions_original.db')
# Absolute path to this file's directory — used for template loading
_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))

def log_site_visit(endpoint):
    """Track site visits for analytics"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        visit_date = datetime.now().strftime('%Y-%m-%d')
        ip_address = request.remote_addr if request else None
        user_agent = request.headers.get('User-Agent') if request else None
        
        cursor.execute('''
            INSERT INTO site_visits (visit_date, ip_address, user_agent, endpoint)
            VALUES (?, ?, ?, ?)
        ''', (visit_date, ip_address, user_agent, endpoint))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error logging site visit: {e}")

SPORTS = {
    'NHL': {'name': 'NHL', 'icon': '🏒', 'color': '#1e3a8a'},
    'NFL': {'name': 'NFL', 'icon': '🏈', 'color': '#059669'},
    'NBA': {'name': 'NBA', 'icon': '🏀', 'color': '#dc2626'},
    'MLB': {'name': 'MLB', 'icon': '⚾', 'color': '#9333ea'},
    'NCAAF': {'name': 'NCAA Football', 'icon': '🏟️', 'color': '#ea580c'},
    'NCAAB': {'name': 'NCAA Basketball', 'icon': '🎓', 'color': '#0891b2'},
}

# ── Public-facing model brand names ───────────────────────────────────────────
# Maps internal identifiers → user-facing names shown in UI / API responses.
# Internal variables, files, and training logic are UNCHANGED.
MODEL_DISPLAY_NAMES = {
    'glicko2':   'Grinder2',
    'trueskill': 'Takedown',
    'elo':       'Edge',
    'xgboost':   'XSharp',
    'ensemble':  'Sharp Consensus',
}

import nfl_data_py as nfl

# ── Puck-Line Cover Probability Configuration ─────────────────────────────────
# Standard deviation for goal-differential normal distribution (tunable per sport).
# Only NHL uses puck-line display; all others keep raw spread in the UI.
PUCK_LINE_STD: dict = {
    'NHL':   1.5,
    'NBA':  12.0,
    'NFL':  10.0,
    'MLB':   2.0,
    'NCAAB': 12.0,
    'NCAAF': 14.0,
    'WNBA':  12.0,
}
_PUCK_LINE_VALUE = 1.5  # NHL puck line is always ±1.5


def compute_puck_line_prob(spread: float, sport: str = 'NHL') -> dict:
    """Convert an XSharp goal-differential spread into puck-line cover probabilities.

    spread > 0  → home team favored
    spread < 0  → away team favored

    Steps:
      1. Assume goal-differential ~ N(|spread|, std)
      2. P_cover_fav = 1 - CDF(1.5 | |spread|, std)   (favorite wins by >1.5)
      3. P_cover_dog =     CDF(1.5 | |spread|, std)   (underdog keeps it within 1.5)
      4. Tag: STRONG ≥55%, LEAN 52–55%, NO EDGE otherwise

    Returns dict with keys:
      puck_line_fav_prob  – favourite -1.5 cover % (0–100)
      puck_line_dog_prob  – underdog  +1.5 cover % (0–100)
      puck_line_tag       – STRONG -1.5 / LEAN -1.5 / STRONG +1.5 / LEAN +1.5 / NO EDGE
      puck_line_fav_side  – 'home' or 'away'
    """
    from scipy.stats import norm
    std  = PUCK_LINE_STD.get(sport, 1.5)
    line = _PUCK_LINE_VALUE
    abs_spread = abs(spread)

    p_fav = float(1.0 - norm.cdf(line, loc=abs_spread, scale=std))
    p_dog = float(norm.cdf(line, loc=abs_spread, scale=std))
    p_fav_pct = round(p_fav * 100, 1)
    p_dog_pct = round(p_dog * 100, 1)

    if p_fav_pct >= 55:
        tag = 'STRONG -1.5'
    elif p_fav_pct >= 52:
        tag = 'LEAN -1.5'
    elif p_dog_pct >= 55:
        tag = 'STRONG +1.5'
    elif p_dog_pct >= 52:
        tag = 'LEAN +1.5'
    else:
        tag = 'NO EDGE'

    return {
        'puck_line_fav_prob': p_fav_pct,
        'puck_line_dog_prob': p_dog_pct,
        'puck_line_tag':      tag,
        'puck_line_fav_side': 'home' if spread >= 0 else 'away',
    }


def update_nfl_scores():
    """
    Fetches and updates NFL scores for the 2025 season.
    Also inserts new games (including playoffs) that don't exist in database.
    """
    try:
        logger.info("Fetching 2025 NFL schedule to update scores...")
        schedule = nfl.import_schedules([2025])
        
        if schedule.empty:
            logger.warning("No NFL schedule data found for the 2025 season.")
            return

        finished_games = schedule[schedule['result'].notna()].copy()

        if finished_games.empty:
            logger.info("No new finished NFL games with results found.")
            return

        logger.info(f"Found {len(finished_games)} finished NFL games to update.")
        
        # Team abbreviation to full name mapping for NFL
        nfl_abbr_to_full = {
            'ARI': 'Arizona Cardinals', 'ATL': 'Atlanta Falcons', 'BAL': 'Baltimore Ravens',
            'BUF': 'Buffalo Bills', 'CAR': 'Carolina Panthers', 'CHI': 'Chicago Bears',
            'CIN': 'Cincinnati Bengals', 'CLE': 'Cleveland Browns', 'DAL': 'Dallas Cowboys',
            'DEN': 'Denver Broncos', 'DET': 'Detroit Lions', 'GB': 'Green Bay Packers',
            'HOU': 'Houston Texans', 'IND': 'Indianapolis Colts', 'JAX': 'Jacksonville Jaguars',
            'KC': 'Kansas City Chiefs', 'LV': 'Las Vegas Raiders', 'LAC': 'Los Angeles Chargers',
            'LAR': 'Los Angeles Rams', 'LA': 'Los Angeles Rams', 'MIA': 'Miami Dolphins',
            'MIN': 'Minnesota Vikings', 'NE': 'New England Patriots', 'NO': 'New Orleans Saints',
            'NYG': 'New York Giants', 'NYJ': 'New York Jets', 'PHI': 'Philadelphia Eagles',
            'PIT': 'Pittsburgh Steelers', 'SF': 'San Francisco 49ers', 'SEA': 'Seattle Seahawks',
            'TB': 'Tampa Bay Buccaneers', 'TEN': 'Tennessee Titans', 'WAS': 'Washington Commanders'
        }
        
        conn = get_db_connection()
        cursor = conn.cursor()
        updates_count = 0
        inserts_count = 0

        for _, game in finished_games.iterrows():
            game_id = game['game_id']
            
            # Check if game exists
            existing = cursor.execute("SELECT 1 FROM games WHERE game_id = ? AND sport = 'NFL'", (game_id,)).fetchone()
            
            if existing:
                # Update existing game
                cursor.execute("""
                    UPDATE games
                    SET home_score = ?, away_score = ?, status = 'final'
                    WHERE sport = 'NFL' AND game_id = ?
                """, (game['home_score'], game['away_score'], game_id))
                if cursor.rowcount > 0:
                    updates_count += 1
            else:
                # Insert new game (including playoffs)
                try:
                    home_team = nfl_abbr_to_full.get(game['home_team'], game['home_team'])
                    away_team = nfl_abbr_to_full.get(game['away_team'], game['away_team'])
                    game_date = str(game['gameday']) if pd.notna(game.get('gameday')) else str(game.get('game_date', ''))
                    
                    cursor.execute("""
                        INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')
                    """, ('NFL', 'NFL', game_id, 2025, game_date, home_team, away_team, game['home_score'], game['away_score']))
                    inserts_count += 1
                    logger.info(f"Inserted new NFL game: {away_team} @ {home_team} (Week {game.get('week', 'N/A')})")
                except Exception as insert_error:
                    logger.error(f"Error inserting NFL game {game_id}: {insert_error}")

        conn.commit()
        conn.close()
        logger.info(f"Successfully updated {updates_count} and inserted {inserts_count} NFL game scores.")

    except Exception as e:
        logger.error(f"An error occurred while updating NFL scores: {e}")

def update_nhl_scores():
    """
    Fetches and updates NHL scores using the NHL API.
    Gets scores from the last 30 days (to catch any missing games).
    """
    try:
        lookback_days = 10
        logger.info(f"Fetching NHL scores from API (last {lookback_days} days)...")
        
        # Fetch recent window to keep request latency low while still catching missed finals.
        from datetime import datetime, timedelta
        today = datetime.now()
        start_date = today - timedelta(days=lookback_days)
        
        # NHL team abbreviation to full name mapping
        nhl_team_map = {
            'ANA': 'Anaheim Ducks', 'BOS': 'Boston Bruins', 'BUF': 'Buffalo Sabres',
            'CGY': 'Calgary Flames', 'CAR': 'Carolina Hurricanes', 'CHI': 'Chicago Blackhawks',
            'COL': 'Colorado Avalanche', 'CBJ': 'Columbus Blue Jackets', 'DAL': 'Dallas Stars',
            'DET': 'Detroit Red Wings', 'EDM': 'Edmonton Oilers', 'FLA': 'Florida Panthers',
            'LAK': 'Los Angeles Kings', 'MIN': 'Minnesota Wild', 'MTL': 'Montreal Canadiens',
            'NSH': 'Nashville Predators', 'NJD': 'New Jersey Devils', 'NYI': 'New York Islanders',
            'NYR': 'New York Rangers', 'OTT': 'Ottawa Senators', 'PHI': 'Philadelphia Flyers',
            'PIT': 'Pittsburgh Penguins', 'SJS': 'San Jose Sharks', 'SEA': 'Seattle Kraken',
            'STL': 'St. Louis Blues', 'TBL': 'Tampa Bay Lightning', 'TOR': 'Toronto Maple Leafs',
            'VAN': 'Vancouver Canucks', 'VGK': 'Vegas Golden Knights', 'WSH': 'Washington Capitals',
            'WPG': 'Winnipeg Jets', 'UTA': 'Utah Hockey Club'
        }
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        updates_count = 0
        current_date = start_date
        
        # Iterate through lookback window
        while current_date <= today:
            date_str = current_date.strftime('%Y-%m-%d')
            
            try:
                # Fetch scores for this date from NHL API
                url = f"https://api-web.nhle.com/v1/score/{date_str}"
                response = requests.get(url, timeout=3)  # Shorter timeout
                
                if response.status_code == 200:
                    data = response.json()
                    games = data.get('games', [])
                    
                    for game in games:
                        # Only process finished games
                        if game.get('gameState') in ['OFF', 'FINAL']:
                            home_abbr = game['homeTeam']['abbrev']
                            away_abbr = game['awayTeam']['abbrev']
                            home_score = game['homeTeam'].get('score', 0)
                            away_score = game['awayTeam'].get('score', 0)
                            
                            # Convert abbreviations to full names
                            home_team = nhl_team_map.get(home_abbr, home_abbr)
                            away_team = nhl_team_map.get(away_abbr, away_abbr)
                            
                            game_id = f"NHL_{game.get('id')}"
                            
                            # Check if game exists
                            existing = cursor.execute("SELECT 1 FROM games WHERE game_id = ? AND sport = 'NHL'", (game_id,)).fetchone()
                            
                            if existing:
                                # Update existing game
                                cursor.execute("""
                                    UPDATE games
                                    SET home_score = ?, away_score = ?, status = 'final'
                                    WHERE sport = 'NHL' 
                                      AND game_id = ?
                                      AND (home_score IS NULL OR home_score != ?)
                                """, (home_score, away_score, game_id, home_score))
                            else:
                                # Insert new completed game
                                try:
                                    cursor.execute("""
                                        INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')
                                    """, ('NHL', 'NHL', game_id, 2025, date_str, home_team, away_team, home_score, away_score))
                                    logger.info(f"Inserted new NHL game: {away_team} @ {home_team} ({date_str})")
                                except Exception as insert_error:
                                    logger.error(f"Error inserting NHL game {game_id}: {insert_error}")
                            
                            if cursor.rowcount > 0:
                                updates_count += 1
                
            except Exception as date_error:
                # Skip silently to avoid log spam
                pass
            
            current_date += timedelta(days=1)
        
        conn.commit()
        conn.close()
        logger.info(f"Successfully updated {updates_count} NHL game scores.")
        
    except Exception as e:
        logger.error(f"An error occurred while updating NHL scores: {e}")

def update_nba_scores():
    """
    Fetches and updates NBA scores using ESPN API.
    Checks last 7 days for score updates.
    """
    update_espn_scores('NBA')

def update_espn_scores(sport):
    """
    Generic ESPN API score updater for NBA, NCAAB, NCAAF, MLB, WNBA.
    Checks last 7 days for score updates.
    """
    ESPN_ENDPOINTS = {
        'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
        'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
        'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
        'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
        'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
    }
    
    if sport not in ESPN_ENDPOINTS:
        logger.warning(f"No ESPN endpoint for {sport}")
        return
    
    try:
        logger.info(f"Fetching {sport} scores from ESPN API (last 7 days)...")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        updates_count = 0
        
        # Check last 7 days
        for days_back in range(7):
            check_date = datetime.now() - timedelta(days=days_back)
            date_str = check_date.strftime('%Y%m%d')
            
            extra_params = '&groups=50&limit=357' if sport == 'NCAAB' else ''
            url = f"{ESPN_ENDPOINTS[sport]}?dates={date_str}{extra_params}"
            
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                events = data.get('events', [])
                
                for event in events:
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    
                    if len(competitors) != 2:
                        continue
                    
                    # Get status
                    status_info = event.get('status', {}).get('type', {})
                    status_name = status_info.get('name', '')
                    
                    if status_name not in ['STATUS_FINAL', 'STATUS_FINAL_OT']:
                        continue
                    
                    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    
                    if not home or not away:
                        continue
                    
                    home_team = home.get('team', {}).get('displayName', '')
                    away_team = away.get('team', {}).get('displayName', '')
                    
                    try:
                        home_score = int(home.get('score', 0))
                        away_score = int(away.get('score', 0))
                    except:
                        continue
                    
                    game_date = check_date.strftime('%Y-%m-%d')
                    game_id = f"{sport}_{event.get('id')}"
                    
                    # Check if game exists
                    existing = cursor.execute("SELECT 1 FROM games WHERE game_id = ? AND sport = ?", (game_id, sport)).fetchone()
                    
                    if existing:
                        # Update existing game
                        cursor.execute("""
                            UPDATE games
                            SET home_score = ?, away_score = ?, status = 'final'
                            WHERE sport = ?
                              AND game_id = ?
                              AND (home_score IS NULL OR home_score != ?)
                        """, (home_score, away_score, sport, game_id, home_score))
                    else:
                        # Insert new completed game
                        try:
                            cursor.execute("""
                                INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')
                            """, (sport, sport, game_id, 2025, game_date, home_team, away_team, home_score, away_score))
                            logger.info(f"Inserted new {sport} game: {away_team} @ {home_team} ({game_date})")
                        except Exception as insert_error:
                            logger.error(f"Error inserting {sport} game {game_id}: {insert_error}")
                    
                    if cursor.rowcount > 0:
                        updates_count += 1
                
            except Exception as e:
                logger.debug(f"Error fetching {sport} for {date_str}: {e}")
        
        conn.commit()
        conn.close()
        
        if updates_count > 0:
            logger.info(f"Successfully updated {updates_count} {sport} game scores.")
        else:
            logger.info(f"No {sport} score updates needed.")
        
    except Exception as e:
        logger.error(f"An error occurred while updating {sport} scores: {e}")

def update_ncaab_scores():
    """Update NCAAB scores from ESPN API"""
    update_espn_scores('NCAAB')

def update_ncaaf_scores():
    """Update NCAAF scores from ESPN API"""
    update_espn_scores('NCAAF')

def update_mlb_scores():
    """Update MLB scores from ESPN API"""
    update_espn_scores('MLB')

def update_wnba_scores():
    """Update WNBA scores from ESPN API"""
    update_espn_scores('WNBA')

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist (safe to run on every startup)."""
    conn = sqlite3.connect(DATABASE)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT, league TEXT, game_id TEXT UNIQUE,
            season INTEGER, game_date TEXT,
            home_team_id TEXT, away_team_id TEXT,
            home_score REAL, away_score REAL, status TEXT
        );
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT, sport TEXT, league TEXT,
            game_date TEXT, home_team_id TEXT, away_team_id TEXT,
            elo_home_prob REAL, xgboost_home_prob REAL,
            logistic_home_prob REAL, meta_home_prob REAL,
            win_probability REAL, locked INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS site_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_date TEXT, ip_address TEXT,
            user_agent TEXT, endpoint TEXT
        );
        CREATE TABLE IF NOT EXISTS betting_odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT, home_moneyline REAL, away_moneyline REAL,
            spread REAL, total REAL,
            home_implied_prob REAL, away_implied_prob REAL,
            num_bookmakers INTEGER
        );
        CREATE TABLE IF NOT EXISTS game_goalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT,
            home_goalie TEXT, away_goalie TEXT,
            home_goalie_save_pct REAL, away_goalie_save_pct REAL,
            home_goalie_gaa REAL, away_goalie_gaa REAL
        );
        CREATE TABLE IF NOT EXISTS betting_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT, spread REAL, total REAL
        );
    ''')
    conn.commit()
    conn.close()
    logger.info("Database tables initialised.")


# Run on every startup — creates tables if missing, no-op if they exist
try:
    init_db()
except Exception as _dbe:
    logger.warning(f"init_db failed: {_dbe}")

def parse_date(date_str):
    """Parse date string from multiple formats (DD/MM/YYYY or YYYY-MM-DD)"""
    try:
        # Strip timestamp if present (everything after space)
        date_only = date_str.split(' ')[0] if ' ' in date_str else date_str
        
        # Try YYYY-MM-DD format first (new format)
        try:
            return datetime.strptime(date_only, '%Y-%m-%d')
        except:
            # Fall back to DD/MM/YYYY format (old format)
            return datetime.strptime(date_only, '%d/%m/%Y')
    except:
        return None

# ============================================================================
# V2 PREDICTION SYSTEM HELPER
# ============================================================================

def get_v2_prediction(sport, home_team, away_team, game_date=None):
    """
    Get predictions from the v2 system (Glicko-2 + Stacked Ensemble + Calibration)
    
    Returns dict with probabilities or None if v2 not available for this sport
    """
    if not HAS_V2_SYSTEM or sport not in V2_PREDICTORS:
        return None
    
    try:
        predictor = V2_PREDICTORS[sport]
        game_df = pd.DataFrame([{
            'home_team': home_team,
            'away_team': away_team,
            'date': game_date or datetime.now().strftime('%Y-%m-%d')
        }])
        
        pred = predictor.predict(game_df)
        row = pred.iloc[0]
        
        return {
            'home_prob': row['home_win_prob'],
            'away_prob': row['away_win_prob'],
            'confidence': row['confidence'],
            'model_agreement': row['model_agreement'],
            'predicted_winner': row['predicted_winner'],
            'expected_home_score': row.get('expected_home_score'),
            'expected_away_score': row.get('expected_away_score'),
            
            # Individual model probabilities for display
            'glicko2_prob': row.get('glicko2_prob'),
            'trueskill_prob': row.get('trueskill_prob'),
            'xgboost_prob': row.get('xgboost_prob'),
            
            # Ratings
            'home_glicko2': row.get('home_glicko2'),
            'away_glicko2': row.get('away_glicko2'),
            'home_trueskill_mu': row.get('home_trueskill_mu'),
            'away_trueskill_mu': row.get('away_trueskill_mu'),
            
            'is_v2': True,
        }
    except Exception as e:
        logger.warning(f"V2 prediction failed for {away_team} @ {home_team}: {e}")
        return None

# ============================================================================
# DATA LOADING FUNCTIONS
# ============================================================================

# ── Cached helpers for spread/total predictors ──────────────────────────────────
_sp_instances: dict = {}   # {sport: (ScorePredictor, timestamp)}
_sp_TTL = 3600             # re-fetch team stats at most once per hour


def _build_team_stats_from_db(sport: str) -> dict:
    """
    Compute team offense/defense PPG from completed games already in the DB.

    Used as a baseline for sports (e.g. NCAAB) where ESPN's /teams endpoint
    only covers ~30 major programs and misses hundreds of small-conference teams.
    Requires >= 3 completed games per team to produce a stat entry.
    """
    try:
        from collections import defaultdict
        conn = get_db_connection()
        rows = conn.execute(
            'SELECT home_team_id, away_team_id, home_score, away_score '
            'FROM games WHERE sport=? AND home_score IS NOT NULL AND away_score IS NOT NULL',
            (sport,)
        ).fetchall()
        conn.close()

        totals = defaultdict(lambda: {'scored': 0.0, 'allowed': 0.0, 'games': 0})
        for row in rows:
            h, a, hs, as_ = row[0], row[1], row[2], row[3]
            if hs is None or as_ is None:
                continue
            totals[h]['scored']  += float(hs);  totals[h]['allowed'] += float(as_);  totals[h]['games'] += 1
            totals[a]['scored']  += float(as_); totals[a]['allowed'] += float(hs);  totals[a]['games'] += 1

        return {
            team: {'offense': d['scored'] / d['games'], 'defense': d['allowed'] / d['games']}
            for team, d in totals.items()
            if d['games'] >= 3  # minimum sample
        }
    except Exception as _e:
        logger.debug(f"_build_team_stats_from_db({sport}) failed: {_e}")
        return {}


def _score_predictor_instance(sport):
    """
    Return a ScorePredictor whose team_stats are cached for the day.

    Strategy:
      1. Build a baseline from completed DB games (covers ALL teams that have played).
      2. Try ESPN API (covers major-conference teams with richer season-level stats).
      3. Merge: DB is the base layer; ESPN overrides where available.

    This ensures small-conference NCAAB teams (and any sport with a large team pool)
    still get spread/total predictions even when ESPN's /teams endpoint omits them.
    """
    try:
        from score_predictor import ScorePredictor
    except ImportError:
        return None
    now = _time.time()
    cached = _sp_instances.get(sport)
    if cached and (now - cached[1]) < _sp_TTL:
        return cached[0]
    sp = ScorePredictor()
    from datetime import datetime as _dt_inner
    _cache_key = f"{sport}_{_dt_inner.now().strftime('%Y-%m-%d')}"

    # 1. DB-derived baseline (all teams with >= 3 games)
    _db_stats = _build_team_stats_from_db(sport)

    # 2. ESPN API (may be empty or partial for large leagues like NCAAB)
    try:
        _api_stats = sp.fetch_team_stats(sport)
    except Exception:
        _api_stats = {}

    # 3. Merge: DB base, ESPN overrides (ESPN data is richer for teams it covers)
    _stats = {**_db_stats, **(_api_stats or {})}

    if _stats:
        sp.team_stats_cache[_cache_key] = _stats
    _sp_instances[sport] = (sp, now)
    logger.debug(f"[{sport}] team_stats loaded: {len(_stats)} teams "
                 f"(db={len(_db_stats)}, api={len(_api_stats or {})})")
    return sp


_xgb_sport_models: dict = {}  # populated lazily; re-uses xgb_spread_model._MODEL_CACHE


def _get_xgb_spread_model(sport):
    """Build (or return cached) XGBSpreadTotalPredictor for `sport`."""
    try:
        from xgb_spread_model import get_or_train_model
    except ImportError:
        return None
    # Need completed games from DB and team stats
    try:
        sp = _score_predictor_instance(sport)
        if not sp:
            return None
        team_stats = sp.team_stats_cache.get(
            f"{sport}_{__import__('datetime').datetime.now().strftime('%Y-%m-%d')}", {}
        )
        conn = get_db_connection()
        rows = conn.execute(
            'SELECT home_team_id, away_team_id, home_score, away_score, game_date '
            'FROM games WHERE sport=? AND home_score IS NOT NULL ORDER BY game_date',
            (sport,)
        ).fetchall()
        conn.close()
        games = [dict(r) for r in rows]
        if not team_stats or not games:
            return None
        return get_or_train_model(sport, games, team_stats)
    except Exception as e:
        logger.debug(f"_get_xgb_spread_model error for {sport}: {e}")
        return None


# ESPN injury endpoints keyed by sport
_INJURY_ENDPOINTS = {
    'NBA':   'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries',
    'NHL':   'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries',
    'NFL':   'https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries',
    'MLB':   'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries',
    'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/injuries',
    'WNBA':  'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/injuries',
}

# Only flag statuses that materially impact a team's chances
_INJURY_SHOW_STATUSES = {'Out', 'Doubtful', 'Injured Reserve', 'IR', 'Suspended'}


def _fetch_injuries(sport: str) -> dict:
    """
    Returns {team_display_name: [{name, status, reason}]} for Out/Doubtful players.
    Uses the 15-min _cached_get cache so it is only fetched once per request cycle.
    Returns {} silently on any error so a bad injury fetch never breaks predictions.
    """
    url = _INJURY_ENDPOINTS.get(sport)
    if not url:
        return {}
    try:
        data = _cached_get(url, timeout=5)
        result = {}
        for team_group in data.get('injuries', []):
            team_name = team_group.get('displayName', '')
            players = []
            for inj in team_group.get('injuries', []):
                status = inj.get('status', '')
                if status not in _INJURY_SHOW_STATUSES:
                    continue
                athlete = inj.get('athlete', {})
                short_name = athlete.get('shortName', athlete.get('displayName', '?'))
                # Extract injury body part from shortComment e.g. "Player (knee) is out..."
                comment = inj.get('shortComment', '')
                import re as _re
                match = _re.search(r'\(([^)]{1,20})\)', comment)
                reason = match.group(1) if match else ''
                players.append({'name': short_name, 'status': status, 'reason': reason})
            if players:
                result[team_name] = players
        return result
    except Exception as _ie:
        logger.debug(f"[injuries] fetch failed for {sport}: {_ie}")
        return {}


def get_upcoming_predictions(sport, days=365):
    """Get ALL game predictions from season start - both completed and upcoming
    
    Loads games from database for all sports including NHL
    
    USER REQUIREMENT: Show ALL games from season start (Oct 7 for NHL), not just upcoming!
    """
    
    # Fast in-process cache to avoid repeated heavy prediction recomputation.
    cache_key = f"{sport}_upcoming_predictions"
    now_ts = _time.time()
    cache_ttl = _PREDICTIONS_TTL_BY_SPORT.get(sport, 180)
    cached = _PREDICTIONS_CACHE.get(cache_key)
    if cached and (now_ts - cached['ts']) < cache_ttl:
        return _copy.deepcopy(cached['data'])

    # Load game data based on sport
    if sport == 'NHL':
        # NHL: Pull from ESPN API (to get correct schedule)
        try:
            nhl_api = NHLAPI()
            # Keep NHL predictions responsive in production (avoid timeout on huge windows).
            # This route must stay below common reverse-proxy timeout budgets.
            api_games = nhl_api.get_recent_and_upcoming_games(days_back=2, days_forward=7)
            
            # For each API game, check if prediction exists in DB
            conn = get_db_connection()
            for game in api_games:
                # Try to find match in database by date and team names
                existing = conn.execute('''
                    SELECT g.game_id, p.elo_home_prob, p.xgboost_home_prob, p.meta_home_prob
                    FROM games g
                    LEFT JOIN predictions p ON g.game_id = p.game_id
                    WHERE g.sport = 'NHL' 
                      AND date(g.game_date) = date(?) 
                      AND g.home_team_id = ? 
                      AND g.away_team_id = ?
                ''', (game['game_date'], game['home_team_name'], game['away_team_name'])).fetchone()
                
                if existing:
                    game['game_id'] = existing['game_id']
                    game['stored_elo_prob'] = existing['elo_home_prob']
                    game['stored_xgb_prob'] = existing['xgboost_home_prob']
                    game['stored_ensemble_prob'] = existing['meta_home_prob']
            
            conn.close()
            
            # Build dates list from API games
            all_games_with_dates = [(parse_date(g['game_date']), g) for g in api_games if parse_date(g['game_date'])]
            all_games_with_dates.sort(key=lambda x: x[0])
        except Exception as e:
            logger.error(f"Error fetching NHL games from ESPN API: {e}")
            all_games_with_dates = []
    
    elif sport in ['NBA', 'NCAAB', 'NCAAF', 'MLB', 'WNBA']:
        # Load from ESPN API and database
        ESPN_ENDPOINTS = {
            'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
            'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
            'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
            'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
            'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
        }
        
        api_games = []

        # NBA needs a longer forward horizon (regular season + playoffs through June).
        if sport == 'NBA':
            start_str = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')
            end_str = (datetime.now() + timedelta(days=120)).strftime('%Y%m%d')
            try:
                url = f"{ESPN_ENDPOINTS[sport]}?dates={start_str}-{end_str}&limit=500"
                data = _cached_get(url)
                events = data.get('events', [])
                for event in events:
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    if len(competitors) != 2:
                        continue

                    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    if not home or not away:
                        continue

                    home_team = home.get('team', {}).get('displayName', '')
                    away_team = away.get('team', {}).get('displayName', '')
                    event_id = event.get('id', '')
                    game_date = (event.get('date') or '')[:10] or datetime.now().strftime('%Y-%m-%d')

                    status_info = event.get('status', {}).get('type', {})
                    status_name = status_info.get('name', 'scheduled')
                    home_score = None
                    away_score = None
                    if status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT']:
                        try:
                            home_score = int(home.get('score', 0))
                            away_score = int(away.get('score', 0))
                        except:
                            pass

                    api_games.append({
                        'game_id': f"{sport}_{event_id}",
                        'home_team_id': home_team,
                        'away_team_id': away_team,
                        'game_date': game_date,
                        'home_score': home_score,
                        'away_score': away_score,
                    })
            except Exception as e:
                logger.debug(f"Error fetching {sport} range {start_str}-{end_str}: {e}")
        else:
            # Other sports: keep shorter day-by-day window.
            for days_offset in range(-7, 15):
                check_date = datetime.now() + timedelta(days=days_offset)
                date_str = check_date.strftime('%Y%m%d')
                
                try:
                    extra_params = '&groups=50&limit=357' if sport == 'NCAAB' else ''
                    url = f"{ESPN_ENDPOINTS[sport]}?dates={date_str}{extra_params}"
                    data = _cached_get(url)
                    
                    events = data.get('events', [])
                    
                    for event in events:
                        competition = event.get('competitions', [{}])[0]
                        competitors = competition.get('competitors', [])
                        
                        if len(competitors) != 2:
                            continue
                        
                        home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                        away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                        
                        if not home or not away:
                            continue
                        
                        home_team = home.get('team', {}).get('displayName', '')
                        away_team = away.get('team', {}).get('displayName', '')
                        event_id = event.get('id', '')
                        
                        # Get status
                        status_info = event.get('status', {}).get('type', {})
                        status_name = status_info.get('name', 'scheduled')
                        
                        home_score = None
                        away_score = None
                        
                        if status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT']:
                            try:
                                home_score = int(home.get('score', 0))
                                away_score = int(away.get('score', 0))
                            except:
                                pass
                        
                        api_games.append({
                            'game_id': f"{sport}_{event_id}",
                            'home_team_id': home_team,
                            'away_team_id': away_team,
                            'game_date': check_date.strftime('%Y-%m-%d'),
                            'home_score': home_score,
                            'away_score': away_score,
                        })
                        
                except Exception as e:
                    logger.debug(f"Error fetching {sport} for {date_str}: {e}")
        
        # Enrich with stored predictions from database
        conn = get_db_connection()
        for game in api_games:
            pred = conn.execute('''
                SELECT elo_home_prob, xgboost_home_prob, logistic_home_prob, win_probability
                FROM predictions WHERE game_id = ? AND sport = ?
            ''', (game['game_id'], sport)).fetchone()
            
            if pred:
                game['stored_elo_prob'] = pred['elo_home_prob']
                game['stored_xgb_prob'] = pred['xgboost_home_prob']
                game['stored_ensemble_prob'] = pred['win_probability']
        conn.close()
        
        # Build dates list
        all_games_with_dates = [(parse_date(g['game_date']), g) for g in api_games if parse_date(g['game_date'])]
        all_games_with_dates.sort(key=lambda x: x[0])
        
        # Remove duplicates (same matchup on same date)
        seen = set()
        unique_games = []
        for date, game in all_games_with_dates:
            key = (date.strftime('%Y-%m-%d'), game['home_team_id'], game['away_team_id'])
            if key not in seen:
                seen.add(key)
                unique_games.append((date, game))
        all_games_with_dates = unique_games
    
    else:
        # NFL and other sports: load from database
        conn = get_db_connection()
        all_games_raw = conn.execute('''
            SELECT g.*, 
                   p.elo_home_prob as stored_elo_prob,
                   p.xgboost_home_prob as stored_xgb_prob,
                   p.win_probability as stored_ensemble_prob,
                   gg.home_goalie, gg.away_goalie,
                   gg.home_goalie_save_pct, gg.away_goalie_save_pct,
                   gg.home_goalie_gaa, gg.away_goalie_gaa,
                   bo.home_moneyline, bo.away_moneyline,
                   bo.spread, bo.total,
                   bo.home_implied_prob, bo.away_implied_prob,
                   bo.num_bookmakers
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            LEFT JOIN game_goalies gg ON g.id = gg.game_id
            LEFT JOIN (
                SELECT game_id, 
                       home_moneyline, away_moneyline, spread, total,
                       home_implied_prob, away_implied_prob, num_bookmakers
                FROM betting_odds
                GROUP BY game_id
            ) bo ON g.id = bo.game_id
            WHERE g.sport = ?
        ''', (sport, sport)).fetchall()
        all_games_raw = [dict(g) for g in all_games_raw]
        conn.close()
        
        all_games_with_dates = []
        for game in all_games_raw:
            parsed_date = parse_date(game['game_date'])
            if parsed_date:
                all_games_with_dates.append((parsed_date, game))
        all_games_with_dates.sort(key=lambda x: x[0])
    
    # Split into completed (for Elo training) and all (for predictions)
    completed_games = [g for d, g in all_games_with_dates if g.get('home_score') is not None]

    # ── NHL: inject team stats directly from completed API games ─────────────
    # The ESPN /teams endpoint doesn't expose NHL goals-per-game stats, and the
    # DB may not yet be populated (update_nhl_scores is only called on results page).
    # We already have 30 days of completed games here with real scores, so we
    # build GPG/GAPG from those and push them into the ScorePredictor cache.
    # This runs every request so the stats are always fresh, regardless of TTL.
    if sport == 'NHL' and completed_games:
        try:
            from collections import defaultdict as _dd_nhl
            _nhl_totals = _dd_nhl(lambda: {'scored': 0.0, 'allowed': 0.0, 'n': 0})
            for _cg in completed_games:
                _h  = _cg.get('home_team_id') or _cg.get('home_team_name', '')
                _a  = _cg.get('away_team_id') or _cg.get('away_team_name', '')
                _hs = _cg.get('home_score')
                _as = _cg.get('away_score')
                if _h and _a and _hs is not None and _as is not None:
                    _nhl_totals[_h]['scored']  += float(_hs)
                    _nhl_totals[_h]['allowed'] += float(_as)
                    _nhl_totals[_h]['n']       += 1
                    _nhl_totals[_a]['scored']  += float(_as)
                    _nhl_totals[_a]['allowed'] += float(_hs)
                    _nhl_totals[_a]['n']       += 1
            _nhl_api_stats = {
                t: {'offense': d['scored'] / d['n'], 'defense': d['allowed'] / d['n']}
                for t, d in _nhl_totals.items() if d['n'] >= 3
            }
            if _nhl_api_stats:
                _sp_nhl = _score_predictor_instance(sport)
                if _sp_nhl:
                    _ck_nhl = f"NHL_{datetime.now().strftime('%Y-%m-%d')}"
                    # Merge: existing richer stats take precedence; API stats fill gaps
                    _existing_nhl = _sp_nhl.team_stats_cache.get(_ck_nhl, {})
                    _sp_nhl.team_stats_cache[_ck_nhl] = {**_nhl_api_stats, **_existing_nhl}
                    logger.debug(f"[NHL] injected {len(_nhl_api_stats)} team stats from API games")
        except Exception as _nhl_stat_err:
            logger.debug(f"[NHL] team stats injection failed: {_nhl_stat_err}")

    # Train Elo system on all completed games (with home/away splits tracking)
    elo_ratings = {}
    home_away_stats = {}  # Track home/away performance
    K_FACTORS = {'NHL': 22, 'NFL': 35, 'NBA': 18, 'MLB': 14, 'NCAAF': 30, 'NCAAB': 25}
    k_factor = K_FACTORS.get(sport, 20)
    
    def get_elo(team):
        return elo_ratings.get(team, 1500)
    
    def expected_score(rating_a, rating_b):
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    
    def get_home_away_stats(team):
        if team not in home_away_stats:
            home_away_stats[team] = {'home_wins': 0, 'home_games': 0, 'away_wins': 0, 'away_games': 0}
        return home_away_stats[team]
    
    # Train Elo and track home/away performance
    for game in completed_games:
        home_rating = get_elo(game['home_team_id'])
        away_rating = get_elo(game['away_team_id'])
        
        expected_home = expected_score(home_rating, away_rating)
        actual_home = 1 if game['home_score'] > game['away_score'] else 0
        
        elo_ratings[game['home_team_id']] = home_rating + k_factor * (actual_home - expected_home)
        elo_ratings[game['away_team_id']] = away_rating + k_factor * ((1-actual_home) - (1-expected_home))
        
        # Track home/away splits
        home_stats = get_home_away_stats(game['home_team_id'])
        away_stats = get_home_away_stats(game['away_team_id'])
        
        home_stats['home_games'] += 1
        away_stats['away_games'] += 1
        
        if actual_home == 1:
            home_stats['home_wins'] += 1
        else:
            away_stats['away_wins'] += 1
    
    # Display logic: Show ALL past games + future games for ONE MONTH from today
    season_starts = {'NHL': datetime(2024, 10, 7), 'NFL': datetime(2024, 9, 4), 'NBA': datetime(2024, 10, 21), 'MLB': datetime(2025, 3, 27), 'NCAAF': datetime(2024, 8, 30), 'NCAAB': datetime(2024, 11, 4), 'WNBA': datetime(2025, 5, 14)}
    season_start = season_starts.get(sport, datetime(2025, 1, 1))
    
    # Calculate cutoff horizon by sport
    # Use module-level datetime/timedelta imports to avoid local shadowing
    today = datetime.now()
    future_window_days = {
        'NBA': 120,
    }
    future_cutoff = today + timedelta(days=future_window_days.get(sport, 30))
    
    predictions = []
    # Fetch injuries once for the whole request (15-min cache keeps it fast)
    _injuries = _fetch_injuries(sport)

    for game_date, game in all_games_with_dates:
        # Show games from season start up to one month from today
        if game_date >= season_start and game_date <= future_cutoff:
            # ============================================================
            # V2 PREDICTION SYSTEM - ALWAYS try v2 first when available
            # ============================================================
            v2_pred = get_v2_prediction(
                    sport, 
                    game.get('home_team_id') or game.get('home_team_name'),
                    game.get('away_team_id') or game.get('away_team_name'),
                    game.get('game_date')
                )
                
            if v2_pred:
                # Use actual stored Elo prob from DB; fall back to Elo rating computation
                stored_elo = game.get('stored_elo_prob')
                if stored_elo is not None:
                    elo_prob = float(stored_elo)
                else:
                    home_rating = get_elo(game.get('home_team_id', ''))
                    away_rating = get_elo(game.get('away_team_id', ''))
                    elo_prob = expected_score(home_rating, away_rating)
                _xgb_raw = v2_pred.get('xgboost_prob')
                xgb_prob = _xgb_raw if _xgb_raw is not None else v2_pred['home_prob']

                # Build ensemble from individual model probs.
                # The meta-learner (v2_pred['home_prob']) frequently defaults to ~0.49
                # when team-name lookup fails, so we compute a weighted blend instead.
                _g2 = v2_pred.get('glicko2_prob')
                _ts = v2_pred.get('trueskill_prob')
                _wp = []
                if _g2       is not None: _wp.append((_g2,      0.30))
                if _ts       is not None: _wp.append((_ts,      0.30))
                if _xgb_raw  is not None: _wp.append((_xgb_raw, 0.25))
                _wp.append((elo_prob, 0.15))
                _tw = sum(w for _, w in _wp)
                ensemble_prob = sum(p * w for p, w in _wp) / _tw

                # Store model probabilities for display (Glicko-2 and TrueSkill only)
                game['glicko2_prob'] = v2_pred.get('glicko2_prob')
                game['trueskill_prob'] = v2_pred.get('trueskill_prob')
                
                # Store v2 metadata for display
                game['v2_confidence'] = v2_pred.get('confidence')
                game['v2_agreement'] = v2_pred.get('model_agreement')
                game['v2_expected_home'] = v2_pred.get('expected_home_score')
                game['v2_expected_away'] = v2_pred.get('expected_away_score')
                game['is_v2'] = True
            else:
                # Fallback to basic Elo for sports without v2
                home_rating = get_elo(game['home_team_id'])
                away_rating = get_elo(game['away_team_id'])
                elo_prob = expected_score(home_rating, away_rating)
                
                # Basic enhancements for non-v2 sports
                goalie_boost = 0.0
                if game.get('home_goalie_save_pct') and game.get('away_goalie_save_pct'):
                    save_pct_diff = float(game['home_goalie_save_pct']) - float(game['away_goalie_save_pct'])
                    goalie_boost = save_pct_diff * 0.3
                
                market_boost = 0.0
                if game.get('home_implied_prob') and game.get('away_implied_prob'):
                    market_home_prob = float(game['home_implied_prob'])
                    market_boost = (market_home_prob - 0.5) * 0.15
                
                home_stats = get_home_away_stats(game['home_team_id'])
                away_stats = get_home_away_stats(game['away_team_id'])
                home_win_pct = home_stats['home_wins'] / home_stats['home_games'] if home_stats['home_games'] > 0 else 0.5
                away_win_pct = away_stats['away_wins'] / away_stats['away_games'] if away_stats['away_games'] > 0 else 0.5
                split_boost = (home_win_pct - away_win_pct) * 0.1
                
                xgb_prob = min(0.95, max(0.05, elo_prob + goalie_boost + market_boost * 0.5 + split_boost))

                if game.get('home_implied_prob'):
                    ensemble_prob = (xgb_prob * 0.5 + elo_prob * 0.3 + float(game['home_implied_prob']) * 0.2)
                else:
                    ensemble_prob = (xgb_prob * 0.6 + elo_prob * 0.4)
                
                if sport == 'NFL':
                    ensemble_prob = elo_prob
            
            # Add predictions to game dict
            game_dict = dict(game)
            game_dict['elo_prob'] = round(elo_prob * 100, 1)
            game_dict['xgb_prob'] = round(xgb_prob * 100, 1)
            game_dict['ensemble_prob'] = round(ensemble_prob * 100, 1)
            game_dict['predicted_winner'] = game['home_team_id'] if ensemble_prob > 0.5 else game['away_team_id']
            
            # Ensure date has no time in GUI
            from datetime import datetime as _dt
            game_dict['game_date'] = _dt.strftime(game_date, '%Y-%m-%d')
            
            # Add V2 metadata
            home_stats = get_home_away_stats(game['home_team_id'])
            away_stats = get_home_away_stats(game['away_team_id'])
            home_win_pct = home_stats['home_wins'] / home_stats['home_games'] if home_stats['home_games'] > 0 else 0.5
            away_win_pct = away_stats['away_wins'] / away_stats['away_games'] if away_stats['away_games'] > 0 else 0.5
            game_dict['has_goalie_data'] = bool(game.get('home_goalie_save_pct'))
            game_dict['has_odds_data'] = bool(game.get('home_implied_prob'))
            game_dict['home_win_pct_home'] = round(home_win_pct * 100, 1)
            game_dict['away_win_pct_away'] = round(away_win_pct * 100, 1)
            
            # V2 model metadata (Glicko-2 + Stacked Ensemble)
            game_dict['is_v2'] = game.get('is_v2', False)
            game_dict['v2_confidence'] = game.get('v2_confidence')
            game_dict['v2_agreement'] = game.get('v2_agreement')
            game_dict['v2_expected_home'] = game.get('v2_expected_home')
            game_dict['v2_expected_away'] = game.get('v2_expected_away')
            
            # Individual model probabilities - ALWAYS pass through
            game_dict['glicko2_prob'] = round(game.get('glicko2_prob', 0) * 100, 1) if game.get('glicko2_prob') else None
            game_dict['trueskill_prob'] = round(game.get('trueskill_prob', 0) * 100, 1) if game.get('trueskill_prob') else None

            # ── Spread / Total predictions ───────────────────────────────────
            # Naive formula (ScorePredictor) and XGBoost model
            # These are only computed for upcoming games (no final score yet)
            game_dict['naive_home_score'] = None
            game_dict['naive_away_score'] = None
            game_dict['naive_spread'] = None
            game_dict['naive_total'] = None
            game_dict['xgb_home_score'] = None
            game_dict['xgb_away_score'] = None
            game_dict['xgb_spread'] = None
            game_dict['xgb_total'] = None
            # Puck-line (NHL) or raw-spread (other sports) display fields
            game_dict['puck_line_fav_prob'] = None
            game_dict['puck_line_dog_prob'] = None
            game_dict['puck_line_tag']      = None
            game_dict['puck_line_fav_side'] = None

            if game_dict.get('home_score') is None:  # upcoming game only
                try:
                    from score_predictor import ScorePredictor
                    _sp = _score_predictor_instance(sport)
                    if _sp:
                        nh, na, ns, nt = _sp.predict_score(
                            game_dict.get('home_team_id', ''),
                            game_dict.get('away_team_id', ''),
                            sport,
                        )
                        if nh is not None:
                            game_dict['naive_home_score'] = nh
                            game_dict['naive_away_score'] = na
                            game_dict['naive_spread'] = ns
                            game_dict['naive_total'] = nt
                except Exception as _e:
                    logger.debug(f"ScorePredictor error: {_e}")

                try:
                    _xm = _get_xgb_spread_model(sport)
                    if _xm:
                        result = _xm.predict(
                            game_dict.get('home_team_id', ''),
                            game_dict.get('away_team_id', ''),
                        )
                        if result and result[0] is not None:
                            game_dict['xgb_home_score'] = result[0]
                            game_dict['xgb_away_score'] = result[1]
                            game_dict['xgb_spread'] = result[2]
                            game_dict['xgb_total'] = result[3]
                except Exception as _e:
                    logger.debug(f"XGBSpread error: {_e}")

                # ── NHL: convert XSharp spread → puck-line cover probabilities ──────────
                # Internal xgb_spread value is preserved unchanged as a model feature;
                # puck_line_* fields are the betting-facing output shown in the UI.
                if sport == 'NHL' and game_dict.get('xgb_spread') is not None:
                    try:
                        _pl = compute_puck_line_prob(game_dict['xgb_spread'], sport)
                        game_dict.update(_pl)
                    except Exception as _ple:
                        logger.debug(f"[NHL] puck_line_prob error: {_ple}")

            # ── Injury warnings (upcoming games only) ─────────────────────────
            if game_dict.get('home_score') is None:
                _ht = game_dict.get('home_team_id', '')
                _at = game_dict.get('away_team_id', '')
                game_dict['home_injuries'] = _injuries.get(_ht, [])
                game_dict['away_injuries'] = _injuries.get(_at, [])
            else:
                game_dict['home_injuries'] = []
                game_dict['away_injuries'] = []

            predictions.append(game_dict)
    
    # For NBA: Save newly generated predictions to database so Results page can use them
    if sport == 'NBA':
        conn_save = get_db_connection()
        cursor_save = conn_save.cursor()
        saved_count = 0
        
        for pred in predictions:
            # Only save if game has game_id and no scores yet (not played)
            if pred.get('game_id') and pred.get('home_score') is None:
                # Check if prediction already exists
                existing = cursor_save.execute('''
                    SELECT id FROM predictions WHERE game_id = ? AND sport = 'NBA'
                ''', (pred['game_id'],)).fetchone()
                
                if not existing:
                    # Save new prediction (locked by default when first saved)
                    try:
                        cursor_save.execute('''
                            INSERT INTO predictions (
                                game_id, sport, league, game_date, home_team_id, away_team_id,
                                elo_home_prob, xgboost_home_prob, win_probability, locked
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        ''', (
                            pred['game_id'], 'NBA', 'NBA', pred['game_date'],
                            pred['home_team_id'], pred['away_team_id'],
                            pred['elo_prob'] / 100.0,
                            pred['xgb_prob'] / 100.0,
                            pred['ensemble_prob'] / 100.0
                        ))
                        saved_count += 1
                    except Exception as e:
                        logger.error(f"Error saving prediction for {pred['game_id']}: {e}")
        
        if saved_count > 0:
            conn_save.commit()
            logger.info(f"Saved {saved_count} new NBA predictions to database")
        conn_save.close()
    
    _PREDICTIONS_CACHE[cache_key] = {'ts': _time.time(), 'data': _copy.deepcopy(predictions)}
    return predictions

def _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=None):
    """Weighted blend matching get_upcoming_predictions weights.
    Avoids v2['home_prob'] which defaults to ~0.49 when team names fail lookup."""
    _wp = []
    if glicko2_prob   is not None: _wp.append((glicko2_prob,   0.30))
    if trueskill_prob is not None: _wp.append((trueskill_prob, 0.30))
    if xgb_prob       is not None: _wp.append((xgb_prob,       0.25))
    if elo_prob       is not None: _wp.append((elo_prob,       0.15))
    _tw = sum(w for _, w in _wp)
    return sum(p * w for p, w in _wp) / _tw if _tw > 0 else fallback


def calculate_nfl_weekly_performance():
    """Calculate NFL model performance week by week using actual stored predictions
    
    Gets completed games and results from nfl_data_py API,
    then looks up predictions from database.
    """
    try:
        # Fetch 2025 NFL schedule with results from API - this is the source of truth
        schedule = nfl.import_schedules([2025])
        
        if schedule.empty:
            return None
        
        # Filter to completed games only (games with results)
        completed_games = schedule[schedule['result'].notna()].copy()
        
        if completed_games.empty:
            return None
        
        # Get database connection for predictions
        conn = get_db_connection()
        
        # Team abbreviation to full name mapping
        abbr_to_full = {
            'ARI': 'Arizona Cardinals', 'ATL': 'Atlanta Falcons', 'BAL': 'Baltimore Ravens',
            'BUF': 'Buffalo Bills', 'CAR': 'Carolina Panthers', 'CHI': 'Chicago Bears',
            'CIN': 'Cincinnati Bengals', 'CLE': 'Cleveland Browns', 'DAL': 'Dallas Cowboys',
            'DEN': 'Denver Broncos', 'DET': 'Detroit Lions', 'GB': 'Green Bay Packers',
            'HOU': 'Houston Texans', 'IND': 'Indianapolis Colts', 'JAX': 'Jacksonville Jaguars',
            'KC': 'Kansas City Chiefs', 'LV': 'Las Vegas Raiders', 'LAC': 'Los Angeles Chargers',
            'LAR': 'Los Angeles Rams', 'LA': 'Los Angeles Rams', 'MIA': 'Miami Dolphins',
            'MIN': 'Minnesota Vikings', 'NE': 'New England Patriots', 'NO': 'New Orleans Saints',
            'NYG': 'New York Giants', 'NYJ': 'New York Jets', 'PHI': 'Philadelphia Eagles',
            'PIT': 'Pittsburgh Steelers', 'SF': 'San Francisco 49ers', 'SEA': 'Seattle Seahawks',
            'TB': 'Tampa Bay Buccaneers', 'TEN': 'Tennessee Titans', 'WAS': 'Washington Commanders'
        }
        
        weekly_results = {}

        # Process each completed game from API
        for _, api_game in completed_games.iterrows():
            week = int(api_game['week'])
            game_id = api_game['game_id']

            # Look up stored predictions from database
            pred = conn.execute('''
                SELECT p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
                FROM predictions p
                WHERE p.game_id = ? AND p.sport = 'NFL'
            ''', (game_id,)).fetchone()

            if not pred or pred[0] is None:
                continue

            # Get team full names
            home_team_full = abbr_to_full.get(api_game['home_team'], api_game['home_team'])
            away_team_full = abbr_to_full.get(api_game['away_team'], api_game['away_team'])

            # Stored DB predictions
            elo_prob = float(pred[0]) if pred[0] else None
            xgb_prob = float(pred[1]) if pred[1] else elo_prob
            ens_prob = elo_prob  # start with elo as fallback

            # V2 model predictions
            v2 = get_v2_prediction('NFL', home_team_full, away_team_full, str(api_game['gameday']))
            glicko2_prob   = v2.get('glicko2_prob')   if v2 else None
            trueskill_prob = v2.get('trueskill_prob') if v2 else None
            if v2:
                xgb_prob = v2.get('xgboost_prob', xgb_prob)
                ens_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)

            actual_home_win = api_game['home_score'] > api_game['away_score']

            if week not in weekly_results:
                weekly_results[week] = {
                    'glicko2':   {'correct': 0, 'total': 0},
                    'trueskill': {'correct': 0, 'total': 0},
                    'elo':       {'correct': 0, 'total': 0},
                    'xgboost':   {'correct': 0, 'total': 0},
                    'ensemble':  {'correct': 0, 'total': 0},
                    'games': []
                }

            glicko2_correct   = (glicko2_prob   > 0.5) == actual_home_win if glicko2_prob   is not None else None
            trueskill_correct = (trueskill_prob > 0.5) == actual_home_win if trueskill_prob is not None else None
            elo_correct       = (elo_prob       > 0.5) == actual_home_win if elo_prob       is not None else None
            xgb_correct       = (xgb_prob       > 0.5) == actual_home_win if xgb_prob       is not None else None
            ens_correct       = (ens_prob       > 0.5) == actual_home_win if ens_prob       is not None else None

            for model, prob, correct in [
                ('glicko2',   glicko2_prob,   glicko2_correct),
                ('trueskill', trueskill_prob, trueskill_correct),
                ('elo',       elo_prob,       elo_correct),
                ('xgboost',   xgb_prob,       xgb_correct),
                ('ensemble',  ens_prob,       ens_correct),
            ]:
                if prob is not None:
                    weekly_results[week][model]['total'] += 1
                    if correct:
                        weekly_results[week][model]['correct'] += 1

            weekly_results[week]['games'].append({
                'date':             str(api_game['gameday']),
                'away':             away_team_full,
                'home':             home_team_full,
                'away_score':       int(api_game['away_score']),
                'home_score':       int(api_game['home_score']),
                'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':         round(elo_prob       * 100, 1) if elo_prob       is not None else None,
                'xgb_prob':         round(xgb_prob       * 100, 1) if xgb_prob       is not None else None,
                'ens_prob':         round(ens_prob       * 100, 1) if ens_prob       is not None else None,
                'glicko2_correct':   glicko2_correct,
                'trueskill_correct': trueskill_correct,
                'elo_correct':       elo_correct,
                'xgb_correct':       xgb_correct,
                'ens_correct':       ens_correct,
            })

        conn.close()

        for week in weekly_results:
            for model in ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                weekly_results[week][model]['accuracy'] = (
                    round(weekly_results[week][model]['correct'] / total * 100, 1) if total > 0 else 0.0
                )

        return weekly_results
        
    except Exception as e:
        logger.error(f"Error calculating NFL weekly performance: {e}")
        return None

def calculate_nhl_weekly_performance():
    """Calculate NHL model performance week by week
    
    Uses data from database since NHL doesn't have a simple API like nfl_data_py.
    Groups games by week number extracted from game_date.
    """
    try:
        from datetime import datetime, timedelta
        conn = get_db_connection()
        
        # Get completed NHL games through yesterday only
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        games = conn.execute('''
            SELECT g.game_date, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.elo_home_prob, p.xgboost_home_prob, p.meta_home_prob
            FROM games g
            LEFT JOIN predictions p ON (p.sport = 'NHL' AND (p.game_id = g.game_id OR 
                (date(p.game_date) = date(g.game_date) AND p.home_team_id = g.home_team_id AND p.away_team_id = g.away_team_id)))
            WHERE g.sport = 'NHL'
              AND g.season = 2025 
              AND g.home_score IS NOT NULL
              AND date(g.game_date) <= ?
            ORDER BY g.game_date
        ''', (yesterday,)).fetchall()
        
        conn.close()
        
        if not games:
            return None
        
        # Group games by week (calculate week number from first game)
        first_game_date = parse_date(games[0]['game_date'])
        season_start = first_game_date if first_game_date else datetime(2025, 10, 7)
        
        weekly_results = {}
        
        for game in games:
            # Parse game date
            game_date = parse_date(game['game_date'])
            if not game_date:
                continue
            
            # Calculate week number (1-indexed)
            days_since_start = (game_date - season_start).days
            week = (days_since_start // 7) + 1
            
            # Extract stored predictions first (fast path)
            elo_prob = float(game['elo_home_prob']) if game['elo_home_prob'] is not None else None
            xgb_prob = (
                float(game['xgboost_home_prob'])
                if game['xgboost_home_prob'] is not None
                else None
            )
            meta_prob = (
                float(game['meta_home_prob'])
                if game['meta_home_prob'] is not None
                else None
            )

            if elo_prob is None and xgb_prob is None and meta_prob is None:
                continue
            if elo_prob is None:
                elo_prob = meta_prob if meta_prob is not None else xgb_prob

            # IMPORTANT FOR PRODUCTION STABILITY:
            # Avoid per-game live v2 inference in historical results calculation.
            # This route can process hundreds of games and live model inference on each
            # can exceed web worker timeouts in production.
            glicko2_prob = None
            trueskill_prob = None

            if xgb_prob is None:
                xgb_prob = elo_prob
            if meta_prob is None:
                meta_prob = _compute_ensemble_prob(
                    glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=elo_prob
                )

            actual_home_win = game['home_score'] > game['away_score']

            if week not in weekly_results:
                weekly_results[week] = {
                    'glicko2':   {'correct': 0, 'total': 0},
                    'trueskill': {'correct': 0, 'total': 0},
                    'elo':       {'correct': 0, 'total': 0},
                    'xgboost':   {'correct': 0, 'total': 0},
                    'ensemble':  {'correct': 0, 'total': 0},
                    'games': []
                }

            glicko2_correct   = (glicko2_prob   > 0.5) == actual_home_win if glicko2_prob   is not None else None
            trueskill_correct = (trueskill_prob > 0.5) == actual_home_win if trueskill_prob is not None else None
            elo_correct       = (elo_prob       > 0.5) == actual_home_win
            xgb_correct       = (xgb_prob       > 0.5) == actual_home_win
            meta_correct      = (meta_prob      > 0.5) == actual_home_win

            weekly_results[week]['elo']['total'] += 1
            if elo_correct: weekly_results[week]['elo']['correct'] += 1

            weekly_results[week]['xgboost']['total'] += 1
            if xgb_correct: weekly_results[week]['xgboost']['correct'] += 1

            weekly_results[week]['ensemble']['total'] += 1
            if meta_correct: weekly_results[week]['ensemble']['correct'] += 1

            if glicko2_correct is not None:
                weekly_results[week]['glicko2']['total'] += 1
                if glicko2_correct: weekly_results[week]['glicko2']['correct'] += 1

            if trueskill_correct is not None:
                weekly_results[week]['trueskill']['total'] += 1
                if trueskill_correct: weekly_results[week]['trueskill']['correct'] += 1

            weekly_results[week]['games'].append({
                'date':             game['game_date'].split()[0],
                'away':             game['away_team_id'],
                'home':             game['home_team_id'],
                'away_score':       int(game['away_score']),
                'home_score':       int(game['home_score']),
                'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':         round(elo_prob  * 100, 1),
                'xgb_prob':         round(xgb_prob  * 100, 1),
                'ens_prob':         round(meta_prob * 100, 1),
                'glicko2_correct':   glicko2_correct,
                'trueskill_correct': trueskill_correct,
                'elo_correct':       elo_correct,
                'xgb_correct':       xgb_correct,
                'ens_correct':       meta_correct,
            })

        for week in weekly_results:
            for model in ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                weekly_results[week][model]['accuracy'] = (
                    round(weekly_results[week][model]['correct'] / total * 100, 1) if total > 0 else 0.0
                )
        
        return weekly_results
        
    except Exception as e:
        logger.error(f"Error calculating NHL weekly performance: {e}")
        return None

def calculate_nba_weekly_performance():
    """Calculate NBA model performance week by week using v2 model predictions."""
    def to_float(val):
        if val is None:
            return None
        if isinstance(val, (float, int)):
            return float(val)
        if isinstance(val, bytes):
            try:
                import struct
                if len(val) == 8:
                    return struct.unpack('d', val)[0]
                elif len(val) == 4:
                    return struct.unpack('f', val)[0]
            except:
                pass
            return None
        try:
            return float(val)
        except:
            return None

    try:
        conn = get_db_connection()
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        games = conn.execute('''
            SELECT g.game_date, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
            FROM games g
            LEFT JOIN predictions p
              ON p.sport = 'NBA' AND (
                   p.game_id = g.game_id
                   OR (
                        date(p.game_date) = date(g.game_date)
                        AND p.home_team_id = g.home_team_id
                        AND p.away_team_id = g.away_team_id
                   )
              )
            WHERE g.sport = 'NBA'
              AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
              AND date(g.game_date) <= ?
            ORDER BY g.game_date
        ''', (yesterday,)).fetchall()
        conn.close()

        if not games:
            return None

        first_game_date = parse_date(games[0]['game_date'])
        season_start = first_game_date if first_game_date else datetime(2025, 10, 21)
        weekly_results = {}

        for game in games:
            game_date = parse_date(game['game_date'])
            if not game_date:
                continue

            home_team = game['home_team_id']
            away_team = game['away_team_id']
            home_score = game['home_score']
            away_score = game['away_score']

            if home_score is None or away_score is None:
                continue

            days_since_start = (game_date - season_start).days
            week = (days_since_start // 7) + 1

            # Stored DB predictions
            elo_prob  = to_float(game['elo_home_prob'])
            xgb_prob  = to_float(game['xgboost_home_prob']) or elo_prob
            ens_prob  = to_float(game['win_probability']) or elo_prob

            # V2 model predictions (Glicko-2, TrueSkill)
            v2 = get_v2_prediction('NBA', home_team, away_team, game['game_date'])
            glicko2_prob   = v2.get('glicko2_prob')   if v2 else None
            trueskill_prob = v2.get('trueskill_prob') if v2 else None
            if v2:
                xgb_prob = v2.get('xgboost_prob', xgb_prob)
                ens_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)

            actual_home_win = home_score > away_score

            if week not in weekly_results:
                weekly_results[week] = {
                    'glicko2':   {'correct': 0, 'total': 0},
                    'trueskill': {'correct': 0, 'total': 0},
                    'elo':       {'correct': 0, 'total': 0},
                    'xgboost':   {'correct': 0, 'total': 0},
                    'ensemble':  {'correct': 0, 'total': 0},
                    'games': []
                }

            glicko2_correct   = (glicko2_prob   > 0.5) == actual_home_win if glicko2_prob   is not None else None
            trueskill_correct = (trueskill_prob > 0.5) == actual_home_win if trueskill_prob is not None else None
            elo_correct       = (elo_prob       > 0.5) == actual_home_win if elo_prob       is not None else None
            xgb_correct       = (xgb_prob       > 0.5) == actual_home_win if xgb_prob       is not None else None
            ens_correct       = (ens_prob       > 0.5) == actual_home_win if ens_prob       is not None else None

            for model, prob, correct in [
                ('glicko2',   glicko2_prob,   glicko2_correct),
                ('trueskill', trueskill_prob, trueskill_correct),
                ('elo',       elo_prob,       elo_correct),
                ('xgboost',   xgb_prob,       xgb_correct),
                ('ensemble',  ens_prob,       ens_correct),
            ]:
                if prob is not None:
                    weekly_results[week][model]['total'] += 1
                    if correct:
                        weekly_results[week][model]['correct'] += 1

            weekly_results[week]['games'].append({
                'date':             game['game_date'].split()[0],
                'away':             away_team,
                'home':             home_team,
                'away_score':       int(away_score),
                'home_score':       int(home_score),
                'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                'elo_prob':         round(elo_prob  * 100, 1) if elo_prob  is not None else None,
                'xgb_prob':         round(xgb_prob  * 100, 1) if xgb_prob  is not None else None,
                'ens_prob':         round(ens_prob  * 100, 1) if ens_prob  is not None else None,
                'glicko2_correct':   glicko2_correct,
                'trueskill_correct': trueskill_correct,
                'elo_correct':       elo_correct,
                'xgb_correct':       xgb_correct,
                'ens_correct':       ens_correct,
            })

        for week in weekly_results:
            for model in ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                weekly_results[week][model]['accuracy'] = (
                    round(weekly_results[week][model]['correct'] / total * 100, 1) if total > 0 else 0.0
                )

        return weekly_results

    except Exception as e:
        logger.error(f"Error calculating NBA weekly performance: {e}")
        return None

def calculate_model_performance(sport):
    """Calculate overall performance per model using stored DB predictions + v2 live inference."""
    conn = get_db_connection()
    results_data = conn.execute('''
        SELECT
            g.game_date, g.home_team_id, g.away_team_id,
            g.away_score, g.home_score,
            p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob,
            p.win_probability as ensemble_prob
        FROM games g
        LEFT JOIN predictions p ON
            g.sport = p.sport AND
            g.game_date = p.game_date AND
            g.home_team_id = p.home_team_id AND
            g.away_team_id = p.away_team_id
        WHERE g.sport = ? AND g.home_score IS NOT NULL
        ORDER BY g.game_date ASC
    ''', (sport,)).fetchall()
    conn.close()

    if len(results_data) == 0:
        return None

    models_list = ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']
    results = {m: {'correct': 0, 'total': 0} for m in models_list}
    dates = []

    def to_float(val):
        if val is None:
            return None
        if isinstance(val, (float, int)):
            return float(val)
        if isinstance(val, bytes):
            try:
                import struct
                if len(val) == 8:
                    return struct.unpack('d', val)[0]
                elif len(val) == 4:
                    return struct.unpack('f', val)[0]
                return float(val.decode('utf-8', errors='ignore'))
            except:
                return None
        try:
            return float(val)
        except:
            return None

    for row in results_data:
        home_score = to_float(row[4])
        away_score = to_float(row[3])
        if home_score is None or away_score is None:
            continue
        actual_home_win = home_score > away_score

        # Stored DB probs
        elo_prob = to_float(row[5])
        xgb_prob = to_float(row[6])
        ens_prob = to_float(row[8])

        # V2 live inference
        v2 = get_v2_prediction(sport, row[1], row[2], row[0])
        glicko2_prob   = v2.get('glicko2_prob')   if v2 else None
        trueskill_prob = v2.get('trueskill_prob') if v2 else None
        if v2:
            xgb_prob = v2.get('xgboost_prob', xgb_prob)
            ens_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)

        for model, prob in [
            ('glicko2',   glicko2_prob),
            ('trueskill', trueskill_prob),
            ('elo',       elo_prob),
            ('xgboost',   xgb_prob),
            ('ensemble',  ens_prob),
        ]:
            if prob is not None:
                results[model]['total'] += 1
                if (prob > 0.5) == actual_home_win:
                    results[model]['correct'] += 1

        dates.append(parse_date(row[0]))

    performance = {}
    for model in models_list:
        total = results[model]['total']
        performance[model] = {
            'accuracy': round(results[model]['correct'] / total * 100, 1) if total > 0 else 0.0,
            'correct':  results[model]['correct'],
            'total':    total
        }
    valid_dates = [d for d in dates if d is not None]
    performance['date_range'] = (
        f"{min(valid_dates).strftime('%d/%m/%Y')} - {max(valid_dates).strftime('%d/%m/%Y')}"
        if valid_dates else 'N/A'
    )
    performance['total_games'] = len(results_data)
    return performance


# Sport-specific O/U benchmarks (season average game totals)
_OU_BENCH = {'NBA': 226.0, 'NHL': 6.1, 'NCAAB': 145.0, 'NCAAF': 56.0, 'MLB': 9.0, 'NFL': 47.0, 'WNBA': 158.0}


def _ou_stats(daily_results, sport):
    """Compute over/under counts from daily_results game scores vs sport benchmark."""
    bench = _OU_BENCH.get(sport, 0)
    total_over = total_under = total_games_ou = total_score_sum = 0
    for dd in daily_results.values():
        for g in dd.get('games', []):
            tot = (g.get('away_score') or 0) + (g.get('home_score') or 0)
            if tot > 0:
                total_games_ou += 1
                total_score_sum += tot
                if tot > bench:
                    total_over += 1
                else:
                    total_under += 1
    avg_total = round(total_score_sum / total_games_ou, 1) if total_games_ou > 0 else 0
    return total_over, total_under, total_games_ou, avg_total, bench


def compute_overall_stats_from_daily(daily_results):
    """Compute per-model totals from a daily_results dict (used by DAILY_RESULTS_TEMPLATE)."""
    model_keys = [
        ('glicko2',   'glicko2_correct'),
        ('trueskill', 'trueskill_correct'),
        ('elo',       'elo_correct'),
        ('xgboost',   'xgb_correct'),
        ('ensemble',  'ens_correct'),
    ]
    overall = {m: {'correct': 0, 'total': 0} for m, _ in model_keys}
    for date_data in daily_results.values():
        for game in date_data.get('games', []):
            for model_name, correct_key in model_keys:
                val = game.get(correct_key)
                if val is not None:
                    overall[model_name]['total'] += 1
                    if val:
                        overall[model_name]['correct'] += 1
    for model_name, _ in model_keys:
        t = overall[model_name]['total']
        overall[model_name]['accuracy'] = (
            round(overall[model_name]['correct'] / t * 100, 1) if t > 0 else 0.0
        )
    return overall


def compute_overall_stats_from_weekly(weekly_results):
    """Compute per-model totals from a weekly_results dict (used by NFL_WEEKLY_RESULTS_TEMPLATE)."""
    models = ['glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble']
    overall = {m: {'correct': 0, 'total': 0} for m in models}
    for week_data in weekly_results.values():
        for model in models:
            if model in week_data:
                overall[model]['correct'] += week_data[model].get('correct', 0)
                overall[model]['total']   += week_data[model].get('total', 0)
    for model in models:
        t = overall[model]['total']
        overall[model]['accuracy'] = (
            round(overall[model]['correct'] / t * 100, 1) if t > 0 else 0.0
        )
    return overall


# ============================================================================
# BASE TEMPLATE
# ============================================================================

BASE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}underdogs.bet{% endblock %}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: #fff;
            min-height: 100vh;
        }
        .navbar {
            background: rgba(15, 23, 42, 0.95);
            padding: 15px 30px;
            border-bottom: 2px solid #334155;
            backdrop-filter: blur(10px);
            position: sticky;
            top: 0;
            z-index: 1000;
        }
        .navbar-content {
            max-width: 1400px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .logo {
            font-size: 1.8em;
            font-weight: bold;
            background: linear-gradient(135deg, #fbbf24, #f59e0b);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            text-decoration: none;
        }
        .hamburger {
            display: none;
            flex-direction: column;
            cursor: pointer;
            gap: 5px;
        }
        .hamburger span {
            width: 25px;
            height: 3px;
            background: #fbbf24;
            border-radius: 2px;
            transition: 0.3s;
        }
        .nav-links {
            display: flex;
            gap: 25px;
        }
        .nav-links a {
            color: #cbd5e1;
            text-decoration: none;
            font-weight: 500;
            transition: color 0.3s;
            white-space: nowrap;
        }
        .nav-links a:hover {
            color: #fbbf24;
        }
        .nav-links a.active {
            color: #fbbf24;
        }
        .nav-donate-btn {
            background: linear-gradient(135deg, #fbbf24, #f59e0b);
            color: #000 !important;
            font-weight: 700 !important;
            padding: 7px 16px;
            border-radius: 20px;
            transition: opacity 0.2s !important;
            white-space: nowrap;
        }
        .nav-donate-btn:hover { opacity: 0.85; color: #000 !important; }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 30px;
        }
        @media (max-width: 768px) {
            .hamburger {
                display: flex;
            }
            .nav-links {
                position: absolute;
                top: 70px;
                left: 0;
                right: 0;
                background: rgba(15, 23, 42, 0.98);
                flex-direction: column;
                gap: 0;
                padding: 20px;
                border-bottom: 2px solid #334155;
                display: none;
            }
            .nav-links.active {
                display: flex;
            }
            .nav-links a {
                padding: 12px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            }
            .container {
                padding: 20px 15px;
            }
        }
        {% block extra_styles %}{% endblock %}
    </style>
</head>
<body>
    <div class="navbar">
        <div class="navbar-content">
            <a href="/" class="logo">🎯 underdogs.bet</a>
            <div class="hamburger" onclick="toggleMenu()">
                <span></span>
                <span></span>
                <span></span>
            </div>
            <div class="nav-links" id="navLinks">
                <a href="/" class="{{ 'active' if page == 'home' else '' }}">Home</a>
                <a href="/sport/NHL/predictions" class="{{ 'active' if page == 'NHL' else '' }}">🏒 NHL</a>
                <a href="/sport/NBA/predictions" class="{{ 'active' if page == 'NBA' else '' }}">🏀 NBA</a>
                <a href="/sport/MLB/predictions" class="{{ 'active' if page == 'MLB' else '' }}">⚾ MLB</a>
                <a href="/sport/NFL/predictions" class="{{ 'active' if page == 'NFL' else '' }}">🏈 NFL</a>
                <a href="/sport/NCAAB/predictions" class="{{ 'active' if page == 'NCAAB' else '' }}">🎓 NCAAB</a>
                <a href="{{ stripe_donation_url }}" target="_blank" class="nav-donate-btn">💛 Donate</a>
            </div>
        </div>
    </div>
    
    <div class="container">
        {% block content %}{% endblock %}
    </div>
    
    <script>
        function toggleMenu() {
            const navLinks = document.getElementById('navLinks');
            navLinks.classList.toggle('active');
        }
        
        // Close menu when clicking a link
        document.addEventListener('DOMContentLoaded', function() {
            const navLinks = document.getElementById('navLinks');
            const links = navLinks.querySelectorAll('a');
            links.forEach(link => {
                link.addEventListener('click', function() {
                    navLinks.classList.remove('active');
                });
            });
        });
        
        // Close menu when clicking outside
        document.addEventListener('click', function(event) {
            const navLinks = document.getElementById('navLinks');
            const hamburger = document.querySelector('.hamburger');
            const navbar = document.querySelector('.navbar');
            
            // If click is outside navbar entirely, close menu
            if (!navbar.contains(event.target)) {
                navLinks.classList.remove('active');
            }
        });
    </script>
</body>
</html>
"""

# ============================================================================
# VALUE BETTING TEMPLATE (NHL only)
# ============================================================================

VALUE_BETTING_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title { font-size: 2.5em; margin-bottom: 30px; text-align: center; }
    .section-tabs { display: flex; gap: 10px; margin-bottom: 30px; justify-content: center; }
    .tab { padding: 12px 30px; border-radius: 8px; text-decoration: none; font-weight: 600; transition: all 0.3s; background: rgba(255, 255, 255, 0.1); color: white; }
    .tab.active { background: linear-gradient(135deg, #3b82f6, #2563eb); }
    .value-picks-container { background: rgba(255, 255, 255, 0.05); border-radius: 15px; padding: 25px; }
    .pick-card { background: rgba(255, 255, 255, 0.1); border-radius: 12px; padding: 20px; margin-bottom: 20px; border-left: 4px solid; }
    .pick-card.HIGH { border-left-color: #10b981; }
    .pick-card.MEDIUM { border-left-color: #fbbf24; }
    .pick-card.LOW { border-left-color: #3b82f6; }
    .matchup { font-size: 1.4em; font-weight: bold; margin-bottom: 10px; }
    .pick-team { color: #10b981; font-size: 1.2em; font-weight: bold; }
    .edge-badge { display: inline-block; padding: 6px 14px; border-radius: 6px; font-weight: bold; margin: 5px; }
    .edge-badge.HIGH { background: #10b981; color: white; }
    .edge-badge.MEDIUM { background: #fbbf24; color: black; }
    .edge-badge.LOW { background: #3b82f6; color: white; }
    .situational { display: flex; gap: 15px; flex-wrap: wrap; margin-top: 10px; font-size: 0.9em; opacity: 0.9; }
    .situational-item { background: rgba(255, 255, 255, 0.1); padding: 6px 12px; border-radius: 6px; }
    .warning { color: #ef4444; font-weight: bold; }
    .no-picks { text-align: center; padding: 60px; opacity: 0.7; font-size: 1.2em; }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - VALUE BETTING PICKS</h1>
    <div class="section-tabs">
        <a href="/sport/{{ sport }}/predictions" class="tab active">💰 Value Picks</a>
        <a href="/sport/{{ sport }}/results" class="tab">🎯 Results</a>
    </div>
    <div style="text-align: center; margin-bottom: 30px; padding: 20px; background: rgba(251, 191, 36, 0.1); border-radius: 10px;">
        <p style="font-size: 1.2em; margin-bottom: 10px;">✅ <strong>Only showing games with +5% or higher edge</strong></p>
        <p style="opacity: 0.8;">Situational factors (rest, back-to-back, form) applied to find mispriced lines</p>
    </div>
    <div class="value-picks-container">
        {% if predictions %}
            {% for pred in predictions %}
            <div class="pick-card {{ pred.confidence }}">
                <div class="matchup">{{ pred.away_team }} @ {{ pred.home_team }}</div>
                <div style="margin: 15px 0;">
                    <span class="edge-badge {{ pred.confidence }}">{{ pred.edge }}% EDGE</span>
                    <span class="edge-badge {{ pred.confidence }}">{{ pred.confidence }} CONFIDENCE</span>
                    {% if pred.best_line %}<span style="padding: 6px 14px; background: rgba(255,255,255,0.2); border-radius: 6px; font-weight: bold;">Best Line: {{ pred.best_line }}</span>{% endif %}
                </div>
                <div style="font-size: 1.1em; margin: 10px 0;">
                    🎯 <span class="pick-team">{{ pred.pick }}</span>
                </div>
                <div style="margin: 10px 0; padding: 10px; background: rgba(255,255,255,0.05); border-radius: 6px;">
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 0.9em;">
                        <div><strong>Edge:</strong> {{ (pred.elo_prob * 100)|round(1) }}%</div>
                        <div><strong>XSharp:</strong> {{ (pred.xgb_prob * 100)|round(1) }}%</div>
                        <div><strong>Sharp Consensus:</strong> {{ (pred.ensemble_prob * 100)|round(1) }}%</div>
                    </div>
                    <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.1);">
                        <strong>Adjusted:</strong> {{ (pred.adjusted_prob * 100)|round(1) }}% &nbsp;|&nbsp; <strong>Market:</strong> {{ (pred.market_prob * 100)|round(1) }}%
                    </div>
                </div>
                <div class="situational">
                    <div class="situational-item">📅 {{ pred.game_date }}</div>
                    <div class="situational-item">🏠 Rest: {{ pred.home_rest }}d</div>
                    <div class="situational-item">✈️ Rest: {{ pred.away_rest }}d</div>
                    {% if pred.home_b2b %}<div class="situational-item warning">⚠️ Home B2B</div>{% endif %}
                    {% if pred.away_b2b %}<div class="situational-item warning">⚠️ Away B2B</div>{% endif %}
                    {% if pred.situational_edge != 0 %}<div class="situational-item">📊 Sit. Edge: {{ (pred.situational_edge * 100)|round(1) }}%</div>{% endif %}
                </div>
            </div>
            {% endfor %}
        {% else %}
        <div class="no-picks">
            ❌ No value bets found for today<br>
            <span style="opacity: 0.7; font-size: 0.9em;">Market is efficiently priced or no games available</span>
        </div>
        {% endif %}
    </div>
""")

# ============================================================================
# PREDICTIONS TEMPLATE
# ============================================================================

PREDICTIONS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title {
        font-size: 2.5em;
        margin-bottom: 30px;
        text-align: center;
    }
    .section-tabs {
        display: flex;
        gap: 10px;
        margin-bottom: 30px;
        justify-content: center;
    }
    .tab {
        padding: 12px 30px;
        border-radius: 8px;
        text-decoration: none;
        font-weight: 600;
        transition: all 0.3s;
        background: rgba(255, 255, 255, 0.1);
        color: white;
    }
    .tab.active {
        background: linear-gradient(135deg, #3b82f6, #2563eb);
    }
    .predictions-table {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 15px;
        padding: 25px;
        overflow-x: auto;
        max-height: 800px;
        overflow-y: auto;
    }
    table {
        width: 100%;
        border-collapse: collapse;
    }
    th {
        background: #1e293b;
        padding: 15px;
        text-align: left;
        font-weight: 600;
        border-bottom: 2px solid #fbbf24;
        position: sticky;
        top: 0;
        z-index: 10;
        box-shadow: 0 2px 4px rgba(0,0,0,0.3);
    }
    td {
        padding: 15px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    }
    tr:hover {
        background: rgba(255, 255, 255, 0.05);
    }
    .model-pred {
        text-align: center;
        font-weight: bold;
    }
    .high-conf {
        color: #10b981;
    }
    .med-conf {
        color: #fbbf24;
    }
    .low-conf {
        color: #ef4444;
    }
    .no-data {
        text-align: center;
        padding: 60px 20px;
        font-size: 1.3em;
        opacity: 0.7;
    }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - Predictions</h1>
    
    <div class="section-tabs">
        <a href="/sport/{{ sport }}/predictions" class="tab active">📊 Predictions</a>
        <a href="/sport/{{ sport }}/results" class="tab">🎯 Results</a>
    </div>
    
    {% if today_date in sorted_dates %}
    <div style="text-align: center; margin-bottom: 20px;">
        <a href="#date-{{ today_date }}" style="background: linear-gradient(135deg, #fbbf24, #f59e0b); color: #000; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; display: inline-block;">⚡ Skip to Today</a>
    </div>
    {% endif %}
    
    <div class="predictions-table">
        {% if grouped_predictions %}
            {% for date in sorted_dates %}
            <div id="date-{{ date }}" style="margin-bottom: 40px;">
                <h2 style="color: #fbbf24; margin-bottom: 15px; padding-left: 10px; {% if date == today_date %}background: rgba(251, 191, 36, 0.1); padding: 10px; border-radius: 8px;{% endif %}">
                    {% if group_by == 'week' %}Week {{ date }}{% else %}📅 {{ date }}{% endif %}
                    {% if date == today_date %} <span style="background: #10b981; color: white; padding: 4px 12px; border-radius: 4px; font-size: 0.8em; margin-left: 10px;">TODAY</span>{% endif %}
                </h2>
                <table style="margin-bottom: 20px;">
                    <thead>
                        <tr>
                            <th>Matchup</th>
                            <th style="background: #1e40af;">Grinder2</th>
                            <th style="background: #7c3aed;">Takedown</th>
                            <th style="background: #059669;">Edge</th>
                            <th style="background: #dc2626;">XSharp</th>
                            <th style="background: #fbbf24; color: #000;">Sharp Consensus</th>
                            <th>Pick</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for pred in grouped_predictions[date] %}
                        <tr>
                            <td>{{ pred.away_team_id }} @ <strong>{{ pred.home_team_id }}</strong></td>
                            <td class="model-pred" style="color: #60a5fa;">{{ pred.glicko2_prob if pred.glicko2_prob else '-' }}{% if pred.glicko2_prob %}%{% endif %}</td>
                            <td class="model-pred" style="color: #a78bfa;">{{ pred.trueskill_prob if pred.trueskill_prob else '-' }}{% if pred.trueskill_prob %}%{% endif %}</td>
                            <td class="model-pred" style="color: #34d399;">{{ pred.elo_prob if pred.elo_prob else '-' }}{% if pred.elo_prob %}%{% endif %}</td>
                            <td class="model-pred" style="color: #f87171;">{{ pred.xgb_prob }}%</td>
                            <td class="model-pred {% if pred.ensemble_prob > 60 %}high-conf{% elif pred.ensemble_prob > 55 %}med-conf{% else %}low-conf{% endif %}" style="font-size: 1.1em;">{{ pred.ensemble_prob }}%</td>
                            <td class="{% if pred.ensemble_prob > 60 %}high-conf{% elif pred.ensemble_prob > 55 %}med-conf{% else %}low-conf{% endif %}"><strong>{{ pred.predicted_winner }}</strong></td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% endfor %}
        {% else %}
        <div class="no-data">No upcoming predictions available for {{ sport_info.name }}</div>
        {% endif %}
    </div>
""")

# ============================================================================
# RESULTS TEMPLATE
# ============================================================================

NHL_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title {
        font-size: 2.5em;
        margin-bottom: 30px;
        text-align: center;
    }
    .section-tabs {
        display: flex;
        gap: 10px;
        margin-bottom: 30px;
        justify-content: center;
    }
    .tab {
        padding: 12px 30px;
        border-radius: 8px;
        text-decoration: none;
        font-weight: 600;
        transition: all 0.3s;
        background: rgba(255, 255, 255, 0.1);
        color: white;
    }
    .tab.active {
        background: linear-gradient(135deg, #10b981, #059669);
    }
    .results-table-container {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 15px;
        padding: 20px;
        overflow-x: auto;
    }
    .results-header {
        text-align: center;
        margin-bottom: 20px;
    }
    .results-header h2 {
        color: #fbbf24;
        font-size: 1.8em;
        margin-bottom: 10px;
    }
    .results-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.95em;
    }
    .results-table th {
        background: rgba(255, 255, 255, 0.1);
        padding: 12px 8px;
        text-align: left;
        font-weight: bold;
        color: #fbbf24;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
    }
    .results-table td {
        padding: 10px 8px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    }
    .results-table tr:hover {
        background: rgba(255, 255, 255, 0.05);
    }
    .prob-high {
        color: #10b981;
        font-weight: bold;
    }
    .prob-low {
        color: #ef4444;
    }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - Completed Games Results</h1>
    
    <div class="section-tabs">
        <a href="/sport/{{ sport }}/predictions" class="tab">📊 Predictions</a>
        <a href="/sport/{{ sport }}/results" class="tab active">🎯 Results</a>
    </div>
    
    <div class="results-table-container">
        <div class="results-header">
            <h2>📅 2025-26 Season - All Completed Games</h2>
            <p style="opacity: 0.8;">Model predictions shown as home team win probability (%)</p>
        </div>
        
        <table class="results-table">
            <thead>
                <tr>
                    <th>Date</th>
                    <th>Away Team</th>
                    <th>Home Team</th>
                    <th>Grinder2</th>
                    <th>Takedown</th>
                    <th>Edge</th>
                    <th>XSharp</th>
                    <th>Sharp Consensus</th>
                </tr>
            </thead>
            <tbody>
                {% for game in results %}
                <tr>
                    <td>{{ game.date }}</td>
                    <td>{{ game.away }}</td>
                    <td>{{ game.home }}</td>
                    <td class="{% if game.glicko2_home|float >= 60 %}prob-high{% elif game.glicko2_home|float <= 40 %}prob-low{% endif %}">{{ game.glicko2_home if game.glicko2_home else '-' }}</td>
                    <td class="{% if game.trueskill_home|float >= 60 %}prob-high{% elif game.trueskill_home|float <= 40 %}prob-low{% endif %}">{{ game.trueskill_home if game.trueskill_home else '-' }}</td>
                    <td class="{% if game.elo_home|float >= 60 %}prob-high{% elif game.elo_home|float <= 40 %}prob-low{% endif %}">{{ game.elo_home }}%</td>
                    <td class="{% if game.xgb_home|float >= 60 %}prob-high{% elif game.xgb_home|float <= 40 %}prob-low{% endif %}">{{ game.xgb_home }}%</td>
                    <td class="{% if game.meta_home|float >= 60 %}prob-high{% elif game.meta_home|float <= 40 %}prob-low{% endif %}">{{ game.meta_home }}%</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        
        <div style="margin-top: 30px; text-align: center; padding: 20px; background: rgba(255, 255, 255, 0.1); border-radius: 10px;">
            <p style="font-size: 1.1em; margin-bottom: 10px;">📊 <strong>Total Games:</strong> {{ results|length }}</p>
            <p style="opacity: 0.8;">Values shown are home team win probabilities. Higher % = model favors home team.</p>
        </div>
    </div>
""")

RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title {
        font-size: 2.5em;
        margin-bottom: 30px;
        text-align: center;
    }
    .section-tabs {
        display: flex;
        gap: 10px;
        margin-bottom: 30px;
        justify-content: center;
    }
    .tab {
        padding: 12px 30px;
        border-radius: 8px;
        text-decoration: none;
        font-weight: 600;
        transition: all 0.3s;
        background: rgba(255, 255, 255, 0.1);
        color: white;
    }
    .tab.active {
        background: linear-gradient(135deg, #10b981, #059669);
    }
    .results-container {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 15px;
        padding: 30px;
    }
    .date-range {
        text-align: center;
        font-size: 1.3em;
        margin-bottom: 10px;
        color: #fbbf24;
    }
    .test-info {
        text-align: center;
        font-size: 1.1em;
        margin-bottom: 30px;
        opacity: 0.9;
    }
    .models-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
        gap: 20px;
    }
    .model-card {
        background: rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 25px;
        text-align: center;
        border: 2px solid rgba(255, 255, 255, 0.2);
    }
    .model-card.ensemble {
        border: 3px solid #fbbf24;
    }
    .model-name {
        font-size: 1.3em;
        font-weight: bold;
        margin-bottom: 15px;
        color: #fbbf24;
    }
    .model-accuracy {
        font-size: 3.5em;
        font-weight: bold;
        margin: 15px 0;
    }
    .model-record {
        font-size: 1.2em;
        opacity: 0.9;
    }
    .no-data {
        text-align: center;
        padding: 60px 20px;
        font-size: 1.3em;
        opacity: 0.7;
    }
    """
).replace('{% block content %}{% endblock %}', """
    <div style="margin-bottom: 20px;">
        <a href="/" style="display: inline-block; padding: 10px 20px; background: rgba(255,255,255,0.1); border-radius: 8px; text-decoration: none; color: white; font-weight: 600;">← Back to Home</a>
    </div>
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - Results</h1>
    
    <div class="section-tabs">
        <a href="/sport/{{ sport }}/predictions" class="tab">📊 Predictions</a>
        <a href="/sport/{{ sport }}/results" class="tab active">🎯 Moneyline Results</a>
        <a href="/sport/{{ sport }}/spreads/results" class="tab">📈 Spreads &amp; Totals Results</a>
    </div>
    
    <div class="results-container">
        {% if performance %}
        <div class="date-range">📅 Test Period: {{ performance.date_range }}</div>
        <div class="test-info">Tested on {{ performance.total_games }} completed games</div>
        
        <div class="models-grid">
            <!-- Rating-Based Models -->
            <div class="model-card" style="border-color: #1e40af;">
                <div class="model-name" style="color: #60a5fa;">📊 Grinder2</div>
                <div class="model-accuracy">{{ performance.glicko2.accuracy if performance.glicko2 else 'N/A' }}{% if performance.glicko2 %}%{% endif %}</div>
                <div class="model-record">{% if performance.glicko2 %}{{ performance.glicko2.correct }}-{{ performance.glicko2.total - performance.glicko2.correct }}{% else %}No data{% endif %}</div>
            </div>
            
            <div class="model-card" style="border-color: #7c3aed;">
                <div class="model-name" style="color: #a78bfa;">🎯 Takedown</div>
                <div class="model-accuracy">{{ performance.trueskill.accuracy if performance.trueskill else 'N/A' }}{% if performance.trueskill %}%{% endif %}</div>
                <div class="model-record">{% if performance.trueskill %}{{ performance.trueskill.correct }}-{{ performance.trueskill.total - performance.trueskill.correct }}{% else %}No data{% endif %}</div>
            </div>
            
            <div class="model-card" style="border-color: #059669;">
                <div class="model-name" style="color: #34d399;">📊 Edge</div>
                <div class="model-accuracy">{{ performance.elo.accuracy if performance.elo else 'N/A' }}{% if performance.elo %}%{% endif %}</div>
                <div class="model-record">{% if performance.elo %}{{ performance.elo.correct }}-{{ performance.elo.total - performance.elo.correct }}{% else %}No data{% endif %}</div>
            </div>
            
            <!-- ML Models -->
            <div class="model-card" style="border-color: #dc2626;">
                <div class="model-name" style="color: #f87171;">🤖 XSharp</div>
                <div class="model-accuracy">{{ performance.xgboost.accuracy }}%</div>
                <div class="model-record">{{ performance.xgboost.correct }}-{{ performance.xgboost.total - performance.xgboost.correct }}</div>
            </div>
            
            <!-- Sharp Consensus -->
            <div class="model-card ensemble" style="grid-column: span 2;">
                <div class="model-name">🏆 Sharp Consensus</div>
                <div class="model-accuracy" style="font-size: 4em;">{{ performance.ensemble.accuracy }}%</div>
                <div class="model-record" style="font-size: 1.4em;">{{ performance.ensemble.correct }}-{{ performance.ensemble.total - performance.ensemble.correct }}</div>
            </div>
        </div>
        {% else %}
        <div class="no-data">Not enough data to calculate performance for {{ sport_info.name }}</div>
        {% endif %}
    </div>
""")

# Daily Results Template (for NHL/NBA/NCAAB etc.)
DAILY_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title { font-size: 2.2em; margin-bottom: 20px; text-align: center; }
    .section-tabs { display: flex; gap: 8px; margin-bottom: 20px; justify-content: center; flex-wrap: wrap; }
    .tab { padding: 10px 22px; border-radius: 8px; text-decoration: none; font-weight: 600; transition: all 0.3s; background: rgba(255,255,255,0.1); color: white; font-size: 0.9em; }
    .tab.active { background: linear-gradient(135deg, #10b981, #059669); }
    /* Date navigation */
    .date-nav { display:flex; align-items:center; justify-content:center; gap:12px; margin:16px 0; padding:12px 16px; background:rgba(255,255,255,0.05); border-radius:12px; }
    .nav-arrow { background:rgba(251,191,36,0.2); border:2px solid #fbbf24; color:#fbbf24; font-size:1.3em; width:36px; height:36px; border-radius:50%; display:flex; align-items:center; justify-content:center; cursor:pointer; transition:all 0.2s; user-select:none; flex-shrink:0; }
    .nav-arrow:hover { background:rgba(251,191,36,0.4); transform:scale(1.1); }
    .date-bubbles { display:flex; gap:8px; overflow-x:auto; padding:4px; max-width:820px; }
    .date-bubble { background:rgba(255,255,255,0.1); border:2px solid rgba(255,255,255,0.2); border-radius:22px; padding:8px 15px; min-width:100px; text-align:center; cursor:pointer; transition:all 0.2s; white-space:nowrap; font-weight:500; font-size:0.84em; }
    .date-bubble:hover { border-color:#fbbf24; }
    .date-bubble.active { background:#fbbf24; border-color:#fbbf24; color:#0f172a; font-weight:700; }
    .date-bubble.today { border-color:#10b981; color:#10b981; }
    .date-bubble.active.today { background:#10b981; color:white; }
    /* Date sections: hidden until selected */
    .date-section { display:none; background:rgba(255,255,255,0.05); border-radius:12px; padding:20px; margin-bottom:20px; }
    .date-section.visible { display:block; }
    .date-header { color:#fbbf24; font-size:1.3em; margin-bottom:14px; padding-bottom:10px; border-bottom:2px solid rgba(255,255,255,0.2); }
    .games-table { width:100%; border-collapse:collapse; font-size:0.88em; display:block; overflow-x:auto; }
    .games-table th { background:rgba(255,255,255,0.1); padding:9px 10px; text-align:left; font-weight:bold; color:#fbbf24; border-bottom:2px solid rgba(255,255,255,0.2); white-space:nowrap; }
    .games-table td { padding:7px 10px; border-bottom:1px solid rgba(255,255,255,0.08); white-space:nowrap; }
    .games-table tr:hover { background:rgba(255,255,255,0.05); }
    .prob-correct { color:#10b981; font-weight:bold; }
    .prob-wrong { color:#ef4444; }
    .prob-na { color:#64748b; font-size:0.85em; }
    .daily-models { display:grid; grid-template-columns:repeat(5,1fr); gap:8px; margin-bottom:14px; }
    @media(max-width:700px){ .daily-models { grid-template-columns:repeat(3,1fr); } }
    .daily-model-card { background:rgba(255,255,255,0.08); border-radius:9px; padding:10px; text-align:center; }
    .daily-model-card.best { border:2px solid #10b981; background:rgba(16,185,129,0.1); }
    .model-label { font-size:0.78em; opacity:0.8; margin-bottom:4px; }
    .model-accuracy { font-size:1.5em; font-weight:bold; color:#10b981; }
    .model-record { font-size:0.82em; opacity:0.9; }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} — Results</h1>
    <div class="section-tabs">
        <a href="/sport/{{ sport }}/predictions" class="tab">📊 Predictions</a>
        <a href="/sport/{{ sport }}/results" class="tab active">🎯 Moneyline Results</a>
        <a href="/sport/{{ sport }}/spreads/results" class="tab">📈 Spreads &amp; Totals</a>
    </div>
    {% if daily_results and overall_stats %}
        {% set ens = overall_stats.ensemble %}
        {% set units_won = (ens.correct * 0.91) - (ens.total - ens.correct) %}
        {% set roi = (units_won / ens.total * 100)|round(1) if ens.total > 0 else 0 %}

        <!-- ── Overall Moneyline Performance ── -->
        <div style="background:linear-gradient(135deg,#1e293b,#0f172a);border:2px solid #10b981;border-radius:14px;padding:22px;margin-bottom:16px;">
            <h2 style="text-align:center;margin:0 0 16px 0;font-size:1.5em;">🏆 Moneyline Record &mdash; {{ ens.total }} Games</h2>
            <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px;">
                {% for m_label, m_key in [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Sharp Consensus','ensemble')] %}
                {% set m = overall_stats[m_key] %}
                <div style="background:rgba(255,255,255,0.07);border-radius:9px;padding:12px;text-align:center;{% if m_key=='ensemble' %}border:2px solid #fbbf24;grid-column:span 4;{% endif %}">
                    <div style="font-size:0.85em;opacity:0.8;margin-bottom:4px;">{{ m_label }}</div>
                    {% if m.total > 0 %}
                    <div style="font-size:{% if m_key=='ensemble' %}2.5em{% else %}1.7em{% endif %};font-weight:bold;color:{% if m.accuracy>=55 %}#10b981{% elif m.accuracy>=50 %}#fbbf24{% else %}#ef4444{% endif %};">{{ m.accuracy }}%</div>
                    <div style="font-size:0.85em;opacity:0.85;">{{ m.correct }}-{{ m.total - m.correct }}</div>
                    {% else %}
                    <div style="font-size:{% if m_key=='ensemble' %}2.5em{% else %}1.7em{% endif %};font-weight:bold;color:#94a3b8;">N/A</div>
                    <div style="font-size:0.85em;opacity:0.85;">N/A</div>
                    {% endif %}
                </div>
                {% endfor %}
            </div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;text-align:center;border-top:1px solid rgba(255,255,255,0.12);padding-top:12px;">
                <div><div style="font-size:0.8em;opacity:0.75;">Units (Consensus @-110)</div>
                    <div style="font-size:1.6em;font-weight:bold;color:{% if units_won>=0 %}#fbbf24{% else %}#ef4444{% endif %};">{{ "+" if units_won>0 else "" }}{{ units_won|round(2) }}u</div></div>
                <div><div style="font-size:0.8em;opacity:0.75;">ROI</div>
                    <div style="font-size:1.6em;font-weight:bold;color:{% if roi>=0 %}#fbbf24{% else %}#ef4444{% endif %};">{{ "+" if roi>0 else "" }}{{ roi }}%</div></div>
                <div><div style="font-size:0.8em;opacity:0.75;">$100/game P&amp;L</div>
                    <div style="font-size:1.6em;font-weight:bold;color:{% if units_won>=0 %}#fbbf24{% else %}#ef4444{% endif %};">{{ "+" if units_won>0 else "" }}${{ (units_won*100)|round(0) }}</div></div>
            </div>
        </div>

        <!-- ── Game Totals O/U Stats ── -->
        {% if total_games_ou is defined and total_games_ou > 0 %}
        <div style="background:rgba(255,255,255,0.04);border:1px solid rgba(251,191,36,0.25);border-radius:12px;padding:16px;margin-bottom:16px;display:grid;grid-template-columns:repeat(4,1fr);gap:10px;text-align:center;">
            <div><div style="font-size:0.75em;color:#94a3b8;text-transform:uppercase;letter-spacing:.3px;margin-bottom:4px;">Games Tracked</div>
                <div style="font-size:1.8em;font-weight:bold;">{{ total_games_ou }}</div></div>
            <div><div style="font-size:0.75em;color:#94a3b8;text-transform:uppercase;letter-spacing:.3px;margin-bottom:4px;">Over / Under</div>
                <div style="font-size:1.8em;font-weight:bold;"><span style="color:#10b981;">{{ total_over }}</span> / <span style="color:#ef4444;">{{ total_under }}</span></div>
                <div style="font-size:0.8em;color:#94a3b8;">(line: {{ ou_bench }})</div></div>
            <div><div style="font-size:0.75em;color:#94a3b8;text-transform:uppercase;letter-spacing:.3px;margin-bottom:4px;">Over Rate</div>
                <div style="font-size:1.8em;font-weight:bold;color:#10b981;">{{ (total_over/total_games_ou*100)|round(1) }}%</div></div>
            <div><div style="font-size:0.75em;color:#94a3b8;text-transform:uppercase;letter-spacing:.3px;margin-bottom:4px;">Avg Game Total</div>
                <div style="font-size:1.8em;font-weight:bold;color:#fbbf24;">{{ avg_total|round(1) }}</div></div>
        </div>
        {% endif %}

        <!-- ── Date Slider ── -->
        <div class="date-nav">
            <div class="nav-arrow" onclick="previousWeek()">&#8249;</div>
            <div class="date-bubbles" id="dateBubbles"></div>
            <div class="nav-arrow" onclick="nextWeek()">&#8250;</div>
        </div>

        {% for date in sorted_dates %}
        {% set date_data = daily_results[date] %}
        <div id="date-{{ date }}" class="date-section">
            <div class="date-header">📅 {{ date }}{% if date == today_date %} <span style="background:#10b981;color:white;padding:3px 10px;border-radius:4px;font-size:0.65em;margin-left:8px;">TODAY</span>{% endif %}</div>

            {% set ns = namespace(
                g2_ok=0, g2_total=0,
                ts_ok=0, ts_total=0,
                elo_ok=0, elo_total=0,
                xgb_ok=0, xgb_total=0,
                ens_ok=0, ens_total=0
            ) %}
            {% for g in date_data.games %}
                {% if g.glicko2_correct is not none %}{% set ns.g2_total = ns.g2_total + 1 %}{% if g.glicko2_correct %}{% set ns.g2_ok = ns.g2_ok + 1 %}{% endif %}{% endif %}
                {% if g.trueskill_correct is not none %}{% set ns.ts_total = ns.ts_total + 1 %}{% if g.trueskill_correct %}{% set ns.ts_ok = ns.ts_ok + 1 %}{% endif %}{% endif %}
                {% if g.elo_correct is not none %}{% set ns.elo_total = ns.elo_total + 1 %}{% if g.elo_correct %}{% set ns.elo_ok = ns.elo_ok + 1 %}{% endif %}{% endif %}
                {% if g.xgb_correct is not none %}{% set ns.xgb_total = ns.xgb_total + 1 %}{% if g.xgb_correct %}{% set ns.xgb_ok = ns.xgb_ok + 1 %}{% endif %}{% endif %}
                {% if g.ens_correct is not none %}{% set ns.ens_total = ns.ens_total + 1 %}{% if g.ens_correct %}{% set ns.ens_ok = ns.ens_ok + 1 %}{% endif %}{% endif %}
            {% endfor %}
            {% set g2_pct  = (ns.g2_ok  / ns.g2_total  * 100) if ns.g2_total  > 0 else none %}
            {% set ts_pct  = (ns.ts_ok  / ns.ts_total  * 100) if ns.ts_total  > 0 else none %}
            {% set elo_pct = (ns.elo_ok / ns.elo_total * 100) if ns.elo_total > 0 else none %}
            {% set xgb_pct = (ns.xgb_ok / ns.xgb_total * 100) if ns.xgb_total > 0 else none %}
            {% set ens_pct = (ns.ens_ok / ns.ens_total * 100) if ns.ens_total > 0 else none %}
            {% set best = [
                g2_pct if g2_pct is not none else -1,
                ts_pct if ts_pct is not none else -1,
                elo_pct if elo_pct is not none else -1,
                xgb_pct if xgb_pct is not none else -1,
                ens_pct if ens_pct is not none else -1
            ]|max %}

            <div class="daily-models">
                {% for lbl,ok,total,pct in [('⭐ Grinder2',ns.g2_ok,ns.g2_total,g2_pct),('🎯 Takedown',ns.ts_ok,ns.ts_total,ts_pct),('📊 Edge',ns.elo_ok,ns.elo_total,elo_pct),('🤖 XSharp',ns.xgb_ok,ns.xgb_total,xgb_pct),('🏆 Consensus',ns.ens_ok,ns.ens_total,ens_pct)] %}
                <div class="daily-model-card {% if pct is not none and pct==best %}best{% endif %}">
                    <div class="model-label">{{ lbl }}</div>
                    {% if total > 0 %}
                    <div class="model-accuracy">{{ pct|round(1) }}%</div>
                    <div class="model-record">{{ ok }}-{{ total-ok }}</div>
                    {% else %}
                    <div class="model-accuracy" style="color:#94a3b8;">N/A</div>
                    <div class="model-record">N/A</div>
                    {% endif %}
                </div>
                {% endfor %}
            </div>

            <table class="games-table">
                <thead><tr>
                    <th>Matchup</th><th>Score</th><th>Total</th>
                    <th>Grinder2</th><th>Takedown</th><th>Edge</th><th>XSharp</th><th>Consensus</th>
                </tr></thead>
                <tbody>
                {% for game in date_data.games %}
                <tr>
                    <td>{{ game.away }} @ <strong>{{ game.home }}</strong></td>
                    <td><strong>{{ game.away_score }}-{{ game.home_score }}</strong></td>
                    <td style="color:#94a3b8;">{{ game.away_score + game.home_score }}</td>
                    {% for prob_val, ok_val in [(game.glicko2_prob, game.glicko2_correct),(game.trueskill_prob,game.trueskill_correct),(game.elo_prob,game.elo_correct),(game.xgb_prob,game.xgb_correct),(game.ens_prob,game.ens_correct)] %}
                    {% if prob_val is none %}<td class="prob-na">N/A</td>
                    {% elif ok_val %}<td class="prob-correct">✅ {{ prob_val }}%</td>
                    {% else %}<td class="prob-wrong">❌ {{ prob_val }}%</td>{% endif %}
                    {% endfor %}
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
        {% endfor %}

    {% else %}
    <div style="text-align:center;padding:60px;opacity:0.7;">No results data available yet.</div>
    {% endif %}
<script>
    const allDates = {{ sorted_dates|reverse|list|tojson }};
    const today = '{{ today_date }}';
    let currentWeekStart = 0, activeDate = null;
    const datesPerWeek = 7;
    function fmtDate(ds) {
        const d = new Date(ds+'T12:00:00');
        const days=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
        const months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        return days[d.getDay()]+', '+months[d.getMonth()]+' '+d.getDate();
    }
    function showDate(date) {
        document.querySelectorAll('.date-section').forEach(s=>s.classList.remove('visible'));
        const sec=document.getElementById('date-'+date);
        if(sec){sec.classList.add('visible');activeDate=date;}
    }
    function renderBubbles() {
        const c=document.getElementById('dateBubbles'); c.innerHTML='';
        const end=Math.min(currentWeekStart+datesPerWeek,allDates.length);
        const week=allDates.slice(currentWeekStart,end);
        if(activeDate && !week.includes(activeDate)){activeDate=week[week.length-1];showDate(activeDate);}
        week.forEach(date=>{
            const b=document.createElement('div'); b.className='date-bubble';
            if(date===today)b.classList.add('today');
            if(date===activeDate)b.classList.add('active');
            b.textContent=fmtDate(date);
            b.onclick=()=>{document.querySelectorAll('.date-bubble').forEach(x=>x.classList.remove('active'));b.classList.add('active');showDate(date);};
            c.appendChild(b);
        });
    }
    function previousWeek(){if(currentWeekStart>0){currentWeekStart=Math.max(0,currentWeekStart-datesPerWeek);renderBubbles();}}
    function nextWeek(){if(currentWeekStart+datesPerWeek<allDates.length){currentWeekStart+=datesPerWeek;renderBubbles();}}
    document.addEventListener('DOMContentLoaded',()=>{
        if(allDates.length>0){
            const lastIdx=allDates.length-1;
            currentWeekStart=Math.max(0,lastIdx-datesPerWeek+1);
            activeDate=allDates[lastIdx];
        }
        showDate(activeDate);renderBubbles();
    });
</script>
""")

# NFL Weekly Results Template
NFL_WEEKLY_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title {
        font-size: 2.5em;
        margin-bottom: 30px;
        text-align: center;
    }
    .section-tabs {
        display: flex;
        gap: 10px;
        margin-bottom: 30px;
        justify-content: center;
    }
    .tab {
        padding: 12px 30px;
        border-radius: 8px;
        text-decoration: none;
        font-weight: 600;
        transition: all 0.3s;
        background: rgba(255, 255, 255, 0.1);
        color: white;
    }
    .tab.active {
        background: linear-gradient(135deg, #10b981, #059669);
    }
    .week-section {
        background: rgba(255, 255, 255, 0.05);
        border-radius: 15px;
        padding: 25px;
        margin-bottom: 30px;
    }
    .week-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 20px;
        padding-bottom: 15px;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
    }
    .week-title {
        font-size: 1.8em;
        color: #fbbf24;
        font-weight: bold;
    }
    .week-models {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 15px;
        margin-bottom: 20px;
    }
    .week-model-card {
        background: rgba(255, 255, 255, 0.1);
        border-radius: 10px;
        padding: 15px;
        text-align: center;
    }
    .week-model-card.best {
        border: 2px solid #10b981;
        background: rgba(16, 185, 129, 0.1);
    }
    .model-label {
        font-size: 0.9em;
        opacity: 0.8;
        margin-bottom: 5px;
    }
    .model-perf {
        font-size: 1.8em;
        font-weight: bold;
        color: #fbbf24;
    }
    .model-record {
        font-size: 0.9em;
        opacity: 0.8;
        margin-top: 5px;
    }
    .games-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.9em;
    }
    .games-table th {
        background: rgba(255, 255, 255, 0.1);
        padding: 10px;
        text-align: left;
        font-weight: bold;
        color: #fbbf24;
        border-bottom: 2px solid rgba(255, 255, 255, 0.2);
    }
    .games-table td {
        padding: 8px 10px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    }
    .games-table tr:hover {
        background: rgba(255, 255, 255, 0.05);
    }
    .score {
        font-weight: bold;
    }
    .winner {
        color: #10b981;
    }
    .loser {
        color: #ef4444;
    }
    .prob-correct {
        color: #10b981;
        font-weight: bold;
    }
    .prob-wrong {
        color: #ef4444;
    }
    .no-data {
        text-align: center;
        padding: 60px 20px;
        font-size: 1.3em;
        opacity: 0.7;
    }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - Week by Week Results</h1>
    
    <div class="section-tabs">
        <a href="/sport/{{ sport }}/predictions" class="tab">📊 Predictions</a>
        <a href="/sport/{{ sport }}/results" class="tab active">🎯 Moneyline Results</a>
        <a href="/sport/{{ sport }}/spreads/results" class="tab">📈 Spreads & Totals Results</a>
    </div>
    
    {% if weekly_results and overall_stats %}
        {% set ens = overall_stats.ensemble %}
        {% set units_won = (ens.correct * 0.91) - (ens.total - ens.correct) %}
        {% set roi = (units_won / ens.total * 100)|round(1) if ens.total > 0 else 0 %}
        <!-- Overall per-model performance -->
        <div style="background: linear-gradient(135deg, #1e293b, #0f172a); border: 2px solid #10b981; border-radius: 15px; padding: 25px; margin-bottom: 25px;">
            <h2 style="text-align: center; margin: 0 0 20px 0; font-size: 1.8em;">🏆 Overall Model Performance &mdash; {{ ens.total }} Games</h2>
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px;">
                {% for m_label, m_key in [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Sharp Consensus','ensemble')] %}
                {% set m = overall_stats[m_key] %}
                <div style="background: rgba(255,255,255,0.08); border-radius: 10px; padding: 15px; text-align: center; {% if m_key == 'ensemble' %}border: 2px solid #fbbf24; grid-column: span 4;{% endif %}">
                    <div style="font-size: 0.9em; opacity: 0.8; margin-bottom: 4px;">{{ m_label }}</div>
                    <div style="font-size: {% if m_key == 'ensemble' %}2.8em{% else %}1.9em{% endif %}; font-weight: bold; color: {% if m.accuracy >= 55 %}#10b981{% elif m.accuracy >= 50 %}#fbbf24{% else %}#ef4444{% endif %};">{{ m.accuracy }}%</div>
                    <div style="font-size: 0.9em; opacity: 0.85;">{{ m.correct }}-{{ m.total - m.correct }}</div>
                </div>
                {% endfor %}
            </div>
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; text-align: center; border-top: 1px solid rgba(255,255,255,0.15); padding-top: 15px;">
                <div><div style="font-size: 0.85em; opacity: 0.8;">Units (Sharp Consensus, -110)</div>
                    <div style="font-size: 1.8em; font-weight: bold; color: {% if units_won >= 0 %}#fbbf24{% else %}#ef4444{% endif %};">{{ "+" if units_won > 0 else "" }}{{ units_won|round(2) }}u</div></div>
                <div><div style="font-size: 0.85em; opacity: 0.8;">ROI</div>
                    <div style="font-size: 1.8em; font-weight: bold; color: {% if roi >= 0 %}#fbbf24{% else %}#ef4444{% endif %};">{{ "+" if roi > 0 else "" }}{{ roi }}%</div></div>
                <div><div style="font-size: 0.85em; opacity: 0.8;">$100/unit P&amp;L</div>
                    <div style="font-size: 1.8em; font-weight: bold; color: {% if units_won >= 0 %}#fbbf24{% else %}#ef4444{% endif %};">{{ "+" if units_won > 0 else "" }}${{ (units_won * 100)|round(0) }}</div></div>
            </div>
        </div>
        {% for week_num in weekly_results|dictsort(reverse=true) %}
        {% set week_data = weekly_results[week_num[0]] %}
        {% set best_acc = [week_data.glicko2.accuracy, week_data.trueskill.accuracy, week_data.elo.accuracy, week_data.xgboost.accuracy, week_data.ensemble.accuracy]|max %}
        <div class="week-section">
            <div class="week-header">
                <div class="week-title">🏈 Week {{ week_num[0] }}</div>
                <div style="opacity: 0.8;">{{ week_data.games|length }} Games</div>
            </div>
            <div class="week-models">
                {% for wm_label, wm_key in [('⭐ Grinder2','glicko2'),('🎯 Takedown','trueskill'),('📊 Edge','elo'),('🤖 XSharp','xgboost'),('🏆 Sharp Consensus','ensemble')] %}
                {% set wm = week_data[wm_key] %}
                <div class="week-model-card {% if wm.accuracy == best_acc %}best{% endif %}">
                    <div class="model-label">{{ wm_label }}</div>
                    <div class="model-perf">{{ wm.accuracy }}%</div>
                    <div class="model-record">{{ wm.correct }}-{{ wm.total - wm.correct }}</div>
                </div>
                {% endfor %}
            </div>
            <table class="games-table">
                <thead><tr>
                    <th>Date</th><th>Matchup</th><th>Score</th>
                    <th>Grinder2</th><th>Takedown</th><th>Edge</th>
                    <th>XSharp</th><th>Sharp Consensus</th>
                </tr></thead>
                <tbody>
                    {% for game in week_data.games %}
                    <tr>
                        <td>{{ game.date }}</td>
                        <td>
                            <span class="{% if game.away_score > game.home_score %}winner{% else %}loser{% endif %}">{{ game.away }}</span> @
                            <span class="{% if game.home_score > game.away_score %}winner{% else %}loser{% endif %}">{{ game.home }}</span>
                        </td>
                        <td class="score">{{ game.away_score }} - {{ game.home_score }}</td>
                        <td class="{% if game.glicko2_correct %}prob-correct{% elif game.glicko2_correct == false %}prob-wrong{% endif %}">{% if game.glicko2_correct is not none %}{% if game.glicko2_correct %}✅{% else %}❌{% endif %} {{ game.glicko2_prob }}%{% else %}N/A{% endif %}</td>
                        <td class="{% if game.trueskill_correct %}prob-correct{% elif game.trueskill_correct == false %}prob-wrong{% endif %}">{% if game.trueskill_correct is not none %}{% if game.trueskill_correct %}✅{% else %}❌{% endif %} {{ game.trueskill_prob }}%{% else %}N/A{% endif %}</td>
                        <td class="{% if game.elo_correct %}prob-correct{% elif game.elo_correct == false %}prob-wrong{% endif %}">{% if game.elo_correct is not none %}{% if game.elo_correct %}✅{% else %}❌{% endif %} {{ game.elo_prob }}%{% else %}N/A{% endif %}</td>
                        <td class="{% if game.xgb_correct %}prob-correct{% elif game.xgb_correct == false %}prob-wrong{% endif %}">{% if game.xgb_correct is not none %}{% if game.xgb_correct %}✅{% else %}❌{% endif %} {{ game.xgb_prob }}%{% else %}N/A{% endif %}</td>
                        <td class="{% if game.ens_correct %}prob-correct{% elif game.ens_correct == false %}prob-wrong{% endif %}">{% if game.ens_correct is not none %}{% if game.ens_correct %}✅{% else %}❌{% endif %} {{ game.ens_prob }}%{% else %}N/A{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endfor %}
    {% else %}
        <div class="no-data">No completed NFL games available yet.</div>
    {% endif %}
""")

# ============================================================================
# ROUTES
# ============================================================================

# Verified season-to-date accuracy numbers shown on the landing page.
# Update these manually when you have fresh backtested results.
_LANDING_ACCURACY = {
    'NHL':  77.0,
    'NFL':  56.8,
    'NBA':  68.5,
    'MLB':  58.0,
    'NCAAB': 65.0,
}


def get_landing_accuracy(sport):
    """Return hardcoded accuracy for the landing page stats bar."""
    return _LANDING_ACCURACY.get(sport, 0.0)

# ── Stripe payment link — replace with your link from dashboard.stripe.com/payment-links
STRIPE_DONATION_URL = 'https://buy.stripe.com/8x228sabu7aV7uj43nao800'

@app.route('/')
def landing_page():
    """Landing page — redesigned with hero, stats, donation, and sport cards"""
    log_site_visit('/')
    nhl_accuracy = get_landing_accuracy('NHL')
    nfl_accuracy = get_landing_accuracy('NFL')
    nba_accuracy = get_landing_accuracy('NBA')

    return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>underdogs.bet — Free AI Sports Predictions</title>
    <meta name="description" content="Free AI-powered sports predictions for NHL, NBA, NFL, MLB, NCAAB and more. 5-model ensemble powered by machine learning.">
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        :root{
            --gold:#fbbf24;--gold2:#f59e0b;
            --green:#10b981;--red:#ef4444;
            --bg:#0f172a;--surface:rgba(255,255,255,0.05);
            --border:rgba(255,255,255,0.1);
        }
        body{
            font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
            background:var(--bg);
            color:#e2e8f0;
            min-height:100vh;
            overflow-x:hidden;
        }

        /* ── Navbar ── */
        nav{
            position:sticky;top:0;z-index:100;
            background:rgba(15,23,42,0.95);
            backdrop-filter:blur(12px);
            border-bottom:1px solid var(--border);
            padding:14px 30px;
            display:flex;align-items:center;justify-content:space-between;
        }
        .nav-logo{
            font-size:1.5em;font-weight:800;
            background:linear-gradient(135deg,var(--gold),var(--gold2));
            -webkit-background-clip:text;-webkit-text-fill-color:transparent;
            text-decoration:none;
        }
        .nav-right{display:flex;gap:20px;align-items:center;}
        .nav-link{color:#94a3b8;text-decoration:none;font-size:0.9em;font-weight:500;transition:color .2s;}
        .nav-link:hover{color:var(--gold);}
        .nav-donate{
            background:linear-gradient(135deg,var(--gold),var(--gold2));
            color:#000;font-weight:700;font-size:0.85em;
            padding:8px 18px;border-radius:20px;
            text-decoration:none;transition:opacity .2s;white-space:nowrap;
        }
        .nav-donate:hover{opacity:.85;}

        /* ── Hero ── */
        .hero{
            text-align:center;
            padding:90px 30px 60px;
            position:relative;
            overflow:hidden;
        }
        .hero::before{
            content:'';
            position:absolute;inset:0;
            background:radial-gradient(ellipse 80% 60% at 50% 0%,rgba(99,102,241,.25) 0%,transparent 70%);
            pointer-events:none;
        }
        .hero-badge{
            display:inline-flex;align-items:center;gap:8px;
            background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.4);
            color:var(--green);font-size:.82em;font-weight:700;
            padding:6px 16px;border-radius:20px;margin-bottom:24px;
            letter-spacing:.5px;
        }
        .hero h1{
            font-size:clamp(2.4em,6vw,4.2em);
            font-weight:900;
            line-height:1.1;
            margin-bottom:18px;
            background:linear-gradient(135deg,#fff 40%,var(--gold));
            -webkit-background-clip:text;-webkit-text-fill-color:transparent;
        }
        .hero-sub{
            font-size:clamp(1em,2.5vw,1.3em);
            color:#94a3b8;
            max-width:600px;
            margin:0 auto 36px;
            line-height:1.6;
        }
        .hero-ctas{display:flex;gap:14px;justify-content:center;flex-wrap:wrap;}
        .btn-primary{
            background:linear-gradient(135deg,#6366f1,#4f46e5);
            color:#fff;font-weight:700;font-size:1em;
            padding:14px 32px;border-radius:10px;
            text-decoration:none;transition:transform .2s,box-shadow .2s;
            box-shadow:0 4px 20px rgba(99,102,241,.4);
        }
        .btn-primary:hover{transform:translateY(-2px);box-shadow:0 6px 28px rgba(99,102,241,.5);}
        .btn-donate-hero{
            background:linear-gradient(135deg,var(--gold),var(--gold2));
            color:#000;font-weight:700;font-size:1em;
            padding:14px 32px;border-radius:10px;
            text-decoration:none;transition:transform .2s,box-shadow .2s;
            box-shadow:0 4px 20px rgba(251,191,36,.3);
        }
        .btn-donate-hero:hover{transform:translateY(-2px);box-shadow:0 6px 28px rgba(251,191,36,.45);}

        /* ── Stats bar ── */
        .stats-bar{
            display:flex;justify-content:center;flex-wrap:wrap;
            gap:0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);
            background:rgba(255,255,255,0.03);
        }
        .stat-item{
            flex:1;min-width:140px;max-width:220px;
            text-align:center;padding:28px 20px;
            border-right:1px solid var(--border);
        }
        .stat-item:last-child{border-right:none;}
        .stat-num{
            font-size:2.2em;font-weight:900;
            background:linear-gradient(135deg,var(--gold),var(--gold2));
            -webkit-background-clip:text;-webkit-text-fill-color:transparent;
        }
        .stat-label{font-size:.8em;color:#64748b;text-transform:uppercase;letter-spacing:.8px;margin-top:4px;}

        /* ── Free banner ── */
        .free-banner{
            max-width:860px;margin:60px auto 0;
            background:linear-gradient(135deg,rgba(16,185,129,.15),rgba(5,150,105,.1));
            border:1px solid rgba(16,185,129,.35);
            border-radius:16px;padding:28px 36px;
            display:flex;gap:20px;align-items:flex-start;
        }
        .free-icon{font-size:2.2em;flex-shrink:0;}
        .free-title{font-size:1.15em;font-weight:800;color:var(--green);margin-bottom:6px;}
        .free-body{font-size:.93em;color:#94a3b8;line-height:1.6;}

        /* ── Sports grid ── */
        .section{padding:70px 30px;max-width:1200px;margin:0 auto;}
        .section-title{
            text-align:center;font-size:1.9em;font-weight:800;
            margin-bottom:8px;
        }
        .section-sub{text-align:center;color:#64748b;font-size:.93em;margin-bottom:40px;}
        .sports-grid{
            display:grid;
            grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
            gap:16px;
        }
        .sport-card{
            background:var(--surface);border:1px solid var(--border);
            border-radius:14px;padding:28px 20px;
            text-align:center;text-decoration:none;color:inherit;
            transition:border-color .2s,transform .2s,box-shadow .2s;
            position:relative;overflow:hidden;
        }
        .sport-card:hover{border-color:var(--gold);transform:translateY(-4px);box-shadow:0 8px 24px rgba(251,191,36,.15);}
        .sport-card.live{border-color:rgba(16,185,129,.4);}
        .sport-card.live:hover{border-color:var(--green);box-shadow:0 8px 24px rgba(16,185,129,.2);}
        .live-dot{
            position:absolute;top:12px;right:12px;
            width:8px;height:8px;border-radius:50%;background:var(--green);
            box-shadow:0 0 0 3px rgba(16,185,129,.25);
            animation:pulse 1.8s infinite;
        }
        @keyframes pulse{
            0%,100%{box-shadow:0 0 0 3px rgba(16,185,129,.25);}
            50%{box-shadow:0 0 0 7px rgba(16,185,129,.0);}
        }
        .sport-icon{font-size:2.8em;margin-bottom:10px;}
        .sport-name{font-size:1.15em;font-weight:700;margin-bottom:4px;}
        .sport-status{font-size:.78em;color:#64748b;text-transform:uppercase;letter-spacing:.5px;}
        .sport-status.live-text{color:var(--green);font-weight:700;}

        /* ── How it works ── */
        .how-section{
            background:rgba(255,255,255,.02);
            border-top:1px solid var(--border);
            border-bottom:1px solid var(--border);
        }
        .steps-grid{
            display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:24px;
        }
        .step{
            background:var(--surface);border:1px solid var(--border);
            border-radius:14px;padding:28px 24px;text-align:center;
        }
        .step-num{
            width:42px;height:42px;border-radius:50%;
            background:linear-gradient(135deg,#6366f1,#4f46e5);
            display:flex;align-items:center;justify-content:center;
            font-weight:900;font-size:1.1em;margin:0 auto 14px;
        }
        .step-title{font-weight:700;font-size:1em;margin-bottom:8px;}
        .step-body{font-size:.86em;color:#64748b;line-height:1.6;}

        /* ── Donation section ── */
        .donate-section{
            max-width:720px;margin:0 auto;
            text-align:center;
        }
        .donate-card{
            background:linear-gradient(135deg,rgba(251,191,36,.1),rgba(245,158,11,.07));
            border:1px solid rgba(251,191,36,.35);
            border-radius:20px;padding:48px 40px;
        }
        .donate-icon{font-size:3em;margin-bottom:16px;}
        .donate-title{font-size:1.8em;font-weight:900;margin-bottom:12px;}
        .donate-body{color:#94a3b8;font-size:.97em;line-height:1.7;margin-bottom:28px;max-width:520px;margin-left:auto;margin-right:auto;}
        .btn-stripe{
            display:inline-flex;align-items:center;gap:10px;
            background:linear-gradient(135deg,var(--gold),var(--gold2));
            color:#000;font-weight:800;font-size:1.05em;
            padding:16px 40px;border-radius:12px;
            text-decoration:none;transition:transform .2s,box-shadow .2s;
            box-shadow:0 4px 20px rgba(251,191,36,.35);
        }
        .btn-stripe:hover{transform:translateY(-3px);box-shadow:0 8px 30px rgba(251,191,36,.5);}
        .donate-note{font-size:.78em;color:#475569;margin-top:14px;}

        /* ── Footer ── */
        .footer{
            border-top:1px solid var(--border);
            padding:36px 30px;
            text-align:center;
            color:#334155;
            font-size:.85em;
        }
        .footer a{color:#475569;text-decoration:none;}
        .footer a:hover{color:var(--gold);}
        .footer-logo{
            font-size:1.3em;font-weight:800;
            background:linear-gradient(135deg,var(--gold),var(--gold2));
            -webkit-background-clip:text;-webkit-text-fill-color:transparent;
            margin-bottom:10px;display:block;
        }

        /* ── Responsive ── */
        @media(max-width:640px){
            .hero{padding:60px 20px 40px;}
            nav{padding:12px 18px;}
            .free-banner{flex-direction:column;}
            .donate-card{padding:36px 24px;}
            .stat-item{min-width:110px;padding:20px 12px;}
            .stats-bar{border-left:none;border-right:none;}
            .nav-right .nav-link{display:none;}
        }
    </style>
</head>
<body>

<!-- Navbar -->
<nav>
    <a href="/" class="nav-logo">🎯 underdogs.bet</a>
    <div class="nav-right">
        <a href="/sport/NHL/predictions" class="nav-link">🏒 NHL</a>
        <a href="/sport/NBA/predictions" class="nav-link">🏀 NBA</a>
        <a href="/sport/MLB/predictions" class="nav-link">⚾ MLB</a>
        <a href="{{ stripe_url }}" target="_blank" class="nav-donate">💛 Support Us</a>
    </div>
</nav>

<!-- Hero -->
<div class="hero">
    <div class="hero-badge">✅ 100% Free &nbsp;·&nbsp; No Sign-Up Required</div>
    <h1>Beat the Books with<br>AI-Powered Picks</h1>
    <p class="hero-sub">
        underdogs.bet runs a 5-model ensemble — Grinder2, Takedown, Edge, XSharp &amp; Sharp Consensus —
        analysing every game so you don't have to.
    </p>
    <div class="hero-ctas">
        <a href="/sport/NHL/predictions" class="btn-primary">📊 View Today's Picks</a>
        <a href="{{ stripe_url }}" target="_blank" class="btn-donate-hero">💛 Support the Site</a>
    </div>

    <!-- Free banner -->
    <div class="free-banner" style="margin-top:48px;">
        <div class="free-icon">🆓</div>
        <div>
            <div class="free-title">Always Free. No Paywalls. No Subscriptions.</div>
            <div class="free-body">
                underdogs.bet is completely free to use — every pick, every sport, every day.
                We run on donations from users who find value in what we build.
                If our models help you, consider supporting us so we can keep improving.
            </div>
        </div>
    </div>
</div>

<!-- Stats bar -->
<div class="stats-bar">
    <div class="stat-item">
        <div class="stat-num">6</div>
        <div class="stat-label">Sports Covered</div>
    </div>
    <div class="stat-item">
        <div class="stat-num">5</div>
        <div class="stat-label">AI Models</div>
    </div>
    <div class="stat-item">
        <div class="stat-num">{{ nhl_accuracy }}%</div>
        <div class="stat-label">NHL Accuracy</div>
    </div>
  <div class="stat-item">
    <div class="stat-num">68.5%</div>
    <div class="stat-label">NBA Accuracy</div>
</div>
    <div class="stat-item">
        <div class="stat-num">FREE</div>
        <div class="stat-label">Forever</div>
    </div>
</div>

<!-- Sports grid -->
<div class="section">
    <h2 class="section-title">Pick Your Sport</h2>
    <p class="section-sub">Live predictions updated daily. Click any sport to view today's picks.</p>
    <div class="sports-grid">
        <a href="/sport/NHL/predictions" class="sport-card live">
            <div class="live-dot"></div>
            <div class="sport-icon">🏒</div>
            <div class="sport-name">NHL</div>
            <div class="sport-status live-text">Live Now</div>
        </a>
        <a href="/sport/NBA/predictions" class="sport-card live">
            <div class="live-dot"></div>
            <div class="sport-icon">🏀</div>
            <div class="sport-name">NBA</div>
            <div class="sport-status live-text">Live Now</div>
        </a>
        <a href="/sport/NCAAB/predictions" class="sport-card live">
            <div class="live-dot"></div>
            <div class="sport-icon">🎓</div>
            <div class="sport-name">NCAAB</div>
            <div class="sport-status live-text">Live Now</div>
        </a>
        <a href="/sport/MLB/predictions" class="sport-card">
            <div class="sport-icon">⚾</div>
            <div class="sport-name">MLB</div>
            <div class="sport-status">Starting Soon</div>
        </a>
        <a href="/sport/NFL/predictions" class="sport-card">
            <div class="sport-icon">🏈</div>
            <div class="sport-name">NFL</div>
            <div class="sport-status">Offseason</div>
        </a>
        <a href="/sport/NCAAF/predictions" class="sport-card">
            <div class="sport-icon">🏟️</div>
            <div class="sport-name">NCAAF</div>
            <div class="sport-status">Offseason</div>
        </a>
        <a href="/sport/WNBA/predictions" class="sport-card">
            <div class="sport-icon">🏀</div>
            <div class="sport-name">WNBA</div>
            <div class="sport-status">Coming Soon</div>
        </a>
    </div>
</div>

<!-- How it works -->
<div class="how-section">
    <div class="section">
        <h2 class="section-title">How It Works</h2>
        <p class="section-sub">Five independent models vote on every game. The Sharp Consensus is the final call.</p>
        <div class="steps-grid">
            <div class="step">
                <div class="step-num">1</div>
                <div class="step-title">Live Data Ingestion</div>
                <div class="step-body">We pull real-time scores, team stats, and schedules from ESPN and official league APIs every day.</div>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <div class="step-title">5-Model Ensemble</div>
                <div class="step-body">Grinder2, Takedown, Edge, XSharp, and Sharp Consensus each generate independent win probabilities, then the final pick is blended from all five.</div>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <div class="step-title">Spread &amp; Total Predictions</div>
                <div class="step-body">XSharp predicts expected scores, derives the spread and total, and — for NHL — converts to puck-line cover probabilities.</div>
            </div>
            <div class="step">
                <div class="step-num">4</div>
                <div class="step-title">You Get the Pick</div>
                <div class="step-body">The Sharp Consensus blends all five models. High-confidence picks are highlighted. All results are tracked so you can verify our accuracy.</div>
            </div>
        </div>
    </div>
</div>

<!-- Donation -->
<div class="section">
    <div class="donate-section">
        <div class="donate-card">
            <div class="donate-icon">💛</div>
            <div class="donate-title">Support underdogs.bet</div>
            <div class="donate-body">
                This site is 100% free and always will be. We never charge for picks or lock content behind a paywall.
                <br><br>
                If our models are helping your research, a small donation goes directly toward
                <strong>server costs, data feeds, and paying our developers</strong> who keep the models sharp.
            </div>
            <a href="{{ stripe_url }}" target="_blank" class="btn-stripe">
                <span>💳</span> Donate via Stripe
            </a>
            <div class="donate-note">Powered by Stripe · Secure &amp; encrypted · Any amount helps</div>
        </div>
    </div>
</div>

<!-- Footer -->
<div class="footer">
    <span class="footer-logo">🎯 underdogs.bet</span>
    <p>AI-powered sports predictions — free forever.</p>
    <p style="margin-top:10px;">
        <a href="/sport/NHL/predictions">NHL</a> &nbsp;·&nbsp;
        <a href="/sport/NBA/predictions">NBA</a> &nbsp;·&nbsp;
        <a href="/sport/MLB/predictions">MLB</a> &nbsp;·&nbsp;
        <a href="/sport/NFL/predictions">NFL</a> &nbsp;·&nbsp;
        <a href="/sport/NCAAB/predictions">NCAAB</a> &nbsp;·&nbsp;
        <a href="{{ stripe_url }}" target="_blank">💛 Donate</a>
    </p>
    <p style="margin-top:12px;opacity:.5;">© 2025 underdogs.bet · underdogsbetemail@gmail.com</p>
</div>

</body>
</html>
    """, nhl_accuracy=nhl_accuracy, nfl_accuracy=nfl_accuracy, nba_accuracy=nba_accuracy,
         stripe_url=STRIPE_DONATION_URL)

@app.route('/sport/<sport>')
def sport_home(sport):
    """Redirect to predictions page"""
    return render_template_string(f"""
        <script>window.location.href = '/sport/{sport}/predictions';</script>
    """)

@app.route('/sport/<sport>/predictions')
def sport_predictions(sport):
    """Show upcoming predictions for a sport"""
    log_site_visit(f'/sport/{sport}/predictions')
    if sport not in SPORTS:
        return "Sport not found", 404
    prediction_error = None
    try:
        predictions = get_upcoming_predictions(sport)
    except Exception as e:
        logger.error(f"Error loading {sport} predictions: {e}")
        predictions = []
        prediction_error = (
            f"N/A — {sport} predictions could not be loaded because an upstream data/model dependency failed. "
            "Please refresh in a minute."
        )
    
    # Group predictions by date for NHL/NBA, by week for NFL
    from collections import defaultdict
    grouped_predictions = defaultdict(list)
    today_date = datetime.now().strftime('%Y-%m-%d')
    
    if sport in ['NHL', 'NBA']:
        # Group by date
        for pred in predictions:
            date_key = pred['game_date']
            grouped_predictions[date_key].append(pred)
    elif sport == 'NFL':
        # Group by week (extract from game data or calculate)
        for pred in predictions:
            # For NFL, we can use week numbers if available, otherwise group by date
            date_key = pred.get('week', pred['game_date'])
            grouped_predictions[date_key].append(pred)
    else:
        # Default: group by date
        for pred in predictions:
            date_key = pred['game_date']
            grouped_predictions[date_key].append(pred)
    
    # Sort dates
    sorted_dates = sorted(grouped_predictions.keys())
    
    # Load ESPN-style template (absolute path so Render/gunicorn always finds it)
    with open(_os.path.join(_BASE_DIR, 'espn_predictions_template.html'), 'r') as f:
        espn_template = f.read()
    
    return render_template_string(
        espn_template,
        page=sport,
        sport=sport,
        sport_info=SPORTS[sport],
        predictions=predictions,
        prediction_error=prediction_error,
        grouped_predictions=grouped_predictions,
        sorted_dates=sorted_dates,
        today_date=today_date,
        group_by='week' if sport == 'NFL' else 'date'
    )

@app.route('/sport/<sport>/results')
def sport_results(sport):
    """Show model performance results for a sport"""
    try:
        if sport not in SPORTS:
            return "Sport not found", 404
        
        if sport == 'NFL':
            update_nfl_scores()
            weekly_results = calculate_nfl_weekly_performance()
            overall_stats = compute_overall_stats_from_weekly(weekly_results) if weekly_results else {}
            return render_template_string(
                NFL_WEEKLY_RESULTS_TEMPLATE,
                page=sport,
                sport=sport,
                sport_info=SPORTS[sport],
                weekly_results=weekly_results,
                overall_stats=overall_stats
            )
        
        if sport == 'NHL':
            cache_key = f'{sport}_moneyline_results_html'
            cache_ttl = _SPORT_RESULTS_TTL_BY_SPORT.get(sport, 300)
            cached_page = _SPORT_RESULTS_CACHE.get(cache_key)
            if isinstance(cached_page, dict):
                cached_ts = cached_page.get('ts')
                cached_html = cached_page.get('html')
                if cached_ts is not None and cached_html and (_time.time() - cached_ts) < cache_ttl:
                    return cached_html

            try:
                # Run NHL score sync at most once every 10 minutes for this process.
                sync_key = f'{sport}_results_score_sync_ts'
                sync_entry = _SPORT_RESULTS_CACHE.get(sync_key)
                sync_last_ts = sync_entry.get('ts') if isinstance(sync_entry, dict) else None
                now_ts = _time.time()
                if sync_last_ts is None or (now_ts - sync_last_ts) >= 600:
                    update_nhl_scores()
                    _SPORT_RESULTS_CACHE[sync_key] = {'ts': now_ts}
            except Exception as e:
                logger.error(f"NHL score sync failed (continuing with existing data): {e}")
            weekly_results = calculate_nhl_weekly_performance()
            
            if not weekly_results:
                return "<h1>N/A — NHL results could not be loaded because no completed NHL games were available for grading yet.</h1>"
            
            # Regroup by date instead of week
            from collections import defaultdict
            daily_results = defaultdict(lambda: {'games': []})
            today_date = datetime.now().strftime('%Y-%m-%d')
            
            try:
                for week, week_data in weekly_results.items():
                    for game in week_data['games']:
                        date_key = game['date']
                        daily_results[date_key]['games'].append(game)
                
                # Filter to only show dates up to yesterday
                yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
                sorted_dates = sorted([d for d in daily_results.keys() if d <= yesterday], reverse=True)
                
                overall_stats = compute_overall_stats_from_daily(daily_results)
                _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)
                rendered = render_template_string(
                    DAILY_RESULTS_TEMPLATE,
                    page=sport, sport=sport, sport_info=SPORTS[sport],
                    daily_results=daily_results, sorted_dates=sorted_dates,
                    today_date=today_date, overall_stats=overall_stats,
                    total_over=_ov, total_under=_un, total_games_ou=_gou,
                    avg_total=_avg, ou_bench=_bench
                )
                _SPORT_RESULTS_CACHE[cache_key] = {'ts': _time.time(), 'html': rendered}
                return rendered
            except Exception as e:
                logger.error(f"Error processing NHL results: {e}")
                return f"<h1>N/A — NHL results page failed to render because of a processing error: {str(e)}</h1>"
        
        if sport == 'NBA':
            update_nba_scores()
            weekly_results = calculate_nba_weekly_performance()
            logger.info(f"NBA weekly_results: {weekly_results is not None}, weeks: {list(weekly_results.keys()) if weekly_results else 'None'}")
            if not weekly_results:
                return "<h1>No NBA results data available yet. Check back after more games are played.</h1>"
            
            # Regroup by date instead of week
            from collections import defaultdict
            daily_results = defaultdict(lambda: {'games': []})
            today_date = datetime.now().strftime('%Y-%m-%d')
            
            for week, week_data in weekly_results.items():
                for game in week_data['games']:
                    date_key = game['date']
                    daily_results[date_key]['games'].append(game)
            
            # Filter to only show dates up to yesterday
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            sorted_dates = sorted([d for d in daily_results.keys() if d <= yesterday], reverse=True)
            
            overall_stats = compute_overall_stats_from_daily(daily_results)
            _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)
            return render_template_string(
                DAILY_RESULTS_TEMPLATE,
                page=sport, sport=sport, sport_info=SPORTS[sport],
                daily_results=daily_results, sorted_dates=sorted_dates,
                today_date=today_date, overall_stats=overall_stats,
                total_over=_ov, total_under=_un, total_games_ou=_gou,
                avg_total=_avg, ou_bench=_bench
            )

        # Handle NCAAB
        if sport in ['NCAAB', 'NCAAF', 'MLB', 'WNBA']:
            # Update scores first
            update_espn_scores(sport)
            
            # Get completed games from database
            conn = get_db_connection()
            completed_games = conn.execute('''
                SELECT g.*, p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
                FROM games g
                LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
                WHERE g.sport = ? AND g.home_score IS NOT NULL
                ORDER BY g.game_date DESC
                LIMIT 100
            ''', (sport, sport)).fetchall()
            conn.close()
            
            if not completed_games:
                # Show message for offseason sports
                offseason_msg = "" 
                if sport in ['MLB', 'WNBA']:
                    offseason_msg = f"<p>The {SPORTS[sport]['name']} season has ended. Results from the 2025 season will be available next year.</p>"
                return f"<h1>No {SPORTS[sport]['name']} results data available yet.</h1>{offseason_msg}<p><a href='/'>← Back to Home</a></p>"
            
            # Process into daily results format
            from collections import defaultdict
            daily_results = defaultdict(lambda: {'games': []})
            today_date = datetime.now().strftime('%Y-%m-%d')
            
            for game in completed_games:
                home_won = game['home_score'] > game['away_score']
                home_team = game['home_team_id']
                away_team = game['away_team_id']
                game_date  = game['game_date'][:10] if game['game_date'] else None

                # Stored DB probs
                elo_prob  = float(game['elo_home_prob']       or 0.5)
                xgb_prob  = float(game['xgboost_home_prob']   or game['elo_home_prob'] or 0.5)
                ens_prob  = float(game['win_probability']      or game['elo_home_prob'] or 0.5)

                # V2 model predictions (Glicko-2, TrueSkill)
                v2 = get_v2_prediction(sport, home_team, away_team, game_date)
                glicko2_prob   = v2.get('glicko2_prob')   if v2 else None
                trueskill_prob = v2.get('trueskill_prob') if v2 else None
                if v2:
                    xgb_prob = v2.get('xgboost_prob', xgb_prob)
                    ens_prob = _compute_ensemble_prob(glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob)

                game_info = {
                    'date':             game_date or 'Unknown',
                    'home':             home_team,
                    'away':             away_team,
                    'home_score':       game['home_score'],
                    'away_score':       game['away_score'],
                    'home_win':         home_won,
                    'glicko2_prob':     round(glicko2_prob   * 100, 1) if glicko2_prob   is not None else None,
                    'trueskill_prob':   round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
                    'elo_prob':         round(elo_prob  * 100, 1),
                    'xgb_prob':         round(xgb_prob  * 100, 1),
                    'ens_prob':         round(ens_prob  * 100, 1),
                    'glicko2_correct':   (glicko2_prob   > 0.5) == home_won if glicko2_prob   is not None else None,
                    'trueskill_correct': (trueskill_prob > 0.5) == home_won if trueskill_prob is not None else None,
                    'elo_correct':       (elo_prob  > 0.5) == home_won,
                    'xgb_correct':       (xgb_prob  > 0.5) == home_won,
                    'ens_correct':       (ens_prob  > 0.5) == home_won,
                }
                daily_results[game_info['date']]['games'].append(game_info)

            sorted_dates = sorted(daily_results.keys(), reverse=True)[:30]
            overall_stats = compute_overall_stats_from_daily(daily_results)
            _ov, _un, _gou, _avg, _bench = _ou_stats(daily_results, sport)

            return render_template_string(
                DAILY_RESULTS_TEMPLATE,
                page=sport, sport=sport, sport_info=SPORTS[sport],
                daily_results=daily_results, sorted_dates=sorted_dates,
                today_date=today_date, overall_stats=overall_stats,
                total_over=_ov, total_under=_un, total_games_ou=_gou,
                avg_total=_avg, ou_bench=_bench
            )
        
        performance = calculate_model_performance(sport)
        return render_template_string(
            RESULTS_TEMPLATE,
            page=sport,
            sport=sport,
            sport_info=SPORTS[sport],
            performance=performance
        )
    except Exception as e:
        logger.exception(f"Error loading /sport/{sport}/results: {e}")
        return (
            f"<h1>N/A — {sport} moneyline results are temporarily unavailable because the server hit an internal processing error.</h1>"
            f"<p>Please refresh in 30-60 seconds. If it persists, the latest server traceback is now logged for diagnosis.</p>"
        ), 200

def get_upcoming_api_games_for_spreads(sport, days_ahead=7):
    """Get upcoming games from API for spread/total picks (next N days)"""
    api_games = []
    
    if sport == 'NHL':
        try:
            nhl_api = NHLAPI()
            api_games_raw = nhl_api.get_recent_and_upcoming_games(days_back=0, days_forward=days_ahead)
            # Normalize keys to match what spreads generator expects
            api_games = []
            for game in api_games_raw:
                api_games.append({
                    'home_team_name': game.get('home_team_name'),
                    'away_team_name': game.get('away_team_name'),
                    'game_date': game.get('game_date')
                })
        except Exception as e:
            logger.error(f"Error fetching NHL games from API: {e}")
    
    elif sport in ['NBA', 'NCAAB', 'NCAAF', 'MLB', 'WNBA']:
        ESPN_ENDPOINTS = {
            'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
            'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
            'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
            'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
            'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
        }
        
        # Fetch games from ESPN API (next N days)
        for days_offset in range(0, days_ahead + 1):
            check_date = datetime.now() + timedelta(days=days_offset)
            date_str = check_date.strftime('%Y%m%d')
            
            try:
                url = f"{ESPN_ENDPOINTS[sport]}?dates={date_str}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                events = data.get('events', [])
                
                for event in events:
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    
                    if len(competitors) != 2:
                        continue
                    
                    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    
                    if not home or not away:
                        continue
                    
                    home_team = home.get('team', {}).get('displayName', '')
                    away_team = away.get('team', {}).get('displayName', '')
                    
                    # Get status to skip completed games
                    status_info = event.get('status', {}).get('type', {})
                    status_name = status_info.get('name', 'scheduled')
                    
                    # Skip completed games
                    if status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT', 'STATUS_FINAL_OT2']:
                        continue
                    
                    api_games.append({
                        'home_team_name': home_team,
                        'away_team_name': away_team,
                        'game_date': check_date.strftime('%Y-%m-%d'),
                    })
            except Exception as e:
                logger.debug(f"Error fetching {sport} for {date_str}: {e}")
    
    elif sport == 'NFL':
        # NFL: Pull from ESPN API similar to other sports
        try:
            api_games_raw = []
            for days_offset in range(0, days_ahead + 1):
                check_date = datetime.now() + timedelta(days=days_offset)
                date_str = check_date.strftime('%Y%m%d')
                
                url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={date_str}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                
                events = data.get('events', [])
                
                for event in events:
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    
                    if len(competitors) != 2:
                        continue
                    
                    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    
                    if not home or not away:
                        continue
                    
                    home_team = home.get('team', {}).get('displayName', '')
                    away_team = away.get('team', {}).get('displayName', '')
                    
                    status_info = event.get('status', {}).get('type', {})
                    status_name = status_info.get('name', 'scheduled')
                    
                    if status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT', 'STATUS_FINAL_OT2']:
                        continue
                    
                    api_games_raw.append({
                        'home_team_name': home_team,
                        'away_team_name': away_team,
                        'game_date': check_date.strftime('%Y-%m-%d'),
                    })
            api_games = api_games_raw
        except Exception as e:
            logger.error(f"Error fetching NFL games from API: {e}")
    
    return api_games

@app.route('/sport/<sport>/spreads')
def sport_spread_total_picks(sport):
    """Redirect to predictions page (spreads now shown inline on predictions card)"""
    if sport not in SPORTS:
        return "Sport not found", 404
    return redirect(url_for('sport_predictions', sport=sport))


def _sport_spread_total_picks_old(sport):
    """Old spread/total picks implementation (kept for reference)"""
    from score_predictor import ScorePredictor
    predictor = ScorePredictor()
    
    # Get upcoming games from API
    api_games = get_upcoming_api_games_for_spreads(sport, days_ahead=7)
    
    # If no upcoming games (off-season), show historical spread/total picks from database
    is_offseason = len(api_games) == 0
    
    if is_offseason:
        # Fetch recent historical games from database with predictions
        conn = get_db_connection()
        historical_games = conn.execute('''
            SELECT g.game_date, g.home_team_id, g.away_team_id, g.home_score, g.away_score
            FROM games g
            WHERE g.sport = ?
              AND g.home_score IS NOT NULL
          AND g.status = 'final'
            ORDER BY g.game_date DESC
            LIMIT 50
        ''', (sport,)).fetchall()
        conn.close()
        
        # Convert to format expected by predictor
        api_games = []
        for game in historical_games:
            api_games.append({
                'home_team_name': game['home_team_id'],
                'away_team_name': game['away_team_id'],
                'game_date': game['game_date'][:10] if game['game_date'] else 'Unknown',
                'actual_home_score': game['home_score'],
                'actual_away_score': game['away_score'],
            })
    
    # Generate picks from API games using the new method
    picks = predictor.generate_spread_total_picks_from_api_games(sport, api_games)
    
    # Add weighted average total analysis for ALL sports
    from weighted_total_predictor import calculate_weighted_average_total
    for pick in picks:
        try:
            # Safely handle Vegas total (handle 'N/A', strings, etc)
            v_total = pick.get('vegas_total')
            if v_total == 'N/A' or v_total is None:
                v_total = None
            else:
                try:
                    v_total = float(v_total)
                except:
                    v_total = None

            weighted_result = calculate_weighted_average_total(
                pick['away_team'],
                pick['home_team'],
                v_total,
                sport=sport
            )
            pick['weighted_total_data'] = weighted_result
        except Exception as e:
            logger.warning(f"Could not calculate weighted total for {pick['away_team']} @ {pick['home_team']}: {e}")
            pick['weighted_total_data'] = None
    
    # Group by date
    from collections import defaultdict
    grouped_picks = defaultdict(list)
    today_date = datetime.now().strftime('%Y-%m-%d')
    
    for pick in picks:
        grouped_picks[pick['game_date']].append(pick)
    
    # Sort dates - most recent first for historical data, chronological for upcoming
    sorted_dates = sorted(grouped_picks.keys(), reverse=is_offseason)
    
    # Simple template for spread/total picks
    template = """
<!DOCTYPE html>
<html>
<head>
    <title>{{ sport_info.name }} - Spread & Total Picks</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 30px; padding: 25px; background: rgba(255,255,255,0.05); border-radius: 15px; }
        h1 { font-size: 2.5em; margin-bottom: 10px; }
        .tabs { display: flex; gap: 10px; margin-bottom: 20px; justify-content: center; }
        .tab { padding: 12px 30px; border-radius: 8px; text-decoration: none; font-weight: 600; transition: all 0.3s; background: rgba(255, 255, 255, 0.1); color: white; }
        .tab.active { background: linear-gradient(135deg, #8b5cf6, #7c3aed); }
        .date-section { margin-bottom: 40px; }
        .date-header { font-size: 1.5em; margin-bottom: 20px; color: #fbbf24; font-weight: 600; }
        .games-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(500px, 1fr)); gap: 20px; }
        .game-card { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; padding: 20px; }
        .game-card:hover { background: rgba(255,255,255,0.08); border-color: #8b5cf6; }
        .matchup { font-size: 1.2em; font-weight: 600; margin-bottom: 15px; }
        .prediction-row { display: flex; justify-content: space-between; padding: 8px 0; border-top: 1px solid rgba(255,255,255,0.1); }
        .prediction-label { color: #a78bfa; font-weight: 500; }
        .prediction-value { font-weight: 600; }
        .spread-pick { color: #10b981; }
        .total-pick { color: #fbbf24; }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" style="display: inline-block; padding: 10px 20px; background: rgba(255,255,255,0.1); border-radius: 8px; text-decoration: none; color: white; margin-bottom: 20px; font-weight: 600;">← Back to Home</a>
        
        <div class="header">
            <h1>{{ sport_info.icon }} {{ sport_info.name }} - Spread & Total Picks</h1>
            <p>Based on Statistical Model (Team Offensive/Defensive Efficiency)</p>
            <p style="font-size: 0.9em; opacity: 0.8; margin-top: 10px;">Note: Vegas lines not available via free APIs</p>
        </div>
        
        <div class="tabs">
            <a href="/sport/{{ sport }}/predictions" class="tab">📊 Predictions</a>
            <a href="/sport/{{ sport }}/results" class="tab">🎯 Win/Loss Results</a>
            <a href="/sport/{{ sport }}/spreads" class="tab active">📈 Spreads & Totals</a>
            <a href="/sport/{{ sport }}/spreads/results" class="tab">📊 Spread/Total Results</a>
        </div>
        
        {% if is_offseason %}
        <div style="background: rgba(251, 191, 36, 0.2); border: 2px solid #fbbf24; border-radius: 12px; padding: 20px; margin-bottom: 30px; text-align: center;">
            <p style="font-size: 1.2em; font-weight: 600; color: #fbbf24;">📅 {{ sport_info.name }} is currently in the off-season</p>
            <p style="opacity: 0.8; margin-top: 10px;">Showing historical spread & total predictions from the most recent games</p>
        </div>
        {% endif %}
        
        {% if grouped_picks %}
            {% for date in sorted_dates %}
            <div class="date-section">
                <div class="date-header">
                    📅 {{ date }}
                    {% if date == today_date %}<span style="background: #10b981; color: white; padding: 4px 12px; border-radius: 4px; font-size: 0.8em; margin-left: 10px;">TODAY</span>{% endif %}
                </div>
                <div class="games-grid">
                    {% for pick in grouped_picks[date] %}
                    <div class="game-card">
                        <div class="matchup">{{ pick.away_team }} @ {{ pick.home_team }}</div>
                        <div class="prediction-row">
                            <span class="prediction-label">Predicted Score</span>
                            <span class="prediction-value">{{ pick.predicted_away_score }} - {{ pick.predicted_home_score }}</span>
                        </div>
                        <div class="prediction-row">
                            <span class="prediction-label">Vegas Spread</span>
                            <span class="prediction-value">{% if pick.vegas_spread %}{{ pick.home_team }} {{ pick.vegas_spread }}{% else %}N/A{% endif %}</span>
                        </div>
                        <div class="prediction-row">
                            <span class="prediction-label">Our Spread</span>
                            <span class="prediction-value spread-pick">{{ pick.spread_pick }} {{ pick.predicted_spread|abs|round(1) }}</span>
                        </div>
                        {% if pick.spread_comparison %}
                        <div class="prediction-row">
                            <span class="prediction-label">Spread Analysis</span>
                            <span class="prediction-value" style="font-size: 0.9em; color: #a78bfa;">{{ pick.spread_comparison }}</span>
                        </div>
                        {% endif %}
                        <div class="prediction-row" style="border-top: 2px solid rgba(255,255,255,0.2); margin-top: 10px; padding-top: 10px;">
                            <span class="prediction-label">Vegas Total</span>
                            <span class="prediction-value">{% if pick.vegas_total %}{{ pick.vegas_total }}{% else %}N/A{% endif %}</span>
                        </div>
                        <div class="prediction-row">
                            <span class="prediction-label">Our Total</span>
                            <span class="prediction-value">{{ pick.predicted_total }}</span>
                        </div>
                        {% if pick.total_pick %}
                        <div class=\"prediction-row\">
                            <span class=\"prediction-label\">Total Pick</span>
                            <span class=\"prediction-value total-pick\">{{ pick.total_pick }} ({{ pick.total_comparison }})</span>
                        </div>
                        {% endif %}
                        {% if pick.weighted_total_data %}
                        <div class=\"prediction-row\" style=\"border-top: 2px solid rgba(255,255,255,0.2); margin-top: 10px; padding-top: 10px;\">
                            <span class=\"prediction-label\" style=\"color: #fbbf24; font-weight: 700;\">⭐ Weighted Avg Total</span>
                            <span class=\"prediction-value\" style=\"color: #fbbf24; font-weight: 700;\">{{ pick.weighted_total_data.projected_total }}</span>
                        </div>
                        {% if pick.weighted_total_data.error %}
                        <div class=\"prediction-row\">
                            <span class=\"prediction-label\">Status</span>
                            <span class=\"prediction-value\" style=\"font-size: 0.85em; color: #f87171;\">{{ pick.weighted_total_data.error }}</span>
                        </div>
                        {% else %}
                        <div class=\"prediction-row\">
                            <span class=\"prediction-label\">Team Averages</span>
                            <span class=\"prediction-value\" style=\"font-size: 0.9em;\">{{ pick.away_team[:3] }}: {{ pick.weighted_total_data.teamB_avg }} | {{ pick.home_team[:3] }}: {{ pick.weighted_total_data.teamA_avg }}</span>
                        </div>
                        <div class=\"prediction-row\">
                            <span class=\"prediction-label\">Over Trend</span>
                            <span class=\"prediction-value\" style=\"font-size: 0.9em;\">{{ pick.weighted_total_data.combined_over_count }}/6</span>
                        </div>
                        <div class=\"prediction-row\">
                            <span class=\"prediction-label\" style=\"font-weight: 700;\">💡 Recommendation</span>
                            <span class=\"prediction-value\" style=\"font-weight: 700; color: {% if pick.weighted_total_data.recommended_bet == 'OVER' %}#10b981{% elif pick.weighted_total_data.recommended_bet == 'UNDER' %}#f87171{% else %}#a78bfa{% endif %};\">{{ pick.weighted_total_data.recommended_bet }}</span>
                        </div>
                        {% endif %}
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
            </div>
            {% endfor %}
        {% else %}
        <div style="text-align: center; padding: 60px; opacity: 0.7;">
            No spread/total picks available for {{ sport_info.name }}
        </div>
        {% endif %}
    </div>
</body>
</html>
    """
    
    return render_template_string(
        template,
        page=sport,
        sport=sport,
        sport_info=SPORTS[sport],
        grouped_picks=grouped_picks,
        sorted_dates=sorted_dates,
        today_date=today_date,
        is_offseason=is_offseason
    )

SPREAD_TOTAL_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title { font-size: 2.2em; margin-bottom: 20px; text-align: center; }
    .section-tabs { display: flex; gap: 10px; margin-bottom: 20px; justify-content: center; }
    .tab { padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; background: rgba(255,255,255,0.1); color: white; }
    .tab.active { background: linear-gradient(135deg, #10b981, #059669); }
    .overall-stats { background: linear-gradient(135deg, #1e293b, #0f172a); border: 2px solid #10b981; border-radius: 14px; padding: 20px; margin-bottom: 20px; }
    .overall-stats h2 { text-align: center; margin: 0 0 14px 0; color: #10b981; }
    .stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
    .stat-card { background: rgba(255,255,255,0.08); border-radius: 10px; padding: 12px; text-align: center; }
    .stat-label { font-size: 0.8em; opacity: 0.85; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.3px; }
    .stat-value { font-size: 1.7em; font-weight: 700; color: #fbbf24; }
    .stat-detail { font-size: 0.85em; opacity: 0.85; }
    .note-box { background: rgba(251,191,36,0.12); border: 1px solid rgba(251,191,36,0.35); border-radius: 10px; padding: 10px 12px; margin-bottom: 15px; font-size: 0.9em; color: #fde68a; }
    .date-section { background: rgba(255,255,255,0.05); border-radius: 12px; padding: 16px; margin-bottom: 16px; overflow-x: auto; }
    .date-header { color: #fbbf24; font-size: 1.2em; margin-bottom: 10px; border-bottom: 1px solid rgba(255,255,255,0.2); padding-bottom: 8px; }
    .daily-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 10px; }
    .daily-card { background: rgba(255,255,255,0.08); border-radius: 8px; padding: 10px; text-align: center; }
    .daily-card .k { font-size: 0.78em; opacity: 0.8; }
    .daily-card .v { font-size: 1.2em; font-weight: 700; color: #fbbf24; }
    .games-table { width: 100%; border-collapse: collapse; font-size: 0.84em; min-width: 1080px; }
    .games-table th { background: rgba(255,255,255,0.12); color: #fbbf24; padding: 8px; border-bottom: 2px solid rgba(255,255,255,0.25); text-align: center; }
    .games-table td { padding: 8px; border-bottom: 1px solid rgba(255,255,255,0.12); text-align: center; }
    .games-table td.left, .games-table th.left { text-align: left; }
    .good { color: #10b981; font-weight: 700; }
    .bad { color: #ef4444; font-weight: 700; }
    .na { color: #9ca3af; opacity: 0.9; }
    """
).replace('{% block content %}{% endblock %}', """
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} — Spread &amp; Totals Results (XSharp)</h1>
    <div class="section-tabs">
        <a href="/sport/{{ sport }}/predictions" class="tab">📊 Predictions</a>
        <a href="/sport/{{ sport }}/results" class="tab">🎯 Moneyline Results</a>
        <a href="/sport/{{ sport }}/spreads/results" class="tab active">📈 Spreads &amp; Totals Results</a>
    </div>

    {% if daily_results %}
        <div class="overall-stats">
            <h2>🏆 XSharp Performance — {{ total_games }} Games (Last {{ lookback_days }} Days)</h2>
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-label">Spread Cover Accuracy</div>
                    <div class="stat-value">{{ "%.1f"|format(xgb_spread_cover_pct) }}%</div>
                    <div class="stat-detail">{{ xgb_spread_covered }} correct / {{ xgb_spread_graded }} graded</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Total Pick Accuracy</div>
                    <div class="stat-value">{{ "%.1f"|format(xgb_total_correct_pct) }}%</div>
                    <div class="stat-detail">{{ xgb_total_correct }} correct / {{ xgb_total_graded }} graded</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Average Error</div>
                    <div class="stat-value">S {{ "%.1f"|format(xgb_avg_spread_err) }} / T {{ "%.1f"|format(xgb_avg_total_err) }}</div>
                    <div class="stat-detail">Spread margin / Game total</div>
                </div>
            </div>
        </div>

        {% if missing_spread_lines > 0 or missing_total_lines > 0 %}
        <div class="note-box">
            N/A is shown when betting line data is unavailable in the database.
            Missing spread lines: {{ missing_spread_lines }} games.
            Missing total lines: {{ missing_total_lines }} games.
            Latest stored {{ sport }} line date: {{ latest_db_line_date if latest_db_line_date else 'N/A' }}.
            {% if live_line_fetch_attempted > 0 %}
            Live fallback fetched {{ live_line_fetch_success }} of {{ live_line_fetch_attempted }} missing recent games.
            {% endif %}
        </div>
        {% endif %}
        {% if thresholds_active %}
        <div class="note-box">
            Quality guardrail active: only grade picks when edge clears threshold.
            Spread threshold: {{ "%.1f"|format(spread_edge_threshold) }}.
            Total threshold: {{ "%.1f"|format(total_edge_threshold) }}.
            Spread PASS (not graded): {{ spread_pass_count }}.
            Total PASS (not graded): {{ total_pass_count }}.
        </div>
        {% endif %}

        {% for date in sorted_dates %}
        {% set date_data = daily_results[date] %}
        <div class="date-section">
            <div class="date-header">📅 {{ date }}{% if date == today_date %} <span style="background:#10b981;color:white;padding:2px 8px;border-radius:4px;font-size:0.7em;margin-left:8px;">TODAY</span>{% endif %}</div>
            <div class="daily-summary">
                <div class="daily-card"><div class="k">Games</div><div class="v">{{ date_data.total_games }}</div></div>
                <div class="daily-card"><div class="k">Spread</div><div class="v">{{ date_data.xgb_spread_covered }}/{{ date_data.xgb_spread_graded }}</div></div>
                <div class="daily-card"><div class="k">Totals</div><div class="v">{{ date_data.xgb_total_correct }}/{{ date_data.xgb_total_graded }}</div></div>
                <div class="daily-card"><div class="k">Avg Total</div><div class="v">{{ date_data.avg_actual_total }}</div></div>
            </div>

            <table class="games-table">
                <thead>
                    <tr>
                        <th class="left">Matchup</th>
                        <th>Score</th>
                        <th>Market Spread</th>
                        <th>XSharp Spread</th>
                        <th>Spread Pick</th>
                        <th>Actual Margin</th>
                        <th>Covered?</th>
                        <th>Market Total</th>
                        <th>XSharp Total</th>
                        <th>Total Pick</th>
                        <th>Actual Total</th>
                        <th>Total Correct?</th>
                    </tr>
                </thead>
                <tbody>
                {% for game in date_data.games %}
                    <tr>
                        <td class="left"><strong>{{ game.matchup }}</strong></td>
                        <td>{{ game.score }}</td>
                        <td>{% if game.market_spread is not none %}{{ "%+.1f"|format(game.market_spread) }}{% else %}<span class="na" title="{{ game.line_reason if game.line_reason else 'No market spread found' }}">N/A</span>{% endif %}</td>
                        <td>{% if game.xgb_spread is not none %}{{ "%+.1f"|format(game.xgb_spread) }}{% else %}<span class="na">N/A</span>{% endif %}</td>
                        <td>{{ game.spread_pick if game.spread_pick else 'N/A' }}</td>
                        <td>{{ "%+.1f"|format(game.actual_margin) }}</td>
                        <td>{% if game.pick_covered is none %}<span class="na">N/A</span>{% elif game.pick_covered %}<span class="good">true</span>{% else %}<span class="bad">false</span>{% endif %}</td>
                        <td>{% if game.market_total is not none %}{{ "%.1f"|format(game.market_total) }}{% else %}<span class="na" title="{{ game.line_reason if game.line_reason else 'No market total found' }}">N/A</span>{% endif %}</td>
                        <td>{% if game.xgb_total is not none %}{{ "%.1f"|format(game.xgb_total) }}{% else %}<span class="na">N/A</span>{% endif %}</td>
                        <td>{{ game.total_pick if game.total_pick else 'N/A' }}</td>
                        <td>{{ game.actual_total }}</td>
                        <td>{% if game.total_correct is none %}<span class="na">N/A</span>{% elif game.total_correct %}<span class="good">true</span>{% else %}<span class="bad">false</span>{% endif %}</td>
                    </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
        {% endfor %}
    {% else %}
    <div style="text-align:center;padding:60px;opacity:0.75;">No spread/total results data available yet.</div>
    {% endif %}
""")

@app.route('/sport/<sport>/spreads/results')
def sport_spread_total_results(sport):
    """Spread & total results — XSharp only, graded against market spread/total lines."""
    if sport not in SPORTS:
        return "Sport not found", 404

    from collections import defaultdict

    xgb_model = _get_xgb_spread_model(sport)
    has_xgb = xgb_model is not None

    conn = get_db_connection()
    latest_db_line_date_row = conn.execute(
        'SELECT MAX(date(game_date)) AS max_date FROM betting_lines WHERE sport = ?',
        (sport,)
    ).fetchone()
    latest_db_line_date = latest_db_line_date_row['max_date'] if latest_db_line_date_row else None
    # Use recent completed games so spread/total performance reflects current line availability.
    lookback_days_map = {
        'NCAAB': 14,
        'NBA': 45,
        'NHL': 45,
        'NCAAF': 120,
        'NFL': 120,
        'MLB': 120,
        'WNBA': 120,
    }
    lookback_days = lookback_days_map.get(sport, 60)
    lookback_start = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')

    completed_games = conn.execute('''
        SELECT
            g.game_id, g.game_date, g.home_team_id, g.away_team_id, g.home_score, g.away_score,
            COALESCE(bl_game.spread, bl_match.spread) AS market_spread,
            COALESCE(bl_game.total, bl_match.total) AS market_total
        FROM games g
        LEFT JOIN (
            SELECT game_id, MAX(spread) AS spread, MAX(total) AS total
            FROM betting_lines
            WHERE sport = ?
            GROUP BY game_id
        ) bl_game ON bl_game.game_id = g.game_id
        LEFT JOIN (
            SELECT date(game_date) AS game_day, home_team, away_team, MAX(spread) AS spread, MAX(total) AS total
            FROM betting_lines
            WHERE sport = ?
            GROUP BY date(game_date), home_team, away_team
        ) bl_match
          ON bl_match.game_day = date(g.game_date)
         AND bl_match.home_team = g.home_team_id
         AND bl_match.away_team = g.away_team_id
        WHERE g.sport = ?
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
          AND g.status = 'final'
          AND date(g.game_date) >= ?
        ORDER BY g.game_date DESC
        LIMIT 2000
    ''', (sport, sport, sport, lookback_start)).fetchall()
    conn.close()
    if sport == 'NHL':
        def _is_country_code_team(name):
            if not name:
                return False
            n = str(name).strip()
            return (' ' not in n) and n.isupper() and (2 <= len(n) <= 4)
        completed_games = [
            game for game in completed_games
            if not (
                _is_country_code_team(game['home_team_id']) and
                _is_country_code_team(game['away_team_id'])
            )
        ]
    pre_missing_line_games = sum(
        1 for game in completed_games
        if game['market_spread'] is None or game['market_total'] is None
    )
    pre_missing_ratio = (pre_missing_line_games / len(completed_games)) if completed_games else 0.0

    # Live API fallback for missing lines (bounded to keep route fast)
    live_lines_by_game = {}
    live_line_fetch_attempted = 0
    live_line_fetch_success = 0
    live_fetch_cap = 120 if sport == 'NCAAB' else 60
    live_recent_days = 14 if sport == 'NCAAB' else 3
    latest_line_dt = parse_date(str(latest_db_line_date)) if latest_db_line_date else None
    db_line_is_stale = latest_line_dt is None or latest_line_dt < (datetime.now() - timedelta(days=7))
    coverage_is_poor = pre_missing_ratio >= 0.50
    # Outage protection:
    # if DB lines are stale or coverage is mostly missing, widen fallback window
    # so the page self-heals by backfilling current lookback scope.
    if db_line_is_stale or coverage_is_poor:
        live_recent_days = lookback_days
        live_fetch_cap = min(2000, max(200, int(len(completed_games) * 0.9)))
    live_recent_cutoff = datetime.now() - timedelta(days=live_recent_days)

    for game in completed_games:
        if live_line_fetch_attempted >= live_fetch_cap:
            break
        if game['market_spread'] is not None and game['market_total'] is not None:
            continue

        game_date_obj = parse_date(str(game['game_date'])) if game['game_date'] else None
        if not game_date_obj or game_date_obj < live_recent_cutoff:
            continue

        live_line_fetch_attempted += 1
        live_line = _fetch_live_market_line(
            sport,
            game['game_id'],
            game['game_date'],
            game['home_team_id'],
            game['away_team_id'],
        )
        if live_line and (live_line.get('spread') is not None or live_line.get('total') is not None):
            live_lines_by_game[game['game_id']] = live_line
            live_line_fetch_success += 1

    # Persist fetched live lines so subsequent requests rely less on network fallbacks.
    if live_lines_by_game:
        try:
            conn_write = get_db_connection()
            for game in completed_games:
                live_line = live_lines_by_game.get(game['game_id'])
                if not live_line:
                    continue
                conn_write.execute('''
                    INSERT OR REPLACE INTO betting_lines
                    (sport, game_id, game_date, home_team, away_team, spread, total, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    sport,
                    game['game_id'],
                    (game['game_date'] or '').split()[0],
                    game['home_team_id'],
                    game['away_team_id'],
                    live_line.get('spread'),
                    live_line.get('total'),
                    live_line.get('source') or 'ESPN Core API (live fallback)',
                ))
            latest_db_line_date_row = conn_write.execute(
                'SELECT MAX(date(game_date)) AS max_date FROM betting_lines WHERE sport = ?',
                (sport,)
            ).fetchone()
            latest_db_line_date = latest_db_line_date_row['max_date'] if latest_db_line_date_row else latest_db_line_date
            conn_write.commit()
            conn_write.close()
        except Exception as e:
            logger.debug(f"Live line persistence skipped: {e}")

    daily_results = defaultdict(lambda: {
        'games': [],
        'xgb_spread_covered': 0,
        'xgb_spread_graded': 0,
        'xgb_total_correct': 0,
        'xgb_total_graded': 0,
        'total_games': 0,
        'actual_total_sum': 0.0,
        'avg_actual_total': 0.0,
    })

    total_games = 0
    xgb_spread_covered = 0
    xgb_spread_graded = 0
    xgb_total_correct = 0
    xgb_total_graded = 0

    xgb_spread_err_sum = 0.0
    xgb_spread_err_n = 0
    xgb_total_err_sum = 0.0
    xgb_total_err_n = 0

    missing_spread_lines = 0
    missing_total_lines = 0
    spread_pass_count = 0
    total_pass_count = 0

    edge_thresholds = {
        'NCAAB': {'spread': 2.5, 'total': 4.5},
    }
    spread_edge_threshold = edge_thresholds.get(sport, {}).get('spread', 0.0)
    total_edge_threshold = edge_thresholds.get(sport, {}).get('total', 0.0)
    thresholds_active = spread_edge_threshold > 0 or total_edge_threshold > 0

    def _to_float(v):
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    def _cover_side(home_margin, home_spread_line):
        # home_margin = home_score - away_score
        # home_spread_line = market home spread (negative=home favorite)
        # home covers when home_margin + spread > 0
        delta = home_margin + home_spread_line
        if abs(delta) < 1e-9:
            return 'push'
        return 'home' if delta > 0 else 'away'

    for game in completed_games:
        try:
            date_key = (game['game_date'] or '').split()[0]
            if not date_key:
                continue

            actual_home = _to_float(game['home_score'])
            actual_away = _to_float(game['away_score'])
            if actual_home is None or actual_away is None:
                continue

            actual_margin = actual_home - actual_away
            actual_total = actual_home + actual_away
            market_spread = _to_float(game['market_spread'])
            market_total = _to_float(game['market_total'])
            line_reason = None

            # Overlay live line data when DB is missing spread/total
            live_line = live_lines_by_game.get(game['game_id'])
            if live_line:
                if market_spread is None:
                    market_spread = _to_float(live_line.get('spread'))
                if market_total is None:
                    market_total = _to_float(live_line.get('total'))

            if market_spread is None or market_total is None:
                if latest_db_line_date:
                    line_reason = f"No matched market line in DB (latest {sport} line date: {latest_db_line_date})"
                else:
                    line_reason = f"No market line data in DB for {sport}"
                if game['game_id'] in live_lines_by_game:
                    if market_spread is None and market_total is not None:
                        line_reason = "Live fallback found total only; spread unavailable"
                    elif market_total is None and market_spread is not None:
                        line_reason = "Live fallback found spread only; total unavailable"

            xgb_spread_val = None
            xgb_total_val = None
            spread_pick = None
            pick_covered = None
            total_pick = None
            total_correct = None
            spread_edge = None
            total_edge = None

            if xgb_model:
                try:
                    xgb_pred = xgb_model.predict(game['home_team_id'], game['away_team_id'])
                    if xgb_pred and xgb_pred[2] is not None:
                        _, _, xgb_spread_val, xgb_total_val = xgb_pred
                        xgb_spread_val = _to_float(xgb_spread_val)
                        xgb_total_val = _to_float(xgb_total_val)
                except Exception:
                    pass

            if xgb_spread_val is not None:
                xgb_spread_err_sum += abs(xgb_spread_val - actual_margin)
                xgb_spread_err_n += 1
            if xgb_total_val is not None:
                xgb_total_err_sum += abs(xgb_total_val - actual_total)
                xgb_total_err_n += 1
            if xgb_spread_val is not None and market_spread is not None:
                spread_edge = xgb_spread_val - market_spread
            if xgb_total_val is not None and market_total is not None:
                total_edge = xgb_total_val - market_total

            # Spread grading: compare model spread vs market spread, then check if pick covered
            if market_spread is None:
                missing_spread_lines += 1
            elif xgb_spread_val is not None:
                if spread_edge_threshold > 0 and (spread_edge is None or abs(spread_edge) < spread_edge_threshold):
                    spread_pick = 'PASS'
                    pick_covered = None
                    spread_pass_count += 1
                else:
                    model_cover_side = _cover_side(xgb_spread_val, market_spread)
                    actual_cover_side = _cover_side(actual_margin, market_spread)

                    if model_cover_side == 'push':
                        spread_pick = 'PUSH'
                        pick_covered = None
                    elif actual_cover_side == 'push':
                        spread_pick = 'HOME' if model_cover_side == 'home' else 'AWAY'
                        pick_covered = None
                    else:
                        spread_pick = 'HOME' if model_cover_side == 'home' else 'AWAY'
                        pick_covered = (model_cover_side == actual_cover_side)
                        xgb_spread_graded += 1
                        daily_results[date_key]['xgb_spread_graded'] += 1
                        if pick_covered:
                            xgb_spread_covered += 1
                            daily_results[date_key]['xgb_spread_covered'] += 1

            # Total grading: compare model total vs market total, then actual total vs market total
            if market_total is None:
                missing_total_lines += 1
            elif xgb_total_val is not None:
                if total_edge_threshold > 0 and (total_edge is None or abs(total_edge) < total_edge_threshold):
                    total_pick = 'PASS'
                    total_correct = None
                    total_pass_count += 1
                else:
                    if abs(xgb_total_val - market_total) < 1e-9:
                        total_pick = 'PUSH'
                    else:
                        total_pick = 'OVER' if xgb_total_val > market_total else 'UNDER'

                    if abs(actual_total - market_total) < 1e-9:
                        actual_total_side = 'PUSH'
                    else:
                        actual_total_side = 'OVER' if actual_total > market_total else 'UNDER'

                    if total_pick == 'PUSH' or actual_total_side == 'PUSH':
                        total_correct = None
                    else:
                        total_correct = (total_pick == actual_total_side)
                        xgb_total_graded += 1
                        daily_results[date_key]['xgb_total_graded'] += 1
                        if total_correct:
                            xgb_total_correct += 1
                            daily_results[date_key]['xgb_total_correct'] += 1

            daily_results[date_key]['games'].append({
                'matchup': f"{game['away_team_id']} @ {game['home_team_id']}",
                'score': f"{int(actual_away)}-{int(actual_home)}",
                'market_spread': market_spread,
                'xgb_spread': round(xgb_spread_val, 1) if xgb_spread_val is not None else None,
                'spread_pick': spread_pick,
                'actual_margin': round(actual_margin, 1),
                'pick_covered': pick_covered,
                'market_total': market_total,
                'xgb_total': round(xgb_total_val, 1) if xgb_total_val is not None else None,
                'total_pick': total_pick,
                'actual_total': int(actual_total),
                'total_correct': total_correct,
                'line_reason': line_reason,
            })
            daily_results[date_key]['total_games'] += 1
            daily_results[date_key]['actual_total_sum'] += actual_total
            total_games += 1

        except Exception as e:
            logger.error(f"Error calculating spread/total results for {game['game_id']}: {e}")
            continue

    for date_key, date_data in daily_results.items():
        if date_data['total_games'] > 0:
            date_data['avg_actual_total'] = round(date_data['actual_total_sum'] / date_data['total_games'], 1)
        else:
            date_data['avg_actual_total'] = 0.0

    xgb_spread_cover_pct = (xgb_spread_covered / xgb_spread_graded * 100) if xgb_spread_graded > 0 else 0.0
    xgb_total_correct_pct = (xgb_total_correct / xgb_total_graded * 100) if xgb_total_graded > 0 else 0.0
    xgb_avg_spread_err = (xgb_spread_err_sum / xgb_spread_err_n) if xgb_spread_err_n > 0 else 0.0
    xgb_avg_total_err = (xgb_total_err_sum / xgb_total_err_n) if xgb_total_err_n > 0 else 0.0

    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    sorted_dates = sorted([d for d in daily_results.keys() if d <= yesterday], reverse=True)
    today_date = datetime.now().strftime('%Y-%m-%d')

    return render_template_string(
        SPREAD_TOTAL_RESULTS_TEMPLATE,
        page=sport,
        sport=sport,
        sport_info=SPORTS[sport],
        daily_results=daily_results,
        sorted_dates=sorted_dates,
        today_date=today_date,
        total_games=total_games,
        xgb_spread_covered=xgb_spread_covered,
        xgb_spread_graded=xgb_spread_graded,
        xgb_spread_cover_pct=xgb_spread_cover_pct,
        xgb_total_correct=xgb_total_correct,
        xgb_total_graded=xgb_total_graded,
        xgb_total_correct_pct=xgb_total_correct_pct,
        xgb_avg_spread_err=xgb_avg_spread_err,
        xgb_avg_total_err=xgb_avg_total_err,
        missing_spread_lines=missing_spread_lines,
        missing_total_lines=missing_total_lines,
        has_xgb=has_xgb,
        lookback_days=lookback_days,
        latest_db_line_date=latest_db_line_date,
        live_line_fetch_attempted=live_line_fetch_attempted,
        live_line_fetch_success=live_line_fetch_success,
        thresholds_active=thresholds_active,
        spread_edge_threshold=spread_edge_threshold,
        total_edge_threshold=total_edge_threshold,
        spread_pass_count=spread_pass_count,
        total_pass_count=total_pass_count
    )

@app.route('/sport/<sport>/ats')
def sport_ats_picks(sport):
    """Show ATS betting picks for a sport"""
    if sport not in SPORTS:
        return "Sport not found", 404
    
    # Initialize ATS system
    ats = ATSSystem()
    
    # Get all picks for next 7 days
    all_picks = ats.get_all_picks(sport, days_ahead=7)
    
    ml_picks = all_picks['moneyline']
    spread_picks = all_picks['spread']
    total_picks = all_picks['totals']
    
    # Get ATS records for context
    ats_records = ats.calculate_ats_records(sport, lookback_days=30)
    ou_records = ats.calculate_over_under_records(sport, lookback_days=30)
    
    return render_template_string(
        ATS_PICKS_TEMPLATE,
        page=sport,
        sport=sport,
        sport_info=SPORTS[sport],
        ml_picks=ml_picks,
        spread_picks=spread_picks,
        total_picks=total_picks,
        ats_records=ats_records.head(10).to_dict('records') if not ats_records.empty else [],
        ou_records=ou_records.head(10).to_dict('records') if not ou_records.empty else []
    )

# ============================================================================
# API ENDPOINTS FOR FRONTEND INTEGRATION
# ============================================================================

@app.route('/api/picks/<sport>', methods=['GET'])
def api_get_picks(sport):
    """API endpoint to get picks for a sport (for Next.js frontend)"""
    log_site_visit(f'/api/picks/{sport}')
    
    if sport.upper() not in SPORTS:
        return jsonify({'error': 'Sport not found'}), 404
    
    try:
        predictions = get_upcoming_predictions(sport.upper())
        
        # Convert to simple JSON format for frontend
        picks = []
        for pred in predictions:
            picks.append({
                'date': pred['game_date'],
                'matchup': f"{pred['away_team_id']} @ {pred['home_team_id']}",
                'homeTeam': pred['home_team_id'],
                'awayTeam': pred['away_team_id'],
                'pick': pred['predicted_winner'],
                'winPercent': pred['ensemble_prob'],
                'edge': pred.get('elo_prob'),
                'xsharp': pred.get('xgb_prob'),
                'grinder2': pred.get('glicko2_prob'),
                'takedown': pred.get('trueskill_prob')
            })
        
        return jsonify({
            'sport': sport.upper(),
            'picks': picks,
            'count': len(picks)
        })
    except Exception as e:
        logger.error(f"Error in API picks endpoint for {sport}: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats/traffic', methods=['GET'])
def api_get_traffic_stats():
    """Get site traffic statistics"""
    try:
        conn = get_db_connection()
        
        # Get today's visits
        today = datetime.now().strftime('%Y-%m-%d')
        today_visits = conn.execute('''
            SELECT COUNT(*) FROM site_visits WHERE visit_date = ?
        ''', (today,)).fetchone()[0]
        
        # Get last 7 days
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        week_visits = conn.execute('''
            SELECT COUNT(*) FROM site_visits WHERE visit_date >= ?
        ''', (week_ago,)).fetchone()[0]
        
        # Get total visits
        total_visits = conn.execute('SELECT COUNT(*) FROM site_visits').fetchone()[0]
        
        # Get top endpoints
        top_endpoints = conn.execute('''
            SELECT endpoint, COUNT(*) as count 
            FROM site_visits 
            GROUP BY endpoint 
            ORDER BY count DESC 
            LIMIT 10
        ''').fetchall()
        
        conn.close()
        
        return jsonify({
            'today': today_visits,
            'last_7_days': week_visits,
            'total': total_visits,
            'top_endpoints': [{'endpoint': row[0], 'count': row[1]} for row in top_endpoints]
        })
    except Exception as e:
        logger.error(f"Error getting traffic stats: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sports', methods=['GET'])
def api_get_sports():
    """Get list of available sports"""
    return jsonify({
        'sports': [{
            'code': code,
            'name': info['name'],
            'icon': info['icon']
        } for code, info in SPORTS.items()]
    })

if __name__ == '__main__':
    import os, socket
    # Use $PORT from Railway/Render, fall back to auto-finding a local port
    env_port = os.environ.get('PORT')
    if env_port:
        port = int(env_port)
    else:
        port = 5000
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex(('0.0.0.0', port)) != 0:
                    break
                port += 1

    print("\n" + "="*60)
    print("🎯 underdogs.bet - Multi-Sport Prediction Platform")
    print("="*60)
    print(f"🌐 Visit http://0.0.0.0:{port}")
    print("="*60 + "\n")
    app.run(debug=False, host='0.0.0.0', port=port, use_reloader=False, threaded=True)
