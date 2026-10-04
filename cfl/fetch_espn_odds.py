#!/usr/bin/env python3
"""
Fetch REAL betting lines from ESPN API
Updates betting_lines table with actual Vegas odds
"""

import sqlite3
from datetime import datetime, timedelta
import requests
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

ESPN_APIS = {
    'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
    'NHL': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard',
    'NFL': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
    'MLB': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
    'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
    'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
    'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard'
}


def get_db_connection():
    return sqlite3.connect(DB_PATH)


def fetch_espn_odds(sport, days_ahead=7):
    """Fetch betting lines from ESPN API"""
    print(f"\n{Fore.CYAN}Fetching {sport} Betting Lines (ESPN){Style.RESET_ALL}")
    
    if sport not in ESPN_APIS:
        print(f"  {Fore.RED}No ESPN API for {sport}{Style.RESET_ALL}")
        return 0
    
    conn = get_db_connection()
    cursor = conn.cursor()
    added = 0
    
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
                
                # Get betting odds
                odds_list = competition.get('odds', [])
                if not odds_list:
                    continue
                
                # Use first odds provider (usually ESPN BET)
                odds = odds_list[0]
                spread = odds.get('spread')
                over_under = odds.get('overUnder')
                
                # Get moneylines
                home_ml = home.get('team', {}).get('odds', {}).get('moneyLine')
                away_ml = away.get('team', {}).get('odds', {}).get('moneyLine')
                
                # Try to get from odds structure
                if not home_ml:
                    home_odds = odds.get('homeTeamOdds', {})
                    away_odds = odds.get('awayTeamOdds', {})
                    home_ml = home_odds.get('moneyLine')
                    away_ml = away_odds.get('moneyLine')
                
                if not game_date_str:
                    continue
                
                # Parse UTC date and convert to ET (UTC-5)
                game_date_utc = datetime.strptime(game_date_str, '%Y-%m-%dT%H:%M%SZ')
                game_date_local = game_date_utc - timedelta(hours=5)
                
                # Find matching game_id in database
                cursor.execute("""
                    SELECT game_id FROM games
                    WHERE sport = ? AND home_team_id = ? AND away_team_id = ?
                    AND date(game_date) = ?
                """, (sport, home_team, away_team, game_date_local.strftime('%Y-%m-%d')))
                
                result = cursor.fetchone()
                if not result:
                    print(f"  ⚠ No match: {away_team} @ {home_team}")
                    continue
                
                game_id = result[0]
                
                # Insert/update betting lines
                if spread is not None or over_under is not None:
                    cursor.execute("""
                        INSERT OR REPLACE INTO betting_lines
                        (sport, game_id, game_date, home_team, away_team, spread, total, home_moneyline, away_moneyline, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ESPN')
                    """, (sport, game_id, game_date_local.strftime('%Y-%m-%d'), home_team, away_team,
                          spread, over_under, home_ml, away_ml))
                    added += 1
            
            if events:
                print(f"  {date_to_fetch.strftime('%Y-%m-%d')}: {added} lines")
            
        except Exception as e:
            print(f"  {Fore.RED}Error fetching {date_str}: {e}{Style.RESET_ALL}")
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Added {added} betting lines{Style.RESET_ALL}")
    return added


def main():
    """Fetch odds for all sports"""
    print(f"{Fore.CYAN}{'='*60}")
    print(f"ESPN Betting Lines Fetcher - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    total = 0
    
    for sport in ['NBA', 'NHL', 'NFL', 'NCAAF', 'NCAAB']:
        total += fetch_espn_odds(sport, days_ahead=14)
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Complete - {total} betting lines fetched")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
