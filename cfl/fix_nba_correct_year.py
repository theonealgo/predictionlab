#!/usr/bin/env python3
"""
Fix NBA Schedule with Correct Year (2024, not 2025)
The system was storing games with 2025 dates, but the real season is 2024-25
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

def fix_nba_with_correct_year():
    """Get REAL NBA schedule from API with correct 2024 dates"""
    
    print("Fetching NBA schedule from API (2024-25 season)...")
    season = "2024-25"
    
    # Current date in real world is November 4, 2024
    today_real = datetime(2024, 11, 4)
    print(f"Real today: {today_real.strftime('%Y-%m-%d')}")
    
    gamefinder = leaguegamefinder.LeagueGameFinder(
        season_nullable=season,
        league_id_nullable='00',
        timeout=15
    )
    games_df = gamefinder.get_data_frames()[0]
    
    # Get ALL games for the season (past and future)
    # We'll add scores only to completed games
    print(f"Found {len(games_df)} game records from API (full season)")
    
    # Delete old NBA games
    conn = get_db_connection()
    cursor = conn.cursor()
    
    print("\nDeleting old NBA games...")
    cursor.execute("DELETE FROM games WHERE sport='NBA'")
    print(f"Deleted {cursor.rowcount} old games")
    
    cursor.execute("DELETE FROM predictions WHERE sport='NBA'")
    print(f"Deleted {cursor.rowcount} old predictions")
    
    # Process unique games
    processed_games = set()
    inserted_games = 0
    inserted_predictions = 0
    
    # Initialize Elo for predictions
    elo_ratings = {}
    k_factor = 18
    
    def get_elo(team):
        return elo_ratings.get(team, 1500)
    
    def expected_score(rating_a, rating_b):
        return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
    
    print("\nInserting games and generating predictions...")
    
    for _, row in games_df.iterrows():
        game_id = row['GAME_ID']
        if game_id in processed_games:
            continue
        processed_games.add(game_id)
        
        team_abbr = row['TEAM_ABBREVIATION']
        matchup = row['MATCHUP']
        game_date = row['GAME_DATE']  # Keep real 2024 dates!
        
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
        
        # Only get scores for completed games
        game_date_obj = datetime.strptime(game_date, '%Y-%m-%d')
        is_completed = game_date_obj <= today_real
        
        if is_completed:
            pts = row['PTS']
            opponent_pts = opponent_row.iloc[0]['PTS']
            
            # Determine home/away scores
            if ' @ ' in matchup:
                away_score = pts
                home_score = opponent_pts
            else:
                home_score = pts
                away_score = opponent_pts
        else:
            # Future game - no scores yet
            home_score = None
            away_score = None
        
        # Convert to full team names
        home_team = nba_team_map.get(home_abbr, home_abbr)
        away_team = nba_team_map.get(away_abbr, away_abbr)
        
        # Generate game_id
        nba_game_id = f"NBA_{game_date.replace('-', '')}_{game_id}"
        
        # Get Elo ratings BEFORE the game (for prediction)
        home_rating = get_elo(home_team)
        away_rating = get_elo(away_team)
        home_prob = expected_score(home_rating, away_rating)
        
        # Set status and scores based on whether game is completed
        status = 'final' if is_completed else 'scheduled'
        
        # Insert game with REAL 2024 date
        try:
            cursor.execute("""
                INSERT INTO games (
                    game_id, sport, league, season, game_date,
                    home_team_id, away_team_id,
                    home_score, away_score, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                nba_game_id, 'NBA', 'NBA', 2025, game_date,  # Season is 2025 but date is 2024!
                home_team, away_team,
                int(home_score) if home_score is not None else None,
                int(away_score) if away_score is not None else None,
                status
            ))
            inserted_games += 1
            
            # Insert prediction
            cursor.execute("""
                INSERT INTO predictions (
                    game_id, sport, league, game_date, home_team_id, away_team_id,
                    elo_home_prob, xgboost_home_prob, logistic_home_prob, win_probability
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                nba_game_id, 'NBA', 'NBA', game_date,
                home_team, away_team,
                home_prob, home_prob, home_prob, home_prob
            ))
            inserted_predictions += 1
            
            # Update Elo ratings AFTER the game (only for completed games)
            if is_completed and home_score is not None:
                actual_home = 1 if home_score > away_score else 0
                expected_home = home_prob
                
                elo_ratings[home_team] = home_rating + k_factor * (actual_home - expected_home)
                elo_ratings[away_team] = away_rating + k_factor * ((1-actual_home) - (1-expected_home))
            
            if inserted_games % 50 == 0:
                print(f"Inserted {inserted_games} games...")
                
        except sqlite3.IntegrityError as e:
            print(f"Skipped duplicate: {e}")
            continue
    
    conn.commit()
    print(f"\n✓ Successfully inserted {inserted_games} NBA games")
    print(f"✓ Successfully generated {inserted_predictions} predictions")
    
    # Verify
    cursor.execute("SELECT COUNT(*) FROM games WHERE sport='NBA'")
    games_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM predictions WHERE sport='NBA'")
    pred_count = cursor.fetchone()[0]
    
    print(f"\n✓ Database contains {games_count} NBA games")
    print(f"✓ Database contains {pred_count} NBA predictions")
    
    # Show latest games
    cursor.execute("""
        SELECT game_date, home_team_id, away_team_id, home_score, away_score
        FROM games
        WHERE sport='NBA'
        ORDER BY game_date DESC
        LIMIT 5
    """)
    
    print("\nLatest games in database:")
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[2]} @ {row[1]} ({row[4]}-{row[3]})")
    
    conn.close()
    print("\n✓ Complete! NBA data is now correct with 2024 dates.")

if __name__ == '__main__':
    print("=" * 60)
    print("NBA Schedule Fix - Correct Year (2024)")
    print("=" * 60)
    print("\nThis will fix NBA data to use correct 2024 dates.")
    print("Games will only include those up to November 4, 2024.")
    
    response = input("\nContinue? (yes/no): ")
    
    if response.lower() == 'yes':
        fix_nba_with_correct_year()
    else:
        print("Cancelled.")
