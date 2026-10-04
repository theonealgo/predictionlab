#!/usr/bin/env python3
"""
Daily NBA Sync Script
Run this once per day (morning) to ensure all NBA games have predictions
"""

import sqlite3
from datetime import datetime
from nba_sportsdata_api import NBASportsDataAPI

DATABASE = 'sports_predictions_original.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def sync_nba_games_to_database():
    """
    Step 1: Fetch NBA games from API and save to database
    """
    print("Step 1: Syncing NBA games from API to database...")
    
    try:
        api = NBASportsDataAPI()
        games = api.get_recent_and_upcoming_games(days_back=7, days_forward=30)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        new_games = 0
        for game in games:
            # Check if game already exists
            existing = cursor.execute('''
                SELECT game_id FROM games 
                WHERE sport = 'NBA' 
                  AND game_date = ? 
                  AND home_team_id = ? 
                  AND away_team_id = ?
            ''', (game['game_date'], game['home_team_id'], game['away_team_id'])).fetchone()
            
            if not existing:
                # Insert new game
                cursor.execute('''
                    INSERT INTO games (
                        game_id, sport, league, season, game_date,
                        home_team_id, away_team_id, home_score, away_score, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    game['game_id'], 'NBA', 'NBA', 2025, game['game_date'],
                    game['home_team_id'], game['away_team_id'],
                    game['home_score'], game['away_score'], game['status']
                ))
                new_games += 1
            else:
                # Update scores if game is final
                if game['status'] == 'final' and game['home_score'] is not None:
                    cursor.execute('''
                        UPDATE games
                        SET home_score = ?, away_score = ?, status = 'final'
                        WHERE sport = 'NBA'
                          AND game_date = ?
                          AND home_team_id = ?
                          AND away_team_id = ?
                    ''', (
                        game['home_score'], game['away_score'],
                        game['game_date'], game['home_team_id'], game['away_team_id']
                    ))
        
        conn.commit()
        conn.close()
        
        print(f"   ✓ Synced {len(games)} games ({new_games} new)")
        return True
        
    except Exception as e:
        print(f"   ✗ Error syncing games: {e}")
        return False


def generate_missing_predictions():
    """
    Step 2: Generate predictions for any games that don't have them yet
    Uses same Elo logic as the predictions page
    """
    print("\nStep 2: Generating predictions for games without them...")
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get all NBA games
        games = cursor.execute("""
            SELECT g.game_id, g.game_date, g.home_team_id, g.away_team_id, 
                   g.home_score, g.away_score
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = 'NBA'
            WHERE g.sport = 'NBA'
              AND p.id IS NULL
            ORDER BY g.game_date
        """).fetchall()
        
        if not games:
            print("   ✓ All games already have predictions")
            conn.close()
            return True
        
        print(f"   Found {len(games)} games without predictions")
        
        # Initialize Elo ratings from completed games
        elo_ratings = {}
        k_factor = 18
        
        # Get all completed games to train Elo
        completed = cursor.execute("""
            SELECT home_team_id, away_team_id, home_score, away_score
            FROM games
            WHERE sport = 'NBA' AND home_score IS NOT NULL
            ORDER BY game_date
        """).fetchall()
        
        def get_elo(team):
            return elo_ratings.get(team, 1500)
        
        def expected_score(rating_a, rating_b):
            return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
        
        # Train Elo on completed games
        for game in completed:
            home_rating = get_elo(game['home_team_id'])
            away_rating = get_elo(game['away_team_id'])
            expected_home = expected_score(home_rating, away_rating)
            actual_home = 1 if game['home_score'] > game['away_score'] else 0
            elo_ratings[game['home_team_id']] = home_rating + k_factor * (actual_home - expected_home)
            elo_ratings[game['away_team_id']] = away_rating + k_factor * ((1-actual_home) - (1-expected_home))
        
        # Generate predictions for missing games
        inserted = 0
        for game in games:
            home_rating = get_elo(game['home_team_id'])
            away_rating = get_elo(game['away_team_id'])
            home_prob = expected_score(home_rating, away_rating)
            
            # Use Elo for all models (keeps consistency with current approach)
            cursor.execute('''
                INSERT INTO predictions (
                    game_id, sport, league, game_date, home_team_id, away_team_id,
                    elo_home_prob, xgboost_home_prob, logistic_home_prob, win_probability
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                game['game_id'], 'NBA', 'NBA', game['game_date'],
                game['home_team_id'], game['away_team_id'],
                home_prob, home_prob, home_prob, home_prob
            ))
            inserted += 1
        
        conn.commit()
        conn.close()
        
        print(f"   ✓ Generated {inserted} new predictions")
        return True
        
    except Exception as e:
        print(f"   ✗ Error generating predictions: {e}")
        return False


def validate_integrity():
    """
    Step 3: Validate that all games with scores have predictions
    """
    print("\nStep 3: Validating prediction integrity...")
    
    try:
        conn = get_db_connection()
        
        orphans = conn.execute('''
            SELECT COUNT(*) as count
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = 'NBA'
            WHERE g.sport = 'NBA' 
              AND g.home_score IS NOT NULL
              AND p.id IS NULL
        ''').fetchone()
        
        conn.close()
        
        if orphans['count'] > 0:
            print(f"   ✗ WARNING: {orphans['count']} games have results but no predictions!")
            return False
        else:
            print("   ✓ All games with results have predictions")
            return True
            
    except Exception as e:
        print(f"   ✗ Error validating: {e}")
        return False


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("DAILY NBA SYNC")
    print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60 + "\n")
    
    # Run sync process
    step1 = sync_nba_games_to_database()
    step2 = generate_missing_predictions()  if step1 else False
    step3 = validate_integrity() if step2 else False
    
    print("\n" + "=" * 60)
    if step1 and step2 and step3:
        print("✓ SYNC COMPLETE - All systems ready")
    else:
        print("✗ SYNC INCOMPLETE - Check errors above")
    print("=" * 60 + "\n")
