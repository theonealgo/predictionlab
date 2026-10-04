#!/usr/bin/env python3
"""
Calculate Team Records from Our Own Data
=========================================
Calculates ML, ATS, and O/U records from completed games in our database.
No scraping needed - we have all the data from ESPN API.
"""

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from colorama import Fore, Style, init
import requests

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

ESPN_APIS = {
    'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
    'NHL': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard',
    'NFL': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
    'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
    'NCAAB': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard'
}


def fetch_historical_scores(sport, days_back=90):
    """Fetch historical scores from ESPN API"""
    print(f"\n{Fore.CYAN}Fetching {sport} Historical Scores (last {days_back} days){Style.RESET_ALL}")
    
    if sport not in ESPN_APIS:
        print(f"  {Fore.RED}No ESPN API for {sport}{Style.RESET_ALL}")
        return 0
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    updated = 0
    
    # Fetch scores for past days
    for days_ago in range(days_back):
        date_to_fetch = datetime.now() - timedelta(days=days_ago)
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
                
                # Check if game is final
                status = competition.get('status', {}).get('type', {}).get('state', '')
                if status != 'post':
                    continue
                
                home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                
                if not home or not away:
                    continue
                
                home_team = home.get('team', {}).get('displayName', '')
                away_team = away.get('team', {}).get('displayName', '')
                home_score = home.get('score')
                away_score = away.get('score')
                game_date_str = event.get('date', '')
                
                if not all([home_team, away_team, home_score, away_score, game_date_str]):
                    continue
                
                # Parse date
                game_date_utc = datetime.strptime(game_date_str, '%Y-%m-%dT%H:%M%SZ')
                game_date_local = game_date_utc - timedelta(hours=5)
                
                # Get betting odds
                odds_list = competition.get('odds', [])
                spread = None
                total = None
                
                if odds_list:
                    odds = odds_list[0]
                    spread = odds.get('spread')
                    total = odds.get('overUnder')
                
                # Find or create game in database
                cursor.execute("""
                    SELECT game_id FROM games
                    WHERE sport = ? AND home_team_id = ? AND away_team_id = ?
                    AND date(game_date) = ?
                """, (sport, home_team, away_team, game_date_local.strftime('%Y-%m-%d')))
                
                result = cursor.fetchone()
                
                if result:
                    game_id = result[0]
                    # Update existing game
                    cursor.execute("""
                        UPDATE games 
                        SET home_score = ?, away_score = ?, status = 'final'
                        WHERE game_id = ?
                    """, (home_score, away_score, game_id))
                else:
                    # Insert new game
                    cursor.execute("""
                        SELECT MAX(CAST(SUBSTR(game_id, LENGTH(?) + 2) AS INTEGER))
                        FROM games WHERE sport = ?
                    """, (sport, sport))
                    max_num = cursor.fetchone()[0] or 0
                    game_id = f"{sport}_{max_num + 1}"
                    
                    current_season = datetime.now().year
                    if sport in ['NBA', 'NHL', 'NCAAB'] and datetime.now().month <= 6:
                        current_season -= 1
                    
                    cursor.execute("""
                        INSERT INTO games
                        (sport, league, game_id, season, game_date, home_team_id, away_team_id, 
                         home_score, away_score, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')
                    """, (sport, sport, game_id, current_season, game_date_local.strftime('%Y-%m-%d'),
                          home_team, away_team, home_score, away_score))
                
                # Insert/update betting lines
                if spread is not None or total is not None:
                    cursor.execute("""
                        INSERT OR REPLACE INTO betting_lines
                        (sport, game_id, game_date, home_team, away_team, spread, total, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'ESPN')
                    """, (sport, game_id, game_date_local.strftime('%Y-%m-%d'), 
                          home_team, away_team, spread, total))
                
                updated += 1
            
        except Exception as e:
            pass  # Skip errors silently
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Updated {updated} historical games{Style.RESET_ALL}")
    return updated


def calculate_records_from_database(sport):
    """Calculate ML, ATS, O/U records from completed games in database"""
    print(f"\n{Fore.CYAN}Calculating {sport} Team Records{Style.RESET_ALL}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get all completed games with betting lines
    cursor.execute("""
        SELECT 
            g.home_team_id, g.away_team_id,
            g.home_score, g.away_score,
            bl.spread, bl.total
        FROM games g
        JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ? 
        AND g.status = 'final'
        AND g.home_score IS NOT NULL
        AND g.away_score IS NOT NULL
        AND bl.spread IS NOT NULL
        AND bl.total IS NOT NULL
    """, (sport,))
    
    games = cursor.fetchall()
    
    if not games:
        print(f"  {Fore.YELLOW}No completed games with betting lines found{Style.RESET_ALL}")
        conn.close()
        return
    
    print(f"  Found {len(games)} completed games with betting lines")
    
    # Calculate records for each team
    records = defaultdict(lambda: {
        'ml_w': 0, 'ml_l': 0, 
        'ats_w': 0, 'ats_l': 0, 
        'over': 0, 'under': 0
    })
    
    for home, away, h_score, a_score, spread, total in games:
        actual_margin = h_score - a_score
        actual_total = h_score + a_score
        
        # ML records
        if h_score > a_score:
            records[home]['ml_w'] += 1
            records[away]['ml_l'] += 1
        else:
            records[home]['ml_l'] += 1
            records[away]['ml_w'] += 1
        
        # ATS records (spread is for home team)
        ats_margin = actual_margin - spread
        if abs(ats_margin) < 0.5:  # Push
            pass
        elif ats_margin > 0:  # Home covered
            records[home]['ats_w'] += 1
            records[away]['ats_l'] += 1
        else:  # Away covered
            records[home]['ats_l'] += 1
            records[away]['ats_w'] += 1
        
        # O/U records
        if abs(actual_total - total) < 0.5:  # Push
            pass
        elif actual_total > total:  # Over
            records[home]['over'] += 1
            records[away]['over'] += 1
        else:  # Under
            records[home]['under'] += 1
            records[away]['under'] += 1
    
    # Update team_records table
    for team, rec in records.items():
        ml_total = rec['ml_w'] + rec['ml_l']
        ats_total = rec['ats_w'] + rec['ats_l']
        ou_total = rec['over'] + rec['under']
        
        ml_pct = rec['ml_w'] / ml_total if ml_total > 0 else 0
        ats_pct = rec['ats_w'] / ats_total if ats_total > 0 else 0
        over_pct = rec['over'] / ou_total if ou_total > 0 else 0
        under_pct = rec['under'] / ou_total if ou_total > 0 else 0
        
        cursor.execute("""
            INSERT OR REPLACE INTO team_records
            (sport, team_name, wins, losses, win_pct,
             ats_wins, ats_losses, ats_pct,
             over_wins, over_losses, over_pct,
             under_wins, under_losses, under_pct,
             last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (sport, team,
              rec['ml_w'], rec['ml_l'], ml_pct,
              rec['ats_w'], rec['ats_l'], ats_pct,
              rec['over'], rec['under'], over_pct,
              rec['under'], rec['over'], under_pct))
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Updated records for {len(records)} teams{Style.RESET_ALL}")


def main():
    """Main function - fetch historical data and calculate records"""
    print(f"{Fore.CYAN}{'='*60}")
    print(f"Internal Team Records Calculator - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Using ESPN data from our database - No scraping needed!")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    for sport in ['NBA', 'NHL', 'NFL', 'NCAAF', 'NCAAB']:
        # First fetch historical scores (full season)
        # NBA/NHL: ~180 days, NFL: ~120 days, NCAAF/NCAAB: ~120 days
        days = 180 if sport in ['NBA', 'NHL'] else 120
        fetch_historical_scores(sport, days_back=days)
        
        # Then calculate records from database
        calculate_records_from_database(sport)
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ All team records calculated and updated")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
