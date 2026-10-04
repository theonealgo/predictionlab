"""
Advanced Feature Engineering Module
====================================

Structured features including:
- Rating differences (Glicko-2, margin-adjusted, Elo)
- Recent form (rolling windows: 5, 10, 20 games)
- Home/away adjustments
- Rest days & travel effects
- Market odds (closing line implied probability)
- Pace, efficiency, or xStats proxies
"""

import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import logging

from .rating_engines import Glicko2Rating, MarginRating, EloFeatureGenerator, TrueSkillRating

logger = logging.getLogger(__name__)


class AdvancedFeatureEngineer:
    """
    Unified feature engineering for all sports
    
    Features are organized into categories:
    1. Rating features (from Glicko-2, margin, Elo)
    2. Form features (rolling windows)
    3. Rest/fatigue features
    4. Home/away splits
    5. Market features (if available)
    6. Head-to-head features
    """
    
    # Home field advantage in rating points
    HOME_ADVANTAGES = {
        'NFL': 48,
        'NBA': 80,
        'NHL': 30,
        'MLB': 20,
        'NCAAF': 50,
        'NCAAB': 85,
        'WNBA': 60,
    }
    
    def __init__(self, db_path: str = 'sports_predictions_original.db'):
        self.db_path = db_path
        
        # Initialize rating systems per sport
        self.glicko2_ratings: Dict[str, Glicko2Rating] = {}
        self.trueskill_ratings: Dict[str, TrueSkillRating] = {}
        self.margin_ratings: Dict[str, MarginRating] = {}
        self.elo_ratings: Dict[str, EloFeatureGenerator] = {}
        
        # Cache for computed features
        self._form_cache: Dict[str, Dict] = {}
        self._h2h_cache: Dict[str, Dict] = {}
        
    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def initialize_ratings(self, sport: str, games_df: pd.DataFrame):
        """
        Initialize rating systems by processing historical games
        
        IMPORTANT: Process games in chronological order to avoid data leakage
        """
        if sport not in self.glicko2_ratings:
            self.glicko2_ratings[sport] = Glicko2Rating()
            self.trueskill_ratings[sport] = TrueSkillRating()
            self.margin_ratings[sport] = MarginRating(sport)
            self.elo_ratings[sport] = EloFeatureGenerator(sport)
        
        home_adv = self.HOME_ADVANTAGES.get(sport, 50)
        
        # Sort by date (critical for time-series integrity)
        games_df = games_df.sort_values('game_date')
        
        for _, game in games_df.iterrows():
            home_team = game['home_team_id']
            away_team = game['away_team_id']
            home_score = game.get('home_score')
            away_score = game.get('away_score')
            
            if pd.isna(home_score) or pd.isna(away_score):
                continue
            
            # Determine winner (1 = home win, 0 = away win, 0.5 = tie)
            if home_score > away_score:
                score = 1.0
            elif home_score < away_score:
                score = 0.0
            else:
                score = 0.5
            
            # Update all rating systems
            self.glicko2_ratings[sport].update_ratings(
                home_team, away_team, score, home_adv
            )
            # TrueSkill update (with margin of victory)
            margin = abs(home_score - away_score)
            winner = home_team if home_score > away_score else away_team
            loser = away_team if home_score > away_score else home_team
            self.trueskill_ratings[sport].update_with_margin(winner, loser, margin)
            
            self.margin_ratings[sport].update_ratings(
                home_team, away_team, home_score, away_score, home_adv / 10
            )
            self.elo_ratings[sport].update_ratings(home_team, away_team, score)
    
    def get_rating_features(self, sport: str, home_team: str, 
                           away_team: str) -> Dict[str, float]:
        """Get all rating-based features"""
        features = {}
        
        # Initialize if needed
        if sport not in self.glicko2_ratings:
            self.glicko2_ratings[sport] = Glicko2Rating()
            self.trueskill_ratings[sport] = TrueSkillRating()
            self.margin_ratings[sport] = MarginRating(sport)
            self.elo_ratings[sport] = EloFeatureGenerator(sport)
        
        # Glicko-2 features
        g2_features = self.glicko2_ratings[sport].get_features(home_team, away_team)
        features.update(g2_features)
        
        # TrueSkill features
        ts_features = self.trueskill_ratings[sport].get_features(home_team, away_team)
        features.update(ts_features)
        
        # Margin features
        margin_features = self.margin_ratings[sport].get_features(home_team, away_team)
        features.update(margin_features)
        
        # Elo features (demoted to feature, not predictor)
        elo_features = self.elo_ratings[sport].get_features(home_team, away_team)
        features.update(elo_features)
        
        return features
    
    def get_form_features(self, sport: str, team_id: str, 
                         game_date: str) -> Dict[str, float]:
        """
        Rolling form features across multiple windows
        
        Windows: 5, 10, 20 games
        Metrics: Win%, Points scored, Points allowed, Margin
        """
        conn = self.get_connection()
        
        features = {}
        
        for window in [5, 10, 20]:
            games = conn.execute('''
                SELECT home_team_id, away_team_id, home_score, away_score
                FROM games 
                WHERE sport = ? 
                AND date(game_date) < date(?)
                AND (home_team_id = ? OR away_team_id = ?)
                AND home_score IS NOT NULL
                ORDER BY game_date DESC
                LIMIT ?
            ''', (sport, game_date, team_id, team_id, window)).fetchall()
            
            if not games:
                features[f'form_L{window}_win_pct'] = 0.5
                features[f'form_L{window}_ppg'] = 0.0
                features[f'form_L{window}_papg'] = 0.0
                features[f'form_L{window}_margin'] = 0.0
                continue
            
            wins = 0
            points_for = 0
            points_against = 0
            
            for game in games:
                if game['home_team_id'] == team_id:
                    if game['home_score'] > game['away_score']:
                        wins += 1
                    points_for += game['home_score']
                    points_against += game['away_score']
                else:
                    if game['away_score'] > game['home_score']:
                        wins += 1
                    points_for += game['away_score']
                    points_against += game['home_score']
            
            n_games = len(games)
            features[f'form_L{window}_win_pct'] = wins / n_games
            features[f'form_L{window}_ppg'] = points_for / n_games
            features[f'form_L{window}_papg'] = points_against / n_games
            features[f'form_L{window}_margin'] = (points_for - points_against) / n_games
        
        conn.close()
        return features
    
    def get_rest_features(self, sport: str, team_id: str, 
                         game_date: str) -> Dict[str, float]:
        """
        Rest and fatigue features
        
        - Days since last game
        - Back-to-back detection
        - Games in last 7 days
        - Road games in last 7 days
        """
        conn = self.get_connection()
        
        # Get last game date
        last_game = conn.execute('''
            SELECT MAX(date(game_date)) as last_date
            FROM games 
            WHERE sport = ? 
            AND date(game_date) < date(?)
            AND (home_team_id = ? OR away_team_id = ?)
        ''', (sport, game_date, team_id, team_id)).fetchone()
        
        if not last_game or not last_game['last_date']:
            rest_days = 7
        else:
            last_date = datetime.strptime(last_game['last_date'], '%Y-%m-%d')
            current_date = datetime.strptime(game_date[:10], '%Y-%m-%d')
            rest_days = (current_date - last_date).days
        
        # Games in last 7 days
        seven_days_ago = (datetime.strptime(game_date[:10], '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
        recent_games = conn.execute('''
            SELECT home_team_id, away_team_id
            FROM games 
            WHERE sport = ? 
            AND date(game_date) BETWEEN date(?) AND date(?)
            AND (home_team_id = ? OR away_team_id = ?)
        ''', (sport, seven_days_ago, game_date, team_id, team_id)).fetchall()
        
        games_in_7_days = len(recent_games)
        road_games_in_7_days = sum(1 for g in recent_games if g['away_team_id'] == team_id)
        
        conn.close()
        
        return {
            'rest_days': min(rest_days, 10),  # Cap at 10
            'is_back_to_back': 1 if rest_days == 1 else 0,
            'is_well_rested': 1 if rest_days >= 4 else 0,
            'games_in_7_days': games_in_7_days,
            'road_games_in_7_days': road_games_in_7_days,
            'fatigue_index': games_in_7_days / max(1, rest_days),
        }
    
    def get_home_away_features(self, sport: str, team_id: str,
                               game_date: str, is_home: bool) -> Dict[str, float]:
        """
        Home/away split features
        """
        conn = self.get_connection()
        
        if is_home:
            split = conn.execute('''
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN home_score > away_score THEN 1 ELSE 0 END) as wins,
                    AVG(home_score) as ppg,
                    AVG(away_score) as papg
                FROM games 
                WHERE sport = ? 
                AND date(game_date) < date(?)
                AND home_team_id = ?
                AND home_score IS NOT NULL
            ''', (sport, game_date, team_id)).fetchone()
        else:
            split = conn.execute('''
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN away_score > home_score THEN 1 ELSE 0 END) as wins,
                    AVG(away_score) as ppg,
                    AVG(home_score) as papg
                FROM games 
                WHERE sport = ? 
                AND date(game_date) < date(?)
                AND away_team_id = ?
                AND away_score IS NOT NULL
            ''', (sport, game_date, team_id)).fetchone()
        
        conn.close()
        
        if not split or split['total'] == 0:
            return {
                'split_win_pct': 0.5,
                'split_ppg': 0.0,
                'split_papg': 0.0,
            }
        
        return {
            'split_win_pct': split['wins'] / split['total'] if split['total'] > 0 else 0.5,
            'split_ppg': split['ppg'] or 0.0,
            'split_papg': split['papg'] or 0.0,
        }
    
    def get_h2h_features(self, sport: str, home_team: str, away_team: str,
                         game_date: str, lookback_years: int = 2) -> Dict[str, float]:
        """
        Head-to-head historical features
        """
        conn = self.get_connection()
        
        cutoff_date = (datetime.strptime(game_date[:10], '%Y-%m-%d') - 
                      timedelta(days=lookback_years * 365)).strftime('%Y-%m-%d')
        
        games = conn.execute('''
            SELECT home_team_id, away_team_id, home_score, away_score
            FROM games 
            WHERE sport = ? 
            AND date(game_date) BETWEEN date(?) AND date(?)
            AND ((home_team_id = ? AND away_team_id = ?) 
                 OR (home_team_id = ? AND away_team_id = ?))
            AND home_score IS NOT NULL
        ''', (sport, cutoff_date, game_date, home_team, away_team, away_team, home_team)).fetchall()
        
        conn.close()
        
        if not games:
            return {
                'h2h_games': 0,
                'h2h_home_win_pct': 0.5,
                'h2h_avg_margin': 0.0,
            }
        
        home_wins = 0
        total_margin = 0
        
        for game in games:
            if game['home_team_id'] == home_team:
                margin = game['home_score'] - game['away_score']
                if margin > 0:
                    home_wins += 1
            else:
                margin = game['away_score'] - game['home_score']
                if margin > 0:
                    home_wins += 1
            total_margin += margin
        
        return {
            'h2h_games': len(games),
            'h2h_home_win_pct': home_wins / len(games),
            'h2h_avg_margin': total_margin / len(games),
        }
    
    def get_market_features(self, sport: str, home_team: str, away_team: str,
                           game_date: str) -> Dict[str, float]:
        """
        Market odds features (if available)
        
        Returns implied probabilities from closing lines
        """
        conn = self.get_connection()
        
        # Try to get betting lines from database
        try:
            lines = conn.execute('''
                SELECT home_ml, away_ml, spread, total
                FROM betting_lines bl
                JOIN games g ON bl.game_id = g.game_id
                WHERE g.sport = ?
                AND g.home_team_id = ? AND g.away_team_id = ?
                AND date(g.game_date) = date(?)
            ''', (sport, home_team, away_team, game_date)).fetchone()
        except:
            lines = None
        
        conn.close()
        
        features = {
            'market_home_implied_prob': 0.5,
            'market_spread': 0.0,
            'market_total': 0.0,
            'has_market_data': 0,
        }
        
        if lines:
            features['has_market_data'] = 1
            
            # Convert moneyline to implied probability
            if lines['home_ml']:
                home_ml = lines['home_ml']
                if home_ml > 0:
                    features['market_home_implied_prob'] = 100 / (home_ml + 100)
                else:
                    features['market_home_implied_prob'] = abs(home_ml) / (abs(home_ml) + 100)
            
            if lines['spread']:
                features['market_spread'] = lines['spread']
            
            if lines['total']:
                features['market_total'] = lines['total']
        
        return features
    
    def engineer_all_features(self, sport: str, home_team: str, away_team: str,
                              game_date: str) -> Dict[str, float]:
        """
        Generate complete feature set for a game
        
        This is the main entry point for feature engineering.
        Returns a flat dictionary of all features.
        """
        features = {}
        
        # 1. Rating features (Glicko-2, margin, Elo)
        rating_features = self.get_rating_features(sport, home_team, away_team)
        features.update(rating_features)
        
        # 2. Form features for both teams
        home_form = self.get_form_features(sport, home_team, game_date)
        away_form = self.get_form_features(sport, away_team, game_date)
        
        for key, value in home_form.items():
            features[f'home_{key}'] = value
        for key, value in away_form.items():
            features[f'away_{key}'] = value
        
        # Form differentials
        for window in [5, 10, 20]:
            features[f'form_L{window}_win_pct_diff'] = (
                home_form[f'form_L{window}_win_pct'] - away_form[f'form_L{window}_win_pct']
            )
            features[f'form_L{window}_margin_diff'] = (
                home_form[f'form_L{window}_margin'] - away_form[f'form_L{window}_margin']
            )
        
        # 3. Rest features for both teams
        home_rest = self.get_rest_features(sport, home_team, game_date)
        away_rest = self.get_rest_features(sport, away_team, game_date)
        
        for key, value in home_rest.items():
            features[f'home_{key}'] = value
        for key, value in away_rest.items():
            features[f'away_{key}'] = value
        
        # Rest differentials
        features['rest_diff'] = home_rest['rest_days'] - away_rest['rest_days']
        features['fatigue_diff'] = away_rest['fatigue_index'] - home_rest['fatigue_index']
        
        # 4. Home/away splits
        home_splits = self.get_home_away_features(sport, home_team, game_date, is_home=True)
        away_splits = self.get_home_away_features(sport, away_team, game_date, is_home=False)
        
        for key, value in home_splits.items():
            features[f'home_{key}'] = value
        for key, value in away_splits.items():
            features[f'away_{key}'] = value
        
        features['split_win_pct_diff'] = home_splits['split_win_pct'] - away_splits['split_win_pct']
        
        # 5. Head-to-head
        h2h_features = self.get_h2h_features(sport, home_team, away_team, game_date)
        features.update(h2h_features)
        
        # 6. Market features
        market_features = self.get_market_features(sport, home_team, away_team, game_date)
        features.update(market_features)
        
        # 7. Home advantage constant
        features['home_advantage'] = self.HOME_ADVANTAGES.get(sport, 50)
        
        return features
    
    def get_feature_names(self) -> List[str]:
        """Get list of all feature names in order"""
        # Generate a sample to get feature names
        sample = self.engineer_all_features('NBA', 'Team A', 'Team B', '2025-01-01')
        return list(sample.keys())
    
    def generate_features_for_game(self, game: pd.Series, 
                                    glicko2: 'Glicko2Rating',
                                    trueskill: 'TrueSkillRating',
                                    margin_rating: 'MarginRating',
                                    elo_features: 'EloFeatureGenerator') -> Dict[str, float]:
        """
        Generate features for a single game using external rating engines
        Used for prediction on new games
        """
        home = game['home_team']
        away = game['away_team']
        sport = margin_rating.sport
        
        features = {}
        
        # Glicko-2 features
        features.update(glicko2.get_features(home, away))
        
        # TrueSkill features
        features.update(trueskill.get_features(home, away))
        
        # Margin features
        features.update(margin_rating.get_features(home, away))
        
        # Elo features
        features.update(elo_features.get_features(home, away))
        
        # Home advantage
        features['home_advantage'] = self.HOME_ADVANTAGES.get(sport, 50)
        
        return features
    
    def prepare_training_data_from_games(self, games_df: pd.DataFrame,
                                          glicko2: 'Glicko2Rating',
                                          trueskill: 'TrueSkillRating',
                                          margin_rating: 'MarginRating',
                                          elo_features: 'EloFeatureGenerator') -> Tuple[pd.DataFrame, np.ndarray]:
        """
        Generate training features from a games DataFrame using external rating engines
        
        This version uses pre-computed ratings from external engines
        """
        sport = margin_rating.sport
        features_list = []
        labels = []
        
        logger.info(f"Generating features for {len(games_df)} {sport} games...")
        
        for idx, game in games_df.iterrows():
            try:
                home = game['home_team']
                away = game['away_team']
                
                # Get rating features from external engines
                features = {}
                
                # Glicko-2 features
                features.update(glicko2.get_features(home, away))
                
                # TrueSkill features
                features.update(trueskill.get_features(home, away))
                
                # Margin features
                features.update(margin_rating.get_features(home, away))
                
                # Elo features
                features.update(elo_features.get_features(home, away))
                
                # Add home advantage
                features['home_advantage'] = self.HOME_ADVANTAGES.get(sport, 50)
                
                features_list.append(features)
                
                # Label: 1 if home won
                labels.append(1 if game['home_score'] > game['away_score'] else 0)
                
            except Exception as e:
                logger.warning(f"Error processing game {idx}: {e}")
                # Add dummy features to maintain alignment
                if features_list:
                    features_list.append({k: 0.0 for k in features_list[0].keys()})
                    labels.append(0)
                continue
        
        X = pd.DataFrame(features_list)
        y = np.array(labels)
        
        # Fill NaN with 0
        X = X.fillna(0)
        
        logger.info(f"Generated {X.shape[1]} features for {len(X)} games")
        return X, y
    
    def prepare_training_data(self, sport: str, 
                              min_games: int = 100) -> Tuple[pd.DataFrame, np.ndarray]:
        """
        Prepare complete training dataset with features and labels
        
        CRITICAL: Uses proper time-series split to avoid data leakage
        """
        conn = self.get_connection()
        
        games_df = pd.read_sql_query('''
            SELECT game_id, game_date, home_team_id, away_team_id, home_score, away_score
            FROM games
            WHERE sport = ?
            AND home_score IS NOT NULL
            AND away_score IS NOT NULL
            ORDER BY game_date ASC
        ''', conn, params=(sport,))
        
        conn.close()
        
        if len(games_df) < min_games:
            raise ValueError(f"Insufficient data: {len(games_df)} games (need {min_games})")
        
        logger.info(f"Processing {len(games_df)} {sport} games for training data...")
        
        # Initialize rating systems
        self.initialize_ratings(sport, games_df.head(min_games // 2))
        
        # Generate features for remaining games
        features_list = []
        labels = []
        
        # Skip first portion for rating initialization
        for idx, game in games_df.iloc[min_games // 2:].iterrows():
            try:
                features = self.engineer_all_features(
                    sport,
                    game['home_team_id'],
                    game['away_team_id'],
                    str(game['game_date'])
                )
                
                features_list.append(features)
                
                # Label: 1 if home team won
                label = 1 if game['home_score'] > game['away_score'] else 0
                labels.append(label)
                
                # Update ratings AFTER generating features (critical for avoiding leakage)
                score = 1 if game['home_score'] > game['away_score'] else 0
                home_adv = self.HOME_ADVANTAGES.get(sport, 50)
                
                self.glicko2_ratings[sport].update_ratings(
                    game['home_team_id'], game['away_team_id'], score, home_adv
                )
                self.margin_ratings[sport].update_ratings(
                    game['home_team_id'], game['away_team_id'],
                    game['home_score'], game['away_score'], home_adv / 10
                )
                self.elo_ratings[sport].update_ratings(
                    game['home_team_id'], game['away_team_id'], score
                )
                
            except Exception as e:
                logger.warning(f"Error processing game {game.get('game_id')}: {e}")
                continue
        
        X = pd.DataFrame(features_list)
        y = np.array(labels)
        
        logger.info(f"Generated {X.shape[1]} features for {len(X)} games")
        
        return X, y
