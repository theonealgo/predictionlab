"""
ML wrappers: LogisticRegression, RandomForest, XGBoost, LightGBM, soft ensemble.

Decision notes:
- LogReg = calibrated linear baseline (interpretable).
- RF = nonlinear bagging without heavy tuning.
- XGB/LGBM = primary gradient boosters; soft-vote averages predict_proba.
- Missing optional libs fall back to sklearn-only so the hub always runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import numpy as np

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
except Exception:  # pragma: no cover
    RandomForestClassifier = None
    LogisticRegression = None
    Pipeline = None
    StandardScaler = None

try:
    from xgboost import XGBClassifier
except Exception:  # pragma: no cover
    XGBClassifier = None

try:
    from lightgbm import LGBMClassifier
except Exception:  # pragma: no cover
    LGBMClassifier = None


def _as_2d(X) -> np.ndarray:
    arr = np.asarray(X, dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    return arr


@dataclass
class ModelBundle:
    name: str
    model: Any
    fitted: bool = False

    def fit(self, X, y) -> "ModelBundle":
        self.model.fit(_as_2d(X), np.asarray(y))
        self.fitted = True
        return self

    def predict_proba(self, X) -> np.ndarray:
        X2 = _as_2d(X)
        if hasattr(self.model, "predict_proba"):
            return self.model.predict_proba(X2)
        if hasattr(self.model, "decision_function"):
            z = np.asarray(self.model.decision_function(X2), dtype=float)
            p = 1.0 / (1.0 + np.exp(-z))
            return np.column_stack([1 - p, p])
        preds = np.asarray(self.model.predict(X2), dtype=float)
        return np.column_stack([1 - preds, preds])


@dataclass
class EnsembleClassifier:
    models: Dict[str, ModelBundle] = field(default_factory=dict)
    weights: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def build_default(cls) -> "EnsembleClassifier":
        models: Dict[str, ModelBundle] = {}
        if LogisticRegression is not None:
            pipe = Pipeline([
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(max_iter=500)),
            ])
            models["logreg"] = ModelBundle("logreg", pipe)
        if RandomForestClassifier is not None:
            models["rf"] = ModelBundle(
                "rf",
                RandomForestClassifier(n_estimators=80, max_depth=6, random_state=42),
            )
        if XGBClassifier is not None:
            models["xgb"] = ModelBundle(
                "xgb",
                XGBClassifier(
                    n_estimators=80, max_depth=4, learning_rate=0.08,
                    eval_metric="logloss", verbosity=0,
                ),
            )
        if LGBMClassifier is not None:
            models["lgbm"] = ModelBundle(
                "lgbm",
                LGBMClassifier(n_estimators=80, max_depth=4, learning_rate=0.08, verbose=-1),
            )
        if not models:
            raise RuntimeError("No sklearn classifiers available")
        return cls(models=models, weights={k: 1.0 for k in models})

    def fit(self, X, y) -> "EnsembleClassifier":
        for m in self.models.values():
            m.fit(X, y)
        return self

    def predict_proba(self, X) -> Dict[str, np.ndarray]:
        out = {name: m.predict_proba(X) for name, m in self.models.items() if m.fitted}
        if not out:
            raise RuntimeError("Ensemble not fitted")
        keys = list(out.keys())
        wsum = sum(self.weights.get(k, 1.0) for k in keys)
        ens = sum(out[k] * self.weights.get(k, 1.0) for k in keys) / wsum
        out["ensemble"] = ens
        return out

    def predict_positive(self, X) -> Dict[str, float]:
        probs = self.predict_proba(X)
        return {k: float(v[0, 1] if v.ndim == 2 else v[1]) for k, v in probs.items()}
