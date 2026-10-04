#!/usr/bin/env python3
"""
Update Team Records - Run Every 3 Days
=======================================
Fetches current standings from ESPN for win%
Calculates ATS and O/U records from completed games
"""

import sqlite3
import requests
from datetime import datetime
from colorama import Fore, Style, init
from bs4 import BeautifulSoup

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

ESPN_STANDINGS = {
    'NBA': 'https://site.api.espn.com/apis/v2/sports/basketball/nba/standings',
    'NHL': 'https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings',
    'NFL': 'https://site.api.espn.com/apis/v2/sports/football/nfl/standings',
}

SPORTSBETTINGDIME_URLS = {
    'NBA': 'https://www.sportsbettingdime.com/nba/team-trends/',
    'NHL': 'https://www.sportsbettingdime.com/nhl/team-trends/',
    'NFL': 'https://www.sportsbettingdime.com/nfl/team-trends/',
    'NCAAF': 'https://www.sportsbettingdime.com/ncaaf/team-trends/'
}

# Team name mappings from abbreviations
TEAM_NAME_MAP = {
    'NHL': {
        'CHI': 'Chicago Blackhawks', 'PIT': 'Pittsburgh Penguins', 'BOS': 'Boston Bruins',
        'SEA': 'Seattle Kraken', 'SJ': 'San Jose Sharks', 'PHI': 'Philadelphia Flyers',
        'CLB': 'Columbus Blue Jackets', 'ANA': 'Anaheim Ducks', 'DET': 'Detroit Red Wings',
        'NYR': 'New York Rangers', 'COL': 'Colorado Avalanche', 'CAL': 'Calgary Flames',
        'WAS': 'Washington Capitals', 'MIN': 'Minnesota Wild', 'NYI': 'New York Islanders',
        'VAN': 'Vancouver Canucks', 'NAS': 'Nashville Predators', 'WIN': 'Winnipeg Jets',
        'CAR': 'Carolina Hurricanes', 'UTA': 'Utah Hockey Club', 'LA': 'Los Angeles Kings',
        'NJ': 'New Jersey Devils', 'OTT': 'Ottawa Senators', 'TB': 'Tampa Bay Lightning',
        'BUF': 'Buffalo Sabres', 'MON': 'Montreal Canadiens', 'DAL': 'Dallas Stars',
        'STL': 'St. Louis Blues', 'TOR': 'Toronto Maple Leafs', 'VEG': 'Vegas Golden Knights',
        'FLA': 'Florida Panthers', 'EDM': 'Edmonton Oilers'
    },
    'NBA': {}, # Will be full team names
    'NFL': {}, # Will be full team names
}


def scrape_sportsbettingdime(sport):
    """Scrape ATS and O/U records from sportsbettingdime.com"""
    print(f"\n{Fore.CYAN}Scraping {sport} ATS/O-U from SportsBettingDime{Style.RESET_ALL}")
    
    if sport not in SPORTSBETTINGDIME_URLS:
        print(f"  {Fore.YELLOW}No URL for {sport}{Style.RESET_ALL}")
        return {}
    
    try:
        response = requests.get(SPORTSBETTINGDIME_URLS[sport], timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        records = {}
        table = soup.find('table')
        
        if not table:
            print(f"  {Fore.RED}No table found{Style.RESET_ALL}")
            return {}
        
        rows = table.find_all('tr')[1:]  # Skip header
        
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 4:
                continue
            
            team_abbr = cols[1].text.strip()
            ml_record = cols[2].text.strip()  # e.g., "8 - 9"
            ats_record = cols[3].text.strip()  # e.g., "14 - 3"
            ou_record = cols[4].text.strip() if len(cols) > 4 else "0 - 0"
            
            # Map abbreviation to full team name
            team_map = TEAM_NAME_MAP.get(sport, {})
            team_name = team_map.get(team_abbr, team_abbr)
            
            # Parse records
            def parse_record(rec_str):
                try:
                    parts = rec_str.split('-')
                    wins = int(parts[0].strip())
                    losses = int(parts[1].strip())
                    return wins, losses
                except:
                    return 0, 0
            
            ml_w, ml_l = parse_record(ml_record)
            ats_w, ats_l = parse_record(ats_record)
            ou_w, ou_l = parse_record(ou_record)
            
            ml_total = ml_w + ml_l
            ats_total = ats_w + ats_l
            ou_total = ou_w + ou_l
            
            records[team_name] = {
                'ml_wins': ml_w,
                'ml_losses': ml_l,
                'ml_pct': ml_w / ml_total if ml_total > 0 else 0,
                'ats_wins': ats_w,
                'ats_losses': ats_l,
                'ats_pct': ats_w / ats_total if ats_total > 0 else 0,
                'over_wins': ou_w,
                'over_losses': ou_l,
                'over_pct': ou_w / ou_total if ou_total > 0 else 0,
                'under_wins': ou_l,
                'under_losses': ou_w,
                'under_pct': ou_l / ou_total if ou_total > 0 else 0
            }
        
        print(f"  {Fore.GREEN}✓ Scraped {len(records)} teams{Style.RESET_ALL}")
        return records
        
    except Exception as e:
        print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")
        return {}


def fetch_espn_standings(sport):
    """Fetch win-loss records from ESPN standings"""
    print(f"\n{Fore.CYAN}Fetching {sport} Standings (ESPN){Style.RESET_ALL}")
    
    if sport not in ESPN_STANDINGS:
        print(f"  {Fore.YELLOW}No ESPN standings for {sport}{Style.RESET_ALL}")
        return {}
    
    try:
        response = requests.get(ESPN_STANDINGS[sport], timeout=10)
        response.raise_for_status()
        data = response.json()
        
        records = {}
        
        # Parse standings structure
        for group in data.get('children', []):
            for standing in group.get('standings', {}).get('entries', []):
                team = standing.get('team', {})
                team_name = team.get('displayName', '')
                stats = standing.get('stats', [])
                
                # Find wins and losses
                wins = 0
                losses = 0
                for stat in stats:
                    if stat.get('name') == 'wins':
                        wins = int(stat.get('value', 0))
                    elif stat.get('name') == 'losses':
                        losses = int(stat.get('value', 0))
                
                total_games = wins + losses
                win_pct = wins / total_games if total_games > 0 else 0
                
                records[team_name] = {
                    'wins': wins,
                    'losses': losses,
                    'win_pct': win_pct
                }
        
        print(f"  {Fore.GREEN}✓ Fetched {len(records)} teams{Style.RESET_ALL}")
        return records
        
    except Exception as e:
        print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")
        return {}


def calculate_ats_records(sport):
    """Calculate ATS records from completed games with betting lines"""
    print(f"\n{Fore.CYAN}Calculating {sport} ATS Records{Style.RESET_ALL}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get completed games with betting lines
    cursor.execute("""
        SELECT g.home_team_id, g.away_team_id, g.home_score, g.away_score,
               bl.spread
        FROM games g
        JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ? AND g.status = 'final' 
        AND bl.spread IS NOT NULL
        AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
    """, (sport,))
    
    games = cursor.fetchall()
    conn.close()
    
    # Calculate ATS for each team
    ats_records = {}
    
    for game in games:
        home, away, h_score, a_score, spread = game
        
        if home not in ats_records:
            ats_records[home] = {'wins': 0, 'losses': 0}
        if away not in ats_records:
            ats_records[away] = {'wins': 0, 'losses': 0}
        
        # Home team covers if they win by more than spread
        actual_margin = h_score - a_score
        
        if abs(actual_margin - spread) < 0.5:
            # Push - don't count
            continue
        elif actual_margin > spread:
            # Home covers
            ats_records[home]['wins'] += 1
            ats_records[away]['losses'] += 1
        else:
            # Away covers
            ats_records[away]['wins'] += 1
            ats_records[home]['losses'] += 1
    
    # Calculate percentages
    for team in ats_records:
        total = ats_records[team]['wins'] + ats_records[team]['losses']
        ats_records[team]['pct'] = ats_records[team]['wins'] / total if total > 0 else 0
    
    print(f"  {Fore.GREEN}✓ Calculated ATS for {len(ats_records)} teams{Style.RESET_ALL}")
    return ats_records


def calculate_over_under_records(sport):
    """Calculate over/under records from completed games with totals"""
    print(f"\n{Fore.CYAN}Calculating {sport} Over/Under Records{Style.RESET_ALL}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get completed games with totals
    cursor.execute("""
        SELECT g.home_team_id, g.away_team_id, g.home_score, g.away_score,
               bl.total
        FROM games g
        JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ? AND g.status = 'final'
        AND bl.total IS NOT NULL
        AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
    """, (sport,))
    
    games = cursor.fetchall()
    conn.close()
    
    # Calculate O/U for each team
    ou_records = {}
    
    for game in games:
        home, away, h_score, a_score, total = game
        
        if home not in ou_records:
            ou_records[home] = {'over': 0, 'under': 0}
        if away not in ou_records:
            ou_records[away] = {'over': 0, 'under': 0}
        
        actual_total = h_score + a_score
        
        if abs(actual_total - total) < 0.5:
            # Push - don't count
            continue
        elif actual_total > total:
            # Over
            ou_records[home]['over'] += 1
            ou_records[away]['over'] += 1
        else:
            # Under
            ou_records[home]['under'] += 1
            ou_records[away]['under'] += 1
    
    # Calculate percentages
    for team in ou_records:
        total_games = ou_records[team]['over'] + ou_records[team]['under']
        ou_records[team]['over_pct'] = ou_records[team]['over'] / total_games if total_games > 0 else 0
        ou_records[team]['under_pct'] = ou_records[team]['under'] / total_games if total_games > 0 else 0
    
    print(f"  {Fore.GREEN}✓ Calculated O/U for {len(ou_records)} teams{Style.RESET_ALL}")
    return ou_records


def update_team_records(sport):
    """Update team_records table with current data"""
    print(f"\n{Fore.CYAN}{'='*60}")
    print(f"Updating {sport} Team Records")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    # Primary source: Scrape from sportsbettingdime (has full season data)
    scraped = scrape_sportsbettingdime(sport)
    
    # Fallback: Use ESPN standings for ML% if scraping fails
    standings = fetch_espn_standings(sport) if not scraped else {}
    
    # Combine and save to database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    all_teams = set(scraped.keys()) | set(standings.keys())
    updated = 0
    
    for team in all_teams:
        if team in scraped:
            # Use scraped data (preferred)
            rec = scraped[team]
            cursor.execute("""
                INSERT OR REPLACE INTO team_records
                (sport, team_name, wins, losses, win_pct,
                 ats_wins, ats_losses, ats_pct,
                 over_wins, over_losses, over_pct,
                 under_wins, under_losses, under_pct,
                 last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                sport, team,
                rec['ml_wins'], rec['ml_losses'], rec['ml_pct'],
                rec['ats_wins'], rec['ats_losses'], rec['ats_pct'],
                rec['over_wins'], rec['over_losses'], rec['over_pct'],
                rec['under_wins'], rec['under_losses'], rec['under_pct']
            ))
        else:
            # Fallback to ESPN standings only (no ATS/O-U)
            ml = standings.get(team, {'wins': 0, 'losses': 0, 'win_pct': 0})
            cursor.execute("""
                INSERT OR REPLACE INTO team_records
                (sport, team_name, wins, losses, win_pct, last_updated)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (sport, team, ml['wins'], ml['losses'], ml['win_pct']))
        
        updated += 1
    
    conn.commit()
    conn.close()
    
    print(f"\n{Fore.GREEN}✓ Updated {updated} {sport} team records{Style.RESET_ALL}")


def main():
    """Update all sports"""
    print(f"{Fore.CYAN}{'='*60}")
    print(f"Team Records Updater - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Run every 3 days to keep records current")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    for sport in ['NBA', 'NHL', 'NFL']:
        update_team_records(sport)
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print("✓ All team records updated")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
