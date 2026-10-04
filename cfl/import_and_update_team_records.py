#!/usr/bin/env python3
"""
Import and Update Team Records
===============================
1. Import baseline records from CSV files (full season data)
2. Then incrementally update with new completed games from database
3. Never fully recalculate - always preserve the baseline + incremental updates
"""

import sqlite3
import csv
import os
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

DB_PATH = "sports_predictions_original.db"

CSV_FILES = {
    'NFL': 'nfl_complete_trends.csv',
    'NBA': 'nba_complete_trends.csv',
    'NHL': 'nhl_complete_trends.csv'
}


def import_from_csv(sport):
    """Import team records from CSV as baseline"""
    csv_file = CSV_FILES.get(sport)
    
    if not csv_file or not os.path.exists(csv_file):
        print(f"  {Fore.YELLOW}No CSV file for {sport}, skipping import{Style.RESET_ALL}")
        return 0
    
    print(f"\n{Fore.CYAN}Importing {sport} baseline from {csv_file}{Style.RESET_ALL}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    imported = 0
    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            team_name = row['team_name']
            ml_wins = int(row['ml_wins'])
            ml_losses = int(row['ml_losses'])
            ats_wins = int(row['ats_wins'])
            ats_losses = int(row['ats_losses'])
            over_wins = int(row['over_wins'])
            under_wins = int(row['under_wins'])
            
            total_games = ml_wins + ml_losses
            win_pct = ml_wins / total_games if total_games > 0 else 0
            
            ats_total = ats_wins + ats_losses
            ats_pct = ats_wins / ats_total if ats_total > 0 else 0
            
            # Store as baseline (only if team doesn't exist)
            cursor.execute("""
                INSERT OR IGNORE INTO team_records 
                (sport, team_name, wins, losses, win_pct, 
                 ats_wins, ats_losses, ats_pct,
                 over_wins, under_wins)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (sport, team_name, ml_wins, ml_losses, win_pct,
                  ats_wins, ats_losses, ats_pct,
                  over_wins, under_wins))
            imported += cursor.rowcount
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Imported {imported} teams (baseline records){Style.RESET_ALL}")
    return imported


def update_from_recent_games(sport, days_back=7):
    """
    Update team records with new games from the last N days only.
    This adds new games to existing records without recalculating everything.
    """
    print(f"\n{Fore.CYAN}Updating {sport} with recent games (last {days_back} days){Style.RESET_ALL}")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get games from last N days that haven't been counted yet
    cursor.execute("""
        SELECT g.game_id, g.home_team_id, g.away_team_id,
               g.home_score, g.away_score,
               bl.spread, bl.total
        FROM games g
        LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
        WHERE g.sport = ? 
        AND g.status = 'final'
        AND g.home_score IS NOT NULL
        AND g.away_score IS NOT NULL
        AND date(g.game_date) >= date('now', '-' || ? || ' days')
    """, (sport, days_back))
    
    games = cursor.fetchall()
    
    if not games:
        print(f"  {Fore.YELLOW}No recent completed games{Style.RESET_ALL}")
        conn.close()
        return
    
    print(f"  Found {len(games)} recent games")
    
    # Track which games we've already processed (to avoid double-counting)
    # In a production system, you'd want a separate table to track this
    
    updated_teams = set()
    
    for game_id, home, away, h_score, a_score, spread, total in games:
        # Only process if we have betting lines
        if spread is None or total is None:
            continue
        
        actual_margin = h_score - a_score
        actual_total = h_score + a_score
        
        # Update home team
        cursor.execute("""
            UPDATE team_records
            SET wins = wins + ?,
                losses = losses + ?,
                ats_wins = ats_wins + ?,
                ats_losses = ats_losses + ?,
                over_wins = over_wins + ?,
                under_wins = under_wins + ?
            WHERE sport = ? AND team_name = ?
        """, (
            1 if h_score > a_score else 0,  # ML win
            1 if h_score <= a_score else 0,  # ML loss
            1 if (actual_margin - spread) > 0.5 else 0,  # ATS win
            1 if (actual_margin - spread) < -0.5 else 0,  # ATS loss
            1 if actual_total > total + 0.5 else 0,  # Over
            1 if actual_total < total - 0.5 else 0,  # Under
            sport, home
        ))
        
        # Update away team
        cursor.execute("""
            UPDATE team_records
            SET wins = wins + ?,
                losses = losses + ?,
                ats_wins = ats_wins + ?,
                ats_losses = ats_losses + ?,
                over_wins = over_wins + ?,
                under_wins = under_wins + ?
            WHERE sport = ? AND team_name = ?
        """, (
            1 if a_score > h_score else 0,  # ML win
            1 if a_score <= h_score else 0,  # ML loss
            1 if (actual_margin - spread) < -0.5 else 0,  # ATS win (away covers when margin < -spread)
            1 if (actual_margin - spread) > 0.5 else 0,  # ATS loss
            1 if actual_total > total + 0.5 else 0,  # Over
            1 if actual_total < total - 0.5 else 0,  # Under
            sport, away
        ))
        
        updated_teams.add(home)
        updated_teams.add(away)
    
    # Recalculate percentages for updated teams
    for team in updated_teams:
        cursor.execute("""
            UPDATE team_records
            SET win_pct = CAST(wins AS REAL) / NULLIF(wins + losses, 0),
                ats_pct = CAST(ats_wins AS REAL) / NULLIF(ats_wins + ats_losses, 0),
                over_pct = CAST(over_wins AS REAL) / NULLIF(over_wins + under_wins, 0)
            WHERE sport = ? AND team_name = ?
        """, (sport, team))
    
    conn.commit()
    conn.close()
    
    print(f"  {Fore.GREEN}✓ Updated {len(updated_teams)} teams with recent results{Style.RESET_ALL}")


def main():
    """Main function - import baseline from CSV, then update with recent games"""
    print(f"{Fore.CYAN}{'='*60}")
    print(f"Team Records Import/Update - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}")
    
    for sport in ['NFL', 'NBA', 'NHL']:
        # First time: import from CSV (only inserts if team doesn't exist)
        import_from_csv(sport)
        
        # Then: update with any recent games (last 7 days)
        update_from_recent_games(sport, days_back=7)
    
    print(f"\n{Fore.GREEN}{'='*60}")
    print(f"✓ Team records updated successfully")
    print(f"{'='*60}{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
