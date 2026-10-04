import sqlite3
import requests
from datetime import datetime, timedelta
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE = 'sports_predictions_original.db'

ESPN_ENDPOINTS = {
    'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
    'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
}

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def backfill_sport(sport, start_date, end_date):
    logger.info(f"Backfilling {sport} from {start_date} to {end_date}...")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Optional: Clear existing games for this sport?
    # cursor.execute("DELETE FROM games WHERE sport = ?", (sport,))
    # conn.commit()
    # logger.info(f"Cleared existing {sport} games.")
    
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime('%Y%m%d')
        formatted_date = current_date.strftime('%Y-%m-%d')
        
        try:
            url = f"{ESPN_ENDPOINTS[sport]}?dates={date_str}&limit=1000"
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                logger.warning(f"Error fetching {date_str}: {response.status_code}")
                current_date += timedelta(days=1)
                continue
                
            data = response.json()
            events = data.get('events', [])
            
            count = 0
            for event in events:
                competition = event.get('competitions', [{}])[0]
                competitors = competition.get('competitors', [])
                
                if len(competitors) != 2:
                    continue
                
                status_name = event.get('status', {}).get('type', {}).get('name', '')
                if status_name not in ['STATUS_FINAL', 'STATUS_FINAL_OT', 'STATUS_FINAL_OT2']:
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
                
                game_id = f"{sport}_{event.get('id')}"
                
                # Upsert
                existing = cursor.execute("SELECT 1 FROM games WHERE game_id = ? AND sport = ?", (game_id, sport)).fetchone()
                
                if existing:
                    cursor.execute("""
                        UPDATE games
                        SET home_score = ?, away_score = ?, status = 'final', game_date = ?
                        WHERE sport = ? AND game_id = ?
                    """, (home_score, away_score, formatted_date, sport, game_id))
                else:
                    cursor.execute("""
                        INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')
                    """, (sport, sport, game_id, 2024, formatted_date, home_team, away_team, home_score, away_score))
                count += 1
            
            if count > 0:
                logger.info(f"Processed {count} games for {formatted_date}")
                conn.commit()
                
        except Exception as e:
            logger.error(f"Exception on {date_str}: {e}")
        
        current_date += timedelta(days=1)
        time.sleep(0.1) # Be nice to API

    conn.close()
    logger.info(f"Finished backfilling {sport}.")

if __name__ == "__main__":
    # MLB 2024 Season: March 28 - Oct 30
    backfill_sport('MLB', datetime(2024, 3, 28), datetime(2024, 10, 30))
    
    # WNBA 2024 Season: May 14 - Oct 20
    backfill_sport('WNBA', datetime(2024, 5, 14), datetime(2024, 10, 20))
