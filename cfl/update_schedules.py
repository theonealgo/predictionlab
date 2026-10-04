#!/usr/bin/env python3
"""
Daily Schedule Updater
Fetches fresh schedules from APIs to keep database current
Run this daily via cron or manually
"""

import sqlite3
from datetime import datetime, timedelta
import requests
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"
NBA_API_KEY = "c8dfafba18d44d078ea60a851bcb9bdc"  # SportsData.io


def get_db_connection():
    """Get database connection"""
    return sqlite3.connect(DB_PATH)


def clear_future_games(sport):
    """Remove all future/scheduled games for a sport"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    today = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("""
        DELETE FROM games 
        WHERE sport = ? AND game_date >= ? AND status != 'final'
    """, (sport, today))
    
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    
    print(f"  Cleared {deleted} future {sport} games")
    return deleted


def fetch_nba_schedule():
    """Fetch NBA schedule from SportsData.io"""
    print(f"\n{Fore.CYAN}Updating NBA Schedule{Style.RESET_ALL}")
    
    # Clear old games
    clear_future_games('NBA')
    
    # Fetch next 7 days
    conn = get_db_connection()
    cursor = conn.cursor()
    added = 0
    
    for days_ahead in range(7):
        game_date = datetime.now() + timedelta(days=days_ahead)
        date_str = game_date.strftime('%Y-%b-%d')  # Format: 2025-NOV-14
        
        url = f"https://api.sportsdata.io/v3/nba/scores/json/GamesByDate/{date_str}"
        params = {"key": NBA_API_KEY}
        
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            games = response.json()
            
            for game in games:
                away_team = game.get('AwayTeam', '')
                home_team = game.get('HomeTeam', '')
                game_time = game.get('DateTime', '')
                
                if not away_team or not home_team:
                    continue
                
                # Convert to matchup format
                matchup = f"{away_team} @ {home_team}"
                
                # Insert into database
                cursor.execute("""
                    INSERT OR REPLACE INTO games 
                    (sport, game_date, matchup, home_team, away_team, status)
                    VALUES (?, ?, ?, ?, ?, 'scheduled')
                """, ('NBA', game_date.strftime('%Y-%m-%d'), matchup, home_team, away_team))
                added += 1
            
            print(f"  {game_date.strftime('%Y-%m-%d')}: {len(games)} games")
            
        except Exception as e:
            print(f"  {Fore.RED}Error fetching {date_str}: {e}{Style.RESET_ALL}")
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Added {added} NBA games{Style.RESET_ALL}")
    return added


def fetch_nfl_schedule():
    """Fetch NFL schedule"""
    print(f"\n{Fore.CYAN}Updating NFL Schedule{Style.RESET_ALL}")
    
    try:
        import nfl_data_py as nfl
        import pandas as pd
        
        clear_future_games('NFL')
        
        # Get current season schedule
        current_year = datetime.now().year
        schedule = nfl.import_schedules([current_year])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        added = 0
        
        # Filter for future games only
        today = datetime.now().date()
        
        for _, game in schedule.iterrows():
            game_date = pd.to_datetime(game['gameday']).date()
            
            # Only add future games
            if game_date < today:
                continue
            
            home_team = game.get('home_team', '')
            away_team = game.get('away_team', '')
            
            if not home_team or not away_team:
                continue
            
            matchup = f"{away_team} @ {home_team}"
            
            cursor.execute("""
                INSERT OR REPLACE INTO games 
                (sport, game_date, matchup, home_team, away_team, status)
                VALUES (?, ?, ?, ?, ?, 'scheduled')
            """, ('NFL', str(game_date), matchup, home_team, away_team))
            added += 1
        
        conn.commit()
        conn.close()
        
        print(f"  {Fore.GREEN}✓ Added {added} NFL games{Style.RESET_ALL}")
        return added
        
    except Exception as e:
        print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")
        return 0


def fetch_nhl_schedule():
    """Fetch NHL schedule from ESPN API"""
    print(f"\n{Fore.CYAN}Updating NHL Schedule{Style.RESET_ALL}")
    
    clear_future_games('NHL')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    added = 0
    
    url = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
    
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
            
            home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
            away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
            
            if not home or not away:
                continue
            
            home_team = home.get('team', {}).get('abbreviation', '')
            away_team = away.get('team', {}).get('abbreviation', '')
            game_date_str = event.get('date', '')
            
            if not home_team or not away_team or not game_date_str:
                continue
            
            # Parse date
            game_date = datetime.strptime(game_date_str, '%Y-%m-%dT%H:%M%SZ')
            matchup = f"{away_team} @ {home_team}"
            
            cursor.execute("""
                INSERT OR REPLACE INTO games 
                (sport, game_date, matchup, home_team, away_team, status)
                VALUES (?, ?, ?, ?, ?, 'scheduled')
            """, ('NHL', game_date.strftime('%Y-%m-%d'), matchup, home_team, away_team))
            added += 1
        
        print(f"  {Fore.GREEN}✓ Added {added} NHL games{Style.RESET_ALL}")
        
    except Exception as e:
        print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")
    
    conn.commit()
    conn.close()
    
    return added


def fetch_ncaaf_schedule():
    """Fetch NCAAF schedule"""
    print(f"\n{Fore.CYAN}Updating NCAAF Schedule{Style.RESET_ALL}")
    
    # NCAAF schedule would need a specific API
    # For now, just note that it needs implementation
    print(f"  {Fore.YELLOW}⚠ NCAAF schedule fetch not implemented{Style.RESET_ALL}")
    
    return 0


def main():
    """Run all schedule updates"""
    print(f"{Fore.CYAN}{'='*60}")
    print(f"Daily Schedule Update - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    total = 0
    
    # Update each sport
    total += fetch_nba_schedule()
    total += fetch_nhl_schedule()
    total += fetch_nfl_schedule()
    total += fetch_ncaaf_schedule()
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Schedule Update Complete - {total} games added")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
