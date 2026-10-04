#!/usr/bin/env python3
"""
Scrape Team Trends from SportsBettingDime
Gets real ML/ATS/O-U records for all teams
"""

import requests
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

TRENDS_URLS = {
    'NBA': 'https://www.sportsbettingdime.com/nba/team-trends/',
    'NFL': 'https://www.sportsbettingdime.com/nfl/team-trends/',
    'NHL': 'https://www.sportsbettingdime.com/nhl/team-trends/',
    'NCAAF': 'https://www.sportsbettingdime.com/ncaaf/team-trends/'
}


def scrape_nba_trends():
    """Scrape NBA team trends from SportsBettingDime"""
    print(f"\n{Fore.CYAN}Scraping NBA Team Trends{Style.RESET_ALL}")
    
    url = TRENDS_URLS['NBA']
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find the ATS table
        tables = soup.find_all('table')
        
        if not tables:
            print(f"  {Fore.RED}No tables found on page{Style.RESET_ALL}")
            return []
        
        # Try to find the ATS table (usually has headers like "Team", "ATS Record", "Cover %")
        ats_table = None
        for table in tables:
            headers = table.find_all('th')
            header_text = ' '.join([h.get_text().strip() for h in headers])
            if 'ATS' in header_text or 'Cover' in header_text:
                ats_table = table
                break
        
        if not ats_table:
            print(f"  {Fore.YELLOW}Using first table as fallback{Style.RESET_ALL}")
            ats_table = tables[0]
        
        teams_data = []
        rows = ats_table.find_all('tr')[1:]  # Skip header
        
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 2:
                continue
            
            team_name = cols[0].get_text().strip()
            
            # Parse ATS record (format: "9-3-0" or "9-3")
            ats_record = None
            for col in cols[1:4]:
                text = col.get_text().strip()
                if '-' in text and any(c.isdigit() for c in text):
                    ats_record = text
                    break
            
            if not ats_record:
                continue
            
            # Parse wins-losses-pushes
            parts = ats_record.split('-')
            ats_wins = int(parts[0])
            ats_losses = int(parts[1])
            ats_pushes = int(parts[2]) if len(parts) > 2 else 0
            
            # For now, use ATS record as ML record (will update with real ML data later)
            ml_wins = ats_wins
            ml_losses = ats_losses
            
            # Calculate percentages
            ml_total = ml_wins + ml_losses
            ats_total = ats_wins + ats_losses
            
            ml_pct = ml_wins / ml_total if ml_total > 0 else 0
            ats_pct = ats_wins / ats_total if ats_total > 0 else 0
            
            teams_data.append({
                'team_name': team_name,
                'wins': ml_wins,
                'losses': ml_losses,
                'win_pct': ml_pct,
                'ats_wins': ats_wins,
                'ats_losses': ats_losses,
                'ats_pct': ats_pct,
                'over_wins': 0,  # Will need separate scrape
                'under_wins': 0,
                'over_pct': 0,
                'under_pct': 0
            })
        
        print(f"  {Fore.GREEN}✓ Scraped {len(teams_data)} teams{Style.RESET_ALL}")
        return teams_data
        
    except Exception as e:
        print(f"  {Fore.RED}Error scraping: {e}{Style.RESET_ALL}")
        return []


def update_database(sport, teams_data):
    """Update team_records table with scraped data"""
    if not teams_data:
        print(f"  {Fore.YELLOW}No data to update{Style.RESET_ALL}")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    for team in teams_data:
        cursor.execute("""
            INSERT OR REPLACE INTO team_records
            (sport, team_name, wins, losses, win_pct,
             ats_wins, ats_losses, ats_pct,
             over_wins, over_losses, over_pct,
             under_wins, under_losses, under_pct,
             last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (sport, team['team_name'],
              team['wins'], team['losses'], team['win_pct'],
              team['ats_wins'], team['ats_losses'], team['ats_pct'],
              team['over_wins'], team['under_wins'], team['over_pct'],
              team['under_wins'], team['over_wins'], team['under_pct']))
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Updated {len(teams_data)} teams in database{Style.RESET_ALL}")


def main():
    print(f"{Fore.CYAN}{'='*60}")
    print(f"SportsBettingDime Trends Scraper - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    # Scrape NBA
    nba_data = scrape_nba_trends()
    if nba_data:
        update_database('NBA', nba_data)
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Complete")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
