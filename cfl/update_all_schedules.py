#!/usr/bin/env python3
"""
Unified Schedule Updater - All Sports
Uses ESPN API for most sports and CFBD API for college football
Run daily to keep schedules fresh
"""

import sqlite3
from datetime import datetime, timedelta
import requests
from colorama import Fore, Style, init
import logging

init(autoreset=True)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "sports_predictions_original.db"
CFBD_API_KEY = "SH+fJUcVIMlSjIJwbqVuSTYU5vN/33MarjANm0toIz9gRmEVObWX6zM2wzU63pwh"

# ESPN API endpoints
ESPN_APIS = {
    'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
    'NHL': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard',
    'NFL': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
    'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
    'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
    'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard'
}


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


def get_next_game_id(sport):
    """Get the next available game_id for a sport"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT game_id FROM games 
        WHERE sport = ? AND game_id LIKE ?
        ORDER BY CAST(SUBSTR(game_id, LENGTH(?) + 2) AS INTEGER) DESC
        LIMIT 1
    """, (sport, f"{sport}_%", sport))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        # Extract number from game_id like "NBA_123"
        last_num = int(result[0].split('_')[1])
        return last_num + 1
    return 1


def fetch_espn_schedule(sport, days_ahead=7):
    """
    Fetch schedule from ESPN API for NBA, NHL, NFL, MLB, WNBA
    ESPN API returns games from a date range when you call it
    """
    print(f"\n{Fore.CYAN}Updating {sport} Schedule (ESPN API){Style.RESET_ALL}")
    
    if sport not in ESPN_APIS:
        print(f"  {Fore.RED}No ESPN API for {sport}{Style.RESET_ALL}")
        return 0
    
    clear_future_games(sport)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    added = 0
    game_num = get_next_game_id(sport)
    
    current_season = datetime.now().year
    if sport in ['NBA', 'NHL']:
        # These leagues span calendar years
        if datetime.now().month <= 6:
            current_season = datetime.now().year - 1
    
    # Fetch games for multiple dates
    for days in range(days_ahead):
        date_to_fetch = datetime.now() + timedelta(days=days)
        date_str = date_to_fetch.strftime('%Y%m%d')
        
        url = f"{ESPN_APIS[sport]}?dates={date_str}"
        
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
                
                home_team = home.get('team', {}).get('displayName', '')
                away_team = away.get('team', {}).get('displayName', '')
                game_date_str = event.get('date', '')
                
                if not home_team or not away_team or not game_date_str:
                    continue
                
                # Parse date from UTC and convert to ET (UTC-5)
                game_date_utc = datetime.strptime(game_date_str, '%Y-%m-%dT%H:%M%SZ')
                # Subtract 5 hours to get ET date
                game_date_local = game_date_utc - timedelta(hours=5)
                game_id = f"{sport}_{game_num}"
                
                cursor.execute("""
                    INSERT OR REPLACE INTO games 
                    (sport, league, game_id, season, game_date, home_team_id, away_team_id, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled')
                """, (sport, sport, game_id, current_season, game_date_local.strftime('%Y-%m-%d'), 
                      home_team, away_team))
                
                added += 1
                game_num += 1
            
            if events:
                print(f"  {date_to_fetch.strftime('%Y-%m-%d')}: {len(events)} games")
            
        except Exception as e:
            logger.error(f"  Error fetching {date_str}: {e}")
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Added {added} {sport} games{Style.RESET_ALL}")
    return added


def fetch_cfbd_schedule():
    """
    Fetch NCAAF schedule from College Football Data API
    """
    print(f"\n{Fore.CYAN}Updating NCAAF Schedule (CFBD API){Style.RESET_ALL}")
    
    clear_future_games('NCAAF')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    added = 0
    game_num = get_next_game_id('NCAAF')
    
    current_year = datetime.now().year
    # NCAAF season runs Aug-Jan
    # If we're in Jan-Jul, use last year's season
    # If we're in Aug-Dec, use current year
    if datetime.now().month < 8:
        current_year -= 1
    
    # Get current week
    url = "https://api.collegefootballdata.com/calendar"
    headers = {"Authorization": f"Bearer {CFBD_API_KEY}"}
    params = {"year": current_year}
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        calendar = response.json()
        
        # Find current/future weeks
        today = datetime.now().date()
        future_weeks = [w for w in calendar if datetime.strptime(w['firstGameStart'], '%Y-%m-%dT%H:%M:%S.%fZ').date() >= today]
        
        if not future_weeks:
            print(f"  {Fore.YELLOW}No upcoming weeks found for {current_year} season{Style.RESET_ALL}")
            conn.close()
            return 0
        
        # Fetch games for next 2 weeks
        for week_data in future_weeks[:2]:
            week = week_data['week']
            
            games_url = "https://api.collegefootballdata.com/games"
            games_params = {
                "year": current_year,
                "week": week,
                "seasonType": "regular"  # or "postseason"
            }
            
            games_response = requests.get(games_url, headers=headers, params=games_params, timeout=10)
            games_response.raise_for_status()
            games = games_response.json()
            
            for game in games:
                home_team = game.get('homeTeam', '')
                away_team = game.get('awayTeam', '')
                start_date = game.get('startDate', '')
                
                if not home_team or not away_team or not start_date:
                    continue
                
                # Parse date
                game_date = datetime.strptime(start_date, '%Y-%m-%dT%H:%M:%S.%fZ')
                game_id = f"NCAAF_{game_num}"
                
                cursor.execute("""
                    INSERT OR REPLACE INTO games 
                    (sport, league, game_id, season, game_date, home_team_id, away_team_id, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled')
                """, ('NCAAF', 'NCAAF', game_id, current_year, game_date.strftime('%Y-%m-%d'),
                      home_team, away_team))
                
                added += 1
                game_num += 1
            
            conn.commit()  # Commit after each week
            print(f"  Week {week}: {len(games)} games added")
        
        print(f"  {Fore.GREEN}✓ Total: {added} NCAAF games{Style.RESET_ALL}")
        
    except Exception as e:
        logger.error(f"  Error: {e}")
    
    conn.close()
    return added


def main():
    """Run all schedule updates"""
    print(f"{Fore.CYAN}{'='*60}")
    print(f"Daily Schedule Update - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    total = 0
    
    # Update all sports with ESPN API
    for sport in ['NBA', 'NHL', 'NFL', 'MLB', 'WNBA', 'NCAAF']:
        total += fetch_espn_schedule(sport, days_ahead=7)
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Schedule Update Complete - {total} games added")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
