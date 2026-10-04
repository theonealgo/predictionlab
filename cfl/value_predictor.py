#!/usr/bin/env python3
"""
Value-Based Prediction System
Integrates: TheOdds API, Situational Analysis, Line Shopping, Value Detection
"""

import sqlite3
import logging
from datetime import datetime
from theodds_api import TheOddsAPI
from situational_analysis import SituationalAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ValuePredictor:
    """
    Enhanced predictor that finds VALUE bets, not just favorites
    
    Key changes from old system:
    1. Compares model probability to market odds
    2. Only recommends bets with +EV (expected value)
    3. Incorporates situational factors (rest, travel, form)
    4. Shows best available lines (line shopping)
    """
    
    def __init__(self, db_path='sports_predictions_original.db'):
        self.db_path = db_path
        self.odds_api = TheOddsAPI()
        self.situational = SituationalAnalyzer(db_path)
        self.MIN_EDGE = 5.0  # Minimum 5% edge to recommend bet
    
    def enhance_predictions(self, sport):
        """
        Enhance existing predictions with:
        - Live odds data
        - Situational analysis
        - Value calculation
        - Betting recommendations
        
        Returns list of enhanced predictions with betting edges
        """
        logger.info(f"Enhancing {sport} predictions with value analysis...")
        
        # Get games from database
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # Get games from 1 month ago to 1 month ahead
        games = conn.execute('''
            SELECT g.*, 
                   p.elo_home_prob, p.xgboost_home_prob, 
                   p.catboost_home_prob, p.meta_home_prob,
                   p.win_probability
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id
            WHERE g.sport = ?
              AND date(g.game_date) >= date('now', '-30 days')
              AND date(g.game_date) <= date('now', '+30 days')
            ORDER BY g.game_date DESC
        ''', (sport,)).fetchall()
        
        conn.close()
        
        if not games:
            logger.warning(f"No upcoming {sport} games found")
            return []
        
        # Get live odds
        odds_map = self._fetch_odds_map(sport)
        
        enhanced = []
        
        for game in games:
            home = game['home_team_id']
            away = game['away_team_id']
            game_date = game['game_date']
            
            # Get model probability (use meta/ensemble if available)
            if sport == 'NBA':
                model_home_prob = game['win_probability'] or game['elo_home_prob']
            else:
                model_home_prob = game['meta_home_prob'] or game['elo_home_prob']
            
            if not model_home_prob:
                continue  # Skip games without predictions
            
            # Get situational factors
            situational = self.situational.analyze_game(sport, home, away, game_date)
            
            # Adjust model probability with situational edge
            adjusted_home_prob = model_home_prob + situational['situational_edge']
            adjusted_home_prob = max(0.05, min(0.95, adjusted_home_prob))  # Cap at 5-95%
            adjusted_away_prob = 1 - adjusted_home_prob
            
            # Get market odds
            matchup_key = f"{away}@{home}"
            odds_data = odds_map.get(matchup_key, {})
            
            market_home_prob = odds_data.get('home_implied_prob', adjusted_home_prob)
            market_away_prob = odds_data.get('away_implied_prob', adjusted_away_prob)
            
            # Calculate edges
            home_edge = self.odds_api.calculate_edge(adjusted_home_prob, market_home_prob)
            away_edge = self.odds_api.calculate_edge(adjusted_away_prob, market_away_prob)
            
            # Determine recommendation
            recommendation = None
            bet_team = None
            edge = 0
            confidence = 'NONE'
            
            if home_edge >= self.MIN_EDGE:
                recommendation = 'BET_HOME'
                bet_team = home
                edge = home_edge
            elif away_edge >= self.MIN_EDGE:
                recommendation = 'BET_AWAY'
                bet_team = away
                edge = away_edge
            else:
                recommendation = 'PASS'
            
            # Confidence levels
            if edge >= 15:
                confidence = 'HIGH'
            elif edge >= 10:
                confidence = 'MEDIUM'
            elif edge >= self.MIN_EDGE:
                confidence = 'LOW'
            
            enhanced.append({
                'game_id': game['game_id'],
                'game_date': game_date,
                'home_team': home,
                'away_team': away,
                # Individual model probabilities
                'elo_home_prob': round(game['elo_home_prob'], 3) if game['elo_home_prob'] else round(model_home_prob, 3),
                'xgb_home_prob': round(game['xgboost_home_prob'], 3) if game['xgboost_home_prob'] else round(model_home_prob, 3),
                'cat_home_prob': round(game['catboost_home_prob'], 3) if game['catboost_home_prob'] else round(model_home_prob, 3),
                'meta_home_prob': round(game['meta_home_prob'], 3) if game['meta_home_prob'] else round(model_home_prob, 3),
                'model_home_prob': round(model_home_prob, 3),
                'adjusted_home_prob': round(adjusted_home_prob, 3),
                'market_home_prob': round(market_home_prob, 3),
                'situational_edge': round(situational['situational_edge'], 3),
                'home_rest_days': situational['home_rest_days'],
                'away_rest_days': situational['away_rest_days'],
                'home_back_to_back': situational['home_back_to_back'],
                'away_back_to_back': situational['away_back_to_back'],
                'home_recent_form': round(situational['home_recent_form'], 2),
                'away_recent_form': round(situational['away_recent_form'], 2),
                'best_home_ml': odds_data.get('best_home_ml'),
                'best_away_ml': odds_data.get('best_away_ml'),
                'recommendation': recommendation,
                'bet_team': bet_team,
                'edge': round(edge, 1),
                'confidence': confidence,
                'bookmaker_count': odds_data.get('bookmaker_count', 0)
            })
        
        # Sort by edge (highest first)
        enhanced.sort(key=lambda x: x['edge'], reverse=True)
        
        logger.info(f"Enhanced {len(enhanced)} games. Found {len([x for x in enhanced if x['recommendation'] != 'PASS'])} value bets.")
        
        return enhanced
    
    def _fetch_odds_map(self, sport):
        """Fetch odds and create matchup key map"""
        odds_map = {}
        
        sport_key_map = {
            'NHL': 'icehockey_nhl',
            'NBA': 'basketball_nba',
            'NFL': 'americanfootball_nfl'
        }
        
        sport_key = sport_key_map.get(sport)
        if not sport_key:
            return odds_map
        
        games = self.odds_api.get_odds(sport_key)
        
        for game in games:
            parsed = self.odds_api.parse_odds_for_game(game)
            # Create matchup key (away@home)
            key = f"{parsed['away_team']}@{parsed['home_team']}"
            odds_map[key] = parsed
        
        return odds_map
    
    def print_recommendations(self, enhanced_predictions, sport):
        """Pretty print betting recommendations"""
        print(f"\n{'='*80}")
        print(f"{sport} VALUE BETTING RECOMMENDATIONS")
        print(f"{'='*80}\n")
        
        value_bets = [p for p in enhanced_predictions if p['recommendation'] != 'PASS']
        
        if not value_bets:
            print("❌ No value bets found. All games are PASS.")
            print("(Market is efficiently priced or model lacks edge)\n")
            return
        
        for pred in value_bets:
            print(f"🎯 {pred['away_team']} @ {pred['home_team']} ({pred['game_date']})")
            print(f"   Recommendation: {pred['recommendation']} - {pred['bet_team']}")
            print(f"   Edge: {pred['edge']:.1f}% | Confidence: {pred['confidence']}")
            print(f"   Model: {pred['adjusted_home_prob']:.1%} vs Market: {pred['market_home_prob']:.1%}")
            
            if pred['bet_team'] == pred['home_team']:
                print(f"   Best Line: {pred['best_home_ml']} (Home)")
            else:
                print(f"   Best Line: {pred['best_away_ml']} (Away)")
            
            # Situational factors
            if pred['home_back_to_back'] or pred['away_back_to_back']:
                if pred['away_back_to_back']:
                    print(f"   ⚠️  Away team on BACK-TO-BACK (fatigue factor)")
                if pred['home_back_to_back']:
                    print(f"   ⚠️  Home team on BACK-TO-BACK (fatigue factor)")
            
            print(f"   Rest: Home {pred['home_rest_days']}d, Away {pred['away_rest_days']}d")
            print(f"   Form: Home {pred['home_recent_form']:.1%}, Away {pred['away_recent_form']:.1%}")
            print()
        
        print(f"{'='*80}\n")


if __name__ == '__main__':
    # Test the value predictor
    predictor = ValuePredictor()
    
    print("\n🏒 NHL VALUE ANALYSIS")
    nhl_enhanced = predictor.enhance_predictions('NHL')
    predictor.print_recommendations(nhl_enhanced, 'NHL')
