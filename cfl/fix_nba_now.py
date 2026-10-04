#!/usr/bin/env python3
"""Direct fix for NBA scores through Nov 4"""

import sqlite3
from nba_api.stats.endpoints import scoreboardv2

conn = sqlite3.connect('sports_predictions_original.db')
cursor = conn.cursor()

# Check what we have for Nov 3 and Nov 4
print("Current database status:")
for date in ['2025-11-03', '2025-11-04']:
    cursor.execute("""
        SELECT game_date, away_team_id, home_team_id, away_score, home_score, status
        FROM games
        WHERE sport = 'NBA' AND game_date LIKE ?
        ORDER BY game_date
    """, (f"{date}%",))
    
    rows = cursor.fetchall()
    print(f"\n{date}: {len(rows)} games")
    for row in rows:
        print(f"  {row[1]} @ {row[2]}: {row[3]}-{row[4]} ({row[5]})")

print("\n" + "="*60)
print("Fetching from NBA API:")
print("="*60)

updates = 0

for api_date, db_date in [('2024-11-03', '2025-11-03'), ('2024-11-04', '2025-11-04')]:
    print(f"\nChecking {api_date}...")
    
    try:
        scoreboard = scoreboardv2.ScoreboardV2(game_date=api_date)
        dfs = scoreboard.get_data_frames()
        
        if len(dfs) < 2:
            print(f"  No data")
            continue
            
        game_header_df = dfs[0]
        linescore_df = dfs[1]
        
        print(f"  Found {len(game_header_df)} games")
        
        for _, header_row in game_header_df.iterrows():
            game_id = header_row['GAME_ID']
            game_status = header_row['GAME_STATUS_TEXT']
            
            if 'Final' not in game_status:
                print(f"    Game {game_id}: Not final ({game_status})")
                continue
            
            game_teams = linescore_df[linescore_df['GAME_ID'] == game_id]
            if len(game_teams) != 2:
                continue
            
            away_row = game_teams.iloc[0]
            home_row = game_teams.iloc[1]
            
            away_team = f"{away_row['TEAM_CITY_NAME']} {away_row['TEAM_NAME']}"
            home_team = f"{home_row['TEAM_CITY_NAME']} {home_row['TEAM_NAME']}"
            away_score = int(away_row['PTS'])
            home_score = int(home_row['PTS'])
            
            # Handle LA Clippers special case
            if away_team == "LA Clippers":
                away_team = "Los Angeles Clippers"
            if home_team == "LA Clippers":
                home_team = "Los Angeles Clippers"
            
            print(f"    {away_team} @ {home_team}: {away_score}-{home_score}")
            
            # Update database
            cursor.execute("""
                UPDATE games
                SET home_score = ?, away_score = ?, status = 'final'
                WHERE sport = 'NBA'
                  AND home_team_id = ?
                  AND away_team_id = ?
                  AND game_date LIKE ?
            """, (home_score, away_score, home_team, away_team, f"{db_date}%"))
            
            if cursor.rowcount > 0:
                updates += 1
                print(f"      ✓ UPDATED in database")
            else:
                print(f"      ⚠️  No matching game in database")
                # Show what we're looking for
                cursor.execute("""
                    SELECT game_date, home_team_id, away_team_id
                    FROM games
                    WHERE sport = 'NBA' AND game_date LIKE ?
                """, (f"{db_date}%",))
                db_games = cursor.fetchall()
                print(f"      Database games on {db_date}:")
                for g in db_games:
                    print(f"        {g[2]} @ {g[1]}")
    
    except Exception as e:
        print(f"  Error: {e}")

conn.commit()
conn.close()

print("\n" + "="*60)
print(f"✓ Updated {updates} games total")
print("="*60)
