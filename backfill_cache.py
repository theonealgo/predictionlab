import sqlite3
import logging
from NHL77FINAL import _frozen_batch_v2_predictions

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('backfill')

def main():
    conn = sqlite3.connect('predictions.db')
    conn.row_factory = sqlite3.Row
    
    logger.info("Fetching all completed games...")
    games_rows = conn.execute('''
        SELECT sport, home_team AS home_team_id, away_team AS away_team_id, game_date
        FROM games
        WHERE home_score IS NOT NULL 
          AND away_score IS NOT NULL 
          AND home_score != away_score
        ORDER BY game_date DESC
    ''').fetchall()
    conn.close()
    
    all_games = [dict(g) for g in games_rows]
    logger.info(f"Found {len(all_games)} completed games.")
    
    chunk_size = 50
    total_processed = 0
    
    for i in range(0, len(all_games), chunk_size):
        chunk = all_games[i:i + chunk_size]
        logger.info(f"Processing games {i} to {i + len(chunk)} of {len(all_games)}...")
        
        # Bypass the length limit since this is the background job
        _frozen_batch_v2_predictions(chunk, bypass_limit=True)
        total_processed += len(chunk)
        
    logger.info(f"Finished backfilling {total_processed} games into the cache!")

if __name__ == "__main__":
    main()
