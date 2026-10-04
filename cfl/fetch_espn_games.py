#!/usr/bin/env python3
"""
Fetch games and scores from ESPN API for all sports
Supports: MLB, NCAAB, NCAAF, WNBA (and existing NBA, NHL, NFL)
"""

import sqlite3
import requests
from datetime import datetime, timedelta
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

# ESPN API endpoints
ESPN_ENDPOINTS = {
    'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
    'NHL': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard',
    'NFL': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
    'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
    'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
    'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
    'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
}

# Sport-specific season info
SPORT_SEASONS = {
    'MLB': {'season': 2025, 'start': '2025-03-27', 'end': '2025-10-01', 'offseason': True},
    'WNBA': {'season': 2025, 'start': '2025-05-14', 'end': '2025-09-20', 'offseason': True},
    'NCAAB': {'season': 2025, 'start': '2024-11-04', 'end': '2025-04-07', 'offseason': False},
    'NCAAF': {'season': 2024, 'start': '2024-08-24', 'end': '2025-01-20', 'offseason': False},
    'NBA': {'season': 2025, 'offseason': False},
    'NHL': {'season': 2025, 'offseason': False},
    'NFL': {'season': 2025, 'offseason': False},
}


def get_db_connection():
    """Get database connection"""
    return sqlite3.connect(DB_PATH)


def get_next_game_id(sport):
    """Get the next available game_id number for a sport"""
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
        try:
            last_num = int(result[0].split('_')[1])
            return last_num + 1
        except:
            return 1
    return 1


def fetch_espn_games(sport, date_str):
    """
    Fetch games from ESPN API for a specific date
    Returns list of game dicts
    """
    if sport not in ESPN_ENDPOINTS:
        return []
    
    url = f"{ESPN_ENDPOINTS[sport]}?dates={date_str}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        games = []
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
            game_date_str = event.get('date', '')
            
            if not home_team or not away_team:
                continue
            
            # Parse game date
            if game_date_str:
                try:
                    game_date_utc = datetime.strptime(game_date_str, '%Y-%m-%dT%H:%M%SZ')
                    game_date_local = game_date_utc - timedelta(hours=5)  # Convert to ET
                    game_date = game_date_local.strftime('%Y-%m-%d')
                except:
                    game_date = date_str[:4] + '-' + date_str[4:6] + '-' + date_str[6:8]
            else:
                game_date = date_str[:4] + '-' + date_str[4:6] + '-' + date_str[6:8]
            
            # Get status
            status_info = event.get('status', {}).get('type', {})
            status_name = status_info.get('name', 'scheduled')
            
            if status_name in ['STATUS_SCHEDULED', 'STATUS_PREGAME']:
                status = 'scheduled'
            elif status_name in ['STATUS_IN_PROGRESS', 'STATUS_HALFTIME', 'STATUS_END_PERIOD']:
                status = 'in_progress'
            elif status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT', 'STATUS_POSTPONED', 'STATUS_CANCELED']:
                status = 'final'
            else:
                status = 'scheduled'
            
            # Get scores if final
            home_score = None
            away_score = None
            
            if status == 'final':
                try:
                    home_score = int(home.get('score', 0))
                    away_score = int(away.get('score', 0))
                except:
                    pass
            
            games.append({
                'event_id': event_id,
                'home_team': home_team,
                'away_team': away_team,
                'game_date': game_date,
                'status': status,
                'home_score': home_score,
                'away_score': away_score
            })
        
        return games
        
    except Exception as e:
        print(f"  Error fetching {date_str}: {e}")
        return []


def import_season_games(sport, start_date=None, end_date=None):
    """
    Import all games for a sport's season
    For offseason sports (MLB, WNBA), imports the full 2025 season
    """
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"Importing {sport} Games from ESPN API")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    season_info = SPORT_SEASONS.get(sport, {})
    season = season_info.get('season', datetime.now().year)
    
    # Set date range
    if start_date:
        current = datetime.strptime(start_date, '%Y-%m-%d')
    elif season_info.get('start'):
        current = datetime.strptime(season_info['start'], '%Y-%m-%d')
    else:
        current = datetime.now() - timedelta(days=7)
    
    if end_date:
        end = datetime.strptime(end_date, '%Y-%m-%d')
    elif season_info.get('end'):
        end = datetime.strptime(season_info['end'], '%Y-%m-%d')
    else:
        end = datetime.now() + timedelta(days=7)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    total_added = 0
    total_updated = 0
    game_num = get_next_game_id(sport)
    
    print(f"  Date range: {current.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
    
    while current <= end:
        date_str = current.strftime('%Y%m%d')
        games = fetch_espn_games(sport, date_str)
        
        if games:
            for game in games:
                # Check if game already exists
                cursor.execute("""
                    SELECT id, home_score FROM games 
                    WHERE sport = ? AND home_team_id = ? AND away_team_id = ? AND date(game_date) = ?
                """, (sport, game['home_team'], game['away_team'], game['game_date']))
                
                existing = cursor.fetchone()
                
                if existing:
                    # Update if we have new scores
                    if game['home_score'] is not None and existing[1] is None:
                        cursor.execute("""
                            UPDATE games SET home_score = ?, away_score = ?, status = ?
                            WHERE id = ?
                        """, (game['home_score'], game['away_score'], game['status'], existing[0]))
                        total_updated += 1
                else:
                    # Insert new game
                    game_id = f"{sport}_{game['event_id']}" if game['event_id'] else f"{sport}_{game_num}"
                    game_num += 1
                    
                    cursor.execute("""
                        INSERT INTO games 
                        (game_id, sport, league, season, game_date, home_team_id, away_team_id, 
                         home_score, away_score, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (game_id, sport, sport, season, game['game_date'],
                          game['home_team'], game['away_team'],
                          game['home_score'], game['away_score'], game['status']))
                    total_added += 1
            
            if len(games) > 0:
                print(f"  {current.strftime('%Y-%m-%d')}: {len(games)} games")
        
        current += timedelta(days=1)
        
        # Commit every 7 days to avoid losing progress
        if (current - datetime.strptime(start_date or season_info.get('start', '2025-01-01'), '%Y-%m-%d')).days % 7 == 0:
            conn.commit()
    
    conn.commit()
    conn.close()
    
    print(f"\n{Fore.GREEN}✓ Added {total_added} new games, updated {total_updated} games{Style.RESET_ALL}")
    return total_added, total_updated


def import_upcoming_games(sport, days_ahead=14):
    """
    Import upcoming games for a sport (for active seasons)
    """
    print(f"\n{Fore.CYAN}Importing upcoming {sport} games (next {days_ahead} days){Style.RESET_ALL}")
    
    season_info = SPORT_SEASONS.get(sport, {})
    season = season_info.get('season', datetime.now().year)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    total_added = 0
    game_num = get_next_game_id(sport)
    
    for days in range(days_ahead):
        current = datetime.now() + timedelta(days=days)
        date_str = current.strftime('%Y%m%d')
        games = fetch_espn_games(sport, date_str)
        
        if games:
            for game in games:
                # Check if game already exists
                cursor.execute("""
                    SELECT id FROM games 
                    WHERE sport = ? AND home_team_id = ? AND away_team_id = ? AND date(game_date) = ?
                """, (sport, game['home_team'], game['away_team'], game['game_date']))
                
                if not cursor.fetchone():
                    game_id = f"{sport}_{game['event_id']}" if game['event_id'] else f"{sport}_{game_num}"
                    game_num += 1
                    
                    cursor.execute("""
                        INSERT INTO games 
                        (game_id, sport, league, season, game_date, home_team_id, away_team_id, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (game_id, sport, sport, season, game['game_date'],
                          game['home_team'], game['away_team'], game['status']))
                    total_added += 1
            
            if len(games) > 0:
                print(f"  {current.strftime('%Y-%m-%d')}: {len(games)} games")
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Added {total_added} {sport} games{Style.RESET_ALL}")
    return total_added


def main():
    """Main function to import games for all new sports"""
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"ESPN Game Importer - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    # Import NCAAB (current season, upcoming)
    import_upcoming_games('NCAAB', days_ahead=14)
    
    # Import NCAAF (current season, upcoming)
    import_upcoming_games('NCAAF', days_ahead=14)
    
    # For MLB and WNBA (offseason), we need to import past season
    # Uncomment below to import full seasons (takes a while)
    # import_season_games('MLB')  # Full 2025 MLB season
    # import_season_games('WNBA')  # Full 2025 WNBA season
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Import Complete")
    print(f"{'='*60}{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
