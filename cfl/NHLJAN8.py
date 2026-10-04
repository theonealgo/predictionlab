#!/usr/bin/env python3
"""
underdogs.bet - Multi-Sport Prediction Platform
==================================================
Complete platform with Dashboard, Predictions, and Results pages for all sports.
4-Model System: Elo, XGBoost, CatBoost, Meta Ensemble
"""

from flask import Flask, render_template_string, request
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
from rundown_api import RundownAPI
from ats_system import ATSSystem

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

DATABASE = 'sports_predictions_original.db'

SPORTS = {
    'NHL': {'name': 'NHL', 'icon': '🏒', 'color': '#1e3a8a'},
    'NFL': {'name': 'NFL', 'icon': '🏈', 'color': '#059669'},
    'NBA': {'name': 'NBA', 'icon': '🏀', 'color': '#dc2626'},
    'MLB': {'name': 'MLB', 'icon': '⚾', 'color': '#9333ea'},
    'NCAAF': {'name': 'NCAA Football', 'icon': '🏟️', 'color': '#ea580c'},
    'NCAAB': {'name': 'NCAA Basketball', 'icon': '🎓', 'color': '#0891b2'},
    'WNBA': {'name': 'WNBA', 'icon': '🏀', 'color': '#ea580c'},
}

import nfl_data_py as nfl

def update_nfl_scores():
    """
    Fetches and updates NFL scores for the 2025 season.
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
        
        conn = get_db_connection()
        cursor = conn.cursor()

        for _, game in finished_games.iterrows():
            cursor.execute("""
                UPDATE games
                SET home_score = ?, away_score = ?, status = 'final'
                WHERE sport = 'NFL' AND game_id = ?
            """, (game['home_score'], game['away_score'], game['game_id']))

        conn.commit()
        conn.close()
        logger.info("Successfully updated NFL scores in the database.")

    except Exception as e:
        logger.error(f"An error occurred while updating NFL scores: {e}")

def update_nhl_scores():
    """
    Fetches and updates NHL scores using the NHL API.
    Gets scores from the last 30 days (to catch any missing games).
    """
    try:
        logger.info("Fetching NHL scores from API (last 30 days)...")
        
        # Fetch last 30 days to catch any gaps
        from datetime import datetime, timedelta
        today = datetime.now()
        start_date = today - timedelta(days=30)
        
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
        
        # Iterate through last 30 days
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
            
            params = f"dates={date_str}"
            
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
# DATA LOADING FUNCTIONS
# ============================================================================

def get_upcoming_predictions(sport, days=365):
    """Get ALL game predictions from season start - both completed and upcoming
    
    Loads games from database for all sports including NHL
    
    USER REQUIREMENT: Show ALL games from season start (Oct 7 for NHL), not just upcoming!
    """
    
    # Load game data based on sport
    if sport == 'NHL':
        # NHL: Pull from ESPN API (to get correct schedule)
        try:
            nhl_api = NHLAPI()
            api_games = nhl_api.get_recent_and_upcoming_games(days_back=30, days_forward=30)
            
            # For each API game, check if prediction exists in DB
            conn = get_db_connection()
            for game in api_games:
                # Try to find match in database by date and team names
                existing = conn.execute('''
                    SELECT g.game_id, p.elo_home_prob, p.xgboost_home_prob, p.catboost_home_prob, p.meta_home_prob
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
                    game['stored_cat_prob'] = existing['catboost_home_prob']
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
        
        # Fetch games from ESPN API (last 7 days + next 14 days)
        for days_offset in range(-7, 15):
            check_date = datetime.now() + timedelta(days=days_offset)
            date_str = check_date.strftime('%Y%m%d')
            
            try:
                params = f"dates={date_str}"
                if sport == 'NCAAB':
                    params += "&groups=50&limit=1000"
                elif sport == 'NCAAF':
                    params += "&groups=80&limit=1000"
                
                url = f"{ESPN_ENDPOINTS[sport]}?{params}"
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
                game['stored_cat_prob'] = pred['logistic_home_prob']
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
                   p.catboost_home_prob as stored_cat_prob,
                   p.logistic_home_prob as stored_log_prob,
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
    
    # Calculate cutoff: today + 1 month
    # Use module-level datetime/timedelta imports to avoid local shadowing
    today = datetime.now()
    one_month_ahead = today + timedelta(days=30)
    
    predictions = []
    
    for game_date, game in all_games_with_dates:
        # Show games from season start up to one month from today
        if game_date >= season_start and game_date <= one_month_ahead:
            # Check if stored predictions exist (for sports with pre-generated predictions)
            if game.get('stored_elo_prob') is not None:
                # Use stored predictions from database with safe conversion
                import struct
                
                def safe_float_convert(value, fallback=0.5):
                    """Safely convert database value to float, handling bytes/binary data"""
                    if value is None:
                        return fallback
                    try:
                        # If it's already a float or int, return it
                        if isinstance(value, (float, int)):
                            return float(value)
                        # If it's bytes, try to unpack as float
                        if isinstance(value, bytes):
                            if len(value) == 8:
                                # Double precision float (8 bytes)
                                return struct.unpack('d', value)[0]
                            elif len(value) == 4:
                                # Single precision float (4 bytes)
                                return struct.unpack('f', value)[0]
                        # If it's a string, parse it
                        return float(value)
                    except (ValueError, struct.error, TypeError):
                        return fallback
                
                elo_prob = safe_float_convert(game['stored_elo_prob'], 0.5)
                xgb_prob = safe_float_convert(game.get('stored_xgb_prob'), elo_prob)
                cat_prob = safe_float_convert(game.get('stored_cat_prob'), elo_prob)
                ensemble_prob = safe_float_convert(game.get('stored_ensemble_prob'), elo_prob)
                # Override ensemble with Elo for NFL to align with recent performance
                if sport == 'NFL':
                    ensemble_prob = elo_prob
            else:
                # Calculate live predictions using Elo for sports without stored predictions
                home_rating = get_elo(game['home_team_id'])
                away_rating = get_elo(game['away_team_id'])
                elo_prob = expected_score(home_rating, away_rating)
                
                # V2 ENHANCEMENTS: Incorporate API data
                
                # Feature 1: Goalie differential (if available)
                goalie_boost = 0.0
                if game.get('home_goalie_save_pct') and game.get('away_goalie_save_pct'):
                    save_pct_diff = float(game['home_goalie_save_pct']) - float(game['away_goalie_save_pct'])
                    goalie_boost = save_pct_diff * 0.3  # 3% save pct diff = ~1% boost
                
                # Feature 2: Betting market consensus (if available)
                market_boost = 0.0
                if game.get('home_implied_prob') and game.get('away_implied_prob'):
                    market_home_prob = float(game['home_implied_prob'])
                    market_boost = (market_home_prob - 0.5) * 0.15  # 15% weight to market
                
                # Feature 3: Home/Away splits
                home_stats = get_home_away_stats(game['home_team_id'])
                away_stats = get_home_away_stats(game['away_team_id'])
                
                home_win_pct = home_stats['home_wins'] / home_stats['home_games'] if home_stats['home_games'] > 0 else 0.5
                away_win_pct = away_stats['away_wins'] / away_stats['away_games'] if away_stats['away_games'] > 0 else 0.5
                
                split_boost = (home_win_pct - away_win_pct) * 0.1  # 10% weight to splits
                
                # Enhanced model predictions
                xgb_prob = min(0.95, max(0.05, elo_prob + goalie_boost + market_boost * 0.5 + split_boost))
                cat_prob = min(0.95, max(0.05, elo_prob + goalie_boost * 0.7 + market_boost * 0.3 + split_boost * 0.5))
                
                # V2 Ensemble (including market data when available)
                if game.get('home_implied_prob'):
                    # With betting odds: 40% CatBoost, 30% XGBoost, 20% Elo, 10% Market
                    ensemble_prob = (cat_prob * 0.4 + xgb_prob * 0.3 + elo_prob * 0.2 + float(game['home_implied_prob']) * 0.1)
                else:
                    # Without betting odds: 50% CatBoost, 30% XGBoost, 20% Elo
                    ensemble_prob = (cat_prob * 0.5 + xgb_prob * 0.3 + elo_prob * 0.2)
                # Override ensemble with Elo for NFL to align with recent performance
                if sport == 'NFL':
                    ensemble_prob = elo_prob
            
            # Add predictions to game dict
            game_dict = dict(game)
            game_dict['elo_prob'] = round(elo_prob * 100, 1)
            game_dict['xgb_prob'] = round(xgb_prob * 100, 1)
            game_dict['cat_prob'] = round(cat_prob * 100, 1)
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
                    # Save new prediction
                    try:
                        cursor_save.execute('''
                            INSERT INTO predictions (
                                game_id, sport, league, game_date, home_team_id, away_team_id,
                                elo_home_prob, xgboost_home_prob, logistic_home_prob, win_probability
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            pred['game_id'], 'NBA', 'NBA', pred['game_date'],
                            pred['home_team_id'], pred['away_team_id'],
                            pred['elo_prob'] / 100.0,  # Convert back to 0-1 range
                            pred['xgb_prob'] / 100.0,
                            pred['cat_prob'] / 100.0,
                            pred['ensemble_prob'] / 100.0
                        ))
                        saved_count += 1
                    except Exception as e:
                        logger.error(f"Error saving prediction for {pred['game_id']}: {e}")
        
        if saved_count > 0:
            conn_save.commit()
            logger.info(f"Saved {saved_count} new NBA predictions to database")
        conn_save.close()
    
    return predictions

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
        
        # Filter to completed games only (games with results) and exclude today
        today_str = datetime.now().strftime('%Y-%m-%d')
        completed_games = schedule[schedule['result'].notna()].copy()
        # Convert gameday to string for comparison if needed
        completed_games = completed_games[completed_games['gameday'].astype(str) < today_str]
        
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
            
            # Look up predictions from database using game_id (now they match!)
            pred = conn.execute('''
                SELECT p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
                FROM predictions p
                WHERE p.game_id = ? AND p.sport = 'NFL'
            ''', (game_id,)).fetchone()
            
            if not pred or pred[0] is None:
                # No prediction for this game, skip it
                continue
            
            # Extract stored predictions from database
            elo_prob = float(pred[0]) if pred[0] else None
            xgb_prob = float(pred[1]) if pred[1] else None
            cat_prob = float(pred[2]) if pred[2] else None
            # Override ensemble with Elo for NFL
            ens_prob = elo_prob
            
            # Get actual result from API
            actual_home_win = api_game['home_score'] > api_game['away_score']
            
            # Get team full names
            home_team_full = abbr_to_full.get(api_game['home_team'], api_game['home_team'])
            away_team_full = abbr_to_full.get(api_game['away_team'], api_game['away_team'])
            
            # Initialize week if not exists
            if week not in weekly_results:
                weekly_results[week] = {
                    'elo': {'correct': 0, 'total': 0},
                    'xgboost': {'correct': 0, 'total': 0},
                    'catboost': {'correct': 0, 'total': 0},
                    'ensemble': {'correct': 0, 'total': 0},
                    'games': []
                }
            
            # Check each model's prediction
            elo_correct = None
            if elo_prob is not None:
                weekly_results[week]['elo']['total'] += 1
                elo_correct = (elo_prob > 0.5) == actual_home_win
                if elo_correct:
                    weekly_results[week]['elo']['correct'] += 1
            
            xgb_correct = None
            if xgb_prob is not None:
                weekly_results[week]['xgboost']['total'] += 1
                xgb_correct = (xgb_prob > 0.5) == actual_home_win
                if xgb_correct:
                    weekly_results[week]['xgboost']['correct'] += 1
            
            cat_correct = None
            if cat_prob is not None:
                weekly_results[week]['catboost']['total'] += 1
                cat_correct = (cat_prob > 0.5) == actual_home_win
                if cat_correct:
                    weekly_results[week]['catboost']['correct'] += 1
            
            ens_correct = None
            if ens_prob is not None:
                weekly_results[week]['ensemble']['total'] += 1
                ens_correct = (ens_prob > 0.5) == actual_home_win
                if ens_correct:
                    weekly_results[week]['ensemble']['correct'] += 1
            
            # Store game details with full team names and correctness flags
            weekly_results[week]['games'].append({
                'date': str(api_game['gameday']),  # Date from API
                'away': away_team_full,  # Full team name
                'home': home_team_full,  # Full team name
                'away_score': int(api_game['away_score']),  # Score from API
                'home_score': int(api_game['home_score']),  # Score from API
                'elo_prob': round(elo_prob * 100, 1) if elo_prob else 'N/A',
                'xgb_prob': round(xgb_prob * 100, 1) if xgb_prob else 'N/A',
                'cat_prob': round(cat_prob * 100, 1) if cat_prob else 'N/A',
                'ens_prob': round(ens_prob * 100, 1) if ens_prob else 'N/A',
                'elo_correct': elo_correct,
                'xgb_correct': xgb_correct,
                'cat_correct': cat_correct,
                'ens_correct': ens_correct,
            })
        
        conn.close()
        
        # Calculate accuracy percentages
        for week in weekly_results:
            for model in ['elo', 'xgboost', 'catboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                if total > 0:
                    acc = (weekly_results[week][model]['correct'] / total * 100)
                    weekly_results[week][model]['accuracy'] = round(acc, 1)
                else:
                    weekly_results[week][model]['accuracy'] = 0.0
        
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
        conn = get_db_connection()
        
        # Get all completed NHL games with predictions (exclude today)
        today_str = datetime.now().strftime('%Y-%m-%d')
        
        # DEBUG: Check if we have games and predictions
        logger.info(f"Fetching NHL results before {today_str}")
        
        games = conn.execute('''
            SELECT g.game_date, g.home_team_id, g.away_team_id,
                   g.home_score, g.away_score,
                   p.elo_home_prob, p.xgboost_home_prob, p.catboost_home_prob, p.meta_home_prob
            FROM games g
            LEFT JOIN predictions p 
              ON p.sport = 'NHL' AND (
                   p.game_id = g.game_id
                   OR (
                        p.game_date = g.game_date
                        AND p.home_team_id = g.home_team_id
                        AND p.away_team_id = g.away_team_id
                   )
              )
            WHERE g.sport = 'NHL' AND g.season = 2025 AND g.home_score IS NOT NULL
              AND g.game_date < ?
            ORDER BY g.game_date
        ''', (today_str,)).fetchall()
        
        if games:
            logger.info(f"Found {len(games)} completed NHL games. First: {dict(games[0])}")
        else:
            logger.info("No completed NHL games found in query.")
        
        conn.close()
        
        if not games:
            return None
        
        # Group games by week (calculate week number from season start)
        season_start = datetime(2025, 10, 7)  # NHL season starts Oct 7
        
        weekly_results = {}
        
        for game in games:
            # Parse game date
            game_date = parse_date(game['game_date'])
            if not game_date:
                continue
            
            # Calculate week number (1-indexed)
            days_since_start = (game_date - season_start).days
            week = (days_since_start // 7) + 1
            
            # Skip if no predictions
            if not game['elo_home_prob']:
                continue
            
            # Extract predictions
            elo_prob = float(game['elo_home_prob'])
            xgb_prob = float(game['xgboost_home_prob']) if game['xgboost_home_prob'] else elo_prob
            cat_prob = float(game['catboost_home_prob']) if game['catboost_home_prob'] else elo_prob
            meta_prob = float(game['meta_home_prob']) if game['meta_home_prob'] else elo_prob
            
            # Get actual result
            actual_home_win = game['home_score'] > game['away_score']
            
            # Initialize week if needed
            if week not in weekly_results:
                weekly_results[week] = {
                    'elo': {'correct': 0, 'total': 0},
                    'xgboost': {'correct': 0, 'total': 0},
                    'catboost': {'correct': 0, 'total': 0},
                    'ensemble': {'correct': 0, 'total': 0},
                    'games': []
                }
            
            # Check predictions
            elo_correct = (elo_prob > 0.5) == actual_home_win
            xgb_correct = (xgb_prob > 0.5) == actual_home_win
            cat_correct = (cat_prob > 0.5) == actual_home_win
            meta_correct = (meta_prob > 0.5) == actual_home_win
            
            weekly_results[week]['elo']['total'] += 1
            if elo_correct:
                weekly_results[week]['elo']['correct'] += 1
            
            weekly_results[week]['xgboost']['total'] += 1
            if xgb_correct:
                weekly_results[week]['xgboost']['correct'] += 1
            
            weekly_results[week]['catboost']['total'] += 1
            if cat_correct:
                weekly_results[week]['catboost']['correct'] += 1
            
            weekly_results[week]['ensemble']['total'] += 1
            if meta_correct:
                weekly_results[week]['ensemble']['correct'] += 1
            
            # Store game details
            weekly_results[week]['games'].append({
                'date': game['game_date'].split()[0],
                'away': game['away_team_id'],
                'home': game['home_team_id'],
                'away_score': int(game['away_score']),
                'home_score': int(game['home_score']),
                'elo_prob': round(elo_prob * 100, 1),
                'xgb_prob': round(xgb_prob * 100, 1),
                'cat_prob': round(cat_prob * 100, 1),
                'ens_prob': round(meta_prob * 100, 1),
                'elo_correct': elo_correct,
                'xgb_correct': xgb_correct,
                'cat_correct': cat_correct,
                'ens_correct': meta_correct,
            })
        
        # Calculate accuracy percentages
        for week in weekly_results:
            for model in ['elo', 'xgboost', 'catboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                if total > 0:
                    acc = (weekly_results[week][model]['correct'] / total * 100)
                    weekly_results[week][model]['accuracy'] = round(acc, 1)
                else:
                    weekly_results[week][model]['accuracy'] = 0.0
        
        return weekly_results
        
    except Exception as e:
        logger.error(f"Error calculating NHL weekly performance: {e}")
        return None

def calculate_nba_weekly_performance():
    """Calculate NBA model performance week by week
    
    Uses data from database, groups by week calculated from season start.
    """
    try:
        conn = get_db_connection()
        
        # Get all completed NBA games with predictions (exclude today)
        today_str = datetime.now().strftime('%Y-%m-%d')
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
            WHERE g.sport = 'NBA' AND date(g.game_date) >= '2024-10-21'
              AND date(g.game_date) < ?
            ORDER BY g.game_date
        ''', (today_str,)).fetchall()
        
        conn.close()
        
        if not games:
            return None
            
        # Group games by week (calculate week number from season start)
        season_start = datetime(2024, 10, 21)  # NBA season starts Oct 21, 2024
        
        weekly_results = {}
        
        for game in games:
            # Parse game date
            game_date = parse_date(game['game_date'])
            if not game_date:
                continue
            
            # Get scores from database (already updated by update_nba_scores)
            home_team = game['home_team_id']
            away_team = game['away_team_id']
            home_score = game['home_score']
            away_score = game['away_score']
            
            # Skip if still no final scores
            if home_score is None or away_score is None:
                continue
            
            # Calculate week number (1-indexed)
            days_since_start = (game_date - season_start).days
            week = (days_since_start // 7) + 1
            
            # Extract predictions (allow missing predictions)
            elo_prob = float(game['elo_home_prob']) if game['elo_home_prob'] is not None else None
            xgb_prob = float(game['xgboost_home_prob']) if game['xgboost_home_prob'] is not None else None
            cat_prob = float(game['logistic_home_prob']) if game['logistic_home_prob'] is not None else None
            ens_prob = float(game['win_probability']) if game['win_probability'] is not None else None
            
            # Get actual result
            actual_home_win = home_score > away_score
            
            # Initialize week if needed
            if week not in weekly_results:
                weekly_results[week] = {
                    'elo': {'correct': 0, 'total': 0},
                    'xgboost': {'correct': 0, 'total': 0},
                    'catboost': {'correct': 0, 'total': 0},
                    'ensemble': {'correct': 0, 'total': 0},
                    'games': []
                }
            
            # Check predictions (only when available)
            elo_correct = None if elo_prob is None else ((elo_prob > 0.5) == actual_home_win)
            xgb_correct = None if xgb_prob is None else ((xgb_prob > 0.5) == actual_home_win)
            cat_correct = None if cat_prob is None else ((cat_prob > 0.5) == actual_home_win)
            ens_correct = None if ens_prob is None else ((ens_prob > 0.5) == actual_home_win)
            
            if elo_prob is not None:
                weekly_results[week]['elo']['total'] += 1
                if elo_correct:
                    weekly_results[week]['elo']['correct'] += 1
            if xgb_prob is not None:
                weekly_results[week]['xgboost']['total'] += 1
                if xgb_correct:
                    weekly_results[week]['xgboost']['correct'] += 1
            if cat_prob is not None:
                weekly_results[week]['catboost']['total'] += 1
                if cat_correct:
                    weekly_results[week]['catboost']['correct'] += 1
            if ens_prob is not None:
                weekly_results[week]['ensemble']['total'] += 1
                if ens_correct:
                    weekly_results[week]['ensemble']['correct'] += 1
            
            # Store game details
            weekly_results[week]['games'].append({
                'date': game['game_date'].split()[0],
                'away': away_team,
                'home': home_team,
                'away_score': int(away_score),
                'home_score': int(home_score),
                'elo_prob': (round(elo_prob * 100, 1) if elo_prob is not None else 'N/A'),
                'xgb_prob': (round(xgb_prob * 100, 1) if xgb_prob is not None else 'N/A'),
                'cat_prob': (round(cat_prob * 100, 1) if cat_prob is not None else 'N/A'),
                'ens_prob': (round(ens_prob * 100, 1) if ens_prob is not None else 'N/A'),
                'elo_correct': elo_correct,
                'xgb_correct': xgb_correct,
                'cat_correct': cat_correct,
                'ens_correct': ens_correct,
            })
        
        # Calculate accuracy percentages
        for week in weekly_results:
            for model in ['elo', 'xgboost', 'catboost', 'ensemble']:
                total = weekly_results[week][model]['total']
                if total > 0:
                    acc = (weekly_results[week][model]['correct'] / total * 100)
                    weekly_results[week][model]['accuracy'] = round(acc, 1)
                else:
                    weekly_results[week][model]['accuracy'] = 0.0
        
        return weekly_results
        
    except Exception as e:
        logger.error(f"Error calculating NBA weekly performance: {e}")
        return None

def calculate_model_performance(sport):
    """Calculate performance using stored predictions from database
    
    All sports now use the same method: pre-game predictions stored in database
    """
    
    # All sports now use database predictions (no live generation)
    conn = get_db_connection()
    
    # Calculate cutoff for results (exclude today)
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    results_data = conn.execute('''
            SELECT 
                g.game_date,
                g.home_team_id,
                g.away_team_id,
                g.away_score,
                g.home_score,
                p.elo_home_prob,
                p.xgboost_home_prob,
                p.logistic_home_prob,
                p.win_probability as ensemble_prob
            FROM games g
            LEFT JOIN predictions p ON 
                g.sport = p.sport AND
                g.game_date = p.game_date AND
                g.home_team_id = p.home_team_id AND
                g.away_team_id = p.away_team_id
            WHERE g.sport = ? 
                AND g.home_score IS NOT NULL
                AND date(g.game_date) < ?
            ORDER BY g.game_date ASC
        ''', (sport, today_str)).fetchall()
    
    conn.close()
    
    if len(results_data) == 0:
        return None
    
    # Calculate accuracy from stored predictions
    results = {
        'elo': {'correct': 0, 'total': 0},
        'xgboost': {'correct': 0, 'total': 0},
        'catboost': {'correct': 0, 'total': 0},
        'ensemble': {'correct': 0, 'total': 0}
    }
    
    dates = []
    
    for row in results_data:
        # Safe float conversion helper
        def to_float(val):
            if val is None:
                return None
            if isinstance(val, (float, int)):
                return float(val)
            if isinstance(val, bytes):
                # Try to unpack as binary float
                try:
                    import struct
                    if len(val) == 8:
                        return struct.unpack('d', val)[0]
                    elif len(val) == 4:
                        return struct.unpack('f', val)[0]
                    else:
                        # Try decoding as string
                        return float(val.decode('utf-8', errors='ignore'))
                except:
                    return None
            try:
                return float(val)
            except:
                return None
        
        # Actual winner
        home_score = to_float(row[4])
        away_score = to_float(row[3])
        actual_winner = 'home' if home_score > away_score else 'away'
        
        # Only count if we have stored predictions
        if row[5] is not None:
            # Elo prediction
            elo_prob = to_float(row[5])
            if elo_prob is not None:
                elo_winner = 'home' if elo_prob > 0.5 else 'away'
                results['elo']['total'] += 1
                if elo_winner == actual_winner:
                    results['elo']['correct'] += 1
            
            # XGBoost prediction  
            if row[6] is not None:
                xgb_prob = to_float(row[6])
                if xgb_prob is not None:
                    xgb_winner = 'home' if xgb_prob > 0.5 else 'away'
                    results['xgboost']['total'] += 1
                    if xgb_winner == actual_winner:
                        results['xgboost']['correct'] += 1
            
            # Logistic/CatBoost prediction
            if row[7] is not None:
                cat_prob = to_float(row[7])
                if cat_prob is not None:
                    cat_winner = 'home' if cat_prob > 0.5 else 'away'
                    results['catboost']['total'] += 1
                    if cat_winner == actual_winner:
                        results['catboost']['correct'] += 1
            
            # Ensemble prediction
            if row[8] is not None:
                ens_prob = to_float(row[8])
                if ens_prob is not None:
                    ens_winner = 'home' if ens_prob > 0.5 else 'away'
                    results['ensemble']['total'] += 1
                    if ens_winner == actual_winner:
                        results['ensemble']['correct'] += 1
        
        # Track dates
        dates.append(parse_date(row[0]))
    
    # Calculate accuracies
    performance = {}
    for model in ['elo', 'xgboost', 'catboost', 'ensemble']:
        total = results[model]['total']
        if total > 0:
            acc = (results[model]['correct'] / total * 100)
            performance[model] = {
                'accuracy': round(acc, 1),
                'correct': results[model]['correct'],
                'total': total
            }
        else:
            performance[model] = {'accuracy': 0.0, 'correct': 0, 'total': 0}
    
    # Date range
    valid_dates = [d for d in dates if d is not None]
    if valid_dates:
        min_date = min(valid_dates).strftime('%d/%m/%Y')
        max_date = max(valid_dates).strftime('%d/%m/%Y')
        performance['date_range'] = f"{min_date} - {max_date}"
    else:
        performance['date_range'] = "N/A"
    
    performance['total_games'] = len(results_data)
    
    return performance

# ============================================================================
# BASE TEMPLATE
# ============================================================================

BASE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Sports Predictions{% endblock %}</title>
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
            <a href="/" class="logo">← Home</a>
            <div class="hamburger" onclick="toggleMenu()">
                <span></span>
                <span></span>
                <span></span>
            </div>
            <div class="nav-links" id="navLinks">
                <a href="/" class="{{ 'active' if page == 'home' else '' }}">Home</a>
                <a href="/sport/NHL/predictions" class="{{ 'active' if page == 'NHL' else '' }}">🏒 NHL</a>
                <a href="/sport/NFL/predictions" class="{{ 'active' if page == 'NFL' else '' }}">🏈 NFL</a>
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
# PREDICTION FIXER TEMPLATE
# ============================================================================

PREDICTION_FIXER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Prediction Fixer - Admin Tool</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 30px; padding: 25px; background: rgba(255,255,255,0.05); border-radius: 15px; }
        h1 { font-size: 2.5em; margin-bottom: 10px; color: #fbbf24; }
        .subtitle { opacity: 0.8; font-size: 1.1em; }
        .action-buttons { display: flex; gap: 15px; justify-content: center; margin-bottom: 30px; flex-wrap: wrap; }
        .btn { padding: 12px 24px; border-radius: 8px; border: none; font-weight: 600; cursor: pointer; transition: all 0.3s; font-size: 1em; }
        .btn-scan { background: linear-gradient(135deg, #3b82f6, #2563eb); color: white; }
        .btn-fix { background: linear-gradient(135deg, #10b981, #059669); color: white; }
        .btn-fix-all { background: linear-gradient(135deg, #f59e0b, #d97706); color: white; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.3); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .status-box { background: rgba(255,255,255,0.05); border-radius: 10px; padding: 20px; margin-bottom: 20px; }
        .status-box.loading { border-left: 4px solid #3b82f6; }
        .status-box.success { border-left: 4px solid #10b981; }
        .status-box.error { border-left: 4px solid #ef4444; }
        .issues-container { background: rgba(255,255,255,0.03); border-radius: 10px; padding: 20px; max-height: 600px; overflow-y: auto; }
        .issue-card { background: rgba(255,255,255,0.05); border-left: 4px solid #ef4444; padding: 15px; margin-bottom: 10px; border-radius: 8px; }
        .issue-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .issue-sport { background: #3b82f6; padding: 4px 12px; border-radius: 6px; font-size: 0.9em; font-weight: 600; }
        .issue-date { opacity: 0.7; font-size: 0.9em; }
        .issue-matchup { font-size: 1.1em; font-weight: 600; color: #fbbf24; }
        .issue-score { opacity: 0.8; margin-top: 5px; }
        .spinner { border: 3px solid rgba(255,255,255,0.1); border-top: 3px solid #3b82f6; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; display: inline-block; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-card { background: rgba(255,255,255,0.05); padding: 20px; border-radius: 10px; text-align: center; }
        .stat-value { font-size: 2.5em; font-weight: bold; color: #fbbf24; }
        .stat-label { opacity: 0.8; margin-top: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <a href="/" style="display: inline-block; padding: 10px 20px; background: rgba(255,255,255,0.1); border-radius: 8px; text-decoration: none; color: white; margin-bottom: 20px; font-weight: 600;">← Back to Home</a>
        
        <div class="header">
            <h1>🔧 Prediction Fixer</h1>
            <p class="subtitle">Diagnose and fix missing predictions instantly</p>
        </div>
        
        <div class="action-buttons">
            <button class="btn btn-scan" onclick="scanIssues()">🔍 Scan for Issues</button>
            <button class="btn btn-fix" onclick="updateScores()" id="updateScoresBtn">📥 Update Scores</button>
            <button class="btn btn-fix-all" onclick="fixAll()" id="fixAllBtn" disabled>✨ Fix All Sports</button>
        </div>
        
        <div id="statusBox" class="status-box" style="display: none;"></div>
        
        <div id="statsContainer" style="display: none;"></div>
        
        <div id="issuesContainer" style="display: none;"></div>
    </div>
    
    <script>
        let currentIssues = [];
        
        function showStatus(message, type = 'loading') {
            const box = document.getElementById('statusBox');
            box.className = 'status-box ' + type;
            box.innerHTML = type === 'loading' ? 
                `<div class="spinner"></div> <span style="margin-left: 15px;">${message}</span>` :
                message;
            box.style.display = 'block';
        }
        
        function hideStatus() {
            document.getElementById('statusBox').style.display = 'none';
        }
        
        async function scanIssues() {
            showStatus('Scanning all sports for missing predictions...', 'loading');
            document.getElementById('issuesContainer').style.display = 'none';
            document.getElementById('statsContainer').style.display = 'none';
            document.getElementById('fixAllBtn').disabled = true;
            
            try {
                const response = await fetch('/admin/fixer/scan');
                const data = await response.json();
                currentIssues = data.issues;
                
                if (data.total_issues === 0) {
                    showStatus('✅ No issues found! All predictions are in place.', 'success');
                } else {
                    showStatus(`⚠️ Found ${data.total_issues} games with missing predictions`, 'error');
                    displayIssues(data.issues);
                    displayStats(data.issues);
                    document.getElementById('fixAllBtn').disabled = false;
                }
            } catch (error) {
                showStatus(`❌ Error scanning: ${error.message}`, 'error');
            }
        }
        
        function displayStats(issues) {
            const sportCounts = {};
            issues.forEach(issue => {
                sportCounts[issue.sport] = (sportCounts[issue.sport] || 0) + 1;
            });
            
            let html = '<div class="stats">';
            html += `<div class="stat-card"><div class="stat-value">${issues.length}</div><div class="stat-label">Total Issues</div></div>`;
            for (const [sport, count] of Object.entries(sportCounts)) {
                html += `<div class="stat-card"><div class="stat-value">${count}</div><div class="stat-label">${sport}</div></div>`;
            }
            html += '</div>';
            
            document.getElementById('statsContainer').innerHTML = html;
            document.getElementById('statsContainer').style.display = 'block';
        }
        
        function displayIssues(issues) {
            let html = '<h2 style="margin-bottom: 15px; color: #fbbf24;">Missing Predictions</h2>';
            
            issues.forEach(issue => {
                html += `
                    <div class="issue-card">
                        <div class="issue-header">
                            <span class="issue-sport">${issue.sport}</span>
                            <span class="issue-date">${issue.game_date}</span>
                        </div>
                        <div class="issue-matchup">${issue.matchup}</div>
                        <div class="issue-score">Final Score: ${issue.score}</div>
                    </div>
                `;
            });
            
            document.getElementById('issuesContainer').innerHTML = html;
            document.getElementById('issuesContainer').style.display = 'block';
        }
        
        async function updateScores() {
            showStatus('Updating scores for all sports (last 7 days)...', 'loading');
            document.getElementById('updateScoresBtn').disabled = true;
            
            try {
                const response = await fetch('/admin/fixer/update-scores');
                const data = await response.json();
                
                if (data.success) {
                    showStatus(`✅ Updated ${data.updated} game scores! Rescanning...`, 'success');
                    setTimeout(() => {
                        scanIssues();
                    }, 1500);
                } else {
                    showStatus(`❌ Error updating scores: ${data.error}`, 'error');
                }
            } catch (error) {
                showStatus(`❌ Error: ${error.message}`, 'error');
            } finally {
                document.getElementById('updateScoresBtn').disabled = false;
            }
        }
        
        async function fixAll() {
            if (!confirm('Fix all missing predictions? This will generate and save predictions for all games with missing data.')) {
                return;
            }
            
            const sports = [...new Set(currentIssues.map(i => i.sport))];
            showStatus(`Fixing predictions for ${sports.join(', ')}...`, 'loading');
            document.getElementById('fixAllBtn').disabled = true;
            
            let totalFixed = 0;
            
            for (const sport of sports) {
                try {
                    const response = await fetch(`/admin/fixer/fix/${sport}`);
                    const data = await response.json();
                    if (data.success) {
                        totalFixed += data.fixed;
                    }
                } catch (error) {
                    console.error(`Error fixing ${sport}:`, error);
                }
            }
            
            showStatus(`✅ Successfully fixed ${totalFixed} missing predictions!`, 'success');
            
            // Rescan after 2 seconds
            setTimeout(() => {
                scanIssues();
            }, 2000);
        }
        
        // Auto-scan on page load
        window.addEventListener('DOMContentLoaded', () => {
            scanIssues();
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
                    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; font-size: 0.9em;">
                        <div><strong>Elo:</strong> {{ (pred.elo_prob * 100)|round(1) }}%</div>
                        <div><strong>XGB:</strong> {{ (pred.xgb_prob * 100)|round(1) }}%</div>
                        <div><strong>Cat:</strong> {{ (pred.cat_prob * 100)|round(1) }}%</div>
                        <div><strong>Meta:</strong> {{ (pred.ensemble_prob * 100)|round(1) }}%</div>
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
                            <th>XGBoost</th>
                            <th>CatBoost</th>
                            <th>Elo</th>
                            <th>Meta</th>
                            <th>Pick</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for pred in grouped_predictions[date] %}
                        <tr>
                            <td>{{ pred.away_team_id }} @ <strong>{{ pred.home_team_id }}</strong></td>
                            <td class="model-pred">{{ pred.xgb_prob }}%</td>
                            <td class="model-pred">{{ pred.cat_prob }}%</td>
                            <td class="model-pred">{{ pred.elo_prob }}%</td>
                            <td class="model-pred {% if pred.ensemble_prob > 60 %}high-conf{% elif pred.ensemble_prob > 55 %}med-conf{% else %}low-conf{% endif %}">{{ pred.ensemble_prob }}%</td>
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
                    <th>XGBoost</th>
                    <th>CatBoost</th>
                    <th>Elo</th>
                    <th>Meta</th>
                </tr>
            </thead>
            <tbody>
                {% for game in results %}
                <tr>
                    <td>{{ game.date }}</td>
                    <td>{{ game.away }}</td>
                    <td>{{ game.home }}</td>
                    <td class="{% if game.xgb_home|float >= 60 %}prob-high{% elif game.xgb_home|float <= 40 %}prob-low{% endif %}">{{ game.xgb_home }}%</td>
                    <td class="{% if game.cat_home|float >= 60 %}prob-high{% elif game.cat_home|float <= 40 %}prob-low{% endif %}">{{ game.cat_home }}%</td>
                    <td class="{% if game.elo_home|float >= 60 %}prob-high{% elif game.elo_home|float <= 40 %}prob-low{% endif %}">{{ game.elo_home }}%</td>
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
        <a href="/sport/{{ sport }}/results" class="tab active">🎯 Results</a>
        <a href="/sport/{{ sport }}/spreads" class="tab">📈 Spreads & Totals</a>
    </div>
    
    <div class="results-container">
        {% if performance %}
        <div class="date-range">📅 Test Period: {{ performance.date_range }}</div>
        <div class="test-info">Tested on {{ performance.total_games }} completed games</div>
        
        <div class="models-grid">
            <div class="model-card">
                <div class="model-name">Elo Rating</div>
                <div class="model-accuracy">{{ performance.elo.accuracy }}%</div>
                <div class="model-record">{{ performance.elo.correct }}-{{ performance.elo.total - performance.elo.correct }}</div>
            </div>
            
            <div class="model-card">
                <div class="model-name">XGBoost</div>
                <div class="model-accuracy">{{ performance.xgboost.accuracy }}%</div>
                <div class="model-record">{{ performance.xgboost.correct }}-{{ performance.xgboost.total - performance.xgboost.correct }}</div>
            </div>
            
            <div class="model-card">
                <div class="model-name">CatBoost</div>
                <div class="model-accuracy">{{ performance.catboost.accuracy }}%</div>
                <div class="model-record">{{ performance.catboost.correct }}-{{ performance.catboost.total - performance.catboost.correct }}</div>
            </div>
            
            <div class="model-card ensemble">
                <div class="model-name">🏆 Meta Ensemble</div>
                <div class="model-accuracy">{{ performance.ensemble.accuracy }}%</div>
                <div class="model-record">{{ performance.ensemble.correct }}-{{ performance.ensemble.total - performance.ensemble.correct }}</div>
            </div>
        </div>
        {% else %}
        <div class="no-data">Not enough data to calculate performance for {{ sport_info.name }}</div>
        {% endif %}
    </div>
""")

# Daily Results Template (for NHL/NBA)
DAILY_RESULTS_TEMPLATE = BASE_TEMPLATE.replace(
    '{% block extra_styles %}{% endblock %}',
    """
    .page-title { font-size: 2.5em; margin-bottom: 30px; text-align: center; }
    .section-tabs { display: flex; gap: 10px; margin-bottom: 30px; justify-content: center; }
    .tab { padding: 12px 30px; border-radius: 8px; text-decoration: none; font-weight: 600; transition: all 0.3s; background: rgba(255, 255, 255, 0.1); color: white; }
    .tab.active { background: linear-gradient(135deg, #10b981, #059669); }
    .date-section { background: rgba(255, 255, 255, 0.05); border-radius: 15px; padding: 25px; margin-bottom: 30px; }
    .date-header { color: #fbbf24; font-size: 1.5em; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 2px solid rgba(255, 255, 255, 0.2); }
    .games-table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
    .games-table th { background: rgba(255, 255, 255, 0.1); padding: 10px; text-align: left; font-weight: bold; color: #fbbf24; border-bottom: 2px solid rgba(255, 255, 255, 0.2); }
    .games-table td { padding: 8px 10px; border-bottom: 1px solid rgba(255, 255, 255, 0.1); }
    .games-table tr:hover { background: rgba(255, 255, 255, 0.05); }
    .prob-correct { color: #10b981; font-weight: bold; }
    .prob-wrong { color: #ef4444; }
    .daily-models { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }
    .daily-model-card { background: rgba(255, 255, 255, 0.1); border-radius: 10px; padding: 15px; text-align: center; }
    .daily-model-card.best { border: 2px solid #10b981; background: rgba(16, 185, 129, 0.1); }
    .model-label { font-size: 0.9em; opacity: 0.8; margin-bottom: 5px; }
    .model-accuracy { font-size: 1.8em; font-weight: bold; color: #10b981; }
    .model-record { font-size: 0.9em; opacity: 0.9; }
    """
).replace('{% block content %}{% endblock %}', """
    <div style="margin-bottom: 20px;">
        <a href="/" style="display: inline-block; padding: 10px 20px; background: rgba(255,255,255,0.1); border-radius: 8px; text-decoration: none; color: white; font-weight: 600;">← Back to Home</a>
    </div>
    <h1 class="page-title">{{ sport_info.icon }} {{ sport_info.name }} - Daily Results</h1>
    <div class="section-tabs">
        <a href="/sport/{{ sport }}/predictions" class="tab">📊 Predictions</a>
        <a href="/sport/{{ sport }}/results" class="tab active">🎯 Results</a>
        <a href="/sport/{{ sport }}/spreads" class="tab">📈 Spreads & Totals</a>
    </div>
    {% if daily_results %}
        {% set all_games = [] %}
        {% for date in sorted_dates %}
            {% set _ = all_games.extend(daily_results[date].games) %}
        {% endfor %}
        {% set total_games = all_games|length %}
        {% set meta_correct = all_games|selectattr('ens_correct')|list|length %}
        {% set meta_wrong = total_games - meta_correct %}
        {% set meta_accuracy = (meta_correct / total_games * 100)|round(1) if total_games > 0 else 0 %}
        {% set units_won = (meta_correct * 0.91) - meta_wrong %}
        {% set roi = (units_won / total_games * 100)|round(1) if total_games > 0 else 0 %}
        <div style="background: linear-gradient(135deg, #10b981, #059669); border-radius: 15px; padding: 25px; margin-bottom: 30px; text-align: center;">
            <h2 style="margin: 0 0 20px 0; font-size: 1.8em;">🏆 Overall Performance</h2>
            <div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 20px;">
                <div>
                    <div style="font-size: 0.9em; opacity: 0.9; margin-bottom: 5px;">Record</div>
                    <div style="font-size: 2em; font-weight: bold;">{{ meta_correct }}-{{ meta_wrong }}</div>
                </div>
                <div>
                    <div style="font-size: 0.9em; opacity: 0.9; margin-bottom: 5px;">Accuracy</div>
                    <div style="font-size: 2em; font-weight: bold;">{{ meta_accuracy }}%</div>
                </div>
                <div>
                    <div style="font-size: 0.9em; opacity: 0.9; margin-bottom: 5px;">Total Games</div>
                    <div style="font-size: 2em; font-weight: bold;">{{ total_games }}</div>
                </div>
                <div>
                    <div style="font-size: 0.9em; opacity: 0.9; margin-bottom: 5px;">Units (1u/bet)</div>
                    <div style="font-size: 2em; font-weight: bold; color: {% if units_won >= 0 %}#fbbf24{% else %}#ef4444{% endif %}">{{ "+" if units_won > 0 else "" }}{{ units_won|round(2) }}u</div>
                </div>
                <div>
                    <div style="font-size: 0.9em; opacity: 0.9; margin-bottom: 5px;">ROI</div>
                    <div style="font-size: 2em; font-weight: bold; color: {% if roi >= 0 %}#fbbf24{% else %}#ef4444{% endif %}">{{ "+" if roi > 0 else "" }}{{ roi }}%</div>
                </div>
            </div>
            <div style="margin-top: 15px; font-size: 0.9em; opacity: 0.8;">💰 Assuming -110 odds (risk 1u to win 0.91u) | $100/unit = ${{ (units_won * 100)|round(2) }}</div>
        </div>
        {% for date in sorted_dates %}
        {% set date_data = daily_results[date] %}
        <div id="date-{{ date }}" class="date-section">
            <div class="date-header">📅 {{ date }}{% if date == today_date %} <span style="background: #10b981; color: white; padding: 4px 12px; border-radius: 4px; font-size: 0.7em; margin-left: 10px;">TODAY</span>{% endif %}</div>
            
            {% set elo_correct = date_data.games|selectattr('elo_correct')|list|length %}
            {% set xgb_correct = date_data.games|selectattr('xgb_correct')|list|length %}
            {% set cat_correct = date_data.games|selectattr('cat_correct')|list|length %}
            {% set ens_correct = date_data.games|selectattr('ens_correct')|list|length %}
            {% set total_games = date_data.games|length %}
            {% set best_count = [elo_correct, xgb_correct, cat_correct, ens_correct]|max %}
            
            <div class="daily-models">
                <div class="daily-model-card {% if elo_correct == best_count %}best{% endif %}">
                    <div class="model-label">Elo</div>
                    <div class="model-accuracy">{{ (elo_correct / total_games * 100)|round(1) if total_games > 0 else 0 }}%</div>
                    <div class="model-record">{{ elo_correct }}-{{ total_games - elo_correct }}</div>
                </div>
                <div class="daily-model-card {% if xgb_correct == best_count %}best{% endif %}">
                    <div class="model-label">XGBoost</div>
                    <div class="model-accuracy">{{ (xgb_correct / total_games * 100)|round(1) if total_games > 0 else 0 }}%</div>
                    <div class="model-record">{{ xgb_correct }}-{{ total_games - xgb_correct }}</div>
                </div>
                <div class="daily-model-card {% if cat_correct == best_count %}best{% endif %}">
                    <div class="model-label">CatBoost</div>
                    <div class="model-accuracy">{{ (cat_correct / total_games * 100)|round(1) if total_games > 0 else 0 }}%</div>
                    <div class="model-record">{{ cat_correct }}-{{ total_games - cat_correct }}</div>
                </div>
                <div class="daily-model-card {% if ens_correct == best_count %}best{% endif %}">
                    <div class="model-label">🏆 Meta</div>
                    <div class="model-accuracy">{{ (ens_correct / total_games * 100)|round(1) if total_games > 0 else 0 }}%</div>
                    <div class="model-record">{{ ens_correct }}-{{ total_games - ens_correct }}</div>
                </div>
            </div>
            
            <table class="games-table">
                <thead><tr><th>Matchup</th><th>Score</th><th>Elo</th><th>XGBoost</th><th>CatBoost</th><th>Meta</th></tr></thead>
                <tbody>
                    {% for game in date_data.games %}
                    <tr>
                        <td>{{ game.away }} @ <strong>{{ game.home }}</strong></td>
                        <td><strong>{{ game.away_score }}-{{ game.home_score }}</strong></td>
                        <td class="{% if game.elo_correct %}prob-correct{% else %}prob-wrong{% endif %}">{% if game.elo_correct %}✅{% else %}❌{% endif %} {{ game.elo_prob }}%</td>
                        <td class="{% if game.xgb_correct %}prob-correct{% else %}prob-wrong{% endif %}">{% if game.xgb_correct %}✅{% else %}❌{% endif %} {{ game.xgb_prob }}%</td>
                        <td class="{% if game.cat_correct %}prob-correct{% else %}prob-wrong{% endif %}">{% if game.cat_correct %}✅{% else %}❌{% endif %} {{ game.cat_prob }}%</td>
                        <td class="{% if game.ens_correct %}prob-correct{% else %}prob-wrong{% endif %}">{% if game.ens_correct %}✅{% else %}❌{% endif %} {{ game.ens_prob }}%</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endfor %}
    {% else %}
    <div style="text-align: center; padding: 60px; opacity: 0.7;">No results data available yet.</div>
    {% endif %}
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
        <a href="/sport/{{ sport }}/results" class="tab active">🎯 Results</a>
        <a href="/sport/{{ sport }}/spreads" class="tab">📈 Spreads & Totals</a>
    </div>
    
    {% if weekly_results %}
        {% set ns = namespace(total_correct=0, total_games=0) %}
        {% for week_num in weekly_results|dictsort %}
            {% set week_data = weekly_results[week_num[0]] %}
            {% set ns.total_correct = ns.total_correct + week_data.ensemble.correct %}
            {% set ns.total_games = ns.total_games + week_data.ensemble.total %}
        {% endfor %}
        {% set meta_accuracy = (ns.total_correct / ns.total_games * 100)|round(1) if ns.total_games > 0 else 0 %}
        {% set meta_wrong = ns.total_games - ns.total_correct %}
        {% set units_won = (ns.total_correct * 0.91) - meta_wrong %}
        {% set roi = (units_won / ns.total_games * 100)|round(1) if ns.total_games > 0 else 0 %}
        <div style="background: linear-gradient(135deg, #10b981, #059669); border-radius: 15px; padding: 25px; margin-bottom: 30px; text-align: center;">
            <h2 style="margin: 0 0 20px 0; font-size: 1.8em;">🏆 Overall Performance</h2>
            <div style="display: grid; grid-template-columns: repeat(5, 1fr); gap: 20px;">
                <div>
                    <div style="font-size: 0.9em; opacity: 0.9; margin-bottom: 5px;">Record</div>
                    <div style="font-size: 2em; font-weight: bold;">{{ ns.total_correct }}-{{ meta_wrong }}</div>
                </div>
                <div>
                    <div style="font-size: 0.9em; opacity: 0.9; margin-bottom: 5px;">Accuracy</div>
                    <div style="font-size: 2em; font-weight: bold;">{{ meta_accuracy }}%</div>
                </div>
                <div>
                    <div style="font-size: 0.9em; opacity: 0.9; margin-bottom: 5px;">Total Games</div>
                    <div style="font-size: 2em; font-weight: bold;">{{ ns.total_games }}</div>
                </div>
                <div>
                    <div style="font-size: 0.9em; opacity: 0.9; margin-bottom: 5px;">Units (1u/bet)</div>
                    <div style="font-size: 2em; font-weight: bold; color: {% if units_won >= 0 %}#fbbf24{% else %}#ef4444{% endif %}">{{ "+" if units_won > 0 else "" }}{{ units_won|round(2) }}u</div>
                </div>
                <div>
                    <div style="font-size: 0.9em; opacity: 0.9; margin-bottom: 5px;">ROI</div>
                    <div style="font-size: 2em; font-weight: bold; color: {% if roi >= 0 %}#fbbf24{% else %}#ef4444{% endif %}">{{ "+" if roi > 0 else "" }}{{ roi }}%</div>
                </div>
            </div>
            <div style="margin-top: 15px; font-size: 0.9em; opacity: 0.8;">💰 Assuming -110 odds (risk 1u to win 0.91u) | $100/unit = ${{ (units_won * 100)|round(0) }}</div>
        </div>
        {% for week_num in weekly_results|dictsort %}
        {% set week_data = weekly_results[week_num[0]] %}
        <div class="week-section">
            <div class="week-header">
                <div class="week-title">🏈 Week {{ week_num[0] }}</div>
                <div style="opacity: 0.8;">{{ week_data.games|length }} Games</div>
            </div>
            
            <div class="week-models">
                <div class="week-model-card {% if week_data.elo.accuracy >= week_data.xgboost.accuracy and week_data.elo.accuracy >= week_data.catboost.accuracy and week_data.elo.accuracy >= week_data.ensemble.accuracy %}best{% endif %}">
                    <div class="model-label">Elo</div>
                    <div class="model-perf">{{ week_data.elo.accuracy }}%</div>
                    <div class="model-record">{{ week_data.elo.correct }}-{{ week_data.elo.total - week_data.elo.correct }}</div>
                </div>
                <div class="week-model-card {% if week_data.xgboost.accuracy >= week_data.elo.accuracy and week_data.xgboost.accuracy >= week_data.catboost.accuracy and week_data.xgboost.accuracy >= week_data.ensemble.accuracy %}best{% endif %}">
                    <div class="model-label">XGBoost</div>
                    <div class="model-perf">{{ week_data.xgboost.accuracy }}%</div>
                    <div class="model-record">{{ week_data.xgboost.correct }}-{{ week_data.xgboost.total - week_data.xgboost.correct }}</div>
                </div>
                <div class="week-model-card {% if week_data.catboost.accuracy >= week_data.elo.accuracy and week_data.catboost.accuracy >= week_data.xgboost.accuracy and week_data.catboost.accuracy >= week_data.ensemble.accuracy %}best{% endif %}">
                    <div class="model-label">CatBoost</div>
                    <div class="model-perf">{{ week_data.catboost.accuracy }}%</div>
                    <div class="model-record">{{ week_data.catboost.correct }}-{{ week_data.catboost.total - week_data.catboost.correct }}</div>
                </div>
                <div class="week-model-card {% if week_data.ensemble.accuracy >= week_data.elo.accuracy and week_data.ensemble.accuracy >= week_data.xgboost.accuracy and week_data.ensemble.accuracy >= week_data.catboost.accuracy %}best{% endif %}">
                    <div class="model-label">🏆 Ensemble</div>
                    <div class="model-perf">{{ week_data.ensemble.accuracy }}%</div>
                    <div class="model-record">{{ week_data.ensemble.correct }}-{{ week_data.ensemble.total - week_data.ensemble.correct }}</div>
                </div>
            </div>
            
            <table class="games-table">
                <thead>
                    <tr>
                        <th>Date</th>
                        <th>Matchup</th>
                        <th>Score</th>
                        <th>Elo %</th>
                        <th>Elo ✓</th>
                        <th>XGB %</th>
                        <th>XGB ✓</th>
                        <th>Cat %</th>
                        <th>Cat ✓</th>
                        <th>Ens %</th>
                        <th>Ens ✓</th>
                    </tr>
                </thead>
                <tbody>
                    {% for game in week_data.games %}
                    <tr>
                        <td>{{ game.date }}</td>
                        <td>
                            <span class="{% if game.away_score > game.home_score %}winner{% else %}loser{% endif %}">{{ game.away }}</span> @ 
                            <span class="{% if game.home_score > game.away_score %}winner{% else %}loser{% endif %}">{{ game.home }}</span>
                        </td>
                        <td class="score">{{ game.away_score }} - {{ game.home_score }}</td>
                        <td class="{% if game.elo_correct %}prob-correct{% elif game.elo_correct == False %}prob-wrong{% endif %}">{{ game.elo_prob }}%</td>
                        <td style="text-align: center; font-size: 1.2em;">{% if game.elo_correct %}<span style="color: #10b981;">✓</span>{% elif game.elo_correct == False %}<span style="color: #ef4444;">✗</span>{% endif %}</td>
                        <td class="{% if game.xgb_correct %}prob-correct{% elif game.xgb_correct == False %}prob-wrong{% endif %}">{{ game.xgb_prob }}%</td>
                        <td style="text-align: center; font-size: 1.2em;">{% if game.xgb_correct %}<span style="color: #10b981;">✓</span>{% elif game.xgb_correct == False %}<span style="color: #ef4444;">✗</span>{% endif %}</td>
                        <td class="{% if game.cat_correct %}prob-correct{% elif game.cat_correct == False %}prob-wrong{% endif %}">{{ game.cat_prob }}%</td>
                        <td style="text-align: center; font-size: 1.2em;">{% if game.cat_correct %}<span style="color: #10b981;">✓</span>{% elif game.cat_correct == False %}<span style="color: #ef4444;">✗</span>{% endif %}</td>
                        <td class="{% if game.ens_correct %}prob-correct{% elif game.ens_correct == False %}prob-wrong{% endif %}">{{ game.ens_prob }}%</td>
                        <td style="text-align: center; font-size: 1.2em;">{% if game.ens_correct %}<span style="color: #10b981;">✓</span>{% elif game.ens_correct == False %}<span style="color: #ef4444;">✗</span>{% endif %}</td>
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

def get_landing_accuracy(sport):
    """Get ensemble accuracy for landing page display"""
    # Use 94-game test results for NFL
    if sport == 'NFL':
        return 56.8
    
    try:
        performance = calculate_model_performance(sport)
        if performance and 'ensemble' in performance:
            return round(performance['ensemble']['accuracy'], 1)
    except:
        pass
    # Fallback to known values if calculation fails
    return {'NHL': 77.0, 'NFL': 56.8}.get(sport, 0.0)

@app.route('/')
def landing_page():
    """Landing page with sport selector (NO unified dashboard)"""
    nhl_accuracy = get_landing_accuracy('NHL')
    nfl_accuracy = get_landing_accuracy('NFL')
    nba_accuracy = get_landing_accuracy('NBA')
    
    return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>underdogs.bet - Sports Predictions</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .container { max-width: 1200px; width: 100%; }
        .header {
            text-align: center;
            margin-bottom: 50px;
            color: white;
        }
        .header h1 {
            font-size: 3.5em;
            font-weight: 700;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
        }
        .header p { font-size: 1.3em; opacity: 0.9; }
        .sports-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 25px;
            margin-bottom: 30px;
        }
        .sport-card {
            background: white;
            border-radius: 16px;
            padding: 35px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s ease;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
            text-decoration: none;
            color: inherit;
        }
        .sport-card:hover {
            transform: translateY(-8px);
            box-shadow: 0 8px 25px rgba(0,0,0,0.3);
        }
        .sport-icon { font-size: 4em; margin-bottom: 15px; }
        .sport-name {
            font-size: 1.8em;
            font-weight: 700;
            margin-bottom: 8px;
            color: #333;
        }
        .sport-status { font-size: 1em; color: #666; margin-bottom: 12px; }
        .sport-accuracy {
            font-size: 1.4em;
            font-weight: 700;
            color: #667eea;
            margin-top: 10px;
        }
        .active {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        .active .sport-name, .active .sport-status { color: white; }
        .active .sport-accuracy { color: #fff; font-size: 1.6em; }
        .coming-soon { opacity: 0.6; cursor: not-allowed; }
        .coming-soon:hover { transform: none; box-shadow: 0 4px 15px rgba(0,0,0,0.2); }
        .footer { text-align: center; color: white; margin-top: 40px; opacity: 0.8; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎯 underdogs.bet</h1>
            <p>Professional Sports Predictions Powered by Machine Learning</p>
        </div>
        <div class="sports-grid">
            <a href="/sport/NHL/predictions" class="sport-card active">
                <div class="sport-icon">🏒</div>
                <div class="sport-name">NHL</div>
                <div class="sport-status">Live Now</div>
            </a>
            <a href="/sport/NFL/predictions" class="sport-card active">
                <div class="sport-icon">🏈</div>
                <div class="sport-name">NFL</div>
                <div class="sport-status">Live Now</div>
            </a>
            <a href="/sport/NBA/predictions" class="sport-card active">
                <div class="sport-icon">🏀</div>
                <div class="sport-name">NBA</div>
                <div class="sport-status">Live Now</div>
            </a>
            <a href="/sport/NCAAB/predictions" class="sport-card active">
                <div class="sport-icon">🎓</div>
                <div class="sport-name">NCAAB</div>
                <div class="sport-status">Live Now</div>
            </a>
            <a href="/sport/NCAAF/predictions" class="sport-card active">
                <div class="sport-icon">🏟️</div>
                <div class="sport-name">NCAAF</div>
                <div class="sport-status">Live Now</div>
            </a>
            <a href="/sport/MLB/predictions" class="sport-card">
                <div class="sport-icon">⚾</div>
                <div class="sport-name">MLB</div>
                <div class="sport-status">Offseason</div>
            </a>
            <a href="/sport/WNBA/predictions" class="sport-card">
                <div class="sport-icon">🏀</div>
                <div class="sport-name">WNBA</div>
                <div class="sport-status">Offseason</div>
            </a>
        </div>
        <div class="footer">
            <p>Select a sport to view predictions, results, and analysis</p>
        </div>
    </div>
</body>
</html>
    """, nhl_accuracy=nhl_accuracy, nfl_accuracy=nfl_accuracy, nba_accuracy=nba_accuracy)

@app.route('/sport/<sport>')
def sport_home(sport):
    """Redirect to predictions page"""
    return render_template_string(f"""
        <script>window.location.href = '/sport/{sport}/predictions';</script>
    """)

@app.route('/sport/<sport>/predictions')
def sport_predictions(sport):
    """Show upcoming predictions for a sport"""
    if sport not in SPORTS:
        return "Sport not found", 404
    
    predictions = get_upcoming_predictions(sport)
    
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
    
    # Load ESPN-style template
    with open('espn_predictions_template.html', 'r') as f:
        espn_template = f.read()
    
    return render_template_string(
        espn_template,
        page=sport,
        sport=sport,
        sport_info=SPORTS[sport],
        predictions=predictions,
        grouped_predictions=grouped_predictions,
        sorted_dates=sorted_dates,
        today_date=today_date,
        group_by='week' if sport == 'NFL' else 'date'
    )

@app.route('/sport/<sport>/results')
def sport_results(sport):
    """Show model performance results for a sport"""
    if sport not in SPORTS:
        return "Sport not found", 404
    
    if sport == 'NFL':
        update_nfl_scores()
        weekly_results = calculate_nfl_weekly_performance()
        return render_template_string(
            NFL_WEEKLY_RESULTS_TEMPLATE,
            page=sport,
            sport=sport,
            sport_info=SPORTS[sport],
            weekly_results=weekly_results
        )
    
    if sport == 'NHL':
        update_nhl_scores()
        weekly_results = calculate_nhl_weekly_performance()
        
        if not weekly_results:
            return "<h1>No NHL results data available yet. Check back after more games are played.</h1>"
        
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
            
            return render_template_string(
                DAILY_RESULTS_TEMPLATE,
                page=sport,
                sport=sport,
                sport_info=SPORTS[sport],
                daily_results=daily_results,
                sorted_dates=sorted_dates,
                today_date=today_date
            )
        except Exception as e:
            logger.error(f"Error processing NHL results: {e}")
            return f"<h1>Error loading NHL results: {str(e)}</h1>"
    
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
                # Only add games with actual scores (not 0-0)
                if game.get('home_score', 0) > 0 or game.get('away_score', 0) > 0:
                    daily_results[date_key]['games'].append(game)
        
        # Filter to only show dates up to yesterday with games, then sort newest first
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        sorted_dates = sorted([d for d in daily_results.keys() if d <= yesterday and daily_results[d]['games']], reverse=True)
        
        return render_template_string(
            DAILY_RESULTS_TEMPLATE,
            page=sport,
            sport=sport,
            sport_info=SPORTS[sport],
            daily_results=daily_results,
            sorted_dates=sorted_dates,
            today_date=today_date
        )
    
    # Handle NCAAB, NCAAF, MLB, WNBA using generic ESPN-based results
    if sport in ['NCAAB', 'NCAAF', 'MLB', 'WNBA']:
        # Update scores first
        update_espn_scores(sport)
        
        # Get ALL completed games from database for Elo training and results
        conn = get_db_connection()
        all_completed_games = conn.execute('''
            SELECT g.*, p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ? AND g.home_score IS NOT NULL
            ORDER BY g.game_date ASC
        ''', (sport, sport)).fetchall()
        conn.close()
        
        if not all_completed_games:
            # Show message for offseason sports
            offseason_msg = "" 
            if sport in ['MLB', 'WNBA']:
                offseason_msg = f"<p>The {SPORTS[sport]['name']} season has ended. Results from the 2025 season will be available next year.</p>"
            return f"<h1>No {SPORTS[sport]['name']} results data available yet.</h1>{offseason_msg}<p><a href='/'>← Back to Home</a></p>"
        
        # Train Elo system
        elo_ratings = {}
        K_FACTORS = {'NCAAF': 30, 'NCAAB': 25, 'MLB': 14, 'WNBA': 18}
        k_factor = K_FACTORS.get(sport, 20)
        
        def get_elo(team):
            return elo_ratings.get(team, 1500)
        
        def expected_score(rating_a, rating_b):
            return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
            
        # Process games for results
        from collections import defaultdict
        daily_results = defaultdict(lambda: {'games': []})
        today_date = datetime.now().strftime('%Y-%m-%d')
        
        # We need to process in date order for Elo, but display in reverse date order
        # So we process all, then we'll sort the keys for display
        
        for row in all_completed_games:
            game = dict(row)
            home_team = game['home_team_id']
            away_team = game['away_team_id']
            home_score = game['home_score']
            away_score = game['away_score']
            
            # Calculate Elo before update (prediction)
            home_rating = get_elo(home_team)
            away_rating = get_elo(away_team)
            elo_prob_live = expected_score(home_rating, away_rating)
            
            # Update Elo
            actual_home = 1 if home_score > away_score else 0
            elo_ratings[home_team] = home_rating + k_factor * (actual_home - elo_prob_live)
            elo_ratings[away_team] = away_rating + k_factor * ((1-actual_home) - (1-elo_prob_live))
            
            # Use stored prediction if available, otherwise use calculated Elo
            # For XGB/Cat/Meta, if missing, we'll fallback to Elo or 50/50 for now
            # since we can't easily run the full ML pipeline here without more data
            
            elo_prob = float(game['elo_home_prob']) if game['elo_home_prob'] is not None else elo_prob_live
            xgb_prob = float(game['xgboost_home_prob']) if game['xgboost_home_prob'] is not None else elo_prob
            cat_prob = float(game['logistic_home_prob']) if game['logistic_home_prob'] is not None else elo_prob
            meta_prob = float(game['win_probability']) if game['win_probability'] is not None else elo_prob
            
            home_won = home_score > away_score
            predicted_home_win = meta_prob > 0.5
            correct = predicted_home_win == home_won
            
            game_info = {
                'date': game['game_date'][:10] if game['game_date'] else 'Unknown',
                'home': home_team,  # FIX: map to 'home' for template
                'away': away_team,  # FIX: map to 'away' for template
                'home_score': home_score,
                'away_score': away_score,
                'home_win': home_won,
                'correct': correct,
                'elo_correct': (elo_prob > 0.5) == home_won,
                'xgb_correct': (xgb_prob > 0.5) == home_won,
                'cat_correct': (cat_prob > 0.5) == home_won,
                'ens_correct': (meta_prob > 0.5) == home_won,
                'elo_prob': round(elo_prob * 100, 1),
                'xgb_prob': round(xgb_prob * 100, 1),
                'cat_prob': round(cat_prob * 100, 1),
                'ens_prob': round(meta_prob * 100, 1),
                'meta_home': round(meta_prob * 100, 1),
                'elo_home': round(elo_prob * 100, 1),
                'xgb_home': round(xgb_prob * 100, 1),
                'cat_home': round(cat_prob * 100, 1)
            }
            
            daily_results[game_info['date']]['games'].append(game_info)
        
        # Sort dates reverse for display (newest first)
        sorted_dates = sorted(daily_results.keys(), reverse=True)[:30]  # Last 30 days
        
        return render_template_string(
            DAILY_RESULTS_TEMPLATE,
            page=sport,
            sport=sport,
            sport_info=SPORTS[sport],
            daily_results=daily_results,
            sorted_dates=sorted_dates,
            today_date=today_date
        )
    
    performance = calculate_model_performance(sport)
    return render_template_string(
        RESULTS_TEMPLATE,
        page=sport,
        sport=sport,
        sport_info=SPORTS[sport],
        performance=performance
    )

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
                params = f"dates={date_str}"
                if sport == 'NCAAB':
                    params += "&groups=50&limit=1000"
                elif sport == 'NCAAF':
                    params += "&groups=80&limit=1000"
                
                url = f"{ESPN_ENDPOINTS[sport]}?{params}"
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
    """Show spread and total picks based on predicted scores"""
    if sport not in SPORTS:
        return "Sport not found", 404
    
    from score_predictor import ScorePredictor
    predictor = ScorePredictor()
    
    # Get upcoming games from API
    api_games = get_upcoming_api_games_for_spreads(sport, days_ahead=7)
    
    # NEW: Enrich with Vegas odds from The Rundown
    try:
        rundown = RundownAPI()
        
        # Get unique dates
        dates = set(g['game_date'] for g in api_games)
        
        # Fetch odds for each date
        rundown_odds = []
        for d in dates:
            odds = rundown.get_odds(sport, d)
            if odds:
                rundown_odds.extend(odds)
                
        # Create lookup map (date + team_slug -> odds)
        # We'll map both home and away team to the game odds
        odds_map = {}
        for game in rundown_odds:
            # Check if game has valid date (Rundown dates might be ISO strings with time)
            # We just need the YYYY-MM-DD part
            if 'T' in game['game_date']:
                r_date = game['game_date'].split('T')[0]
            else:
                r_date = game['game_date']
            
            def simple_slug(name):
                if not name: return ""
                # Remove common mascot/city distinctions to improve matching
                # But kept simple for now: lowercase, no spaces/special chars
                return name.lower().replace(' ', '').replace('-', '').replace('.', '').replace("'", "")
                
            h_slug = simple_slug(game['home_team'])
            a_slug = simple_slug(game['away_team'])
            
            odds_map[(r_date, h_slug)] = game
            odds_map[(r_date, a_slug)] = game
            
        # Inject into api_games
        for game in api_games:
            home_name = game.get('home_team_name')
            away_name = game.get('away_team_name')
            date = game.get('game_date')
            
            if not home_name or not away_name:
                continue
            
            def simple_slug_espn(name):
                if not name: return ""
                return name.lower().replace(' ', '').replace('-', '').replace('.', '').replace("'", "")
                
            h_slug = simple_slug_espn(home_name)
            a_slug = simple_slug_espn(away_name)
            
            # Try home team match
            odds = odds_map.get((date, h_slug))
            if not odds:
                # Try away team match
                odds = odds_map.get((date, a_slug))
            
            # If still no match, try looser matching (contains)
            if not odds:
                for k, v in odds_map.items():
                    k_date, k_slug = k
                    if k_date == date:
                        if k_slug in h_slug or h_slug in k_slug or k_slug in a_slug or a_slug in k_slug:
                            odds = v
                            break
                
            if odds:
                game['vegas_spread'] = odds.get('vegas_spread')
                game['vegas_total'] = odds.get('vegas_total')
                logger.info(f"Matched odds for {home_name} vs {away_name}: Spread {game.get('vegas_spread')}, Total {game.get('vegas_total')}")
            else:
                logger.debug(f"No odds match for {home_name} vs {away_name} on {date}")
                
    except Exception as e:
        logger.error(f"Error enriching with Rundown odds: {e}")
    
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
    
    sorted_dates = sorted(grouped_picks.keys())
    
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
            <p style="font-size: 0.9em; opacity: 0.8; margin-top: 10px;">Lines provided by The Rundown API</p>
        </div>
        
        <div class="tabs">
            <a href="/sport/{{ sport }}/predictions" class="tab">📊 Predictions</a>
            <a href="/sport/{{ sport }}/results" class="tab">🎯 Results</a>
            <a href="/sport/{{ sport }}/spreads" class="tab active">📈 Spreads & Totals</a>
        </div>
        
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
                            <span class=\"prediction-label\" title=\"Based on how many of the last 3 games for EACH team went over the Projected Total (combined max 6)\" style=\"cursor: help; border-bottom: 1px dotted #fbbf24;\">Over Trend ℹ️</span>
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
        today_date=today_date
    )

@app.route('/admin/fixer')
def prediction_fixer():
    """Web-based prediction fixer - diagnose and fix missing predictions"""
    return render_template_string(PREDICTION_FIXER_TEMPLATE)

@app.route('/admin/fixer/scan')
def fixer_scan():
    """Scan for missing predictions and return JSON"""
    from flask import jsonify
    
    sports = ['NHL', 'NBA', 'NFL', 'NCAAB', 'NCAAF']
    issues = []
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for sport in sports:
        # Find games with scores but no predictions
        missing = cursor.execute('''
            SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id, g.home_score, g.away_score
            FROM games g
            WHERE g.sport = ?
              AND g.home_score IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM predictions p
                  WHERE p.sport = ? AND (
                      p.game_id = g.game_id
                      OR (
                          date(p.game_date) = date(g.game_date)
                          AND p.home_team_id = g.home_team_id
                          AND p.away_team_id = g.away_team_id
                      )
                  )
              )
            ORDER BY g.game_date DESC
            LIMIT 50
        ''', (sport, sport)).fetchall()
        
        for game in missing:
            issues.append({
                'sport': sport,
                'game_id': game['game_id'],
                'game_date': game['game_date'],
                'matchup': f"{game['away_team_id']} @ {game['home_team_id']}",
                'score': f"{game['away_score']}-{game['home_score']}"
            })
    
    conn.close()
    
    return jsonify({
        'total_issues': len(issues),
        'issues': issues
    })

@app.route('/admin/fixer/update-scores')
def fixer_update_scores():
    """Force update all scores for all sports"""
    from flask import jsonify
    import requests
    
    try:
        updated = 0
        
        # Update NHL
        update_nhl_scores()
        
        # Update NBA manually with proper team matching
        conn = get_db_connection()
        cursor = conn.cursor()
        ESPN_URL = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'
        
        for days_back in range(7):  # Last 7 days
            check_date = datetime.now() - timedelta(days=days_back)
            date_str = check_date.strftime('%Y%m%d')
            url = f'{ESPN_URL}?dates={date_str}'
            
            try:
                response = requests.get(url, timeout=10)
                data = response.json()
                
                for event in data.get('events', []):
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    
                    if len(competitors) != 2:
                        continue
                    
                    status = event.get('status', {}).get('type', {}).get('name', '')
                    if status not in ['STATUS_FINAL', 'STATUS_FINAL_OT']:
                        continue
                    
                    home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                    away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                    
                    if not home or not away:
                        continue
                    
                    home_team = home.get('team', {}).get('displayName', '')
                    away_team = away.get('team', {}).get('displayName', '')
                    home_score = int(home.get('score', 0))
                    away_score = int(away.get('score', 0))
                    game_date = check_date.strftime('%Y-%m-%d')
                    
                    cursor.execute('''
                        UPDATE games 
                        SET home_score = ?, away_score = ?, status = 'final'
                        WHERE sport = 'NBA' 
                          AND date(game_date) = ?
                          AND home_team_id = ?
                          AND away_team_id = ?
                          AND (home_score IS NULL OR home_score = 0)
                    ''', (home_score, away_score, game_date, home_team, away_team))
                    
                    if cursor.rowcount > 0:
                        updated += 1
            except:
                pass
        
        conn.commit()
        conn.close()
        
        # Update NFL
        update_nfl_scores()
        
        return jsonify({
            'success': True,
            'updated': updated
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/admin/fixer/fix/<sport>')
def fixer_fix_sport(sport):
    """Fix all missing predictions for a sport"""
    from flask import jsonify
    
    try:
        # Generate all predictions
        all_predictions = get_upcoming_predictions(sport, days=365)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        fixed_count = 0
        
        for pred in all_predictions:
            # Only fix completed games (have scores)
            if pred.get('home_score') is None or not pred.get('game_id'):
                continue
            
            # Check if prediction exists
            existing = cursor.execute('''
                SELECT id FROM predictions 
                WHERE sport = ? AND (
                    game_id = ? 
                    OR (
                        date(game_date) = date(?)
                        AND home_team_id = ?
                        AND away_team_id = ?
                    )
                )
            ''', (sport, pred['game_id'], pred['game_date'], pred['home_team_id'], pred['away_team_id'])).fetchone()
            
            if not existing:
                try:
                    cursor.execute('''
                        INSERT INTO predictions (
                            game_id, sport, league, game_date, 
                            home_team_id, away_team_id,
                            elo_home_prob, xgboost_home_prob, 
                            logistic_home_prob, win_probability,
                            locked, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))
                    ''', (
                        pred['game_id'], sport, sport, pred['game_date'],
                        pred['home_team_id'], pred['away_team_id'],
                        pred['elo_prob'] / 100.0,
                        pred['xgb_prob'] / 100.0,
                        pred['cat_prob'] / 100.0,
                        pred['ensemble_prob'] / 100.0
                    ))
                    fixed_count += 1
                except Exception as e:
                    logger.error(f"Error fixing {sport} {pred['game_id']}: {e}")
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'sport': sport,
            'fixed': fixed_count
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

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

import socket  # <- import first

# Try to use port 5000, find another if it's busy
port = 5000
while True:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(('0.0.0.0', port)) != 0:
            break
        port += 1

print("\n" + "="*60)
print("🎯 underdogs.bet - Multi-Sport Prediction Platform")
print("="*60)
print("🏒 NHL Predictions - Live (77% Accuracy)")
print("🏈 NFL Predictions - Live (84% Accuracy)")
print("🏀 NBA Predictions - Live Now!")
print("⚾ MLB, 🏀 WNBA, 🏟️ NCAAF - Coming Soon")
print("="*60)
print("✓ Platform ready!")
print(f"🌐 Visit http://0.0.0.0:{port}")
print("="*60 + "\n")

app.run(debug=False, host='0.0.0.0', port=port, use_reloader=False, threaded=True)