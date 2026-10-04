"""Tennis ML wrappers — uses shared EnsembleClassifier."""
from __future__ import annotations

from typing import Dict, List, Sequence

import numpy as np

from shared.ml import EnsembleClassifier
from shared.features import permutation_importance_stub

FEATURE_NAMES: List[str] = ['elo_diff', 'surface_elo_diff', 'serve_rating_diff', 'return_rating_diff', 'fatigue_index', 'h2h_edge', 'best_of']


class TennisModel:
    """Clean prediction interface for Tennis."""

    def __init__(self) -> None:
        self.ensemble = EnsembleClassifier.build_default()
        self._fitted = False

    def fit_demo(self, n: int = 120) -> None:
        # Synthetic demo fit so predict works without historical dumps.
        rng = np.random.default_rng(42)
        X = rng.normal(size=(n, len(FEATURE_NAMES)))
        # Weak signal on first feature
        logits = X[:, 0] * 0.8 + rng.normal(scale=0.5, size=n)
        y = (logits > 0).astype(int)
        self.ensemble.fit(X, y)
        self._fitted = True

    def predict_row(self, features: Sequence[float]) -> Dict[str, float]:
        if not self._fitted:
            self.fit_demo()
        return self.ensemble.predict_positive([list(features)])

    def feature_importance(self):
        return permutation_importance_stub(FEATURE_NAMES)
