#!/usr/bin/env python3
"""Insert missing NBA games from API into database"""

import sqlite3
from datetime import datetime, timedelta
from nba_api.stats.endpoints import scoreboardv2

conn = sqlite3.connect('sports_predictions_original.db')
cursor = conn.cursor()

# Check Oct 22, 2024 through today for NBA 2024-25 season
# NBA season started Oct 22, 2024
start_date = datetime(2024, 10, 22)
today = datetime.now()
# But check 2024 dates since season started in 2024
if today.year == 2025 and today.month <= 6:
    # We're in 2025 but checking 2024 games
    check_end = datetime(2024, 12, 31)  # Check through end of 2024
else:
    check_end = today
current_date = start_date

inserted = 0
updated = 0

print("Checking for missing NBA games from API...")

while current_date <= check_end:
    check_date = current_date.strftime('%Y-%m-%d')
    
    try:
        scoreboard = scoreboardv2.ScoreboardV2(game_date=check_date)
        dfs = scoreboard.get_data_frames()
        
        if len(dfs) < 2:
            current_date += timedelta(days=1)
            continue
            
        game_header_df = dfs[0]
        linescore_df = dfs[1]
        
        if len(game_header_df) == 0:
            current_date += timedelta(days=1)
            continue
        
        for _, header_row in game_header_df.iterrows():
            game_id = header_row['GAME_ID']
            game_status = header_row['GAME_STATUS_TEXT']
            
            # Only process finished games
            if 'Final' not in game_status:
                continue
            
            game_teams = linescore_df[linescore_df['GAME_ID'] == game_id]
            if len(game_teams) != 2:
                continue
            
            away_row = game_teams.iloc[0]
            home_row = game_teams.iloc[1]
            
            away_score = int(away_row['PTS'])
            home_score = int(home_row['PTS'])
            
            away_city = away_row['TEAM_CITY_NAME']
            away_name = away_row['TEAM_NAME']
            home_city = home_row['TEAM_CITY_NAME']
            home_name = home_row['TEAM_NAME']
            
            away_team = f"{away_city} {away_name}"
            home_team = f"{home_city} {home_name}"
            
            # Handle LA Clippers
            if away_team == "LA Clippers":
                away_team = "Los Angeles Clippers"
            if home_team == "LA Clippers":
                home_team = "Los Angeles Clippers"
            
            # Database uses 2025 for all dates
            if check_date.startswith('2024'):
                db_date = check_date.replace('2024', '2025')
            else:
                db_date = check_date
            
            db_datetime = f"{db_date} 00:00:00"
            
            # Check if this exact game already exists with scores
            cursor.execute("""
                SELECT id
                FROM games
                WHERE sport = 'NBA'
                  AND home_team_id = ?
                  AND away_team_id = ?
                  AND game_date LIKE ?
                  AND home_score = ?
                  AND away_score = ?
            """, (home_team, away_team, f"{db_date}%", home_score, away_score))
            
            if cursor.fetchone():
                # Already have this exact result, skip
                continue
            
            # Check if game exists but needs update
            cursor.execute("""
                SELECT id, home_score, away_score
                FROM games
                WHERE sport = 'NBA'
                  AND home_team_id = ?
                  AND away_team_id = ?
                  AND game_date LIKE ?
            """, (home_team, away_team, f"{db_date}%"))
            
            existing = cursor.fetchone()
            
            if existing:
                # Update existing game
                cursor.execute("""
                    UPDATE games
                    SET home_score = ?, away_score = ?, status = 'final'
                    WHERE id = ?
                """, (home_score, away_score, existing[0]))
                updated += 1
                print(f"✓ Updated: {away_team} @ {home_team}: {away_score}-{home_score} on {db_date}")
            else:
                # Insert new game
                cursor.execute("""
                    INSERT INTO games (
                        sport, home_team_id, away_team_id, 
                        home_score, away_score, status, game_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, ('NBA', home_team, away_team, home_score, away_score, 'final', db_datetime))
                inserted += 1
                print(f"✓ Inserted: {away_team} @ {home_team}: {away_score}-{home_score} on {db_date}")
    
    except Exception as e:
        print(f"Error on {check_date}: {e}")
    
    current_date += timedelta(days=1)

conn.commit()
conn.close()

print("\n" + "="*60)
print(f"✓ Inserted {inserted} new games")
print(f"✓ Updated {updated} existing games")
print(f"✓ Total: {inserted + updated} games processed")
print("="*60)
