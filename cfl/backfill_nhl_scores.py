#!/usr/bin/env python3
"""
Backfill missing NHL scores from November 14 to December 9
"""
import requests
import sqlite3
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = 'sports_predictions_original.db'

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

def backfill_nhl_scores(start_date_str, end_date_str):
    """Backfill NHL scores for date range"""
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
    end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    current_date = start_date
    total_updates = 0
    
    while current_date <= end_date:
        date_str = current_date.strftime('%Y-%m-%d')
        logger.info(f"Fetching scores for {date_str}...")
        
        try:
            url = f"https://api-web.nhle.com/v1/score/{date_str}"
            response = requests.get(url, timeout=10)
            
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
                        
                        # Update database (match by team names and date)
                        cursor.execute("""
                            UPDATE games
                            SET home_score = ?, away_score = ?, status = 'final'
                            WHERE sport = 'NHL' 
                              AND home_team_id = ? 
                              AND away_team_id = ?
                              AND game_date LIKE ?
                              AND (home_score IS NULL OR home_score != ?)
                        """, (home_score, away_score, home_team, away_team, f"{date_str}%", home_score))
                        
                        if cursor.rowcount > 0:
                            total_updates += 1
                            logger.info(f"  ✓ Updated {away_team} @ {home_team}: {away_score}-{home_score}")
        
        except Exception as e:
            logger.error(f"Error fetching {date_str}: {e}")
        
        current_date += timedelta(days=1)
    
    conn.commit()
    conn.close()
    
    logger.info(f"\n✓ Backfill complete! Updated {total_updates} games.")
    return total_updates

if __name__ == '__main__':
    start_date = '2025-11-14'  # first date you want to backfill
    yesterday = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    backfill_nhl_scores(start_date, yesterday)