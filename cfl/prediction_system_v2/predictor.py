"""
Advanced Sports Predictor (v2)
==============================

Main class that integrates all components:
1. Rating engines (Glicko-2, Margin, Elo→features)
2. Feature engineering
3. Base models (XGBoost, CatBoost, LightGBM, Poisson)
4. Stacked ensemble
5. Calibration
6. Evaluation

This is the single entry point for training and prediction.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from pathlib import Path
import pickle
import logging
from datetime import datetime

from .rating_engines import Glicko2Rating, MarginRating, EloFeatureGenerator, TrueSkillRating
from .feature_engineering import AdvancedFeatureEngineer
from .base_models import PoissonScoreModel, BaseModelTrainer
from .ensemble import StackedEnsemble
from .calibration import ProbabilityCalibrator
from .evaluation import ModelEvaluator, EvaluationResult

logger = logging.getLogger(__name__)


class AdvancedPredictor:
    """
    Advanced Sports Prediction System v2
    -------------------------------------
    
    Improvements over v1:
    - Elo demoted to feature generator (was standalone predictor)
    - Glicko-2 replaces Elo as primary rating (has uncertainty tracking)
    - Margin-aware ratings for goal/run differential
    - Stacked ensemble (learns dynamic weights, not static average)
    - Probability calibration (Platt/Isotonic)
    - Better evaluation (log loss, Brier, not just accuracy)
    
    Usage:
        predictor = AdvancedPredictor(sport='NHL')
        predictor.train(historical_games)
        predictions = predictor.predict(upcoming_games)
    """
    
    SPORT_CONFIG = {
        'NHL': {
            'is_low_scoring': True,  # Use Poisson model
            'home_advantage': 0.3,
            'typical_score': 3.0,
            'max_margin': 8,
        },
        'MLB': {
            'is_low_scoring': True,
            'home_advantage': 0.15,
            'typical_score': 4.5,
            'max_margin': 15,
        },
        'NFL': {
            'is_low_scoring': False,
            'home_advantage': 2.5,
            'typical_score': 23.0,
            'max_margin': 50,
        },
        'NBA': {
            'is_low_scoring': False,
            'home_advantage': 3.0,
            'typical_score': 110.0,
            'max_margin': 40,
        },
        'NCAAF': {
            'is_low_scoring': False,
            'home_advantage': 3.0,
            'typical_score': 28.0,
            'max_margin': 60,
        },
        'NCAAB': {
            'is_low_scoring': False,
            'home_advantage': 3.5,
            'typical_score': 70.0,
            'max_margin': 45,
        },
        'WNBA': {
            'is_low_scoring': False,
            'home_advantage': 2.0,
            'typical_score': 80.0,
            'max_margin': 35,
        },
    }
    
    def __init__(self, sport: str, model_dir: Optional[str] = None):
        """
        Initialize predictor for a specific sport
        
        sport: 'NHL', 'MLB', 'NFL', 'NBA', 'NCAAF', 'NCAAB', 'WNBA'
        model_dir: directory to save/load models
        """
        if sport not in self.SPORT_CONFIG:
            raise ValueError(f"Sport {sport} not supported. Options: {list(self.SPORT_CONFIG.keys())}")
        
        self.sport = sport
        self.config = self.SPORT_CONFIG[sport]
        self.model_dir = Path(model_dir) if model_dir else Path(f'models/{sport}_v2')
        
        # Initialize components
        self.glicko2 = Glicko2Rating()
        self.trueskill = TrueSkillRating()
        self.margin_rating = MarginRating(sport=sport)
        self.elo_features = EloFeatureGenerator(sport=sport)
        self.feature_engineer = AdvancedFeatureEngineer(sport)
        
        # Base models
        self.model_trainer = BaseModelTrainer(sport=sport)
        self.poisson_model = PoissonScoreModel(sport=sport) if self.config['is_low_scoring'] else None
        
        # Ensemble and calibration
        self.ensemble = StackedEnsemble(use_context_features=False)  # Simpler, more robust
        self.calibrator = ProbabilityCalibrator(method='auto')
        
        # Evaluation
        self.evaluator = ModelEvaluator()
        
        # State
        self.trained = False
        self.training_metrics: Dict = {}
        
    def train(self, games: pd.DataFrame, 
              validate: bool = True,
              save: bool = True) -> Dict[str, float]:
        """
        Train the full prediction system
        
        games DataFrame should have columns:
        - date: game date
        - home_team, away_team: team identifiers
        - home_score, away_score: final scores
        - (optional) home_odds, away_odds: betting odds
        
        Returns training metrics
        """
        logger.info(f"Training {self.sport} predictor on {len(games)} games")
        
        # Sort by date
        games = games.sort_values('date').reset_index(drop=True)
        
        # 1. Process games through rating systems
        logger.info("Processing ratings...")
        for _, game in games.iterrows():
            home, away = game['home_team'], game['away_team']
            home_score, away_score = game['home_score'], game['away_score']
            home_win = 1 if home_score > away_score else 0
            
            # Update all rating systems
            self.glicko2.update_ratings(home, away, home_win)
            # TrueSkill with margin of victory
            margin = abs(home_score - away_score)
            winner = home if home_score > away_score else away
            loser = away if home_score > away_score else home
            self.trueskill.update_with_margin(winner, loser, margin)
            self.margin_rating.update_ratings(home, away, home_score, away_score)
            self.elo_features.update_ratings(home, away, home_win)
        
        # 2. Generate features
        logger.info("Generating features...")
        X, y = self.feature_engineer.prepare_training_data_from_games(
            games, 
            self.glicko2,
            self.trueskill,
            self.margin_rating, 
            self.elo_features
        )
        
        logger.info(f"Feature matrix: {X.shape}")
        
        # 3. Train base models and get OOF predictions
        logger.info("Training base models...")
        base_oof_preds, base_models = self.model_trainer.train_all(X, y, return_oof=True)
        
        # Add Poisson model for low-scoring sports
        if self.poisson_model is not None:
            logger.info("Training Poisson score model...")
            self.poisson_model.fit(games)
            poisson_probs = self.poisson_model.predict_proba_batch(games)
            base_oof_preds['poisson'] = poisson_probs
        
        # 4. Train stacked ensemble on OOF predictions
        logger.info("Training stacked ensemble...")
        ensemble_metrics = self.ensemble.train(base_oof_preds, y, X)
        
        # 5. Get final ensemble predictions and calibrate
        logger.info("Calibrating probabilities...")
        ensemble_probs = self.ensemble.predict_proba(base_oof_preds, X)
        calibration_metrics = self.calibrator.fit(ensemble_probs, y)
        
        # 6. Final calibrated predictions
        calibrated_probs = self.calibrator.transform(ensemble_probs)
        
        # 7. Evaluate
        logger.info("Evaluating...")
        eval_result = self.evaluator.evaluate(y, calibrated_probs)
        self.evaluator.print_report(eval_result)
        
        # Compare individual models
        all_model_probs = {**base_oof_preds}
        all_model_probs['ensemble_raw'] = ensemble_probs
        all_model_probs['ensemble_calibrated'] = calibrated_probs
        
        comparison = self.evaluator.compare_models(y, all_model_probs)
        logger.info("\nModel Comparison:")
        logger.info(comparison.to_string())
        
        # Store metrics
        self.training_metrics = {
            'n_games': len(games),
            'n_features': X.shape[1],
            **ensemble_metrics,
            **calibration_metrics,
            'final_log_loss': eval_result.log_loss,
            'final_brier': eval_result.brier_score,
            'final_accuracy': eval_result.accuracy,
            'model_weights': self.ensemble.get_model_weights(),
        }
        
        self.trained = True
        
        # Save models
        if save:
            self.save()
        
        return self.training_metrics
    
    def predict(self, games: pd.DataFrame) -> pd.DataFrame:
        """
        Generate predictions for upcoming games
        
        games DataFrame should have:
        - home_team, away_team: team identifiers
        - date: game date
        - (optional) home_odds, away_odds for edge calculation
        
        Returns DataFrame with predictions
        """
        if not self.trained:
            raise ValueError("Model not trained yet. Call train() first.")
        
        results = []
        
        for _, game in games.iterrows():
            home, away = game['home_team'], game['away_team']
            
            # Get ratings
            home_glicko = self.glicko2.get_rating(home)
            away_glicko = self.glicko2.get_rating(away)
            
            home_ts = self.trueskill.get_rating(home)
            away_ts = self.trueskill.get_rating(away)
            
            home_margin = self.margin_rating.get_rating(home)
            away_margin = self.margin_rating.get_rating(away)
            
            # Generate features
            features = self.feature_engineer.generate_features_for_game(
                game, self.glicko2, self.trueskill, self.margin_rating, self.elo_features
            )
            
            X = pd.DataFrame([features])
            
            # Get base model predictions
            base_preds = {}
            for name, model in self.model_trainer.models.items():
                base_preds[name] = model.predict_proba(X)[:, 1]
            
            # Poisson model
            if self.poisson_model is not None:
                poisson_prob = self.poisson_model.get_win_probability(home, away)
                base_preds['poisson'] = np.array([poisson_prob])
            
            # Ensemble prediction (don't pass context features - use base model agreement only)
            ensemble_prob = self.ensemble.predict_proba(base_preds, None)[0]
            
            # Calibrate
            calibrated_prob = self.calibrator.transform(np.array([ensemble_prob]))[0]
            
            # Confidence score
            confidence = self.ensemble.get_confidence(base_preds)[0]
            
            # Edge vs market (if odds available)
            edge = None
            if 'home_odds' in game:
                implied_prob = self._odds_to_prob(game['home_odds'])
                edge = calibrated_prob - implied_prob
            
            # Model agreement
            model_probs = list(base_preds.values())
            agreement = np.mean([1 if p[0] > 0.5 else 0 for p in model_probs])
            
            # Calculate Glicko-2 win probability
            glicko2_prob, _, _ = self.glicko2.win_probability(home, away, self.config['home_advantage'] * 10)
            
            # Calculate TrueSkill win probability
            trueskill_prob = self.trueskill.win_probability(home, away)
            
            # Poisson probability (if available)
            poisson_prob = base_preds.get('poisson', [0.5])[0]
            
            # LightGBM probability
            lightgbm_prob = base_preds.get('lightgbm', [0.5])[0]
            
            result = {
                'home_team': home,
                'away_team': away,
                'date': game.get('date', ''),
                
                # Main prediction (calibrated ensemble)
                'home_win_prob': round(calibrated_prob, 4),
                'away_win_prob': round(1 - calibrated_prob, 4),
                'predicted_winner': home if calibrated_prob > 0.5 else away,
                
                # Confidence metrics
                'confidence': round(confidence, 3),
                'model_agreement': round(agreement, 2),
                
                # Edge (if odds available)
                'edge_vs_market': round(edge, 4) if edge is not None else None,
                
                # ===== INDIVIDUAL MODEL PROBABILITIES =====
                # Rating-based models
                'glicko2_prob': round(glicko2_prob, 4),
                'trueskill_prob': round(trueskill_prob, 4),
                'poisson_prob': round(poisson_prob, 4) if isinstance(poisson_prob, float) else round(float(poisson_prob), 4),
                
                # ML models
                'xgboost_prob': round(base_preds.get('xgboost', [0.5])[0], 4),
                'catboost_prob': round(base_preds.get('catboost', [0.5])[0], 4),
                'lightgbm_prob': round(lightgbm_prob, 4) if isinstance(lightgbm_prob, float) else round(float(lightgbm_prob), 4),
                
                # Ensemble
                'raw_ensemble_prob': round(ensemble_prob, 4),
                
                # ===== RATINGS =====
                # Glicko-2 ratings
                'home_glicko2': round(home_glicko.mu, 1),
                'away_glicko2': round(away_glicko.mu, 1),
                'home_glicko2_uncertainty': round(home_glicko.sigma, 1),
                'away_glicko2_uncertainty': round(away_glicko.sigma, 1),
                
                # TrueSkill ratings
                'home_trueskill_mu': round(home_ts[0], 2),
                'away_trueskill_mu': round(away_ts[0], 2),
                'home_trueskill_sigma': round(home_ts[1], 2),
                'away_trueskill_sigma': round(away_ts[1], 2),
            }
            
            # Add score predictions for low-scoring sports
            if self.poisson_model is not None:
                home_exp, away_exp = self.poisson_model.predict_score(home, away)
                result['expected_home_score'] = round(home_exp, 2)
                result['expected_away_score'] = round(away_exp, 2)
                result['expected_total'] = round(home_exp + away_exp, 2)
            
            results.append(result)
        
        return pd.DataFrame(results)
    
    def _odds_to_prob(self, american_odds: float) -> float:
        """Convert American odds to implied probability"""
        if american_odds > 0:
            return 100 / (american_odds + 100)
        else:
            return -american_odds / (-american_odds + 100)
    
    def save(self):
        """Save all model components"""
        self.model_dir.mkdir(parents=True, exist_ok=True)
        
        # Save each component
        self.glicko2.save(str(self.model_dir / 'glicko2.pkl'))
        self.trueskill.save(str(self.model_dir / 'trueskill.pkl'))
        self.margin_rating.save(str(self.model_dir / 'margin_rating.pkl'))
        self.elo_features.save(str(self.model_dir / 'elo_features.pkl'))
        self.model_trainer.save(str(self.model_dir / 'base_models.pkl'))
        self.ensemble.save(str(self.model_dir / 'ensemble.pkl'))
        self.calibrator.save(str(self.model_dir / 'calibrator.pkl'))
        
        if self.poisson_model is not None:
            self.poisson_model.save(str(self.model_dir / 'poisson.pkl'))
        
        # Save training metrics
        import json
        with open(self.model_dir / 'training_metrics.json', 'w') as f:
            # Convert numpy types to native Python
            metrics = {}
            for k, v in self.training_metrics.items():
                if isinstance(v, (np.floating, np.integer)):
                    metrics[k] = float(v)
                elif isinstance(v, dict):
                    metrics[k] = {str(kk): float(vv) if isinstance(vv, (np.floating, np.integer)) else vv 
                                  for kk, vv in v.items()}
                else:
                    metrics[k] = v
            json.dump(metrics, f, indent=2)
        
        logger.info(f"Saved all models to {self.model_dir}")
    
    @classmethod
    def load(cls, sport: str, model_dir: str) -> 'AdvancedPredictor':
        """Load a trained predictor"""
        predictor = cls(sport, model_dir)
        model_dir = Path(model_dir)
        
        predictor.glicko2 = Glicko2Rating.load(str(model_dir / 'glicko2.pkl'))
        try:
            predictor.trueskill = TrueSkillRating.load(str(model_dir / 'trueskill.pkl'))
        except FileNotFoundError:
            logger.warning("TrueSkill model not found, initializing new one")
            predictor.trueskill = TrueSkillRating()
        predictor.margin_rating = MarginRating.load(str(model_dir / 'margin_rating.pkl'))
        predictor.elo_features = EloFeatureGenerator.load(str(model_dir / 'elo_features.pkl'))
        predictor.model_trainer = BaseModelTrainer.load(str(model_dir / 'base_models.pkl'))
        predictor.ensemble = StackedEnsemble.load(str(model_dir / 'ensemble.pkl'))
        predictor.calibrator = ProbabilityCalibrator.load(str(model_dir / 'calibrator.pkl'))
        
        if predictor.config['is_low_scoring']:
            try:
                predictor.poisson_model = PoissonScoreModel.load(str(model_dir / 'poisson.pkl'))
            except FileNotFoundError:
                logger.warning("Poisson model not found, skipping")
        
        # Load metrics
        import json
        try:
            with open(model_dir / 'training_metrics.json', 'r') as f:
                predictor.training_metrics = json.load(f)
        except FileNotFoundError:
            pass
        
        predictor.trained = True
        logger.info(f"Loaded {sport} predictor from {model_dir}")
        
        return predictor
    
    def get_team_ratings(self) -> pd.DataFrame:
        """Get current ratings for all teams"""
        teams = set(self.glicko2.ratings.keys())
        
        data = []
        for team in teams:
            glicko = self.glicko2.get_rating(team)
            ts_mu, ts_sigma = self.trueskill.get_rating(team)
            margin_overall, margin_off, margin_def = self.margin_rating.get_rating(team)
            elo = self.elo_features.get_rating(team)
            
            data.append({
                'team': team,
                'glicko2_rating': round(glicko.mu, 1),
                'glicko2_sigma': round(glicko.sigma, 1),
                'glicko2_phi': round(glicko.phi, 3),
                'trueskill_mu': round(ts_mu, 2),
                'trueskill_sigma': round(ts_sigma, 2),
                'trueskill_conservative': round(ts_mu - 3 * ts_sigma, 2),
                'margin_rating': round(margin_overall, 1),
                'margin_offense': round(margin_off, 2),
                'margin_defense': round(margin_def, 2),
                'elo': round(elo, 1),
            })
        
        df = pd.DataFrame(data)
        df = df.sort_values('glicko2_rating', ascending=False)
        
        return df


def train_sport_predictor(sport: str, games: pd.DataFrame, 
                          model_dir: Optional[str] = None) -> AdvancedPredictor:
    """
    Convenience function to train a predictor
    
    Example:
        import pandas as pd
        games = pd.read_csv('nhl_games.csv')
        predictor = train_sport_predictor('NHL', games)
        
        upcoming = pd.DataFrame([{
            'home_team': 'Boston Bruins',
            'away_team': 'Toronto Maple Leafs',
            'date': '2025-02-14'
        }])
        predictions = predictor.predict(upcoming)
    """
    predictor = AdvancedPredictor(sport, model_dir)
    predictor.train(games)
    return predictor
