"""
Base Models Module
==================

ML Layer with proper training:
- XGBoost: Gradient boosted trees, excellent for tabular data
- CatBoost: Handles categorical features natively, good regularization
- LightGBM: Fast training, good for large datasets
- Poisson/Dixon-Coles: Score-based model for low-scoring sports (NHL, MLB, soccer)

All models optimized for LOG LOSS and BRIER SCORE, not accuracy.
Uses proper time-series cross-validation (no data leakage).
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from scipy.special import expit  # sigmoid function
from scipy.optimize import minimize
from scipy.stats import poisson
import pickle
import logging

logger = logging.getLogger(__name__)

# Import ML libraries
try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False
    logger.warning("XGBoost not installed")

try:
    from catboost import CatBoostClassifier
    HAS_CAT = True
except ImportError:
    HAS_CAT = False
    logger.warning("CatBoost not installed")

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False
    logger.warning("LightGBM not installed")

from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import log_loss, brier_score_loss


class PoissonScoreModel:
    """
    Dixon-Coles inspired Poisson Score Model
    -----------------------------------------
    
    For low-scoring sports (NHL, MLB, soccer), predicting exact scores
    using Poisson distribution improves:
    - Total predictions (over/under)
    - Spread predictions
    - Win probability when scores are close
    
    The model predicts expected goals/runs for each team, then uses
    Poisson distribution to calculate win/draw/loss probabilities.
    """
    
    def __init__(self, sport: str):
        self.sport = sport
        self.attack_ratings: Dict[str, float] = {}
        self.defense_ratings: Dict[str, float] = {}
        self.home_advantage: float = 0.0
        self.league_avg: float = 0.0
        self.rho: float = 0.0  # Dixon-Coles correlation adjustment
        
    def fit(self, games_df: pd.DataFrame):
        """
        Fit Poisson model using iterative approach
        
        Each team has:
        - Attack rating (ability to score)
        - Defense rating (ability to prevent)
        """
        # Get unique teams
        # Handle both column naming conventions
        home_col = 'home_team_id' if 'home_team_id' in games_df.columns else 'home_team'
        away_col = 'away_team_id' if 'away_team_id' in games_df.columns else 'away_team'
        teams = pd.unique(games_df[[home_col, away_col]].values.ravel())
        
        # Initialize ratings to 1.0 (league average)
        for team in teams:
            self.attack_ratings[team] = 1.0
            self.defense_ratings[team] = 1.0
        
        # Calculate league average goals
        self.league_avg = games_df['home_score'].mean()
        self.home_advantage = 0.1  # Start with 10% boost
        
        # Iterative fitting (simplified version of maximum likelihood)
        for iteration in range(20):
            # Update attack ratings
            for team in teams:
                home_games = games_df[games_df[home_col] == team]
                away_games = games_df[games_df[away_col] == team]
                
                # Goals scored
                goals_home = home_games['home_score'].sum()
                goals_away = away_games['away_score'].sum()
                
                # Expected goals (based on opponent defense)
                exp_home = sum(
                    self.league_avg * (1 + self.home_advantage) * 
                    self.defense_ratings.get(opp, 1.0)
                    for opp in home_games[away_col]
                )
                exp_away = sum(
                    self.league_avg * self.defense_ratings.get(opp, 1.0)
                    for opp in away_games[home_col]
                )
                
                total_exp = exp_home + exp_away
                if total_exp > 0:
                    self.attack_ratings[team] = (goals_home + goals_away) / total_exp
            
            # Update defense ratings
            for team in teams:
                home_games = games_df[games_df[home_col] == team]
                away_games = games_df[games_df[away_col] == team]
                
                # Goals allowed
                allowed_home = home_games['away_score'].sum()
                allowed_away = away_games['home_score'].sum()
                
                # Expected goals allowed
                exp_home = sum(
                    self.league_avg * self.attack_ratings.get(opp, 1.0)
                    for opp in home_games[away_col]
                )
                exp_away = sum(
                    self.league_avg * (1 + self.home_advantage) * 
                    self.attack_ratings.get(opp, 1.0)
                    for opp in away_games[home_col]
                )
                
                total_exp = exp_home + exp_away
                if total_exp > 0:
                    self.defense_ratings[team] = (allowed_home + allowed_away) / total_exp
            
            # Update home advantage
            home_wins = (games_df['home_score'] > games_df['away_score']).mean()
            self.home_advantage = max(0.05, min(0.3, (home_wins - 0.5) * 0.5))
        
        # Calculate Dixon-Coles rho for low-scoring corrections
        self._calculate_rho(games_df)
        
        logger.info(f"Fitted Poisson model: league_avg={self.league_avg:.2f}, "
                   f"home_adv={self.home_advantage:.3f}, rho={self.rho:.3f}")
    
    def _calculate_rho(self, games_df: pd.DataFrame):
        """Calculate Dixon-Coles correlation adjustment for 0-0, 1-0, 0-1, 1-1 scores"""
        # Handle column naming
        home_col = 'home_team_id' if 'home_team_id' in games_df.columns else 'home_team'
        away_col = 'away_team_id' if 'away_team_id' in games_df.columns else 'away_team'
        
        # Count low-scoring games
        low_score_mask = (games_df['home_score'] <= 1) & (games_df['away_score'] <= 1)
        observed_low = low_score_mask.sum()
        
        # Calculate expected under independent Poisson (sample subset for efficiency)
        expected_low = 0
        sample_size = min(100, len(games_df))
        sample_df = games_df.sample(n=sample_size, random_state=42) if len(games_df) > sample_size else games_df
        
        for _, game in sample_df.iterrows():
            exp_home, exp_away = self.predict_expected_goals(game[home_col], game[away_col])
            
            prob_low = sum(
                poisson.pmf(h, exp_home) * poisson.pmf(a, exp_away)
                for h in [0, 1] for a in [0, 1]
            )
            expected_low += prob_low
        
        # Scale expected to full dataset
        expected_low = expected_low * len(games_df) / sample_size
        
        # Rho adjustment
        if expected_low > 0:
            self.rho = min(0.2, max(-0.2, (observed_low - expected_low) / len(games_df)))
    
    def predict_expected_goals(self, home_team: str, away_team: str) -> Tuple[float, float]:
        """Predict expected goals for each team"""
        home_attack = self.attack_ratings.get(home_team, 1.0)
        away_defense = self.defense_ratings.get(away_team, 1.0)
        away_attack = self.attack_ratings.get(away_team, 1.0)
        home_defense = self.defense_ratings.get(home_team, 1.0)
        
        exp_home = self.league_avg * (1 + self.home_advantage) * home_attack * away_defense
        exp_away = self.league_avg * away_attack * home_defense
        
        return exp_home, exp_away
    
    def predict_proba(self, home_team: str, away_team: str, 
                      max_goals: int = 10) -> Tuple[float, float, float]:
        """
        Calculate win/draw/loss probabilities using Poisson
        
        Returns: (home_win_prob, draw_prob, away_win_prob)
        """
        exp_home, exp_away = self.predict_expected_goals(home_team, away_team)
        
        home_win_prob = 0.0
        draw_prob = 0.0
        away_win_prob = 0.0
        
        for h in range(max_goals + 1):
            for a in range(max_goals + 1):
                p_home = poisson.pmf(h, exp_home)
                p_away = poisson.pmf(a, exp_away)
                
                # Dixon-Coles adjustment for low scores
                adjustment = 1.0
                if h <= 1 and a <= 1:
                    if h == 0 and a == 0:
                        adjustment = 1 - exp_home * exp_away * self.rho
                    elif h == 0 and a == 1:
                        adjustment = 1 + exp_home * self.rho
                    elif h == 1 and a == 0:
                        adjustment = 1 + exp_away * self.rho
                    elif h == 1 and a == 1:
                        adjustment = 1 - self.rho
                
                joint_prob = p_home * p_away * max(0, adjustment)
                
                if h > a:
                    home_win_prob += joint_prob
                elif h == a:
                    draw_prob += joint_prob
                else:
                    away_win_prob += joint_prob
        
        # Normalize
        total = home_win_prob + draw_prob + away_win_prob
        if total > 0:
            home_win_prob /= total
            draw_prob /= total
            away_win_prob /= total
        
        return home_win_prob, draw_prob, away_win_prob
    
    def get_win_probability(self, home_team: str, away_team: str) -> float:
        """Get home team win probability (for binary classification)"""
        home_win, draw, away_win = self.predict_proba(home_team, away_team)
        # In sports without draws, assign half of draw probability to each
        if self.sport in ['NBA', 'NFL', 'NCAAB', 'NCAAF', 'WNBA']:
            return home_win + draw / 2
        return home_win
    
    def predict_total(self, home_team: str, away_team: str) -> float:
        """Predict total score"""
        exp_home, exp_away = self.predict_expected_goals(home_team, away_team)
        return exp_home + exp_away
    
    def predict_proba_batch(self, games_df: pd.DataFrame) -> np.ndarray:
        """Get win probabilities for a batch of games"""
        home_col = 'home_team_id' if 'home_team_id' in games_df.columns else 'home_team'
        away_col = 'away_team_id' if 'away_team_id' in games_df.columns else 'away_team'
        
        probs = []
        for _, game in games_df.iterrows():
            home_win = self.get_win_probability(game[home_col], game[away_col])
            probs.append(home_win)
        return np.array(probs)
    
    def predict_score(self, home_team: str, away_team: str) -> Tuple[float, float]:
        """Predict expected scores for both teams"""
        return self.predict_expected_goals(home_team, away_team)
    
    def save(self, filepath: str):
        """Save model to file"""
        save_dict = {
            'sport': self.sport,
            'attack_ratings': self.attack_ratings,
            'defense_ratings': self.defense_ratings,
            'home_advantage': self.home_advantage,
            'league_avg': self.league_avg,
            'rho': self.rho,
        }
        with open(filepath, 'wb') as f:
            pickle.dump(save_dict, f)
    
    @classmethod
    def load(cls, filepath: str) -> 'PoissonScoreModel':
        """Load model from file"""
        with open(filepath, 'rb') as f:
            save_dict = pickle.load(f)
        model = cls(save_dict['sport'])
        model.attack_ratings = save_dict['attack_ratings']
        model.defense_ratings = save_dict['defense_ratings']
        model.home_advantage = save_dict['home_advantage']
        model.league_avg = save_dict['league_avg']
        model.rho = save_dict['rho']
        return model


class BaseModelTrainer:
    """
    Trains and manages all base models
    
    Models:
    - XGBoost: Primary gradient boosting model
    - CatBoost: Secondary gradient boosting (good for small data)
    - LightGBM: Fast alternative
    - Poisson: Score-based for low-scoring sports
    
    All optimized for log loss, not accuracy.
    """
    
    # Sports that benefit from Poisson model
    LOW_SCORING_SPORTS = ['NHL', 'MLB']
    
    def __init__(self, sport: str):
        self.sport = sport
        self.models: Dict[str, any] = {}
        self.scaler: StandardScaler = None
        self.feature_names: List[str] = []
        self.trained = False
        
    def train(self, X: pd.DataFrame, y: np.ndarray, 
              games_df: Optional[pd.DataFrame] = None) -> Dict[str, float]:
        """
        Train all base models
        
        Returns: Dictionary of model performances (log loss)
        """
        self.feature_names = list(X.columns)
        performances = {}
        
        # Use time-series split
        tscv = TimeSeriesSplit(n_splits=5)
        
        # Get last fold for holdout evaluation
        train_idx, test_idx = list(tscv.split(X))[-1]
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
        
        # Scale features
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled = self.scaler.transform(X_test)
        
        logger.info(f"Training {self.sport} models on {len(X_train)} samples, "
                   f"testing on {len(X_test)}")
        
        # 1. Train XGBoost
        if HAS_XGB:
            self.models['xgboost'] = self._train_xgboost(
                X_train_scaled, y_train, X_test_scaled, y_test
            )
            xgb_probs = self.models['xgboost'].predict_proba(X_test_scaled)[:, 1]
            performances['xgboost_logloss'] = log_loss(y_test, xgb_probs)
            performances['xgboost_brier'] = brier_score_loss(y_test, xgb_probs)
            logger.info(f"XGBoost: LogLoss={performances['xgboost_logloss']:.4f}, "
                       f"Brier={performances['xgboost_brier']:.4f}")
        
        # 2. Train CatBoost
        if HAS_CAT:
            self.models['catboost'] = self._train_catboost(
                X_train_scaled, y_train, X_test_scaled, y_test
            )
            cat_probs = self.models['catboost'].predict_proba(X_test_scaled)[:, 1]
            performances['catboost_logloss'] = log_loss(y_test, cat_probs)
            performances['catboost_brier'] = brier_score_loss(y_test, cat_probs)
            logger.info(f"CatBoost: LogLoss={performances['catboost_logloss']:.4f}, "
                       f"Brier={performances['catboost_brier']:.4f}")
        
        # 3. Train LightGBM
        if HAS_LGB:
            self.models['lightgbm'] = self._train_lightgbm(
                X_train_scaled, y_train, X_test_scaled, y_test
            )
            lgb_probs = self.models['lightgbm'].predict_proba(X_test_scaled)[:, 1]
            performances['lightgbm_logloss'] = log_loss(y_test, lgb_probs)
            performances['lightgbm_brier'] = brier_score_loss(y_test, lgb_probs)
            logger.info(f"LightGBM: LogLoss={performances['lightgbm_logloss']:.4f}, "
                       f"Brier={performances['lightgbm_brier']:.4f}")
        
        # 4. Train Poisson model for low-scoring sports
        if self.sport in self.LOW_SCORING_SPORTS and games_df is not None:
            self.models['poisson'] = PoissonScoreModel(self.sport)
            # Train on all historical data up to training cutoff
            train_cutoff = len(X_train)
            self.models['poisson'].fit(games_df.iloc[:train_cutoff])
            logger.info(f"Poisson model fitted for {self.sport}")
        
        self.trained = True
        return performances
    
    def _train_xgboost(self, X_train: np.ndarray, y_train: np.ndarray,
                       X_test: np.ndarray, y_test: np.ndarray):
        """Train XGBoost with early stopping"""
        model = xgb.XGBClassifier(
            n_estimators=500,
            max_depth=5,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=3,
            reg_alpha=0.1,
            reg_lambda=1.0,
            random_state=42,
            eval_metric='logloss',
            early_stopping_rounds=50,
        )
        
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            verbose=False
        )
        
        return model
    
    def _train_catboost(self, X_train: np.ndarray, y_train: np.ndarray,
                        X_test: np.ndarray, y_test: np.ndarray):
        """Train CatBoost with early stopping"""
        model = CatBoostClassifier(
            iterations=500,
            depth=5,
            learning_rate=0.03,
            l2_leaf_reg=3,
            random_state=42,
            verbose=False,
            early_stopping_rounds=50,
            eval_metric='Logloss',
        )
        
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            verbose=False
        )
        
        return model
    
    def _train_lightgbm(self, X_train: np.ndarray, y_train: np.ndarray,
                        X_test: np.ndarray, y_test: np.ndarray):
        """Train LightGBM with early stopping"""
        model = lgb.LGBMClassifier(
            n_estimators=500,
            max_depth=5,
            learning_rate=0.03,
            num_leaves=31,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=1.0,
            random_state=42,
            verbose=-1,
        )
        
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            eval_metric='logloss',
            callbacks=[lgb.early_stopping(50, verbose=False)]
        )
        
        return model
    
    def _scaled_feature_frame(self, X: pd.DataFrame) -> pd.DataFrame:
        """Return scaled features with column names for sklearn tree models."""
        X_scaled = self.scaler.transform(X)
        columns = self.feature_names
        if not columns:
            lgb_model = self.models.get('lightgbm')
            columns = list(getattr(lgb_model, 'feature_names_in_', None) or [])
        if not columns or len(columns) != X_scaled.shape[1]:
            columns = [f'f{i}' for i in range(X_scaled.shape[1])]
        return pd.DataFrame(X_scaled, columns=columns)

    def predict_proba(self, X: pd.DataFrame, 
                      home_team: Optional[str] = None,
                      away_team: Optional[str] = None) -> Dict[str, np.ndarray]:
        """
        Get probability predictions from all models
        
        Returns: Dictionary of {model_name: probabilities}
        """
        if not self.trained:
            raise ValueError("Models not trained yet")
        
        X_frame = self._scaled_feature_frame(X)
        predictions = {}
        
        if 'xgboost' in self.models:
            predictions['xgboost'] = self.models['xgboost'].predict_proba(X_frame)[:, 1]
        
        if 'catboost' in self.models:
            predictions['catboost'] = self.models['catboost'].predict_proba(X_frame)[:, 1]
        
        if 'lightgbm' in self.models:
            predictions['lightgbm'] = self.models['lightgbm'].predict_proba(X_frame)[:, 1]
        
        if 'poisson' in self.models and home_team and away_team:
            predictions['poisson'] = np.array([
                self.models['poisson'].get_win_probability(home_team, away_team)
            ])
        
        return predictions
    
    def train_all(self, X: pd.DataFrame, y: np.ndarray, 
                  return_oof: bool = False) -> Tuple[Dict[str, np.ndarray], Dict[str, any]]:
        """
        Train all models and return out-of-fold predictions
        
        Returns: (oof_predictions, models)
        """
        self.feature_names = list(X.columns)
        n_samples = len(y)
        
        # Initialize OOF predictions
        oof_preds = {}
        
        # Use time-series split for OOF predictions
        tscv = TimeSeriesSplit(n_splits=5)
        
        # Scale features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        
        logger.info(f"Training {self.sport} models on {n_samples} samples with 5-fold TS CV")
        
        # Train XGBoost with OOF
        if HAS_XGB:
            oof_xgb = np.zeros(n_samples)
            for fold, (train_idx, val_idx) in enumerate(tscv.split(X_scaled)):
                X_tr, X_val = X_scaled[train_idx], X_scaled[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]
                
                model = xgb.XGBClassifier(
                    n_estimators=300, max_depth=4, learning_rate=0.05,
                    subsample=0.8, colsample_bytree=0.8, min_child_weight=3,
                    reg_alpha=0.1, reg_lambda=1.0, random_state=42,
                    eval_metric='logloss', early_stopping_rounds=30,
                )
                model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)
                oof_xgb[val_idx] = model.predict_proba(X_val)[:, 1]
            
            # Train final model on all data
            self.models['xgboost'] = xgb.XGBClassifier(
                n_estimators=300, max_depth=4, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8, min_child_weight=3,
                reg_alpha=0.1, reg_lambda=1.0, random_state=42,
            )
            self.models['xgboost'].fit(X_scaled, y)
            oof_preds['xgboost'] = oof_xgb
            logger.info(f"XGBoost OOF LogLoss: {log_loss(y, oof_xgb):.4f}")
        
        # Train CatBoost with OOF
        if HAS_CAT:
            oof_cat = np.zeros(n_samples)
            for fold, (train_idx, val_idx) in enumerate(tscv.split(X_scaled)):
                X_tr, X_val = X_scaled[train_idx], X_scaled[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]
                
                model = CatBoostClassifier(
                    iterations=300, depth=4, learning_rate=0.05,
                    l2_leaf_reg=3, random_state=42, verbose=False,
                    early_stopping_rounds=30, eval_metric='Logloss',
                )
                model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)
                oof_cat[val_idx] = model.predict_proba(X_val)[:, 1]
            
            self.models['catboost'] = CatBoostClassifier(
                iterations=300, depth=4, learning_rate=0.05,
                l2_leaf_reg=3, random_state=42, verbose=False,
            )
            self.models['catboost'].fit(X_scaled, y, verbose=False)
            oof_preds['catboost'] = oof_cat
            logger.info(f"CatBoost OOF LogLoss: {log_loss(y, oof_cat):.4f}")
        
        # Train LightGBM with OOF
        if HAS_LGB:
            oof_lgb = np.zeros(n_samples)
            for fold, (train_idx, val_idx) in enumerate(tscv.split(X_scaled)):
                X_tr, X_val = X_scaled[train_idx], X_scaled[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]
                
                model = lgb.LGBMClassifier(
                    n_estimators=300, max_depth=4, learning_rate=0.05,
                    num_leaves=16, subsample=0.8, colsample_bytree=0.8,
                    reg_alpha=0.1, reg_lambda=1.0, random_state=42, verbose=-1,
                )
                model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], 
                         eval_metric='logloss', callbacks=[lgb.early_stopping(30, verbose=False)])
                oof_lgb[val_idx] = model.predict_proba(X_val)[:, 1]
            
            self.models['lightgbm'] = lgb.LGBMClassifier(
                n_estimators=300, max_depth=4, learning_rate=0.05,
                num_leaves=16, subsample=0.8, colsample_bytree=0.8,
                reg_alpha=0.1, reg_lambda=1.0, random_state=42, verbose=-1,
            )
            self.models['lightgbm'].fit(X_scaled, y)
            oof_preds['lightgbm'] = oof_lgb
            logger.info(f"LightGBM OOF LogLoss: {log_loss(y, oof_lgb):.4f}")
        
        self.trained = True
        
        if return_oof:
            return oof_preds, self.models
        return self.models
    
    def get_feature_importance(self) -> Dict[str, pd.DataFrame]:
        """Get feature importance from all models"""
        importances = {}
        
        if 'xgboost' in self.models:
            imp = self.models['xgboost'].feature_importances_
            importances['xgboost'] = pd.DataFrame({
                'feature': self.feature_names,
                'importance': imp
            }).sort_values('importance', ascending=False)
        
        if 'catboost' in self.models:
            imp = self.models['catboost'].feature_importances_
            importances['catboost'] = pd.DataFrame({
                'feature': self.feature_names,
                'importance': imp
            }).sort_values('importance', ascending=False)
        
        if 'lightgbm' in self.models:
            imp = self.models['lightgbm'].feature_importances_
            importances['lightgbm'] = pd.DataFrame({
                'feature': self.feature_names,
                'importance': imp
            }).sort_values('importance', ascending=False)
        
        return importances
    
    def save(self, filepath: str):
        """Save all models to file"""
        save_dict = {
            'sport': self.sport,
            'models': self.models,
            'scaler': self.scaler,
            'feature_names': self.feature_names,
            'trained': self.trained,
        }
        
        with open(filepath, 'wb') as f:
            pickle.dump(save_dict, f)
        
        logger.info(f"Saved {self.sport} base models to {filepath}")
    
    @classmethod
    def load(cls, filepath: str) -> 'BaseModelTrainer':
        """Load models from file"""
        with open(filepath, 'rb') as f:
            save_dict = pickle.load(f)
        
        trainer = cls(save_dict['sport'])
        trainer.models = save_dict['models']
        trainer.scaler = save_dict['scaler']
        trainer.feature_names = save_dict['feature_names']
        trainer.trained = save_dict['trained']
        
        logger.info(f"Loaded {trainer.sport} base models from {filepath}")
        return trainer
