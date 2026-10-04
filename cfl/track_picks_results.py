#!/usr/bin/env python3
"""
Track ATS System Picks and Results
===================================
1. Save daily picks to system_picks table
2. Fetch game results from ESPN API
3. Compare picks vs results to calculate W-L record
"""

import sqlite3
from datetime import datetime, timedelta
import requests
from colorama import Fore, Style, init
from ats_system import ATSSystem

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

ESPN_APIS = {
    'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
    'NHL': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard',
    'NFL': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
    'NCAAF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard'
}


def save_daily_picks(sport):
    """Save today's picks to system_picks table"""
    print(f"\n{Fore.CYAN}Saving {sport} picks...{Style.RESET_ALL}")
    
    system = ATSSystem()
    picks = system.get_all_picks(sport, days_ahead=1)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    saved = 0
    
    # Save moneyline picks
    for pick in picks['moneyline']:
        cursor.execute("""
            INSERT OR IGNORE INTO system_picks
            (sport, game_id, game_date, pick_type, pick_team, pick_value)
            VALUES (?, ?, ?, 'MONEYLINE', ?, NULL)
        """, (sport, pick['game_id'], pick['game_date'], pick['pick_team']))
        saved += cursor.rowcount
    
    # Save spread picks
    for pick in picks['spread']:
        cursor.execute("""
            INSERT OR IGNORE INTO system_picks
            (sport, game_id, game_date, pick_type, pick_team, pick_value)
            VALUES (?, ?, ?, 'SPREAD', ?, ?)
        """, (sport, pick['game_id'], pick['game_date'], pick['pick_team'], pick['model_spread']))
        saved += cursor.rowcount
    
    # Save total picks
    for pick in picks['totals']:
        cursor.execute("""
            INSERT OR IGNORE INTO system_picks
            (sport, game_id, game_date, pick_type, pick_team, pick_value)
            VALUES (?, ?, ?, ?, NULL, ?)
        """, (sport, pick['game_id'], pick['game_date'], pick['pick_type'], pick['model_total']))
        saved += cursor.rowcount
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Saved {saved} picks{Style.RESET_ALL}")
    return saved


def fetch_results(sport, days_back=1):
    """Fetch final scores from ESPN API and update games table"""
    print(f"\n{Fore.CYAN}Fetching {sport} results...{Style.RESET_ALL}")
    
    if sport not in ESPN_APIS:
        return 0
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    updated = 0
    
    for days in range(days_back):
        date_to_fetch = datetime.now() - timedelta(days=days)
        date_str = date_to_fetch.strftime('%Y%m%d')
        
        url = f"{ESPN_APIS[sport]}?dates={date_str}"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            events = data.get('events', [])
            
            for event in events:
                status = event.get('status', {}).get('type', {}).get('completed', False)
                
                if not status:
                    continue
                
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
                home_score = int(home.get('score', 0))
                away_score = int(away.get('score', 0))
                game_date_str = event.get('date', '')
                
                if not game_date_str:
                    continue
                
                # Convert UTC to local time
                game_date_utc = datetime.strptime(game_date_str, '%Y-%m-%dT%H:%M%SZ')
                game_date_local = game_date_utc - timedelta(hours=5)
                
                # Update game with final score
                cursor.execute("""
                    UPDATE games
                    SET status = 'final', home_score = ?, away_score = ?
                    WHERE sport = ? AND home_team_id = ? AND away_team_id = ?
                    AND date(game_date) = ?
                """, (home_score, away_score, sport, home_team, away_team, game_date_local.strftime('%Y-%m-%d')))
                
                updated += cursor.rowcount
            
        except Exception as e:
            print(f"  {Fore.RED}Error: {e}{Style.RESET_ALL}")
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Updated {updated} game results{Style.RESET_ALL}")
    return updated


def calculate_pick_results(sport):
    """Calculate win/loss for picks based on game results"""
    print(f"\n{Fore.CYAN}Calculating {sport} pick results...{Style.RESET_ALL}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get picks for completed games
    cursor.execute("""
        SELECT sp.id, sp.game_id, sp.pick_type, sp.pick_team, sp.pick_value,
               g.home_team_id, g.away_team_id, g.home_score, g.away_score,
               bl.spread, bl.total
        FROM system_picks sp
        JOIN games g ON sp.game_id = g.game_id
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE sp.sport = ? AND g.status = 'final' AND sp.result IS NULL
    """, (sport,))
    
    picks = cursor.fetchall()
    updated = 0
    
    for pick in picks:
        pick_id, game_id, pick_type, pick_team, pick_value, home_team, away_team, home_score, away_score, spread, total = pick
        
        result = None
        
        if pick_type == 'MONEYLINE':
            # Check if pick_team won
            if pick_team == home_team:
                result = 'WIN' if home_score > away_score else 'LOSS'
            else:
                result = 'WIN' if away_score > home_score else 'LOSS'
        
        elif pick_type == 'SPREAD':
            if spread is None:
                continue
            
            # Calculate actual margin vs spread
            if pick_team == home_team:
                margin = home_score - away_score
                covered = margin > spread
            else:
                margin = away_score - home_score
                covered = margin > -spread
            
            if abs(margin - spread) < 0.5:
                result = 'PUSH'
            else:
                result = 'WIN' if covered else 'LOSS'
        
        elif pick_type in ['OVER', 'UNDER']:
            if total is None:
                continue
            
            actual_total = home_score + away_score
            
            if abs(actual_total - total) < 0.5:
                result = 'PUSH'
            elif pick_type == 'OVER':
                result = 'WIN' if actual_total > total else 'LOSS'
            else:
                result = 'WIN' if actual_total < total else 'LOSS'
        
        if result:
            cursor.execute("""
                UPDATE system_picks SET result = ? WHERE id = ?
            """, (result, pick_id))
            updated += 1
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Updated {updated} pick results{Style.RESET_ALL}")
    return updated


def get_daily_records(sport, days_back=7):
    """Get W-L records by day"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT game_date,
               SUM(CASE WHEN pick_type = 'MONEYLINE' AND result = 'WIN' THEN 1 ELSE 0 END) as ml_wins,
               SUM(CASE WHEN pick_type = 'MONEYLINE' AND result = 'LOSS' THEN 1 ELSE 0 END) as ml_losses,
               SUM(CASE WHEN pick_type = 'SPREAD' AND result = 'WIN' THEN 1 ELSE 0 END) as spread_wins,
               SUM(CASE WHEN pick_type = 'SPREAD' AND result = 'LOSS' THEN 1 ELSE 0 END) as spread_losses,
               SUM(CASE WHEN pick_type IN ('OVER', 'UNDER') AND result = 'WIN' THEN 1 ELSE 0 END) as total_wins,
               SUM(CASE WHEN pick_type IN ('OVER', 'UNDER') AND result = 'LOSS' THEN 1 ELSE 0 END) as total_losses
        FROM system_picks
        WHERE sport = ? AND result IS NOT NULL
        AND date(game_date) >= date('now', '-{} days')
        GROUP BY game_date
        ORDER BY game_date DESC
    """.format(days_back), (sport,))
    
    records = cursor.fetchall()
    conn.close()
    
    return records


def main():
    """Run daily picks tracking workflow"""
    print(f"{Fore.CYAN}{'='*60}")
    print(f"ATS System - Picks Tracking - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    sports = ['NBA', 'NHL', 'NFL', 'NCAAF']
    
    # Save today's picks
    for sport in sports:
        save_daily_picks(sport)
    
    # Fetch yesterday's results
    for sport in sports:
        fetch_results(sport, days_back=2)
    
    # Calculate pick results
    for sport in sports:
        calculate_pick_results(sport)
    
    # Show recent records
    print(f"\n{Fore.GREEN}{'='*60}")
    print("Recent Records (Last 7 Days)")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    for sport in sports:
        records = get_daily_records(sport, days_back=7)
        if records:
            print(f"\n{Fore.CYAN}{sport}:{Style.RESET_ALL}")
            for record in records:
                date, ml_w, ml_l, sp_w, sp_l, tot_w, tot_l = record
                print(f"  {date}: ML {ml_w}-{ml_l} | Spread {sp_w}-{sp_l} | Total {tot_w}-{tot_l}")


if __name__ == "__main__":
    main()
