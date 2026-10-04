#!/usr/bin/env python3
import sqlite3
from nba_api.stats.endpoints import scoreboardv2

conn = sqlite3.connect('sports_predictions_original.db')
cursor = conn.cursor()

inserted = 0
updated = 0

for api_date, db_date in [('2024-11-03', '2025-11-03'), ('2024-11-04', '2025-11-04')]:
    print(f"\nProcessing {api_date}...")
    
    scoreboard = scoreboardv2.ScoreboardV2(game_date=api_date)
    dfs = scoreboard.get_data_frames()
    
    game_header_df = dfs[0]
    linescore_df = dfs[1]
    
    for _, header_row in game_header_df.iterrows():
        game_id = header_row['GAME_ID']
        game_status = header_row['GAME_STATUS_TEXT']
        
        if 'Final' not in game_status:
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
        
        if away_team == "LA Clippers":
            away_team = "Los Angeles Clippers"
        if home_team == "LA Clippers":
            home_team = "Los Angeles Clippers"
        
        # Check if exists
        cursor.execute("""
            SELECT id FROM games
            WHERE sport = 'NBA' AND home_team_id = ? AND away_team_id = ? AND game_date LIKE ?
        """, (home_team, away_team, f"{db_date}%"))
        
        if cursor.fetchone():
            cursor.execute("""
                UPDATE games SET home_score = ?, away_score = ?, status = 'final'
                WHERE sport = 'NBA' AND home_team_id = ? AND away_team_id = ? AND game_date LIKE ?
            """, (home_score, away_score, home_team, away_team, f"{db_date}%"))
            updated += 1
            print(f"  ✓ Updated: {away_team} @ {home_team}: {away_score}-{home_score}")
        else:
            cursor.execute("""
                INSERT INTO games (sport, league, game_id, season, game_date, home_team_id, away_team_id, home_score, away_score, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ('NBA', 'NBA', game_id, 2025, f"{db_date} 00:00:00", home_team, away_team, home_score, away_score, 'final'))
            inserted += 1
            print(f"  ✓ Inserted: {away_team} @ {home_team}: {away_score}-{home_score}")

conn.commit()
conn.close()

print(f"\n✓ Inserted {inserted}, Updated {updated}")
