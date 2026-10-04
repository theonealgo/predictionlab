#!/usr/bin/env python3
"""
Populate NBA Schedule from API
Gets the REAL NBA schedule directly from the NBA API and populates the database.
"""

import sqlite3
from datetime import datetime
from nba_api.stats.endpoints import leaguegamefinder

DATABASE = 'sports_predictions_original.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# NBA team abbreviation to full name mapping
nba_team_map = {
    'ATL': 'Atlanta Hawks', 'BOS': 'Boston Celtics', 'BKN': 'Brooklyn Nets',
    'CHA': 'Charlotte Hornets', 'CHI': 'Chicago Bulls', 'CLE': 'Cleveland Cavaliers',
    'DAL': 'Dallas Mavericks', 'DEN': 'Denver Nuggets', 'DET': 'Detroit Pistons',
    'GSW': 'Golden State Warriors', 'HOU': 'Houston Rockets', 'IND': 'Indiana Pacers',
    'LAC': 'LA Clippers', 'LAL': 'Los Angeles Lakers', 'MEM': 'Memphis Grizzlies',
    'MIA': 'Miami Heat', 'MIL': 'Milwaukee Bucks', 'MIN': 'Minnesota Timberwolves',
    'NOP': 'New Orleans Pelicans', 'NYK': 'New York Knicks', 'OKC': 'Oklahoma City Thunder',
    'ORL': 'Orlando Magic', 'PHI': 'Philadelphia 76ers', 'PHX': 'Phoenix Suns',
    'POR': 'Portland Trail Blazers', 'SAC': 'Sacramento Kings', 'SAS': 'San Antonio Spurs',
    'TOR': 'Toronto Raptors', 'UTA': 'Utah Jazz', 'WAS': 'Washington Wizards'
}
nba_team_map['PHO'] = 'Phoenix Suns'
nba_team_map['BRK'] = 'Brooklyn Nets'

def populate_nba_schedule():
    """Get NBA schedule from API and populate database"""
    
    print("Fetching NBA schedule from API...")
    season = "2024-25"
    
    gamefinder = leaguegamefinder.LeagueGameFinder(
        season_nullable=season,
        league_id_nullable='00',
        timeout=15
    )
    games_df = gamefinder.get_data_frames()[0]
    
    print(f"Found {len(games_df)} game records from API")
    
    # Delete old NBA games
    conn = get_db_connection()
    cursor = conn.cursor()
    
    print("Deleting old NBA games...")
    cursor.execute("DELETE FROM games WHERE sport='NBA'")
    print(f"Deleted {cursor.rowcount} old games")
    
    cursor.execute("DELETE FROM predictions WHERE sport='NBA'")
    print(f"Deleted {cursor.rowcount} old predictions")
    
    # Process unique games
    processed_games = set()
    inserted_count = 0
    
    print("\nInserting games from API...")
    
    for _, row in games_df.iterrows():
        game_id = row['GAME_ID']
        if game_id in processed_games:
            continue
        processed_games.add(game_id)
        
        team_abbr = row['TEAM_ABBREVIATION']
        matchup = row['MATCHUP']
        game_date_api = row['GAME_DATE']  # Format: 2024-10-21
        
        # Parse matchup
        if ' @ ' in matchup:
            away_abbr, home_abbr = matchup.split(' @ ')
        elif ' vs. ' in matchup:
            home_abbr, away_abbr = matchup.split(' vs. ')
        else:
            continue
        
        # Get both team scores
        opponent_row = games_df[
            (games_df['GAME_ID'] == game_id) & 
            (games_df['TEAM_ABBREVIATION'] != team_abbr)
        ]
        
        if opponent_row.empty:
            continue
        
        pts = row['PTS']
        opponent_pts = opponent_row.iloc[0]['PTS']
        
        # Determine home/away scores
        if ' @ ' in matchup:
            away_score = pts
            home_score = opponent_pts
        else:
            home_score = pts
            away_score = opponent_pts
        
        # Convert to full team names
        home_team = nba_team_map.get(home_abbr, home_abbr)
        away_team = nba_team_map.get(away_abbr, away_abbr)
        
        # Convert date to database format (2025-XX-XX)
        date_parts = game_date_api.split('-')
        year = int(date_parts[0])
        month = int(date_parts[1])
        day = date_parts[2]
        
        # All games stored as 2025 in database
        db_date = f"2025-{date_parts[1]}-{day}"
        
        # Generate game_id
        nba_game_id = f"NBA_{game_date_api.replace('-', '')}_{game_id}"
        
        # Insert game
        try:
            cursor.execute("""
                INSERT INTO games (
                    game_id, sport, league, season, game_date,
                    home_team_id, away_team_id,
                    home_score, away_score, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                nba_game_id, 'NBA', 'NBA', 2025, db_date,
                home_team, away_team,
                int(home_score), int(away_score), 'final'
            ))
            inserted_count += 1
            
            if inserted_count % 100 == 0:
                print(f"Inserted {inserted_count} games...")
                
        except sqlite3.IntegrityError:
            # Duplicate game, skip
            continue
    
    conn.commit()
    print(f"\n✓ Successfully inserted {inserted_count} NBA games from API")
    
    # Verify
    cursor.execute("SELECT COUNT(*) FROM games WHERE sport='NBA'")
    final_count = cursor.fetchone()[0]
    print(f"✓ Database now contains {final_count} NBA games")
    
    # Show sample
    cursor.execute("""
        SELECT game_date, COUNT(*) as count
        FROM games
        WHERE sport='NBA'
        GROUP BY game_date
        ORDER BY game_date
        LIMIT 10
    """)
    
    print("\nSample of game dates:")
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} games")
    
    conn.close()
    print("\n✓ Complete!")

if __name__ == '__main__':
    print("=" * 60)
    print("NBA Schedule Population from API")
    print("=" * 60)
    
    response = input("\nThis will DELETE all NBA games and repopulate from API.\nContinue? (yes/no): ")
    
    if response.lower() == 'yes':
        populate_nba_schedule()
    else:
        print("Cancelled.")
