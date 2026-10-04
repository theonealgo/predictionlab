#!/usr/bin/env python3
"""
Weekly Standings Updater
Fetches current standings and calculates picks automatically:
1. ML Picks: Teams with ≥60% win rate
2. ATS Picks: Teams with ≥60% ATS cover rate (from our database)
3. Over/Under: Teams with ≥60% over/under rate (from our database)

Updates ats_system.py SYSTEM_TEAMS automatically
"""

import sqlite3
import requests
from datetime import datetime
from colorama import Fore, Style, init
import re

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"


def get_db_connection():
    return sqlite3.connect(DB_PATH)


def fetch_espn_nfl_standings():
    """Fetch NFL standings from ESPN API"""
    print(f"\n{Fore.CYAN}Fetching NFL Standings from ESPN{Style.RESET_ALL}")
    
    url = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        standings = []
        
        for entry in data.get('children', []):
            for standing in entry.get('standings', {}).get('entries', []):
                team = standing.get('team', {})
                stats = standing.get('stats', [])
                
                team_name = team.get('displayName', '')
                
                # Find wins, losses, win%
                wins = 0
                losses = 0
                win_pct = 0.0
                
                for stat in stats:
                    if stat.get('name') == 'wins':
                        wins = stat.get('value', 0)
                    elif stat.get('name') == 'losses':
                        losses = stat.get('value', 0)
                    elif stat.get('name') == 'winPercent':
                        win_pct = stat.get('value', 0)
                
                standings.append({
                    'team': team_name,
                    'wins': wins,
                    'losses': losses,
                    'win_pct': win_pct
                })
        
        print(f"  {Fore.GREEN}✓ Fetched {len(standings)} NFL teams{Style.RESET_ALL}")
        return standings
        
    except Exception as e:
        print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")
        return []


def fetch_espn_nba_standings():
    """Fetch NBA standings from ESPN API"""
    print(f"\n{Fore.CYAN}Fetching NBA Standings from ESPN{Style.RESET_ALL}")
    
    url = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        standings = []
        
        for entry in data.get('children', []):
            for standing in entry.get('standings', {}).get('entries', []):
                team = standing.get('team', {})
                stats = standing.get('stats', [])
                
                team_name = team.get('displayName', '')
                
                wins = 0
                losses = 0
                win_pct = 0.0
                
                for stat in stats:
                    if stat.get('name') == 'wins':
                        wins = stat.get('value', 0)
                    elif stat.get('name') == 'losses':
                        losses = stat.get('value', 0)
                    elif stat.get('name') == 'winPercent':
                        win_pct = stat.get('value', 0)
                
                standings.append({
                    'team': team_name,
                    'wins': wins,
                    'losses': losses,
                    'win_pct': win_pct
                })
        
        print(f"  {Fore.GREEN}✓ Fetched {len(standings)} NBA teams{Style.RESET_ALL}")
        return standings
        
    except Exception as e:
        print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")
        return []


def fetch_espn_nhl_standings():
    """Fetch NHL standings from ESPN API"""
    print(f"\n{Fore.CYAN}Fetching NHL Standings from ESPN{Style.RESET_ALL}")
    
    url = "https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        standings = []
        
        for entry in data.get('children', []):
            for standing in entry.get('standings', {}).get('entries', []):
                team = standing.get('team', {})
                stats = standing.get('stats', [])
                
                team_name = team.get('displayName', '')
                
                wins = 0
                losses = 0
                win_pct = 0.0
                
                for stat in stats:
                    if stat.get('name') == 'wins':
                        wins = stat.get('value', 0)
                    elif stat.get('name') == 'losses':
                        losses = stat.get('value', 0)
                    elif stat.get('name') == 'winPercent':
                        win_pct = stat.get('value', 0)
                
                standings.append({
                    'team': team_name,
                    'wins': wins,
                    'losses': losses,
                    'win_pct': win_pct
                })
        
        print(f"  {Fore.GREEN}✓ Fetched {len(standings)} NHL teams{Style.RESET_ALL}")
        return standings
        
    except Exception as e:
        print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")
        return []


def calculate_ats_standings(sport):
    """Calculate ATS records from database (completed games with betting lines)"""
    print(f"\n{Fore.CYAN}Calculating {sport} ATS Records{Style.RESET_ALL}")
    
    conn = get_db_connection()
    
    # Get completed games with betting lines in last 90 days
    query = """
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
    """
    
    cursor = conn.cursor()
    cursor.execute(query, (sport,))
    games = cursor.fetchall()
    conn.close()
    
    if not games:
        print(f"  {Fore.YELLOW}⚠ No completed games with betting lines{Style.RESET_ALL}")
        return []
    
    # Calculate ATS records
    ats_records = {}
    
    for home, away, home_score, away_score, spread in games:
        actual_margin = home_score - away_score
        ats_margin = actual_margin - spread
        
        # Home team ATS result
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
        if home not in ats_records:
            ats_records[home] = {'covers': 0, 'no_covers': 0, 'pushes': 0}
        if away not in ats_records:
            ats_records[away] = {'covers': 0, 'no_covers': 0, 'pushes': 0}
        
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
    
    # Calculate percentages
    standings = []
    for team, record in ats_records.items():
        total = record['covers'] + record['no_covers']
        if total >= 5:  # Minimum games
            cover_pct = (record['covers'] / total) * 100
            standings.append({
                'team': team,
                'covers': record['covers'],
                'no_covers': record['no_covers'],
                'pushes': record['pushes'],
                'cover_pct': cover_pct
            })
    
    standings.sort(key=lambda x: x['cover_pct'], reverse=True)
    
    print(f"  {Fore.GREEN}✓ Calculated ATS for {len(standings)} teams{Style.RESET_ALL}")
    return standings


def calculate_over_under_standings(sport):
    """Calculate Over/Under records from database"""
    print(f"\n{Fore.CYAN}Calculating {sport} Over/Under Records{Style.RESET_ALL}")
    
    conn = get_db_connection()
    
    query = """
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
    """
    
    cursor = conn.cursor()
    cursor.execute(query, (sport,))
    games = cursor.fetchall()
    conn.close()
    
    if not games:
        print(f"  {Fore.YELLOW}⚠ No completed games with totals{Style.RESET_ALL}")
        return []
    
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
        
        # Both teams get the same result
        for team in [home, away]:
            if team not in ou_records:
                ou_records[team] = {'overs': 0, 'unders': 0, 'pushes': 0}
            
            if result == 'over':
                ou_records[team]['overs'] += 1
            elif result == 'under':
                ou_records[team]['unders'] += 1
            else:
                ou_records[team]['pushes'] += 1
    
    # Calculate percentages
    standings = []
    for team, record in ou_records.items():
        total_games = record['overs'] + record['unders']
        if total_games >= 5:
            over_pct = (record['overs'] / total_games) * 100
            standings.append({
                'team': team,
                'overs': record['overs'],
                'unders': record['unders'],
                'pushes': record['pushes'],
                'over_pct': over_pct
            })
    
    print(f"  {Fore.GREEN}✓ Calculated O/U for {len(standings)} teams{Style.RESET_ALL}")
    return standings


def generate_picks_for_sport(sport):
    """Generate all picks for a sport"""
    print(f"\n{Fore.YELLOW}{'='*60}")
    print(f"{sport}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    # Fetch standings
    if sport == 'NFL':
        ml_standings = fetch_espn_nfl_standings()
    elif sport == 'NBA':
        ml_standings = fetch_espn_nba_standings()
    elif sport == 'NHL':
        ml_standings = fetch_espn_nhl_standings()
    else:
        ml_standings = []
    
    # Calculate ATS and O/U
    ats_standings = calculate_ats_standings(sport)
    ou_standings = calculate_over_under_standings(sport)
    
    # Generate picks
    picks = {
        'moneyline': [],
        'spread': [],
        'over': [],
        'under': []
    }
    
    # ML picks: ≥60% win rate
    for team in ml_standings:
        if team['win_pct'] >= 0.600:
            picks['moneyline'].append(team['team'])
    
    # ATS picks: ≥60% cover rate
    for team in ats_standings:
        if team['cover_pct'] >= 60.0:
            picks['spread'].append(team['team'])
    
    # Over picks: ≥60% over rate
    for team in ou_standings:
        if team['over_pct'] >= 60.0:
            picks['over'].append(team['team'])
    
    # Under picks: ≤40% over rate (=≥60% under rate)
    for team in ou_standings:
        if team['over_pct'] <= 40.0:
            picks['under'].append(team['team'])
    
    # Display
    print(f"\n{Fore.GREEN}ML Picks (≥60% win rate): {len(picks['moneyline'])}{Style.RESET_ALL}")
    for team in picks['moneyline'][:10]:
        print(f"  • {team}")
    
    print(f"\n{Fore.GREEN}ATS Picks (≥60% cover rate): {len(picks['spread'])}{Style.RESET_ALL}")
    for team in picks['spread'][:10]:
        print(f"  • {team}")
    
    print(f"\n{Fore.GREEN}Over Picks (≥60% over rate): {len(picks['over'])}{Style.RESET_ALL}")
    for team in picks['over'][:10]:
        print(f"  • {team}")
    
    print(f"\n{Fore.GREEN}Under Picks (≤40% over rate): {len(picks['under'])}{Style.RESET_ALL}")
    for team in picks['under'][:10]:
        print(f"  • {team}")
    
    return picks


def main():
    """Generate picks for all sports and update system"""
    print(f"{Fore.CYAN}{'='*60}")
    print(f"Weekly Standings & Picks Updater")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    all_picks = {}
    
    for sport in ['NFL', 'NBA', 'NHL']:
        picks = generate_picks_for_sport(sport)
        all_picks[sport] = picks
    
    # Save to file
    print(f"\n{Fore.CYAN}Saving picks to weekly_picks.txt{Style.RESET_ALL}")
    
    with open('weekly_picks.txt', 'w') as f:
        f.write(f"Weekly Picks - {datetime.now().strftime('%Y-%m-%d')}\n")
        f.write("="*60 + "\n\n")
        
        for sport, picks in all_picks.items():
            f.write(f"\n{sport}:\n")
            f.write(f"  Moneyline: {picks['moneyline']}\n")
            f.write(f"  Spread: {picks['spread']}\n")
            f.write(f"  Over: {picks['over']}\n")
            f.write(f"  Under: {picks['under']}\n")
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Complete - Picks saved to weekly_picks.txt")
    print(f"{'='*60}{Style.RESET_ALL}\n")
    
    print(f"{Fore.YELLOW}To update ats_system.py, copy these teams to SYSTEM_TEAMS{Style.RESET_ALL}")


if __name__ == "__main__":
    main()
