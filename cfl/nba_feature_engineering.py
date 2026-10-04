#!/usr/bin/env python3
"""
NBA Feature Engineering Module
Advanced features to push NBA prediction accuracy from 61% to 70%
"""
import sqlite3
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class NBAFeatureEngineer:
    def __init__(self, db_path='sports_predictions_original.db'):
        self.db_path = db_path
        
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def get_rest_days(self, team_id, game_date):
        """Calculate days of rest since last game"""
        conn = self.get_connection()
        
        # Get most recent game before this date
        last_game = conn.execute('''
            SELECT MAX(date(game_date)) as last_date
            FROM games 
            WHERE sport = 'NBA' 
            AND date(game_date) < date(?)
            AND (home_team_id = ? OR away_team_id = ?)
        ''', (game_date, team_id, team_id)).fetchone()
        
        conn.close()
        
        if not last_game or not last_game['last_date']:
            return 3  # Default rest
        
        last_date = datetime.strptime(last_game['last_date'], '%Y-%m-%d')
        current_date = datetime.strptime(game_date, '%Y-%m-%d')
        
        return (current_date - last_date).days
    
    def detect_back_to_back(self, team_id, game_date):
        """Check if team played yesterday"""
        rest_days = self.get_rest_days(team_id, game_date)
        return rest_days == 1
    
    def is_road_back_to_back(self, team_id, game_date, is_home):
        """Check if this is second game of a road back-to-back (most fatiguing)"""
        if is_home or not self.detect_back_to_back(team_id, game_date):
            return False
        
        conn = self.get_connection()
        yesterday = (datetime.strptime(game_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Check if yesterday's game was also on the road
        yesterday_game = conn.execute('''
            SELECT home_team_id
            FROM games 
            WHERE sport = 'NBA' 
            AND date(game_date) = date(?)
            AND (home_team_id = ? OR away_team_id = ?)
        ''', (yesterday, team_id, team_id)).fetchone()
        
        conn.close()
        
        if yesterday_game:
            # If team was away yesterday (not home team), it's a road B2B
            return yesterday_game['home_team_id'] != team_id
        
        return False
    
    def get_recent_form(self, team_id, game_date, num_games=10, weighted=True):
        """Get team's recent win rate with optional recency weighting"""
        conn = self.get_connection()
        
        games = conn.execute('''
            SELECT home_team_id, away_team_id, home_score, away_score
            FROM games 
            WHERE sport = 'NBA' 
            AND date(game_date) < date(?)
            AND (home_team_id = ? OR away_team_id = ?)
            AND home_score IS NOT NULL
            AND away_score IS NOT NULL
            ORDER BY game_date DESC
            LIMIT ?
        ''', (game_date, team_id, team_id, num_games)).fetchall()
        
        conn.close()
        
        if not games:
            return 0.5
        
        wins = 0
        total_weight = 0
        
        for idx, game in enumerate(games):
            is_win = False
            if game['home_team_id'] == team_id:
                is_win = game['home_score'] > game['away_score']
            else:
                is_win = game['away_score'] > game['home_score']
            
            # Exponential decay: more recent games weighted heavier
            weight = (1.5 ** (num_games - idx)) if weighted else 1.0
            
            if is_win:
                wins += weight
            total_weight += weight
        
        return wins / total_weight if total_weight > 0 else 0.5
    
    def get_pace(self, team_id, game_date, num_games=10):
        """Calculate team's pace (possessions per game) from recent games"""
        conn = self.get_connection()
        
        games = conn.execute('''
            SELECT home_team_id, home_score, away_score
            FROM games 
            WHERE sport = 'NBA' 
            AND date(game_date) < date(?)
            AND (home_team_id = ? OR away_team_id = ?)
            AND home_score IS NOT NULL
            ORDER BY game_date DESC
            LIMIT ?
        ''', (game_date, team_id, team_id, num_games)).fetchall()
        
        conn.close()
        
        if not games:
            return 100.0  # League average pace
        
        # Rough pace estimate: (Points For + Points Against) / 2.2
        # (This is simplified; actual pace uses possessions)
        total_pace = 0
        for game in games:
            total_points = game['home_score'] + game['away_score']
            pace = total_points / 2.2
            total_pace += pace
        
        return total_pace / len(games)
    
    def get_offensive_efficiency(self, team_id, game_date, num_games=10):
        """Points per game from recent games"""
        conn = self.get_connection()
        
        games = conn.execute('''
            SELECT home_team_id, home_score, away_score
            FROM games 
            WHERE sport = 'NBA' 
            AND date(game_date) < date(?)
            AND (home_team_id = ? OR away_team_id = ?)
            AND home_score IS NOT NULL
            ORDER BY game_date DESC
            LIMIT ?
        ''', (game_date, team_id, team_id, num_games)).fetchall()
        
        conn.close()
        
        if not games:
            return 112.0  # League average
        
        points = sum(g['home_score'] if g['home_team_id'] == team_id else g['away_score'] for g in games)
        return points / len(games)
    
    def get_defensive_efficiency(self, team_id, game_date, num_games=10):
        """Points allowed per game from recent games"""
        conn = self.get_connection()
        
        games = conn.execute('''
            SELECT home_team_id, home_score, away_score
            FROM games 
            WHERE sport = 'NBA' 
            AND date(game_date) < date(?)
            AND (home_team_id = ? OR away_team_id = ?)
            AND home_score IS NOT NULL
            ORDER BY game_date DESC
            LIMIT ?
        ''', (game_date, team_id, team_id, num_games)).fetchall()
        
        conn.close()
        
        if not games:
            return 112.0  # League average
        
        points_against = sum(g['away_score'] if g['home_team_id'] == team_id else g['home_score'] for g in games)
        return points_against / len(games)
    
    def get_home_away_splits(self, team_id, game_date, is_home):
        """Get team's home or away win percentage"""
        conn = self.get_connection()
        
        if is_home:
            games = conn.execute('''
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN home_score > away_score THEN 1 ELSE 0 END) as wins
                FROM games 
                WHERE sport = 'NBA' 
                AND date(game_date) < date(?)
                AND home_team_id = ?
                AND home_score IS NOT NULL
            ''', (game_date, team_id)).fetchone()
        else:
            games = conn.execute('''
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN away_score > home_score THEN 1 ELSE 0 END) as wins
                FROM games 
                WHERE sport = 'NBA' 
                AND date(game_date) < date(?)
                AND away_team_id = ?
                AND away_score IS NOT NULL
            ''', (game_date, team_id)).fetchone()
        
        conn.close()
        
        if not games or games['total'] == 0:
            return 0.5
        
        return games['wins'] / games['total']
    
    def engineer_features(self, home_team_id, away_team_id, game_date):
        """Generate all advanced features for a game"""
        features = {
            # Rest and fatigue
            'home_rest_days': self.get_rest_days(home_team_id, game_date),
            'away_rest_days': self.get_rest_days(away_team_id, game_date),
            'home_back_to_back': 1 if self.detect_back_to_back(home_team_id, game_date) else 0,
            'away_back_to_back': 1 if self.detect_back_to_back(away_team_id, game_date) else 0,
            'away_road_b2b': 1 if self.is_road_back_to_back(away_team_id, game_date, is_home=False) else 0,
            
            # Recent form with weighting
            'home_form_L10_weighted': self.get_recent_form(home_team_id, game_date, 10, weighted=True),
            'away_form_L10_weighted': self.get_recent_form(away_team_id, game_date, 10, weighted=True),
            'home_form_L5': self.get_recent_form(home_team_id, game_date, 5, weighted=False),
            'away_form_L5': self.get_recent_form(away_team_id, game_date, 5, weighted=False),
            
            # Pace and tempo
            'home_pace': self.get_pace(home_team_id, game_date, 10),
            'away_pace': self.get_pace(away_team_id, game_date, 10),
            
            # Offensive/Defensive efficiency
            'home_offensive_rating': self.get_offensive_efficiency(home_team_id, game_date, 10),
            'away_offensive_rating': self.get_offensive_efficiency(away_team_id, game_date, 10),
            'home_defensive_rating': self.get_defensive_efficiency(home_team_id, game_date, 10),
            'away_defensive_rating': self.get_defensive_efficiency(away_team_id, game_date, 10),
            
            # Home/Away splits
            'home_win_pct_at_home': self.get_home_away_splits(home_team_id, game_date, is_home=True),
            'away_win_pct_on_road': self.get_home_away_splits(away_team_id, game_date, is_home=False),
        }
        
        # Derived features
        features['rest_advantage'] = features['home_rest_days'] - features['away_rest_days']
        features['form_diff_L10'] = features['home_form_L10_weighted'] - features['away_form_L10_weighted']
        features['form_diff_L5'] = features['home_form_L5'] - features['away_form_L5']
        features['pace_diff'] = features['home_pace'] - features['away_pace']
        features['avg_pace'] = (features['home_pace'] + features['away_pace']) / 2
        features['offensive_matchup'] = features['home_offensive_rating'] - features['away_defensive_rating']
        features['defensive_matchup'] = features['away_offensive_rating'] - features['home_defensive_rating']
        features['net_rating_diff'] = (features['home_offensive_rating'] - features['home_defensive_rating']) - \
                                       (features['away_offensive_rating'] - features['away_defensive_rating'])
        
        return features
    
    def calculate_probability_boost(self, features):
        """Convert features into probability adjustments"""
        boost = 0.0
        
        # Rest advantage: Each extra day = +2% (capped)
        rest_impact = min(0.06, max(-0.06, features['rest_advantage'] * 0.02))
        boost += rest_impact
        
        # Road back-to-back penalty (brutal in NBA)
        if features['away_road_b2b']:
            boost += 0.08  # Big advantage for home team
        
        # Recent form (weighted heavily toward last 5 games)
        boost += features['form_diff_L5'] * 0.18
        boost += features['form_diff_L10'] * 0.10
        
        # Net rating differential (strong predictor in NBA)
        boost += features['net_rating_diff'] * 0.008  # 5 point net rating diff = 4% boost
        
        # Pace matchup (high variance games favor better team)
        if features['avg_pace'] > 105:  # Fast-paced game
            boost += features['net_rating_diff'] * 0.003  # Extra boost
        
        # Home/away splits (already strong signal)
        split_diff = features['home_win_pct_at_home'] - features['away_win_pct_on_road']
        boost += split_diff * 0.12
        
        # Clamp to reasonable range
        return max(-0.30, min(0.30, boost))


if __name__ == "__main__":
    # Test the feature engineering
    engineer = NBAFeatureEngineer()
    
    # Example: test with recent game
    features = engineer.engineer_features('Los Angeles Lakers', 'Boston Celtics', '2026-01-08')
    print("\nNBA Feature Engineering Test:")
    print("=" * 50)
    for key, value in features.items():
        if isinstance(value, float):
            print(f"{key:30s}: {value:.3f}")
        else:
            print(f"{key:30s}: {value}")
    
    boost = engineer.calculate_probability_boost(features)
    print(f"\n{'Probability Boost':30s}: {boost:+.3f} ({boost*100:+.1f}%)")
