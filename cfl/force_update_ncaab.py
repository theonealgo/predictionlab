import sqlite3
import requests
from datetime import datetime, timedelta
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE = 'sports_predictions_original.db'

def update_ncaab_recent():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    today = datetime.now()
    # Go back 30 days
    for d in range(1, 31):
        date = today - timedelta(days=d)
        date_str = date.strftime('%Y%m%d')
        formatted_date = date.strftime('%Y-%m-%d')
        
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={date_str}&groups=50&limit=1000"
        
        try:
            r = requests.get(url, timeout=10)
            data = r.json()
            events = data.get('events', [])
            logger.info(f"Date {formatted_date}: Found {len(events)} events")
            
            for event in events:
                if event.get('status', {}).get('type', {}).get('name') not in ['STATUS_FINAL', 'STATUS_FINAL_OT']:
                    continue
                    
                competitors = event['competitions'][0]['competitors']
                home = next(c for c in competitors if c['homeAway'] == 'home')
                away = next(c for c in competitors if c['homeAway'] == 'away')
                
                game_id = f"NCAAB_{event['id']}"
                home_score = int(home['score'])
                away_score = int(away['score'])
                
                # Check if exists
                existing = cursor.execute("SELECT 1 FROM games WHERE game_id = ?", (game_id,)).fetchone()
                
                if existing:
                    cursor.execute("UPDATE games SET home_score=?, away_score=?, status='final' WHERE game_id=?", 
                                   (home_score, away_score, game_id))
                else:
                    # We might be missing games if they weren't in the schedule initially
                    # Insert them
                    home_team = home['team']['displayName']
                    away_team = away['team']['displayName']
                    cursor.execute("INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                   ('NCAAB', 'NCAAB', game_id, 2025, formatted_date, home_team, away_team, home_score, away_score, 'final'))
            
            conn.commit()
        except Exception as e:
            logger.error(f"Error on {formatted_date}: {e}")
            
    conn.close()

if __name__ == "__main__":
    update_ncaab_recent()
