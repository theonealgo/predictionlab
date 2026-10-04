"""
Advanced Sports Prediction System v2
=====================================

Architecture:
    Rating Engines → Feature Engineering → Base Models → Stacked Ensemble → Calibration

Components:
    - rating_engines: Glicko-2, margin-adjusted ratings (Elo demoted to feature)
    - feature_engineering: Comprehensive feature generation
    - base_models: XGBoost, CatBoost, LightGBM, Poisson
    - ensemble: Stacked meta-learner with dynamic weights
    - calibration: Platt scaling and isotonic regression
    - evaluation: Log loss, Brier score, calibration curves, ROI
"""

from .rating_engines import Glicko2Rating, MarginRating, EloFeatureGenerator, TrueSkillRating
from .feature_engineering import AdvancedFeatureEngineer
from .base_models import BaseModelTrainer, PoissonScoreModel
from .ensemble import StackedEnsemble
from .calibration import ProbabilityCalibrator, PlattCalibrator, IsotonicCalibrator
from .evaluation import ModelEvaluator, BacktestEvaluator, EvaluationResult
from .predictor import AdvancedPredictor, train_sport_predictor

__version__ = "2.0.0"
__all__ = [
    # Main API
    "AdvancedPredictor",
    "train_sport_predictor",
    # Rating engines
    "Glicko2Rating",
    "TrueSkillRating",
    "MarginRating",
    "EloFeatureGenerator",
    # Features & models
    "AdvancedFeatureEngineer",
    "BaseModelTrainer",
    "PoissonScoreModel",
    "StackedEnsemble",
    # Calibration
    "ProbabilityCalibrator",
    "PlattCalibrator",
    "IsotonicCalibrator",
    # Evaluation
    "ModelEvaluator",
    "BacktestEvaluator",
    "EvaluationResult",
]
