#!/usr/bin/env python3
from nba_api.stats.endpoints import scoreboardv2

scoreboard = scoreboardv2.ScoreboardV2(game_date="2024-11-04")

# Get all dataframes
dfs = scoreboard.get_data_frames()
print(f"Number of dataframes: {len(dfs)}")

# Check the LineScore dataframe (usually index 1)
if len(dfs) > 1:
    linescore_df = dfs[1]
    print(f"\nLineScore columns: {linescore_df.columns.tolist()}")
    print(f"LineScore shape: {linescore_df.shape}")
    
    if len(linescore_df) > 0:
        print("\nFirst two rows (home and away team):")
        print(linescore_df.iloc[0])
        print("\n")
        print(linescore_df.iloc[1])
