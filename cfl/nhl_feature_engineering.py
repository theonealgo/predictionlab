#!/usr/bin/env python3
"""
NHL Feature Engineering Module
Advanced features to push NHL prediction accuracy from 54% to 70%
"""
import sqlite3
from datetime import datetime, timedelta
import requests
import logging

logger = logging.getLogger(__name__)

class NHLFeatureEngineer:
    def __init__(self, db_path='sports_predictions_original.db'):
        self.db_path = db_path
        
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def detect_back_to_back(self, team_id, game_date):
        """Check if team played yesterday (back-to-back games)"""
        conn = self.get_connection()
        yesterday = (datetime.strptime(game_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        
        result = conn.execute('''
            SELECT COUNT(*) as count FROM games 
            WHERE sport = 'NHL' 
            AND date(game_date) = date(?)
            AND (home_team_id = ? OR away_team_id = ?)
        ''', (yesterday, team_id, team_id)).fetchone()
        
        conn.close()
        return result['count'] > 0
    
    def get_games_in_last_n_days(self, team_id, game_date, days=4):
        """Get number of games in last N days (fatigue indicator)"""
        conn = self.get_connection()
        start_date = (datetime.strptime(game_date, '%Y-%m-%d') - timedelta(days=days)).strftime('%Y-%m-%d')
        
        result = conn.execute('''
            SELECT COUNT(*) as count FROM games 
            WHERE sport = 'NHL' 
            AND date(game_date) BETWEEN date(?) AND date(?)
            AND (home_team_id = ? OR away_team_id = ?)
        ''', (start_date, game_date, team_id, team_id)).fetchone()
        
        conn.close()
        return result['count']
    
    def get_recent_form(self, team_id, game_date, num_games=10):
        """Get team's win rate in last N games"""
        conn = self.get_connection()
        
        # Get last N completed games before this date
        games = conn.execute('''
            SELECT home_team_id, away_team_id, home_score, away_score
            FROM games 
            WHERE sport = 'NHL' 
            AND date(game_date) < date(?)
            AND (home_team_id = ? OR away_team_id = ?)
            AND home_score IS NOT NULL
            AND away_score IS NOT NULL
            ORDER BY game_date DESC
            LIMIT ?
        ''', (game_date, team_id, team_id, num_games)).fetchall()
        
        conn.close()
        
        if not games:
            return 0.5  # Neutral if no history
        
        wins = 0
        for game in games:
            if game['home_team_id'] == team_id:
                if game['home_score'] > game['away_score']:
                    wins += 1
            else:
                if game['away_score'] > game['home_score']:
                    wins += 1
        
        return wins / len(games)
    
    def get_goalie_stats(self, team_id, game_date):
        """Get team's starting goalie stats (save%, GAA, recent starts)"""
        conn = self.get_connection()
        
        # Get most recent goalie stats from game_goalies table
        goalie = conn.execute('''
            SELECT gg.home_goalie_save_pct, gg.home_goalie_gaa
            FROM games g
            JOIN game_goalies gg ON g.id = gg.game_id
            WHERE g.sport = 'NHL'
            AND date(g.game_date) < date(?)
            AND g.home_team_id = ?
            AND gg.home_goalie_save_pct IS NOT NULL
            ORDER BY g.game_date DESC
            LIMIT 1
        ''', (game_date, team_id)).fetchone()
        
        conn.close()
        
        if goalie:
            return {
                'save_pct': float(goalie['home_goalie_save_pct']),
                'gaa': float(goalie['home_goalie_gaa'])
            }
        
        return {'save_pct': 0.910, 'gaa': 2.8}  # League average
    
    def get_special_teams_efficiency(self, team_id, game_date):
        """Calculate power play % and penalty kill % from recent games"""
        conn = self.get_connection()
        
        # This would ideally come from API or detailed stats
        # For now, use goals scored/allowed as proxy
        games = conn.execute('''
            SELECT home_team_id, away_team_id, home_score, away_score
            FROM games 
            WHERE sport = 'NHL' 
            AND date(game_date) < date(?)
            AND (home_team_id = ? OR away_team_id = ?)
            AND home_score IS NOT NULL
            ORDER BY game_date DESC
            LIMIT 20
        ''', (game_date, team_id, team_id)).fetchall()
        
        conn.close()
        
        if not games:
            return {'pp_pct': 0.20, 'pk_pct': 0.80}  # League average
        
        # Calculate goals per game as proxy for special teams
        goals_for = 0
        goals_against = 0
        for game in games:
            if game['home_team_id'] == team_id:
                goals_for += game['home_score']
                goals_against += game['away_score']
            else:
                goals_for += game['away_score']
                goals_against += game['home_score']
        
        gpg = goals_for / len(games)
        ga_pg = goals_against / len(games)
        
        # Normalize to reasonable special teams ranges
        pp_pct = min(0.35, max(0.10, (gpg - 2.5) * 0.1 + 0.20))
        pk_pct = min(0.90, max(0.70, 0.90 - (ga_pg - 2.5) * 0.05))
        
        return {'pp_pct': pp_pct, 'pk_pct': pk_pct}
    
    def get_shot_quality_metrics(self, team_id, game_date):
        """Expected goals (xG) proxy using shots and goals from recent games"""
        conn = self.get_connection()
        
        games = conn.execute('''
            SELECT home_team_id, home_score, away_score
            FROM games 
            WHERE sport = 'NHL' 
            AND date(game_date) < date(?)
            AND (home_team_id = ? OR away_team_id = ?)
            AND home_score IS NOT NULL
            ORDER BY game_date DESC
            LIMIT 15
        ''', (game_date, team_id, team_id)).fetchall()
        
        conn.close()
        
        if not games:
            return 1.0  # Neutral
        
        # Calculate shooting percentage as proxy for shot quality
        goals = sum(g['home_score'] if g['home_team_id'] == team_id else g['away_score'] for g in games)
        avg_goals = goals / len(games)
        
        # Normalize: league average is ~3 goals per game
        return avg_goals / 3.0
    
    def engineer_features(self, home_team_id, away_team_id, game_date):
        """Generate all advanced features for a game"""
        features = {
            # Fatigue features
            'home_back_to_back': 1 if self.detect_back_to_back(home_team_id, game_date) else 0,
            'away_back_to_back': 1 if self.detect_back_to_back(away_team_id, game_date) else 0,
            'home_games_in_4_days': self.get_games_in_last_n_days(home_team_id, game_date, 4),
            'away_games_in_4_days': self.get_games_in_last_n_days(away_team_id, game_date, 4),
            
            # Recent form (momentum)
            'home_form_L10': self.get_recent_form(home_team_id, game_date, 10),
            'away_form_L10': self.get_recent_form(away_team_id, game_date, 10),
            'home_form_L5': self.get_recent_form(home_team_id, game_date, 5),
            'away_form_L5': self.get_recent_form(away_team_id, game_date, 5),
            
            # Goalie quality
            'home_goalie_save_pct': self.get_goalie_stats(home_team_id, game_date)['save_pct'],
            'away_goalie_save_pct': self.get_goalie_stats(away_team_id, game_date)['save_pct'],
            'home_goalie_gaa': self.get_goalie_stats(home_team_id, game_date)['gaa'],
            'away_goalie_gaa': self.get_goalie_stats(away_team_id, game_date)['gaa'],
            
            # Special teams
            'home_pp_pct': self.get_special_teams_efficiency(home_team_id, game_date)['pp_pct'],
            'away_pp_pct': self.get_special_teams_efficiency(away_team_id, game_date)['pp_pct'],
            'home_pk_pct': self.get_special_teams_efficiency(home_team_id, game_date)['pk_pct'],
            'away_pk_pct': self.get_special_teams_efficiency(away_team_id, game_date)['pk_pct'],
            
            # Shot quality
            'home_shot_quality': self.get_shot_quality_metrics(home_team_id, game_date),
            'away_shot_quality': self.get_shot_quality_metrics(away_team_id, game_date),
        }
        
        # Derived features (differentials)
        features['fatigue_diff'] = features['away_games_in_4_days'] - features['home_games_in_4_days']
        features['form_diff_L10'] = features['home_form_L10'] - features['away_form_L10']
        features['form_diff_L5'] = features['home_form_L5'] - features['away_form_L5']
        features['goalie_save_pct_diff'] = features['home_goalie_save_pct'] - features['away_goalie_save_pct']
        features['goalie_gaa_diff'] = features['away_goalie_gaa'] - features['home_goalie_gaa']  # Lower is better
        features['pp_pk_matchup'] = features['home_pp_pct'] - features['away_pk_pct']
        features['shot_quality_diff'] = features['home_shot_quality'] - features['away_shot_quality']
        
        return features
    
    def calculate_probability_boost(self, features):
        """Convert features into probability adjustments"""
        boost = 0.0
        
        # Fatigue impact: B2B = -5% win probability
        if features['home_back_to_back']:
            boost -= 0.05
        if features['away_back_to_back']:
            boost += 0.05
        
        # Recent form: Strong momentum = up to +8%
        boost += features['form_diff_L5'] * 0.15  # Last 5 games weighted more
        boost += features['form_diff_L10'] * 0.08
        
        # Goalie differential: 3% save% diff = ~8% win prob
        boost += features['goalie_save_pct_diff'] * 2.5
        
        # Special teams: PP vs PK matchup
        boost += features['pp_pk_matchup'] * 0.3
        
        # Shot quality
        boost += features['shot_quality_diff'] * 0.05
        
        # Clamp to reasonable range
        return max(-0.25, min(0.25, boost))


if __name__ == "__main__":
    # Test the feature engineering
    engineer = NHLFeatureEngineer()
    
    # Example: test with recent game
    features = engineer.engineer_features('Toronto Maple Leafs', 'Boston Bruins', '2026-01-08')
    print("\nNHL Feature Engineering Test:")
    print("=" * 50)
    for key, value in features.items():
        if isinstance(value, float):
            print(f"{key:30s}: {value:.3f}")
        else:
            print(f"{key:30s}: {value}")
    
    boost = engineer.calculate_probability_boost(features)
    print(f"\n{'Probability Boost':30s}: {boost:+.3f} ({boost*100:+.1f}%)")
