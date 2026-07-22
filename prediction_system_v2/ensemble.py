"""
Stacked Ensemble Module
=======================

Meta model that learns dynamic weights for base models.
NOT static averaging - weights adapt based on:
- Model agreement/disagreement
- Prediction confidence
- Context features

The meta model is LightGBM (or Logistic with Elastic Net as fallback),
which learns optimal combinations from out-of-fold predictions.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from sklearn.linear_model import LogisticRegressionCV
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import log_loss, brier_score_loss
import pickle
import logging

logger = logging.getLogger(__name__)

try:
    import lightgbm as lgb
    HAS_LGB = True
except ImportError:
    HAS_LGB = False


class StackedEnsemble:
    """
    Stacked Meta-Learner Ensemble
    -----------------------------
    
    Why stacking over simple averaging:
    - Learns which models perform better in which contexts
    - Captures model disagreement as a feature
    - Adapts weights based on prediction confidence
    - Better calibrated than any individual model
    
    The meta model receives:
    - Base model predictions (probabilities)
    - Model disagreement features
    - Original context features (optionally)
    """
    
    def __init__(self, use_context_features: bool = True):
        self.use_context_features = use_context_features
        self.meta_model = None
        self.trained = False
        self.base_model_names: List[str] = []
        self.meta_feature_names: List[str] = []
        
    def _create_meta_features(self, base_predictions: Dict[str, np.ndarray],
                              context_features: Optional[pd.DataFrame] = None) -> np.ndarray:
        """
        Create features for meta model
        
        Includes:
        - Raw base model probabilities
        - Model agreement/disagreement indicators
        - Prediction spread (max - min)
        - Mean and std of predictions
        """
        # Stack base predictions
        n_samples = len(list(base_predictions.values())[0])
        # Only set base_model_names during training (not inference)
        if not self.trained:
            self.base_model_names = list(base_predictions.keys())
        
        meta_features = []
        
        # 1. Raw probabilities from each model
        for model_name in self.base_model_names:
            meta_features.append(base_predictions[model_name].reshape(-1, 1))
        
        # 2. Model agreement features
        all_probs = np.column_stack([base_predictions[m] for m in self.base_model_names])
        
        # Mean prediction
        meta_features.append(np.mean(all_probs, axis=1).reshape(-1, 1))
        
        # Std of predictions (disagreement)
        meta_features.append(np.std(all_probs, axis=1).reshape(-1, 1))
        
        # Max - Min spread
        meta_features.append((np.max(all_probs, axis=1) - np.min(all_probs, axis=1)).reshape(-1, 1))
        
        # How many models agree on the pick (>0.5 or <0.5)
        agreement = np.mean((all_probs > 0.5).astype(int), axis=1)
        meta_features.append(agreement.reshape(-1, 1))
        
        # 3. Optionally include context features
        if self.use_context_features and context_features is not None:
            # Select most important context features
            key_features = [
                'glicko2_rating_diff', 'glicko2_confidence',
                'form_L5_win_pct_diff', 'rest_diff',
                'home_advantage', 'h2h_home_win_pct'
            ]
            for feat in key_features:
                if feat in context_features.columns:
                    meta_features.append(context_features[feat].values.reshape(-1, 1))
        
        return np.hstack(meta_features)

    def _meta_feature_names(self, context_features: Optional[pd.DataFrame] = None) -> List[str]:
        """Stable column names for the stacked meta-model."""
        names = [f'pred_{model_name}' for model_name in self.base_model_names]
        names.extend(['pred_mean', 'pred_std', 'pred_spread', 'pred_agreement'])
        if self.use_context_features and context_features is not None:
            key_features = [
                'glicko2_rating_diff', 'glicko2_confidence',
                'form_L5_win_pct_diff', 'rest_diff',
                'home_advantage', 'h2h_home_win_pct'
            ]
            for feat in key_features:
                if feat in context_features.columns:
                    names.append(f'ctx_{feat}')
        return names

    def _meta_input(self, X_meta: np.ndarray,
                    context_features: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """Wrap meta features with names expected by the fitted meta model."""
        names = getattr(self.meta_model, 'feature_names_in_', None)
        if names is None or len(names) != X_meta.shape[1]:
            names = self.meta_feature_names or self._meta_feature_names(context_features)
        if len(names) != X_meta.shape[1]:
            names = [f'meta_{i}' for i in range(X_meta.shape[1])]
        return pd.DataFrame(X_meta, columns=list(names))
    
    def train(self, base_predictions: Dict[str, np.ndarray], 
              y: np.ndarray,
              context_features: Optional[pd.DataFrame] = None) -> Dict[str, float]:
        """
        Train meta model on out-of-fold base model predictions
        
        IMPORTANT: base_predictions should be out-of-fold predictions
        to avoid overfitting.
        """
        X_meta = self._create_meta_features(base_predictions, context_features)
        self.meta_feature_names = self._meta_feature_names(context_features)
        
        logger.info(f"Training stacked ensemble with {X_meta.shape[1]} meta features "
                   f"on {len(y)} samples")
        
        # Use time-series split
        tscv = TimeSeriesSplit(n_splits=5)
        train_idx, test_idx = list(tscv.split(X_meta))[-1]
        
        X_train, X_test = X_meta[train_idx], X_meta[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
        X_train_df = pd.DataFrame(X_train, columns=self.meta_feature_names)
        X_test_df = pd.DataFrame(X_test, columns=self.meta_feature_names)
        
        # Train meta model
        if HAS_LGB:
            self.meta_model = lgb.LGBMClassifier(
                n_estimators=100,
                max_depth=3,
                learning_rate=0.05,
                num_leaves=8,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_alpha=0.5,
                reg_lambda=0.5,
                random_state=42,
                verbose=-1,
            )
            self.meta_model.fit(
                X_train_df, y_train,
                eval_set=[(X_test_df, y_test)],
                eval_metric='logloss',
                callbacks=[lgb.early_stopping(20, verbose=False)]
            )
        else:
            # Fallback to Logistic Regression with Elastic Net
            self.meta_model = LogisticRegressionCV(
                cv=5,
                penalty='elasticnet',
                solver='saga',
                l1_ratios=[0.3, 0.5, 0.7],
                max_iter=1000,
                random_state=42,
            )
            self.meta_model.fit(X_train_df, y_train)
        
        # Evaluate
        meta_probs = self.meta_model.predict_proba(X_test_df)[:, 1]
        
        performances = {
            'ensemble_logloss': log_loss(y_test, meta_probs),
            'ensemble_brier': brier_score_loss(y_test, meta_probs),
        }
        
        # Compare to simple average baseline
        avg_probs = np.mean([base_predictions[m][test_idx] for m in self.base_model_names], axis=0)
        performances['simple_avg_logloss'] = log_loss(y_test, avg_probs)
        performances['simple_avg_brier'] = brier_score_loss(y_test, avg_probs)
        
        logger.info(f"Stacked Ensemble: LogLoss={performances['ensemble_logloss']:.4f}, "
                   f"Brier={performances['ensemble_brier']:.4f}")
        logger.info(f"Simple Average: LogLoss={performances['simple_avg_logloss']:.4f}, "
                   f"Brier={performances['simple_avg_brier']:.4f}")
        logger.info(f"Improvement: {performances['simple_avg_logloss'] - performances['ensemble_logloss']:.4f} log loss")
        
        self.trained = True
        return performances
    
    def predict_proba(self, base_predictions: Dict[str, np.ndarray],
                      context_features: Optional[pd.DataFrame] = None) -> np.ndarray:
        """
        Get stacked ensemble probability predictions
        """
        if not self.trained:
            raise ValueError("Ensemble not trained yet")
        
        X_meta = self._create_meta_features(base_predictions, context_features)
        return self.meta_model.predict_proba(
            self._meta_input(X_meta, context_features)
        )[:, 1]
    
    def get_model_weights(self) -> Dict[str, float]:
        """
        Get approximate model weights (for interpretability)
        
        Note: These are rough approximations since the meta model
        learns non-linear combinations.
        """
        if not self.trained:
            return {}
        
        weights = {}
        
        if HAS_LGB and hasattr(self.meta_model, 'feature_importances_'):
            importances = self.meta_model.feature_importances_
            # First N features are the base model predictions
            for i, model_name in enumerate(self.base_model_names):
                weights[model_name] = importances[i] if i < len(importances) else 0
            
            # Normalize
            total = sum(weights.values())
            if total > 0:
                weights = {k: v/total for k, v in weights.items()}
        
        return weights
    
    def get_confidence(self, base_predictions: Dict[str, np.ndarray]) -> np.ndarray:
        """
        Calculate confidence score based on model agreement
        
        High confidence = models agree AND predict away from 0.5
        Low confidence = models disagree OR predictions near 0.5
        """
        all_probs = np.column_stack([base_predictions[m] for m in base_predictions.keys()])
        
        # Agreement: how much models agree
        agreement = 1 - np.std(all_probs, axis=1)
        
        # Distance from 0.5: how confident is the average prediction
        mean_prob = np.mean(all_probs, axis=1)
        distance_from_half = np.abs(mean_prob - 0.5) * 2
        
        # Combined confidence
        confidence = agreement * 0.5 + distance_from_half * 0.5
        
        return confidence
    
    def save(self, filepath: str):
        """Save ensemble to file"""
        save_dict = {
            'meta_model': self.meta_model,
            'trained': self.trained,
            'base_model_names': self.base_model_names,
            'meta_feature_names': self.meta_feature_names,
            'use_context_features': self.use_context_features,
        }
        
        with open(filepath, 'wb') as f:
            pickle.dump(save_dict, f)
        
        logger.info(f"Saved stacked ensemble to {filepath}")
    
    @classmethod
    def load(cls, filepath: str) -> 'StackedEnsemble':
        """Load ensemble from file"""
        with open(filepath, 'rb') as f:
            save_dict = pickle.load(f)
        
        ensemble = cls(use_context_features=save_dict['use_context_features'])
        ensemble.meta_model = save_dict['meta_model']
        ensemble.trained = save_dict['trained']
        ensemble.base_model_names = save_dict['base_model_names']
        ensemble.meta_feature_names = save_dict.get('meta_feature_names', [])
        
        logger.info(f"Loaded stacked ensemble from {filepath}")
        return ensemble
