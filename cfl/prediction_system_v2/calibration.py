"""
Calibration Module
==================

Ensures probability predictions are well-calibrated:
- If model predicts 70% win probability, ~70% of those should actually win

Methods:
1. Platt Scaling - sigmoid transformation
2. Isotonic Regression - non-parametric, monotonic

Why calibration matters for betting:
- Raw ML probabilities are often overconfident
- Proper calibration is required for accurate edge calculations
- Brier score and log loss both reward calibration
"""

import numpy as np
import pandas as pd
from typing import Dict, Tuple, Optional
from sklearn.calibration import CalibratedClassifierCV
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_predict, TimeSeriesSplit
import pickle
import logging

logger = logging.getLogger(__name__)


class PlattCalibrator:
    """
    Platt Scaling (Sigmoid Calibration)
    ------------------------------------
    
    Fits a logistic regression to transform raw scores to calibrated probabilities:
    P(y=1|s) = 1 / (1 + exp(A*s + B))
    
    Pros:
    - Works well when predictions need minor adjustment
    - Smooth output, never extreme
    - Fast to fit
    
    Cons:
    - Assumes sigmoid relationship
    - Can't fix complex miscalibration patterns
    """
    
    def __init__(self):
        self.A = 0.0  # slope
        self.B = 0.0  # intercept
        self.fitted = False
        
    def fit(self, raw_probs: np.ndarray, y: np.ndarray):
        """
        Fit Platt scaling parameters
        
        raw_probs: uncalibrated probability predictions
        y: actual outcomes (0 or 1)
        """
        # Transform to log-odds
        eps = 1e-10
        raw_probs = np.clip(raw_probs, eps, 1 - eps)
        log_odds = np.log(raw_probs / (1 - raw_probs)).reshape(-1, 1)
        
        # Fit logistic regression
        lr = LogisticRegression(fit_intercept=True, solver='lbfgs')
        lr.fit(log_odds, y)
        
        self.A = lr.coef_[0][0]
        self.B = lr.intercept_[0]
        self.fitted = True
        
        logger.info(f"Platt calibration fitted: A={self.A:.4f}, B={self.B:.4f}")
        
    def transform(self, raw_probs: np.ndarray) -> np.ndarray:
        """Apply Platt scaling to get calibrated probabilities"""
        if not self.fitted:
            raise ValueError("Calibrator not fitted")
        
        eps = 1e-10
        raw_probs = np.clip(raw_probs, eps, 1 - eps)
        log_odds = np.log(raw_probs / (1 - raw_probs))
        
        calibrated = 1 / (1 + np.exp(-(self.A * log_odds + self.B)))
        return calibrated


class IsotonicCalibrator:
    """
    Isotonic Regression Calibration
    --------------------------------
    
    Non-parametric calibration that learns a monotonic mapping.
    
    Pros:
    - Can fix complex miscalibration patterns
    - No distributional assumptions
    - Often better than Platt when more data available
    
    Cons:
    - Requires more data
    - Can overfit with small samples
    - May have "jumps" in output
    """
    
    def __init__(self):
        self.isotonic = IsotonicRegression(out_of_bounds='clip')
        self.fitted = False
        
    def fit(self, raw_probs: np.ndarray, y: np.ndarray):
        """Fit isotonic regression"""
        self.isotonic.fit(raw_probs, y)
        self.fitted = True
        logger.info("Isotonic calibration fitted")
        
    def transform(self, raw_probs: np.ndarray) -> np.ndarray:
        """Apply isotonic calibration"""
        if not self.fitted:
            raise ValueError("Calibrator not fitted")
        return self.isotonic.predict(raw_probs)


class ProbabilityCalibrator:
    """
    Main Calibration Class
    ----------------------
    
    Automatically selects best calibration method based on:
    - Sample size
    - Initial calibration quality
    - Cross-validation performance
    """
    
    def __init__(self, method: str = 'auto'):
        """
        method: 'platt', 'isotonic', or 'auto'
        """
        self.method = method
        self.platt = PlattCalibrator()
        self.isotonic = IsotonicCalibrator()
        self.selected_method: Optional[str] = None
        self.fitted = False
        
    def fit(self, raw_probs: np.ndarray, y: np.ndarray) -> Dict[str, float]:
        """
        Fit calibrator and return calibration metrics
        
        Returns dict with calibration errors for each method
        """
        # Fit both methods
        self.platt.fit(raw_probs, y)
        self.isotonic.fit(raw_probs, y)
        
        # Calculate calibration errors
        platt_probs = self.platt.transform(raw_probs)
        iso_probs = self.isotonic.transform(raw_probs)
        
        metrics = {}
        metrics['uncalibrated_ece'] = self._expected_calibration_error(raw_probs, y)
        metrics['platt_ece'] = self._expected_calibration_error(platt_probs, y)
        metrics['isotonic_ece'] = self._expected_calibration_error(iso_probs, y)
        
        # Select method
        if self.method == 'auto':
            if len(y) < 500:
                # Small sample: prefer Platt (less overfitting)
                self.selected_method = 'platt'
            elif metrics['platt_ece'] < metrics['isotonic_ece']:
                self.selected_method = 'platt'
            else:
                self.selected_method = 'isotonic'
        else:
            self.selected_method = self.method
        
        metrics['selected_method'] = self.selected_method
        
        logger.info(f"Calibration results:")
        logger.info(f"  Uncalibrated ECE: {metrics['uncalibrated_ece']:.4f}")
        logger.info(f"  Platt ECE: {metrics['platt_ece']:.4f}")
        logger.info(f"  Isotonic ECE: {metrics['isotonic_ece']:.4f}")
        logger.info(f"  Selected: {self.selected_method}")
        
        self.fitted = True
        return metrics
    
    def transform(self, raw_probs: np.ndarray) -> np.ndarray:
        """Apply selected calibration"""
        if not self.fitted:
            raise ValueError("Calibrator not fitted")
        
        if self.selected_method == 'platt':
            return self.platt.transform(raw_probs)
        else:
            return self.isotonic.transform(raw_probs)
    
    def _expected_calibration_error(self, probs: np.ndarray, y: np.ndarray, 
                                     n_bins: int = 10) -> float:
        """
        Calculate Expected Calibration Error (ECE)
        
        ECE = sum(|avg_confidence - avg_accuracy| * bin_weight)
        
        Lower is better. 0 = perfectly calibrated.
        """
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
    
    def get_reliability_diagram_data(self, probs: np.ndarray, y: np.ndarray,
                                      n_bins: int = 10) -> Dict[str, np.ndarray]:
        """
        Get data for plotting reliability diagram
        
        Returns bin midpoints, accuracy per bin, and counts
        """
        bin_boundaries = np.linspace(0, 1, n_bins + 1)
        
        bin_mids = []
        accuracies = []
        confidences = []
        counts = []
        
        for i in range(n_bins):
            mask = (probs >= bin_boundaries[i]) & (probs < bin_boundaries[i + 1])
            if mask.sum() > 0:
                bin_mids.append((bin_boundaries[i] + bin_boundaries[i + 1]) / 2)
                accuracies.append(y[mask].mean())
                confidences.append(probs[mask].mean())
                counts.append(mask.sum())
        
        return {
            'bin_mids': np.array(bin_mids),
            'accuracies': np.array(accuracies),
            'confidences': np.array(confidences),
            'counts': np.array(counts),
        }
    
    def save(self, filepath: str):
        """Save calibrator"""
        save_dict = {
            'method': self.method,
            'selected_method': self.selected_method,
            'platt_A': self.platt.A,
            'platt_B': self.platt.B,
            'isotonic': self.isotonic.isotonic,
            'fitted': self.fitted,
        }
        with open(filepath, 'wb') as f:
            pickle.dump(save_dict, f)
        logger.info(f"Saved calibrator to {filepath}")
    
    @classmethod
    def load(cls, filepath: str) -> 'ProbabilityCalibrator':
        """Load calibrator"""
        with open(filepath, 'rb') as f:
            save_dict = pickle.load(f)
        
        calibrator = cls(method=save_dict['method'])
        calibrator.selected_method = save_dict['selected_method']
        calibrator.platt.A = save_dict['platt_A']
        calibrator.platt.B = save_dict['platt_B']
        calibrator.platt.fitted = True
        calibrator.isotonic.isotonic = save_dict['isotonic']
        calibrator.isotonic.fitted = True
        calibrator.fitted = save_dict['fitted']
        
        logger.info(f"Loaded calibrator from {filepath}")
        return calibrator
