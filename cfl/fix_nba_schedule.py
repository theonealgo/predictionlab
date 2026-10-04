#!/usr/bin/env python3
"""
Fix NBA Schedule in Database
Replaces the incorrect pre-populated NBA schedule with the official schedule.
"""

import sqlite3
from datetime import datetime
from nbaschedules import get_nba_schedule

DATABASE = 'sports_predictions_original.db'

def parse_schedule_date(date_str):
    """Parse schedule date format: 'Tue, Oct 21, 2025 11:30p'"""
    try:
        date_parts = date_str.split(', ')
        if len(date_parts) >= 2:
            month_day_year = date_parts[1] + ' ' + date_parts[2].split(' ')[0]
            # Parse "Oct 21 2025" to datetime
            parsed_date = datetime.strptime(month_day_year, '%b %d %Y')
            # Database stores all as 2025-XX-XX
            db_date = parsed_date.strftime('2025-%m-%d')
            
            # Extract time (e.g., "11:30p")
            time_part = date_parts[2].split(' ')[1] if len(date_parts[2].split(' ')) > 1 else '12:00a'
            db_datetime = f"{db_date} {time_part}"
            
            return db_datetime
    except Exception as e:
        print(f"Error parsing date '{date_str}': {e}")
        return None

def fix_nba_schedule():
    """Delete old NBA games and insert official schedule"""
    
    print("Loading official NBA schedule...")
    official_schedule = get_nba_schedule()
    print(f"Found {len(official_schedule)} games in official schedule")
    
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # 1. Delete all existing NBA games
    print("\nDeleting old NBA games from database...")
    cursor.execute("DELETE FROM games WHERE sport='NBA'")
    deleted_count = cursor.rowcount
    print(f"Deleted {deleted_count} old NBA games")
    
    # 2. Delete NBA predictions
    print("Deleting old NBA predictions...")
    cursor.execute("DELETE FROM predictions WHERE sport='NBA'")
    pred_deleted = cursor.rowcount
    print(f"Deleted {pred_deleted} old NBA predictions")
    
    # 3. Insert official schedule
    print("\nInserting official NBA schedule...")
    inserted_count = 0
    
    for match in official_schedule:
        game_date = parse_schedule_date(match['date'])
        
        if not game_date:
            print(f"Skipping match {match['match_id']} - couldn't parse date")
            continue
        
        home_team = match['home_team']
        away_team = match['away_team']
        
        # Generate game_id
        date_part = game_date.split(' ')[0].replace('-', '')
        game_id = f"NBA_{date_part}_{match['match_id']:03d}"
        
        # Parse result if available
        result = match.get('result', '')
        home_score = None
        away_score = None
        status = 'scheduled'
        
        if result and result.strip() and ' - ' in result:
            try:
                scores = result.split(' - ')
                home_score = int(scores[0].strip())
                away_score = int(scores[1].strip())
                status = 'final'
            except:
                pass
        
        # Insert game (note: league column set to 'NBA')
        cursor.execute("""
            INSERT INTO games (
                game_id, sport, league, season, game_date, 
                home_team_id, away_team_id,
                home_score, away_score, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            game_id, 'NBA', 'NBA', 2025, game_date,
            home_team, away_team,
            home_score, away_score, status
        ))
        
        inserted_count += 1
        
        if inserted_count % 50 == 0:
            print(f"Inserted {inserted_count} games...")
    
    conn.commit()
    print(f"\n✓ Successfully inserted {inserted_count} games from official schedule")
    
    # Verify
    cursor.execute("SELECT COUNT(*) FROM games WHERE sport='NBA'")
    final_count = cursor.fetchone()[0]
    print(f"✓ Database now contains {final_count} NBA games")
    
    # Show sample of dates
    cursor.execute("""
        SELECT game_date, COUNT(*) as game_count 
        FROM games 
        WHERE sport='NBA' 
        GROUP BY game_date 
        ORDER BY game_date 
        LIMIT 10
    """)
    
    print("\nSample of game dates in database:")
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} games")
    
    conn.close()
    print("\n✓ NBA schedule fix complete!")

if __name__ == '__main__':
    print("=" * 60)
    print("NBA Schedule Fix Utility")
    print("=" * 60)
    
    response = input("\nThis will DELETE all NBA games and replace with official schedule.\nContinue? (yes/no): ")
    
    if response.lower() == 'yes':
        fix_nba_schedule()
    else:
        print("Cancelled.")
