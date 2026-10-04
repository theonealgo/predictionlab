#!/usr/bin/env python3
"""
Automated Weekly System Teams Updater
Runs every Monday to:
1. Fetch current standings from ESPN (for ML picks)
2. Calculate ATS records from completed games with betting lines
3. Calculate O/U records from completed games with totals
4. Auto-update ats_system.py SYSTEM_TEAMS dictionary
"""

import sqlite3
import requests
import re
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"


def get_db_connection():
    return sqlite3.connect(DB_PATH)


def fetch_espn_standings(sport):
    """Fetch standings from ESPN for ML picks"""
    print(f"\n{Fore.CYAN}Fetching {sport} Standings from ESPN{Style.RESET_ALL}")
    
    urls = {
        'NFL': 'https://site.api.espn.com/apis/v2/sports/football/nfl/standings',
        'NBA': 'https://site.api.espn.com/apis/v2/sports/basketball/nba/standings',
        'NHL': 'https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings'
    }
    
    if sport not in urls:
        return []
    
    try:
        response = requests.get(urls[sport], timeout=10)
        response.raise_for_status()
        data = response.json()
        
        teams_60_plus = []
        
        for entry in data.get('children', []):
            for standing in entry.get('standings', {}).get('entries', []):
                team = standing.get('team', {})
                stats = standing.get('stats', [])
                
                team_name = team.get('displayName', '')
                win_pct = 0.0
                
                for stat in stats:
                    if stat.get('name') == 'winPercent':
                        win_pct = stat.get('value', 0)
                
                if win_pct >= 0.600:
                    teams_60_plus.append(team_name)
        
        print(f"  {Fore.GREEN}✓ Found {len(teams_60_plus)} teams with ≥60% win rate{Style.RESET_ALL}")
        return teams_60_plus
        
    except Exception as e:
        print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")
        return []


def calculate_ats_records(sport):
    """Calculate ATS records from completed games with betting lines"""
    print(f"\n{Fore.CYAN}Calculating {sport} ATS Records{Style.RESET_ALL}")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Get completed games with betting lines (last 90 days)
    cursor.execute("""
        SELECT 
            g.home_team_id,
            g.away_team_id,
            g.home_score,
            g.away_score,
            bl.spread
        FROM games g
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ?
          AND g.status = 'final'
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
          AND bl.spread IS NOT NULL
          AND date(g.game_date) >= date('now', '-90 days')
    """, (sport,))
    
    games = cursor.fetchall()
    conn.close()
    
    if not games:
        print(f"  {Fore.YELLOW}⚠ No completed games with betting lines (need historical data){Style.RESET_ALL}")
        return []
    
    # Calculate ATS records
    ats_records = {}
    
    for home, away, home_score, away_score, spread in games:
        actual_margin = home_score - away_score
        ats_margin = actual_margin - spread
        
        # Home team result
        if abs(ats_margin) < 0.5:
            home_result = 'push'
            away_result = 'push'
        elif ats_margin > 0:
            home_result = 'cover'
            away_result = 'no_cover'
        else:
            home_result = 'no_cover'
            away_result = 'cover'
        
        # Update records
        for team in [home, away]:
            if team not in ats_records:
                ats_records[team] = {'covers': 0, 'no_covers': 0, 'pushes': 0}
        
        if home_result == 'cover':
            ats_records[home]['covers'] += 1
        elif home_result == 'no_cover':
            ats_records[home]['no_covers'] += 1
        else:
            ats_records[home]['pushes'] += 1
        
        if away_result == 'cover':
            ats_records[away]['covers'] += 1
        elif away_result == 'no_cover':
            ats_records[away]['no_covers'] += 1
        else:
            ats_records[away]['pushes'] += 1
    
    # Find teams with ≥60% cover rate
    teams_60_plus = []
    for team, record in ats_records.items():
        total = record['covers'] + record['no_covers']
        if total >= 5:  # Minimum games
            cover_pct = (record['covers'] / total) * 100
            if cover_pct >= 60.0:
                teams_60_plus.append(team)
    
    print(f"  {Fore.GREEN}✓ Found {len(teams_60_plus)} teams with ≥60% ATS cover rate{Style.RESET_ALL}")
    return teams_60_plus


def calculate_over_under_records(sport):
    """Calculate Over/Under records from completed games"""
    print(f"\n{Fore.CYAN}Calculating {sport} Over/Under Records{Style.RESET_ALL}")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            g.home_team_id,
            g.away_team_id,
            g.home_score,
            g.away_score,
            bl.total
        FROM games g
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ?
          AND g.status = 'final'
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
          AND bl.total IS NOT NULL
          AND date(g.game_date) >= date('now', '-90 days')
    """, (sport,))
    
    games = cursor.fetchall()
    conn.close()
    
    if not games:
        print(f"  {Fore.YELLOW}⚠ No completed games with totals{Style.RESET_ALL}")
        return [], []
    
    # Calculate O/U records
    ou_records = {}
    
    for home, away, home_score, away_score, total in games:
        actual_total = home_score + away_score
        diff = actual_total - total
        
        if abs(diff) < 0.5:
            result = 'push'
        elif diff > 0:
            result = 'over'
        else:
            result = 'under'
        
        # Both teams get same result
        for team in [home, away]:
            if team not in ou_records:
                ou_records[team] = {'overs': 0, 'unders': 0, 'pushes': 0}
            
            if result == 'over':
                ou_records[team]['overs'] += 1
            elif result == 'under':
                ou_records[team]['unders'] += 1
            else:
                ou_records[team]['pushes'] += 1
    
    # Find over/under teams
    over_teams = []
    under_teams = []
    
    for team, record in ou_records.items():
        total_games = record['overs'] + record['unders']
        if total_games >= 5:
            over_pct = (record['overs'] / total_games) * 100
            
            if over_pct >= 60.0:
                over_teams.append(team)
            elif over_pct <= 40.0:
                under_teams.append(team)
    
    print(f"  {Fore.GREEN}✓ Found {len(over_teams)} over teams, {len(under_teams)} under teams{Style.RESET_ALL}")
    return over_teams, under_teams


def update_ats_system_file(all_picks):
    """Update ats_system.py with new system teams"""
    print(f"\n{Fore.CYAN}Updating ats_system.py{Style.RESET_ALL}")
    
    # Read current file
    with open('ats_system.py', 'r') as f:
        content = f.read()
    
    # Build new SYSTEM_TEAMS dictionary
    new_system_teams = "    SYSTEM_TEAMS = {\n"
    
    for sport in ['NBA', 'NFL', 'NHL', 'NCAAF']:
        picks = all_picks.get(sport, {'moneyline': [], 'spread': [], 'over': [], 'under': []})
        
        new_system_teams += f"        '{sport}': {{\n"
        new_system_teams += f"            'spread': {picks['spread']},\n"
        new_system_teams += f"            'moneyline': {picks['moneyline']},\n"
        new_system_teams += f"            'over': {picks['over']},\n"
        new_system_teams += f"            'under': {picks['under']}\n"
        new_system_teams += "        },\n"
    
    new_system_teams += "    }"
    
    # Replace SYSTEM_TEAMS in file
    pattern = r'SYSTEM_TEAMS\s*=\s*\{[^}]*(?:\{[^}]*\}[^}]*)*\}'
    
    new_content = re.sub(pattern, new_system_teams, content, flags=re.DOTALL)
    
    # Write back
    with open('ats_system.py', 'w') as f:
        f.write(new_content)
    
    print(f"  {Fore.GREEN}✓ Updated ats_system.py{Style.RESET_ALL}")


def main():
    """Main update routine"""
    print(f"{Fore.CYAN}{'='*70}")
    print(f"Automated Weekly System Teams Update")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}{Style.RESET_ALL}")
    
    all_picks = {}
    
    # NFL
    print(f"\n{Fore.YELLOW}{'='*70}")
    print("NFL")
    print(f"{'='*70}{Style.RESET_ALL}")
    
    nfl_ml = fetch_espn_standings('NFL')
    nfl_ats = calculate_ats_records('NFL')
    nfl_over, nfl_under = calculate_over_under_records('NFL')
    
    all_picks['NFL'] = {
        'moneyline': nfl_ml,
        'spread': nfl_ats,
        'over': nfl_over,
        'under': nfl_under
    }
    
    # NBA
    print(f"\n{Fore.YELLOW}{'='*70}")
    print("NBA")
    print(f"{'='*70}{Style.RESET_ALL}")
    
    nba_ml = fetch_espn_standings('NBA')
    nba_ats = calculate_ats_records('NBA')
    nba_over, nba_under = calculate_over_under_records('NBA')
    
    all_picks['NBA'] = {
        'moneyline': nba_ml,
        'spread': nba_ats,
        'over': nba_over,
        'under': nba_under
    }
    
    # NHL
    print(f"\n{Fore.YELLOW}{'='*70}")
    print("NHL")
    print(f"{'='*70}{Style.RESET_ALL}")
    
    nhl_ml = fetch_espn_standings('NHL')
    nhl_ats = calculate_ats_records('NHL')
    nhl_over, nhl_under = calculate_over_under_records('NHL')
    
    all_picks['NHL'] = {
        'moneyline': nhl_ml,
        'spread': nhl_ats,
        'over': nhl_over,
        'under': nhl_under
    }
    
    # NCAAF (use existing data - no ESPN standings API)
    print(f"\n{Fore.YELLOW}{'='*70}")
    print("NCAAF")
    print(f"{'='*70}{Style.RESET_ALL}")
    
    print(f"\n{Fore.CYAN}Using existing NCAAF moneyline teams (62 teams){Style.RESET_ALL}")
    ncaaf_ats = calculate_ats_records('NCAAF')
    ncaaf_over, ncaaf_under = calculate_over_under_records('NCAAF')
    
    # Keep existing ML teams from ats_system.py
    all_picks['NCAAF'] = {
        'moneyline': ['Indiana', 'Ohio State', 'Texas A&M', 'Texas Tech', 'Mississippi',
                     'North Texas', 'Oregon', 'Georgia', 'Alabama', 'James Madison',
                     'BYU', 'Georgia Tech', 'Houston', 'Memphis', 'Virginia',
                     'Vanderbilt', 'San Diego State', 'Texas', 'USC', 'Navy',
                     'Utah', 'Tulane', 'Kennesaw State', 'Cincinnati', 'Louisville',
                     'Miami', 'South Florida', 'Michigan', 'UNLV', 'Oklahoma',
                     'Southern Miss', 'Western Kentucky', 'Pittsburgh', 'Notre Dame',
                     'SMU', 'Hawaii', 'UConn', 'Old Dominion', 'Nebraska',
                     'New Mexico', 'Jacksonville State', 'Boise State', 'Tennessee',
                     'Washington', 'Missouri', 'Illinois', 'East Carolina', 'TCU',
                     'Wake Forest', 'Minnesota', 'Arizona State', 'Iowa',
                     'Missouri State', 'Arizona', 'Fresno State', 'Coastal Carolina',
                     'Central Michigan', 'Toledo', 'Western Michigan', 'Troy',
                     'Iowa State', 'California', 'Ohio'],
        'spread': ncaaf_ats,
        'over': ncaaf_over,
        'under': ncaaf_under
    }
    
    # Update file
    update_ats_system_file(all_picks)
    
    # Summary
    print(f"\n{Fore.GREEN}{'='*70}")
    print("✓ Update Complete!")
    print(f"{'='*70}{Style.RESET_ALL}")
    
    for sport, picks in all_picks.items():
        print(f"\n{sport}:")
        print(f"  ML: {len(picks['moneyline'])} teams")
        print(f"  ATS: {len(picks['spread'])} teams")
        print(f"  Over: {len(picks['over'])} teams")
        print(f"  Under: {len(picks['under'])} teams")
    
    print(f"\n{Fore.YELLOW}Restart the app to see updated picks:{Style.RESET_ALL}")
    print(f"  pkill -f ats_app.py && python3 ats_app.py > /tmp/ats_app.log 2>&1 &\n")


if __name__ == "__main__":
    main()
