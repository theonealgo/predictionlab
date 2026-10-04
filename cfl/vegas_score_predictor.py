#!/usr/bin/env python3
"""
Vegas-Style Score Predictor
===========================
Uses Vegas totals and team power ratings to predict scores
Similar to how sportsbooks actually calculate expected scores
"""
import sqlite3
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VegasScorePredictor:
    """
    Predicts scores using Vegas methodology:
    1. Start with Vegas total (they're accurate on pace/scoring environment)
    2. Use team power ratings to split the total
    3. Apply home field advantage
    """
    
    # Home field advantage in points by sport
    HOME_ADVANTAGE = {
        'NBA': 3.0,
        'NFL': 2.5,
        'NHL': 0.3,
        'NCAAF': 3.0,
        'NCAAB': 3.0,
        'MLB': 0.15
    }
    
    # League average totals (fallback if no Vegas line)
    LEAGUE_AVG_TOTALS = {
        'NBA': 220.0,
        'NFL': 45.0,
        'NHL': 6.0,
        'NCAAF': 55.0,
        'NCAAB': 140.0,
        'MLB': 9.0
    }
    
    def __init__(self, db_path='sports_predictions_original.db'):
        self.db_path = db_path
    
    def get_team_power_rating(self, team_name, sport):
        """
        Calculate team power rating from recent performance
        Uses offensive and defensive efficiency
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Get team's recent games (last 10)
        cursor.execute("""
            SELECT 
                CASE 
                    WHEN home_team_id = ? THEN home_score 
                    ELSE away_score 
                END as team_score,
                CASE 
                    WHEN home_team_id = ? THEN away_score 
                    ELSE home_score 
                END as opp_score,
                CASE 
                    WHEN home_team_id = ? THEN 1 
                    ELSE 0 
                END as is_home
            FROM games
            WHERE sport = ? AND status = 'final'
            AND (home_team_id = ? OR away_team_id = ?)
            AND home_score IS NOT NULL
            ORDER BY game_date DESC
            LIMIT 10
        """, (team_name, team_name, team_name, sport, team_name, team_name))
        
        games = cursor.fetchall()
        conn.close()
        
        if not games:
            return 0.0  # Neutral rating
        
        # Calculate offensive and defensive ratings
        total_scored = sum(g[0] for g in games)
        total_allowed = sum(g[1] for g in games)
        num_games = len(games)
        
        avg_scored = total_scored / num_games
        avg_allowed = total_allowed / num_games
        
        # Get league averages for comparison
        league_avg = self.LEAGUE_AVG_TOTALS.get(sport, 100) / 2
        
        # Power rating = how much better/worse than league average
        # Positive = better offense/defense, negative = worse
        offensive_rating = avg_scored - league_avg
        defensive_rating = league_avg - avg_allowed  # Lower allowed = better
        
        # Overall rating (weight offense and defense equally)
        power_rating = (offensive_rating + defensive_rating) / 2
        
        return power_rating
    
    def predict_score_vegas_method(self, home_team, away_team, sport, vegas_total=None, vegas_spread=None):
        """
        Predict scores using Vegas methodology:
        
        Step 1: Use Vegas total (or league average if unavailable)
        Step 2: Calculate team power ratings
        Step 3: Distribute total based on power differential + home advantage
        Step 4: Adjust to match Vegas spread if available
        
        Returns: (home_score, away_score, implied_spread, implied_total)
        """
        
        # Step 1: Get the total
        if vegas_total is not None:
            total = vegas_total
        else:
            total = self.LEAGUE_AVG_TOTALS.get(sport, 100)
        
        # Step 2: Get power ratings
        home_power = self.get_team_power_rating(home_team, sport)
        away_power = self.get_team_power_rating(away_team, sport)
        home_adv = self.HOME_ADVANTAGE.get(sport, 0)
        
        # Step 3: Calculate expected point differential
        # Power diff tells us how much better one team is
        # Add home advantage
        expected_margin = (home_power - away_power) + home_adv
        
        # If Vegas spread is available, use it (it's more accurate)
        if vegas_spread is not None:
            # Vegas spread is from home team perspective (negative = home favored)
            expected_margin = -vegas_spread
        
        # Step 4: Distribute total around the margin
        # home_score - away_score = expected_margin
        # home_score + away_score = total
        # Solving: home_score = (total + expected_margin) / 2
        home_score = (total + expected_margin) / 2
        away_score = (total - expected_margin) / 2
        
        # Calculate implied spread and total
        implied_spread = home_score - away_score
        implied_total = home_score + away_score
        
        return round(home_score, 1), round(away_score, 1), round(implied_spread, 1), round(implied_total, 1)
    
    def generate_predictions(self, sport, days_ahead=7):
        """Generate score predictions for upcoming games"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                g.game_id,
                g.game_date,
                g.home_team_id,
                g.away_team_id,
                bl.spread,
                bl.total
            FROM games g
            LEFT JOIN betting_lines bl ON g.game_id = bl.game_id
            WHERE g.sport = ?
            AND g.status != 'final'
            AND date(g.game_date) >= date('now')
            AND date(g.game_date) <= date('now', '+' || ? || ' days')
            ORDER BY g.game_date
        """, (sport, days_ahead))
        
        games = cursor.fetchall()
        conn.close()
        
        predictions = []
        for game_id, game_date, home_team, away_team, vegas_spread, vegas_total in games:
            home_score, away_score, spread, total = self.predict_score_vegas_method(
                home_team, away_team, sport, vegas_total, vegas_spread
            )
            
            predictions.append({
                'game_id': game_id,
                'game_date': game_date,
                'home_team': home_team,
                'away_team': away_team,
                'predicted_home_score': home_score,
                'predicted_away_score': away_score,
                'predicted_spread': spread,
                'predicted_total': total,
                'vegas_spread': vegas_spread,
                'vegas_total': vegas_total
            })
        
        return predictions


if __name__ == '__main__':
    predictor = VegasScorePredictor()
    
    for sport in ['NBA', 'NHL', 'NFL']:
        print(f"\n{'='*60}")
        print(f"{sport} VEGAS-STYLE SCORE PREDICTIONS")
        print(f"{'='*60}\n")
        
        predictions = predictor.generate_predictions(sport, days_ahead=3)
        
        for pred in predictions[:5]:
            print(f"{pred['away_team']} @ {pred['home_team']}")
            print(f"  Predicted Score: {pred['predicted_away_score']:.1f} - {pred['predicted_home_score']:.1f}")
            print(f"  Predicted Spread: {pred['predicted_spread']:+.1f} (Vegas: {pred['vegas_spread'] if pred['vegas_spread'] else 'N/A'})")
            print(f"  Predicted Total: {pred['predicted_total']:.1f} (Vegas: {pred['vegas_total'] if pred['vegas_total'] else 'N/A'})")
            print()
