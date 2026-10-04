#!/usr/bin/env python3
"""Update NBA scores for games with predictions"""
import sqlite3
from datetime import datetime, timedelta
from nba_api.stats.endpoints import scoreboardv2

conn = sqlite3.connect('sports_predictions_original.db')
cursor = conn.cursor()

updated = 0

# Check Oct 22 - Nov 5 (2024 dates for API, 2025 for database)
start = datetime(2024, 10, 22)
end = datetime(2024, 11, 5)
current = start

print("Updating NBA scores from API...")

while current <= end:
    api_date = current.strftime('%Y-%m-%d')
    db_date = api_date.replace('2024', '2025')
    
    try:
        scoreboard = scoreboardv2.ScoreboardV2(game_date=api_date)
        dfs = scoreboard.get_data_frames()
        
        if len(dfs) < 2:
            current += timedelta(days=1)
            continue
            
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
            
            # Update ANY game matching these teams and date
            cursor.execute("""
                UPDATE games 
                SET home_score = ?, away_score = ?, status = 'final'
                WHERE sport = 'NBA'
                  AND home_team_id = ?
                  AND away_team_id = ?
                  AND game_date LIKE ?
                  AND (home_score IS NULL OR home_score != ?)
            """, (home_score, away_score, home_team, away_team, f"{db_date}%", home_score))
            
            if cursor.rowcount > 0:
                updated += 1
                print(f"✓ {db_date}: {away_team} @ {home_team}: {away_score}-{home_score}")
    
    except Exception as e:
        pass
    
    current += timedelta(days=1)

conn.commit()
conn.close()

print(f"\n✓ Updated {updated} games")
