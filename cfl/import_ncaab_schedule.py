#!/usr/bin/env python3
"""
Import NCAAB Schedule from ESPN API
"""

import requests
from datetime import datetime, timedelta
import sqlite3
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

def import_ncaab_schedule(days_ahead=14):
    """Import NCAAB games from ESPN API"""
    
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"Importing NCAAB Schedule from ESPN")
    print(f"{'='*60}{Style.RESET_ALL}\n")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    total_added = 0
    
    for days in range(days_ahead):
        date_to_fetch = datetime.now() + timedelta(days=days)
        date_str = date_to_fetch.strftime('%Y%m%d')
        
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={date_str}"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            events = data.get('events', [])
            
            if events:
                print(f"{Fore.YELLOW}{date_to_fetch.strftime('%Y-%m-%d')}: {len(events)} games{Style.RESET_ALL}")
            
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
                game_date_str = event.get('date', '')
                
                # Parse game date
                if game_date_str:
                    try:
                        game_date_utc = datetime.strptime(game_date_str, '%Y-%m-%dT%H:%M%SZ')
                        game_date_local = game_date_utc - timedelta(hours=5)  # Convert to ET
                        game_date = game_date_local.strftime('%Y-%m-%d')
                    except:
                        game_date = date_to_fetch.strftime('%Y-%m-%d')
                else:
                    game_date = date_to_fetch.strftime('%Y-%m-%d')
                
                # Get status
                status_info = event.get('status', {}).get('type', {})
                status_name = status_info.get('name', 'scheduled')
                
                # Map ESPN status to our status
                if status_name in ['STATUS_SCHEDULED', 'STATUS_PREGAME']:
                    status = 'scheduled'
                elif status_name in ['STATUS_IN_PROGRESS', 'STATUS_HALFTIME']:
                    status = 'in_progress'
                elif status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT']:
                    status = 'final'
                else:
                    status = 'scheduled'
                
                # Get scores if final
                home_score = None
                away_score = None
                
                if status == 'final':
                    home_score = home.get('score')
                    away_score = away.get('score')
                
                game_id = f"NCAAB_{event_id}"
                season = 2025  # Current season
                
                # Insert game
                try:
                    cursor.execute("""
                        INSERT OR REPLACE INTO games 
                        (game_id, sport, league, season, game_date, home_team_id, away_team_id, 
                         home_score, away_score, status)
                        VALUES (?, 'NCAAB', 'NCAAB', ?, ?, ?, ?, ?, ?, ?)
                    """, (game_id, season, game_date, home_team, away_team, home_score, away_score, status))
                    
                    total_added += 1
                    
                except Exception as e:
                    print(f"  {Fore.RED}Error adding game: {e}{Style.RESET_ALL}")
        
        except Exception as e:
            print(f"  {Fore.RED}Error fetching {date_str}: {e}{Style.RESET_ALL}")
    
    conn.commit()
    conn.close()
    
    print(f"\n{Fore.GREEN}✓ Added {total_added} NCAAB games{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")
    
    return total_added

if __name__ == "__main__":
    import_ncaab_schedule(days_ahead=14)
