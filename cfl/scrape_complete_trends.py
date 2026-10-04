#!/usr/bin/env python3
"""
Scrape Complete Team Trends from SportsBettingDime
Gets ML, ATS, and O/U records for all teams
"""

import requests
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
from colorama import Fore, Style, init
import re

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

TRENDS_URLS = {
    'NBA': 'https://www.sportsbettingdime.com/nba/team-trends/',
    'NFL': 'https://www.sportsbettingdime.com/nfl/team-trends/',
    'NHL': 'https://www.sportsbettingdime.com/nhl/team-trends/',
    'NCAAF': 'https://www.sportsbettingdime.com/ncaaf/team-trends/'
}

# Team name mappings for inconsistencies
TEAM_MAPPINGS = {
    'LA Lakers': 'Los Angeles Lakers',
    'LAL': 'LA Lakers',
    'LAC': 'LA Clippers',
    'BK': 'Brooklyn Nets',
    'NY': 'New York Knicks',
    'SA': 'San Antonio Spurs',
    'GS': 'Golden State Warriors',
    'NO': 'New Orleans Pelicans',
    'PHO': 'Phoenix Suns'
}


def parse_record(record_str):
    """Parse record string like '9-3' or '9-3-0' into wins, losses, pushes"""
    parts = record_str.strip().split('-')
    wins = int(parts[0]) if len(parts) > 0 else 0
    losses = int(parts[1]) if len(parts) > 1 else 0
    pushes = int(parts[2]) if len(parts) > 2 else 0
    return wins, losses, pushes


def scrape_nba_trends():
    """Scrape NBA team trends from SportsBettingDime"""
    print(f"\n{Fore.CYAN}Scraping NBA Team Trends from SportsBettingDime{Style.RESET_ALL}")
    
    url = TRENDS_URLS['NBA']
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Look for table with team trends data
        tables = soup.find_all('table')
        
        if not tables:
            print(f"  {Fore.RED}No tables found on page{Style.RESET_ALL}")
            return []
        
        print(f"  Found {len(tables)} tables, parsing...")
        
        teams_data = []
        
        # Try each table to find the one with team trends
        for table in tables:
            rows = table.find_all('tr')
            if len(rows) < 2:
                continue
            
            # Check header row
            header = rows[0]
            header_cells = header.find_all(['th', 'td'])
            header_text = ' '.join([c.get_text().strip() for c in header_cells])
            
            # Skip if not team trends table
            if 'Team' not in header_text:
                continue
            
            print(f"  Parsing team trends table...")
            
            # Parse data rows
            for row in rows[1:]:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 4:
                    continue
                
                # Extract data from cells
                team_name = cells[1].get_text().strip() if len(cells) > 1 else ""
                ml_record = cells[2].get_text().strip() if len(cells) > 2 else ""
                ats_record = cells[3].get_text().strip() if len(cells) > 3 else ""
                ou_record = cells[4].get_text().strip() if len(cells) > 4 else ""
                
                if not team_name or not ml_record:
                    continue
                
                # Parse records
                ml_w, ml_l, _ = parse_record(ml_record)
                ats_w, ats_l, ats_p = parse_record(ats_record)
                ou_over, ou_under, ou_p = parse_record(ou_record)
                
                # Calculate percentages
                ml_total = ml_w + ml_l
                ats_total = ats_w + ats_l
                ou_total = ou_over + ou_under
                
                ml_pct = ml_w / ml_total if ml_total > 0 else 0
                ats_pct = ats_w / ats_total if ats_total > 0 else 0
                over_pct = ou_over / ou_total if ou_total > 0 else 0
                
                # Apply team name mappings
                team_name = TEAM_MAPPINGS.get(team_name, team_name)
                
                teams_data.append({
                    'team_name': team_name,
                    'wins': ml_w,
                    'losses': ml_l,
                    'win_pct': ml_pct,
                    'ats_wins': ats_w,
                    'ats_losses': ats_l,
                    'ats_pct': ats_pct,
                    'over_wins': ou_over,
                    'under_wins': ou_under,
                    'over_pct': over_pct
                })
        
        if teams_data:
            print(f"  {Fore.GREEN}✓ Scraped {len(teams_data)} teams{Style.RESET_ALL}")
            return teams_data
        else:
            print(f"  {Fore.YELLOW}No team data found, check page structure{Style.RESET_ALL}")
            return []
        
    except Exception as e:
        print(f"  {Fore.RED}Error scraping: {e}{Style.RESET_ALL}")
        import traceback
        traceback.print_exc()
        return []


def update_database(sport, teams_data):
    """Update team_records table with scraped data"""
    if not teams_data:
        print(f"  {Fore.YELLOW}No data to update{Style.RESET_ALL}")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"\n{Fore.CYAN}Updating database...{Style.RESET_ALL}")
    
    for team in teams_data:
        cursor.execute("""
            INSERT OR REPLACE INTO team_records
            (sport, team_name, wins, losses, win_pct,
             ats_wins, ats_losses, ats_pct,
             over_wins, under_wins, over_pct,
             under_losses, over_losses, under_pct,
             last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (sport, team['team_name'],
              team['wins'], team['losses'], team['win_pct'],
              team['ats_wins'], team['ats_losses'], team['ats_pct'],
              team['over_wins'], team['under_wins'], team['over_pct'],
              team['under_wins'], team['over_wins'], 1 - team['over_pct']))
        
        print(f"  {team['team_name']:30} ML: {team['wins']}-{team['losses']}  ATS: {team['ats_wins']}-{team['ats_losses']}  O/U: {team['over_wins']}-{team['under_wins']}")
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Updated {len(teams_data)} teams in database{Style.RESET_ALL}")


def main():
    print(f"{Fore.CYAN}{'='*60}")
    print(f"SportsBettingDime Complete Trends Scraper")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    # Scrape NBA
    nba_data = scrape_nba_trends()
    if nba_data:
        update_database('NBA', nba_data)
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Complete - Updated {len(nba_data)} teams")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
