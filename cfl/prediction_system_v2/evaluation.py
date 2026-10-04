"""
Evaluation Module
=================

Comprehensive model evaluation for sports prediction:
- Log Loss: measures probability quality
- Brier Score: measures calibration + discrimination
- Calibration Curve: visual check of calibration
- ROI: actual betting performance

This is NOT just accuracy tracking - accuracy is a poor metric
for probability predictions.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from sklearn.metrics import log_loss, brier_score_loss, roc_auc_score
from dataclasses import dataclass
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """Container for evaluation metrics"""
    log_loss: float
    brier_score: float
    accuracy: float
    auc: float
    calibration_error: float
    roi: float
    n_bets: int
    n_wins: int
    timestamp: str
    
    def to_dict(self) -> Dict:
        return {
            'log_loss': self.log_loss,
            'brier_score': self.brier_score,
            'accuracy': self.accuracy,
            'auc': self.auc,
            'calibration_error': self.calibration_error,
            'roi': self.roi,
            'n_bets': self.n_bets,
            'n_wins': self.n_wins,
            'timestamp': self.timestamp,
        }


class ModelEvaluator:
    """
    Comprehensive Model Evaluation
    ------------------------------
    
    Key metrics:
    
    1. Log Loss (Cross-Entropy)
       - Lower is better
       - Heavily penalizes confident wrong predictions
       - Standard: ~0.69 (coin flip), Good: <0.65, Great: <0.60
    
    2. Brier Score
       - Lower is better (0 = perfect)
       - = Mean Squared Error of probabilities
       - Standard: 0.25 (coin flip), Good: <0.22, Great: <0.20
    
    3. Expected Calibration Error (ECE)
       - Lower is better (0 = perfect calibration)
       - Measures gap between confidence and accuracy
    
    4. ROI (Return on Investment)
       - The ultimate metric for betting
       - Requires odds data
    """
    
    def __init__(self):
        self.history: List[EvaluationResult] = []
        
    def evaluate(self, y_true: np.ndarray, y_prob: np.ndarray,
                 odds: Optional[np.ndarray] = None) -> EvaluationResult:
        """
        Full evaluation of predictions
        
        y_true: actual outcomes (0 or 1)
        y_prob: predicted probabilities for class 1
        odds: optional American odds for ROI calculation
        """
        n_samples = len(y_true)
        
        # Core metrics
        ll = log_loss(y_true, y_prob)
        brier = brier_score_loss(y_true, y_prob)
        
        # Accuracy (pick > 0.5)
        y_pred = (y_prob > 0.5).astype(int)
        accuracy = np.mean(y_pred == y_true)
        
        # AUC
        try:
            auc = roc_auc_score(y_true, y_prob)
        except ValueError:
            auc = 0.5  # If only one class in y_true
        
        # Calibration Error
        ece = self._expected_calibration_error(y_prob, y_true)
        
        # ROI calculation
        if odds is not None:
            roi, n_bets = self._calculate_roi(y_true, y_prob, odds)
        else:
            roi, n_bets = 0.0, 0
        
        n_wins = int(y_true.sum())
        
        result = EvaluationResult(
            log_loss=ll,
            brier_score=brier,
            accuracy=accuracy,
            auc=auc,
            calibration_error=ece,
            roi=roi,
            n_bets=n_bets,
            n_wins=n_wins,
            timestamp=datetime.now().isoformat(),
        )
        
        self.history.append(result)
        return result
    
    def _expected_calibration_error(self, probs: np.ndarray, y: np.ndarray,
                                     n_bins: int = 10) -> float:
        """Calculate Expected Calibration Error"""
        bin_boundaries = np.linspace(0, 1, n_bins + 1)
        ece = 0.0
        
        for i in range(n_bins):
            mask = (probs >= bin_boundaries[i]) & (probs < bin_boundaries[i + 1])
            if mask.sum() > 0:
                bin_confidence = probs[mask].mean()
                bin_accuracy = y[mask].mean()
                bin_weight = mask.sum() / len(y)
                ece += abs(bin_confidence - bin_accuracy) * bin_weight
        
        return ece
    
    def _calculate_roi(self, y_true: np.ndarray, y_prob: np.ndarray,
                       odds: np.ndarray, edge_threshold: float = 0.02) -> Tuple[float, int]:
        """
        Calculate ROI with edge-based betting
        
        Only bet when model has edge over implied probability.
        Uses Kelly-like stake sizing.
        
        Returns (roi_percentage, n_bets)
        """
        # Convert American odds to implied probability
        implied_prob = np.where(
            odds > 0,
            100 / (odds + 100),
            -odds / (-odds + 100)
        )
        
        # Calculate edge
        edge = y_prob - implied_prob
        
        # Only bet when edge > threshold
        bet_mask = edge > edge_threshold
        n_bets = bet_mask.sum()
        
        if n_bets == 0:
            return 0.0, 0
        
        # Calculate returns
        total_wagered = n_bets  # 1 unit per bet
        
        # Win payout calculation
        wins = y_true[bet_mask]
        bet_odds = odds[bet_mask]
        
        payouts = np.where(wins == 1,
                          np.where(bet_odds > 0, bet_odds / 100, 100 / -bet_odds),
                          -1)
        
        total_return = payouts.sum()
        roi = (total_return / total_wagered) * 100
        
        return roi, n_bets
    
    def evaluate_by_confidence(self, y_true: np.ndarray, y_prob: np.ndarray,
                                confidence_bins: int = 4) -> Dict[str, Dict]:
        """
        Break down performance by confidence level
        
        Helps identify if model is well-calibrated across
        different confidence levels.
        """
        # Confidence = distance from 0.5
        confidence = np.abs(y_prob - 0.5) * 2
        
        bin_edges = np.linspace(0, 1, confidence_bins + 1)
        results = {}
        
        for i in range(confidence_bins):
            low, high = bin_edges[i], bin_edges[i + 1]
            mask = (confidence >= low) & (confidence < high)
            
            if mask.sum() > 10:  # Minimum samples
                bin_result = {
                    'n_samples': int(mask.sum()),
                    'accuracy': float(np.mean((y_prob[mask] > 0.5) == y_true[mask])),
                    'mean_prob': float(np.mean(y_prob[mask])),
                    'actual_rate': float(np.mean(y_true[mask])),
                    'brier': float(brier_score_loss(y_true[mask], y_prob[mask])),
                }
                results[f'conf_{low:.2f}_{high:.2f}'] = bin_result
        
        return results
    
    def compare_models(self, y_true: np.ndarray, 
                       model_probs: Dict[str, np.ndarray]) -> pd.DataFrame:
        """
        Compare multiple models side-by-side
        
        model_probs: dict mapping model name to probability predictions
        """
        results = []
        
        for model_name, probs in model_probs.items():
            metrics = {
                'model': model_name,
                'log_loss': log_loss(y_true, probs),
                'brier': brier_score_loss(y_true, probs),
                'accuracy': np.mean((probs > 0.5) == y_true),
                'ece': self._expected_calibration_error(probs, y_true),
            }
            results.append(metrics)
        
        df = pd.DataFrame(results)
        df = df.sort_values('log_loss')  # Best model first
        
        return df
    
    def get_summary(self) -> Dict:
        """Get summary statistics from evaluation history"""
        if not self.history:
            return {}
        
        recent = self.history[-1]
        
        return {
            'latest_log_loss': recent.log_loss,
            'latest_brier': recent.brier_score,
            'latest_accuracy': recent.accuracy,
            'latest_roi': recent.roi,
            'n_evaluations': len(self.history),
            'avg_log_loss': np.mean([r.log_loss for r in self.history]),
            'avg_brier': np.mean([r.brier_score for r in self.history]),
        }
    
    def print_report(self, result: EvaluationResult):
        """Print formatted evaluation report"""
        print("\n" + "="*50)
        print("MODEL EVALUATION REPORT")
        print("="*50)
        print(f"Timestamp: {result.timestamp}")
        print(f"\n📊 CORE METRICS:")
        print(f"  Log Loss:    {result.log_loss:.4f}  {'✓ Good' if result.log_loss < 0.65 else '⚠ High'}")
        print(f"  Brier Score: {result.brier_score:.4f}  {'✓ Good' if result.brier_score < 0.22 else '⚠ High'}")
        print(f"  Accuracy:    {result.accuracy:.1%}")
        print(f"  AUC:         {result.auc:.4f}")
        print(f"\n📈 CALIBRATION:")
        print(f"  ECE:         {result.calibration_error:.4f}  {'✓ Well calibrated' if result.calibration_error < 0.05 else '⚠ Needs calibration'}")
        print(f"\n💰 BETTING (if applicable):")
        print(f"  ROI:         {result.roi:+.1f}%")
        print(f"  Bets Made:   {result.n_bets}")
        print("="*50 + "\n")
    
    def save_history(self, filepath: str):
        """Save evaluation history to JSON"""
        history_dicts = [r.to_dict() for r in self.history]
        with open(filepath, 'w') as f:
            json.dump(history_dicts, f, indent=2)
        logger.info(f"Saved evaluation history to {filepath}")
    
    def load_history(self, filepath: str):
        """Load evaluation history from JSON"""
        with open(filepath, 'r') as f:
            history_dicts = json.load(f)
        
        self.history = [
            EvaluationResult(**h) for h in history_dicts
        ]
        logger.info(f"Loaded {len(self.history)} evaluation records")


class BacktestEvaluator:
    """
    Backtesting framework for time-series evaluation
    
    Simulates real-world model usage where predictions
    are made before outcomes are known.
    """
    
    def __init__(self, min_train_size: int = 200):
        self.min_train_size = min_train_size
        self.results: List[Dict] = []
        
    def walk_forward(self, data: pd.DataFrame, 
                     prediction_fn, 
                     target_col: str = 'home_win',
                     date_col: str = 'date',
                     window_size: Optional[int] = None) -> pd.DataFrame:
        """
        Walk-forward backtesting
        
        For each time step:
        1. Train on all data up to that point
        2. Predict next batch
        3. Record predictions vs actuals
        
        prediction_fn: function(train_data) -> model that has predict_proba(test_data)
        """
        data = data.sort_values(date_col).reset_index(drop=True)
        unique_dates = data[date_col].unique()
        
        all_predictions = []
        
        for i, test_date in enumerate(unique_dates):
            if i < self.min_train_size:
                continue
            
            # Train/test split
            train_mask = data[date_col] < test_date
            test_mask = data[date_col] == test_date
            
            if window_size:
                # Rolling window
                train_dates = unique_dates[max(0, i-window_size):i]
                train_mask = data[date_col].isin(train_dates)
            
            train_data = data[train_mask]
            test_data = data[test_mask]
            
            if len(test_data) == 0:
                continue
            
            # Get predictions
            model = prediction_fn(train_data)
            probs = model.predict_proba(test_data)
            
            # Store results
            for idx, prob in zip(test_data.index, probs):
                all_predictions.append({
                    'date': test_date,
                    'index': idx,
                    'y_true': data.loc[idx, target_col],
                    'y_prob': prob,
                })
        
        return pd.DataFrame(all_predictions)
    
    def evaluate_backtest(self, backtest_results: pd.DataFrame) -> Dict:
        """Evaluate backtest results"""
        y_true = backtest_results['y_true'].values
        y_prob = backtest_results['y_prob'].values
        
        evaluator = ModelEvaluator()
        result = evaluator.evaluate(y_true, y_prob)
        
        # Add temporal analysis
        by_month = backtest_results.copy()
        by_month['month'] = pd.to_datetime(by_month['date']).dt.to_period('M')
        
        monthly_metrics = []
        for month, group in by_month.groupby('month'):
            monthly_metrics.append({
                'month': str(month),
                'n_games': len(group),
                'accuracy': np.mean((group['y_prob'] > 0.5) == group['y_true']),
                'log_loss': log_loss(group['y_true'], group['y_prob']),
            })
        
        return {
            'overall': result.to_dict(),
            'monthly': monthly_metrics,
        }
