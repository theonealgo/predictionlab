#!/usr/bin/env python3
from nba_api.stats.endpoints import scoreboardv2

scoreboard = scoreboardv2.ScoreboardV2(game_date="2024-11-04")
games_df = scoreboard.get_data_frames()[0]

print("Available columns:")
print(games_df.columns.tolist())
print(f"\nTotal games: {len(games_df)}")

if len(games_df) > 0:
    print("\nFirst game data:")
    print(games_df.iloc[0])
