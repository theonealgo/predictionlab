#!/usr/bin/env python3
"""Debug NBA score fetching"""

from datetime import datetime
from nba_api.stats.endpoints import scoreboardv2
from nbaschedules import get_nba_schedule

# Get schedule and parse first few dates
schedule = get_nba_schedule()
print(f"Schedule has {len(schedule)} games\n")

# Check first few dates
print("First 5 schedule entries:")
for i, match in enumerate(schedule[:5]):
    print(f"{i+1}. {match['date']} - {match['away_team']} @ {match['home_team']} - Result: {match['result']}")

print("\n" + "="*60)
print("Testing date parsing:")
print("="*60)

dates_to_check = set()
for match in schedule[:10]:
    date_str = match['date']
    try:
        date_parts = date_str.split(', ')
        if len(date_parts) >= 2:
            month_day_year = date_parts[1] + ' ' + date_parts[2].split(' ')[0]
            parsed_date = datetime.strptime(month_day_year, '%b %d %Y')
            
            # NBA season spans Oct-Dec 2024, then Jan-Apr 2025
            if parsed_date.month >= 10:  # Oct, Nov, Dec
                api_date = parsed_date.strftime('2024-%m-%d')
            else:  # Jan-Apr
                api_date = parsed_date.strftime('2025-%m-%d')
            
            print(f"Schedule date: {date_str} -> API date: {api_date}")
            dates_to_check.add(api_date)
    except Exception as e:
        print(f"Error parsing {date_str}: {e}")

print("\n" + "="*60)
print("Testing nba_api for a specific date (2024-11-04):")
print("="*60)

try:
    scoreboard = scoreboardv2.ScoreboardV2(game_date="2024-11-04")
    games_df = scoreboard.get_data_frames()[0]
    
    print(f"Found {len(games_df)} games on 2024-11-04:\n")
    
    if len(games_df) > 0:
        for _, game_row in games_df.iterrows():
            print(f"{game_row['VISITOR_TEAM_ABBREVIATION']} @ {game_row['HOME_TEAM_ABBREVIATION']}: "
                  f"{game_row['VISITOR_TEAM_SCORE']}-{game_row['HOME_TEAM_SCORE']} "
                  f"({game_row['GAME_STATUS_TEXT']})")
    
except Exception as e:
    print(f"Error: {e}")

print("\n" + "="*60)
print("Testing database query:")
print("="*60)

import sqlite3
conn = sqlite3.connect('sports_predictions_original.db')
cursor = conn.cursor()

cursor.execute("""
    SELECT game_date, home_team_id, away_team_id, home_score, away_score, status
    FROM games
    WHERE sport = 'NBA'
    AND game_date LIKE '2025-11-04%'
    LIMIT 5
""")

rows = cursor.fetchall()
print(f"Found {len(rows)} NBA games in DB for 2025-11-04:")
for row in rows:
    print(f"  {row[2]} @ {row[1]}: {row[4]}-{row[3]} (Status: {row[5]})")

conn.close()
