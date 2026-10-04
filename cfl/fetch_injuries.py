#!/usr/bin/env python3
"""
Fetch Injury Data from ESPN API
Gets injury reports for all sports to inform betting decisions
"""

import sqlite3
import requests
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

ESPN_INJURY_APIS = {
    'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams',
    'NFL': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams',
    'NHL': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams',
    'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams',
    'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams'
}


def create_injuries_table():
    """Create injuries table if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS injuries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT NOT NULL,
            team_name TEXT NOT NULL,
            player_name TEXT NOT NULL,
            position TEXT,
            status TEXT,
            injury_type TEXT,
            return_date TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sport, team_name, player_name)
        )
    """)
    
    conn.commit()
    conn.close()


def fetch_team_injuries(sport, team_id):
    """Fetch injuries for a specific team"""
    url = f"{ESPN_INJURY_APIS[sport]}/{team_id}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        team_name = data.get('team', {}).get('displayName', '')
        injuries = []
        
        # Get roster with injury data
        roster_url = f"{url}/roster"
        roster_response = requests.get(roster_url, timeout=10)
        
        if roster_response.status_code == 200:
            roster_data = roster_response.json()
            athletes = roster_data.get('athletes', [])
            
            for athlete in athletes:
                injury = athlete.get('injuries', [])
                if injury:
                    for inj in injury:
                        injuries.append({
                            'team_name': team_name,
                            'player_name': athlete.get('displayName', ''),
                            'position': athlete.get('position', {}).get('abbreviation', ''),
                            'status': inj.get('status', ''),
                            'injury_type': inj.get('type', ''),
                            'return_date': inj.get('details', {}).get('fantasyStatus', '')
                        })
        
        return injuries
        
    except Exception as e:
        return []


def fetch_all_injuries(sport):
    """Fetch injuries for all teams in a sport"""
    print(f"\n{Fore.CYAN}Fetching {sport} Injuries{Style.RESET_ALL}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Clear old injuries for this sport
    cursor.execute("DELETE FROM injuries WHERE sport = ?", (sport,))
    
    try:
        # Get all teams
        teams_url = ESPN_INJURY_APIS[sport]
        response = requests.get(teams_url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        teams = data.get('sports', [{}])[0].get('leagues', [{}])[0].get('teams', [])
        total_injuries = 0
        
        for team_data in teams:
            team = team_data.get('team', {})
            team_id = team.get('id')
            team_name = team.get('displayName', '')
            
            if not team_id:
                continue
            
            injuries = fetch_team_injuries(sport, team_id)
            
            for injury in injuries:
                cursor.execute("""
                    INSERT OR REPLACE INTO injuries
                    (sport, team_name, player_name, position, status, injury_type, return_date, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (sport, injury['team_name'], injury['player_name'], 
                      injury['position'], injury['status'], injury['injury_type'], 
                      injury['return_date']))
                
                total_injuries += 1
        
        conn.commit()
        print(f"  {Fore.GREEN}✓ Found {total_injuries} injuries across {len(teams)} teams{Style.RESET_ALL}")
        
    except Exception as e:
        print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")
    
    finally:
        conn.close()


def main():
    print(f"{Fore.CYAN}{'='*60}")
    print(f"ESPN Injury Data Fetcher - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    create_injuries_table()
    
    for sport in ['NBA', 'NFL', 'NHL', 'NCAAF', 'NCAAB']:
        fetch_all_injuries(sport)
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Injury data updated for all sports")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
