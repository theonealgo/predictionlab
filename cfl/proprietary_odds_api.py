"""
Proprietary Sports Odds API
Generates synthetic market-style spreads and totals using only ESPN public data
No sportsbook or bookmaker odds are used
"""

import sqlite3
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import logging
from functools import lru_cache
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ProprietaryOddsAPI:
    """
    Generates internal market-style odds using statistical modeling
    Uses only ESPN data and derived calculations
    """
    
    def __init__(self, db_path: str = 'sports_predictions_original.db'):
        self.db_path = db_path
        self.cache_duration = timedelta(hours=6)  # Configurable cache interval
        self._odds_cache = {}
        
        # Sport-specific home advantage (in points)
        self.home_advantage = {
            'NBA': 3.0,
            'NFL': 2.5,
            'NHL': 0.3,
            'MLB': 0.15,
            'NCAAB': 3.5,
            'NCAAF': 3.0,
            'WNBA': 2.5
        }
        
    def get_db_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def get_team_stats(self, team_id: str, sport: str, lookback_games: int = 10) -> Dict:
        """
        Get recent team statistics from games table
        Returns: offensive efficiency, defensive efficiency, pace, variance
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        # Get recent games as both home and away
        recent_games = cursor.execute('''
            SELECT 
                game_date,
                CASE WHEN home_team_id = ? THEN home_score ELSE away_score END as team_score,
                CASE WHEN home_team_id = ? THEN away_score ELSE home_score END as opp_score,
                CASE WHEN home_team_id = ? THEN 1 ELSE 0 END as is_home
            FROM games
            WHERE sport = ?
              AND (home_team_id = ? OR away_team_id = ?)
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
              AND status = 'final'
            ORDER BY game_date DESC
            LIMIT ?
        ''', (team_id, team_id, team_id, sport, team_id, team_id, lookback_games)).fetchall()
        
        conn.close()
        
        if not recent_games:
            return None
        
        scores_for = [g['team_score'] for g in recent_games]
        scores_against = [g['opp_score'] for g in recent_games]
        total_points = [g['team_score'] + g['opp_score'] for g in recent_games]
        
        stats = {
            'offensive_rating': np.mean(scores_for),
            'defensive_rating': np.mean(scores_against),
            'offensive_std': np.std(scores_for),
            'defensive_std': np.std(scores_against),
            'pace': np.mean(total_points),  # Proxy for pace
            'pace_std': np.std(total_points),
            'games_played': len(recent_games),
            'recent_form': np.mean(scores_for[-5:]) if len(scores_for) >= 5 else np.mean(scores_for)
        }
        
        return stats
    
    def calculate_expected_margin(self, home_team: str, away_team: str, sport: str) -> Tuple[float, float]:
        """
        Calculate expected scoring margin using offensive/defensive efficiency
        Returns: (expected_margin, confidence_std_dev)
        """
        home_stats = self.get_team_stats(home_team, sport)
        away_stats = self.get_team_stats(away_team, sport)
        
        if not home_stats or not away_stats:
            return None, None
        
        # Expected scores based on offense vs defense matchup
        home_expected = (home_stats['offensive_rating'] + away_stats['defensive_rating']) / 2
        away_expected = (away_stats['offensive_rating'] + home_stats['defensive_rating']) / 2
        
        # Apply home court advantage
        home_advantage = self.home_advantage.get(sport, 2.5)
        home_expected += home_advantage
        
        # Calculate expected margin
        expected_margin = home_expected - away_expected
        
        # Calculate confidence (lower std = higher confidence)
        margin_variance = np.sqrt(
            home_stats['offensive_std']**2 + 
            away_stats['offensive_std']**2 +
            home_stats['defensive_std']**2 + 
            away_stats['defensive_std']**2
        ) / 2
        
        return expected_margin, margin_variance
    
    def calculate_expected_total(self, home_team: str, away_team: str, sport: str) -> Tuple[float, float]:
        """
        Calculate expected total points using pace and efficiency
        Returns: (expected_total, confidence_std_dev)
        """
        home_stats = self.get_team_stats(home_team, sport)
        away_stats = self.get_team_stats(away_team, sport)
        
        if not home_stats or not away_stats:
            return None, None
        
        # Expected scores for each team
        home_expected = (home_stats['offensive_rating'] + away_stats['defensive_rating']) / 2
        away_expected = (away_stats['offensive_rating'] + home_stats['defensive_rating']) / 2
        
        # Apply home advantage to home team only
        home_advantage = self.home_advantage.get(sport, 2.5)
        home_expected += home_advantage
        
        # Total = sum of expected scores
        expected_total = home_expected + away_expected
        
        # Adjust for pace (if both teams play fast or slow)
        avg_pace = (home_stats['pace'] + away_stats['pace']) / 2
        league_avg_pace = self._get_league_avg_pace(sport)
        if league_avg_pace:
            pace_adjustment = (avg_pace - league_avg_pace) * 0.1  # Moderate pace impact
            expected_total += pace_adjustment
        
        # Calculate variance
        total_variance = np.sqrt(
            home_stats['pace_std']**2 + 
            away_stats['pace_std']**2
        )
        
        return expected_total, total_variance
    
    def _get_league_avg_pace(self, sport: str) -> Optional[float]:
        """Get league average pace from recent games"""
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        result = cursor.execute('''
            SELECT AVG(home_score + away_score) as avg_total
            FROM games
            WHERE sport = ?
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
              AND status = 'final'
              AND game_date >= date('now', '-30 days')
        ''', (sport,)).fetchone()
        
        conn.close()
        
        return result['avg_total'] if result else None
    
    def calculate_confidence_score(self, margin_std: float, total_std: float, games_played: int) -> float:
        """
        Calculate confidence score (0-1) based on variance and sample size
        Lower variance + more games = higher confidence
        """
        # Normalize standard deviations (typical NBA std ~10-15 pts)
        norm_margin_std = margin_std / 15.0
        norm_total_std = total_std / 20.0
        
        # Sample size factor (10 games = full confidence)
        sample_factor = min(games_played / 10.0, 1.0)
        
        # Combined confidence
        variance_factor = 1.0 - (norm_margin_std + norm_total_std) / 2
        confidence = (variance_factor * 0.7 + sample_factor * 0.3)
        
        return max(0.1, min(1.0, confidence))
    
    def generate_odds(self, game_id: str, home_team: str, away_team: str, sport: str) -> Dict:
        """
        Generate complete odds package for a game
        Returns: Dict with spread, total, confidence, and distributions
        """
        # Check cache first
        cache_key = f"{game_id}_{home_team}_{away_team}_{sport}"
        if cache_key in self._odds_cache:
            cached_data, cache_time = self._odds_cache[cache_key]
            if datetime.now() - cache_time < self.cache_duration:
                logger.info(f"Using cached odds for {game_id}")
                return cached_data
        
        # Calculate spread
        expected_margin, margin_std = self.calculate_expected_margin(home_team, away_team, sport)
        if expected_margin is None:
            logger.warning(f"Could not calculate odds for {game_id} - insufficient data")
            return None
        
        # Calculate total
        expected_total, total_std = self.calculate_expected_total(home_team, away_team, sport)
        if expected_total is None:
            return None
        
        # Get team stats for confidence calculation
        home_stats = self.get_team_stats(home_team, sport)
        away_stats = self.get_team_stats(away_team, sport)
        avg_games = (home_stats['games_played'] + away_stats['games_played']) / 2
        
        # Calculate confidence
        confidence = self.calculate_confidence_score(margin_std, total_std, avg_games)
        
        # Round spread to nearest 0.5
        model_spread = round(expected_margin * 2) / 2
        model_total = round(expected_total * 2) / 2
        
        odds_data = {
            'game_id': game_id,
            'home_team': home_team,
            'away_team': away_team,
            'sport': sport,
            'model_spread': model_spread,
            'model_total': model_total,
            'confidence_score': round(confidence, 3),
            'implied_margin_distribution': round(margin_std, 2),
            'implied_total_distribution': round(total_std, 2),
            'generated_at': datetime.now().isoformat()
        }
        
        # Cache the result
        self._odds_cache[cache_key] = (odds_data, datetime.now())
        
        logger.info(f"Generated odds for {game_id}: Spread={model_spread}, Total={model_total}, Confidence={confidence:.2f}")
        
        return odds_data
    
    def get_upcoming_games_odds(self, sport: str, days_ahead: int = 7) -> List[Dict]:
        """
        Generate odds for all upcoming games in a sport
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()
        
        today = datetime.now().strftime('%Y-%m-%d')
        future_date = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
        
        upcoming_games = cursor.execute('''
            SELECT game_id, game_date, home_team_id, away_team_id
            FROM games
            WHERE sport = ?
              AND game_date >= ?
              AND game_date <= ?
              AND status IN ('scheduled', 'pre')
            ORDER BY game_date ASC
        ''', (sport, today, future_date)).fetchall()
        
        conn.close()
        
        odds_list = []
        for game in upcoming_games:
            odds = self.generate_odds(
                game['game_id'],
                game['home_team_id'],
                game['away_team_id'],
                sport
            )
            if odds:
                odds['game_date'] = game['game_date']
                odds_list.append(odds)
        
        return odds_list
    
    def calculate_edge(self, predicted_spread: float, model_spread: float, 
                       predicted_total: float, model_total: float,
                       confidence: float) -> Dict:
        """
        Calculate edge between predictions and model odds
        Positive edge means prediction is more favorable than model odds
        
        Returns: Dict with spread_edge, total_edge, and recommendation
        """
        # Spread edge (absolute difference, weighted by confidence)
        spread_edge = abs(predicted_spread - model_spread) * confidence
        
        # Total edge
        total_edge = abs(predicted_total - model_total) * confidence
        
        # Determine if there's significant edge (>3 points for spread, >5 for total)
        spread_threshold = 3.0
        total_threshold = 5.0
        
        edge_data = {
            'spread_edge': round(spread_edge, 2),
            'total_edge': round(total_edge, 2),
            'spread_direction': 'favor_home' if predicted_spread > model_spread else 'favor_away',
            'total_direction': 'over' if predicted_total > model_total else 'under',
            'has_spread_edge': spread_edge >= spread_threshold,
            'has_total_edge': total_edge >= total_threshold,
            'confidence': confidence,
            'recommendation': self._generate_recommendation(
                spread_edge, total_edge, spread_threshold, total_threshold
            )
        }
        
        return edge_data
    
    def _generate_recommendation(self, spread_edge: float, total_edge: float, 
                                 spread_threshold: float, total_threshold: float) -> str:
        """Generate betting recommendation based on edge"""
        if spread_edge >= spread_threshold and total_edge >= total_threshold:
            return "STRONG_EDGE_BOTH"
        elif spread_edge >= spread_threshold:
            return "EDGE_SPREAD"
        elif total_edge >= total_threshold:
            return "EDGE_TOTAL"
        else:
            return "NO_SIGNIFICANT_EDGE"
    
    def clear_cache(self):
        """Clear the odds cache"""
        self._odds_cache = {}
        logger.info("Odds cache cleared")


# REST API wrapper (Flask integration)
def create_odds_api_routes(app, odds_api: ProprietaryOddsAPI):
    """
    Add REST API routes to existing Flask app
    """
    from flask import jsonify, request
    
    @app.route('/api/odds/<sport>/upcoming', methods=['GET'])
    def get_upcoming_odds(sport):
        """Get model odds for upcoming games"""
        days = request.args.get('days', default=7, type=int)
        
        if sport.upper() not in ['NBA', 'NFL', 'NHL', 'MLB', 'NCAAB', 'NCAAF', 'WNBA']:
            return jsonify({'error': 'Invalid sport'}), 400
        
        odds_list = odds_api.get_upcoming_games_odds(sport.upper(), days)
        
        return jsonify({
            'sport': sport.upper(),
            'games': odds_list,
            'count': len(odds_list),
            'source': 'proprietary_model',
            'data_source': 'ESPN_public_API'
        })
    
    @app.route('/api/odds/game/<game_id>', methods=['GET'])
    def get_game_odds(game_id):
        """Get model odds for a specific game"""
        conn = odds_api.get_db_connection()
        cursor = conn.cursor()
        
        game = cursor.execute('''
            SELECT game_id, sport, home_team_id, away_team_id
            FROM games
            WHERE game_id = ?
        ''', (game_id,)).fetchone()
        
        conn.close()
        
        if not game:
            return jsonify({'error': 'Game not found'}), 404
        
        odds = odds_api.generate_odds(
            game['game_id'],
            game['home_team_id'],
            game['away_team_id'],
            game['sport']
        )
        
        if not odds:
            return jsonify({'error': 'Could not generate odds - insufficient data'}), 500
        
        return jsonify({
            'game': odds,
            'source': 'proprietary_model',
            'data_source': 'ESPN_public_API'
        })
    
    @app.route('/api/odds/edge', methods=['POST'])
    def calculate_prediction_edge():
        """Calculate edge between prediction and model odds"""
        data = request.json
        
        required_fields = ['game_id', 'predicted_spread', 'predicted_total']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Get model odds for the game
        conn = odds_api.get_db_connection()
        cursor = conn.cursor()
        
        game = cursor.execute('''
            SELECT game_id, sport, home_team_id, away_team_id
            FROM games
            WHERE game_id = ?
        ''', (data['game_id'],)).fetchone()
        
        conn.close()
        
        if not game:
            return jsonify({'error': 'Game not found'}), 404
        
        model_odds = odds_api.generate_odds(
            game['game_id'],
            game['home_team_id'],
            game['away_team_id'],
            game['sport']
        )
        
        if not model_odds:
            return jsonify({'error': 'Could not generate model odds'}), 500
        
        # Calculate edge
        edge = odds_api.calculate_edge(
            data['predicted_spread'],
            model_odds['model_spread'],
            data['predicted_total'],
            model_odds['model_total'],
            model_odds['confidence_score']
        )
        
        return jsonify({
            'game_id': data['game_id'],
            'model_odds': model_odds,
            'prediction': {
                'spread': data['predicted_spread'],
                'total': data['predicted_total']
            },
            'edge_analysis': edge
        })
    
    @app.route('/api/odds/cache/clear', methods=['POST'])
    def clear_odds_cache():
        """Clear the odds cache (admin endpoint)"""
        odds_api.clear_cache()
        return jsonify({'message': 'Cache cleared successfully'})


if __name__ == '__main__':
    # Test the odds API
    api = ProprietaryOddsAPI()
    
    # Test NBA odds generation
    print("\n=== Testing NBA Odds Generation ===")
    nba_odds = api.get_upcoming_games_odds('NBA', days_ahead=3)
    for odds in nba_odds[:3]:  # Show first 3 games
        print(f"\n{odds['away_team']} @ {odds['home_team']}")
        print(f"  Spread: {odds['home_team']} {odds['model_spread']:+.1f}")
        print(f"  Total: {odds['model_total']:.1f}")
        print(f"  Confidence: {odds['confidence_score']:.2f}")
    
    # Test edge calculation
    if nba_odds:
        test_game = nba_odds[0]
        print(f"\n=== Testing Edge Calculation ===")
        edge = api.calculate_edge(
            predicted_spread=test_game['model_spread'] + 3.5,  # Simulate different prediction
            model_spread=test_game['model_spread'],
            predicted_total=test_game['model_total'] + 8.0,
            model_total=test_game['model_total'],
            confidence=test_game['confidence_score']
        )
        print(f"Spread Edge: {edge['spread_edge']:.2f} ({edge['spread_direction']})")
        print(f"Total Edge: {edge['total_edge']:.2f} ({edge['total_direction']})")
        print(f"Recommendation: {edge['recommendation']}")
