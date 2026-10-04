import sqlite3
import nfl_data_py as nfl
import logging
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE = 'sports_predictions_original.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def populate_nfl_games():
    """
    Populate NFL games for 2025 season from nfl_data_py
    """
    try:
        logger.info("Fetching 2025 NFL schedule...")
        schedule = nfl.import_schedules([2025])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Filter for games from now onwards, and recent past games
        today = datetime.now().strftime('%Y-%m-%d')
        recent_games = schedule[schedule['gameday'] >= '2025-12-01'].copy()
        
        logger.info(f"Found {len(recent_games)} games from Dec 1 2025 onwards")
        
        inserted = 0
        updated = 0
        
        for _, game in recent_games.iterrows():
            game_id = game['game_id']
            gameday = game['gameday']
            home_team = game['home_team']
            away_team = game['away_team']
            home_score = int(game['home_score']) if pd.notna(game['home_score']) else None
            away_score = int(game['away_score']) if pd.notna(game['away_score']) else None
            
            # NFL data py uses short abbreviations, map them if needed or use as is
            # For now, we will use what's provided but ensure consistency
            
            # Check if exists
            existing = cursor.execute("SELECT 1 FROM games WHERE game_id = ? AND sport = 'NFL'", (game_id,)).fetchone()
            
            status = 'final' if home_score is not None else 'scheduled'
            
            if existing:
                # Update scores if changed
                cursor.execute("""
                    UPDATE games 
                    SET home_score = ?, away_score = ?, status = ?, game_date = ?
                    WHERE game_id = ? AND sport = 'NFL'
                """, (home_score, away_score, status, gameday, game_id))
                updated += 1
            else:
                # Insert
                cursor.execute("""
                    INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, ('NFL', 'NFL', game_id, 2025, gameday, home_team, away_team, home_score, away_score, status))
                inserted += 1
                
        conn.commit()
        conn.close()
        logger.info(f"NFL Update: {inserted} inserted, {updated} updated")
        
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()

import pandas as pd
if __name__ == "__main__":
    populate_nfl_games()
