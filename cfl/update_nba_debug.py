import sqlite3
import logging
from nba_sportsdata_api import NBASportsDataAPI
from datetime import datetime, timedelta

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE = 'sports_predictions_original.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def update_nba_scores_debug():
    """
    Fetches and updates NBA scores using SportsData API.
    Debug version.
    """
    try:
        logger.info("Fetching NBA scores from SportsData API (last 14 days)...")
        
        nba_api = NBASportsDataAPI()
        
        # Get games from last 14 days to be sure
        games = nba_api.get_recent_and_upcoming_games(days_back=14, days_forward=0)
        
        logger.info(f"Fetched {len(games)} games from API.")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        updates_count = 0
        
        for game in games:
            # Debug: Print game status for recent games
            if game['game_date'] >= '2025-12-09':
                logger.info(f"Game: {game['away_team_id']} @ {game['home_team_id']} on {game['game_date']} status={game['status']} score={game['away_score']}-{game['home_score']}")

            # Only process finished games
            if game['status'] != 'final' or game['home_score'] is None:
                continue
            
            home_team = game['home_team_id']
            away_team = game['away_team_id']
            home_score = game['home_score']
            away_score = game['away_score']
            game_date = game['game_date']
            
            # Update database - match by teams and date
            cursor.execute("""
                UPDATE games
                SET home_score = ?, away_score = ?, status = 'final'
                WHERE sport = 'NBA'
                  AND home_team_id = ?
                  AND away_team_id = ?
                  AND game_date LIKE ?
                  AND (home_score IS NULL OR home_score != ?)
            """, (
                home_score,
                away_score,
                home_team,
                away_team,
                f"{game_date}%",
                home_score
            ))
            
            if cursor.rowcount > 0:
                updates_count += 1
                logger.info(f"✓ Updated {away_team} @ {home_team} on {game_date}: {away_score}-{home_score}")
        
        conn.commit()
        conn.close()
        
        if updates_count > 0:
            logger.info(f"Successfully updated {updates_count} NBA game scores.")
        else:
            logger.info("No NBA score updates needed.")
        
    except Exception as e:
        logger.error(f"An error occurred while updating NBA scores: {e}")

if __name__ == "__main__":
    update_nba_scores_debug()
