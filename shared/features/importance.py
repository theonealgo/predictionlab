"""Feature importance helpers."""
from __future__ import annotations

from typing import List, Sequence


def rank_features(names: Sequence[str], importances: Sequence[float]) -> List[dict]:
    pairs = sorted(zip(names, importances), key=lambda x: abs(x[1]), reverse=True)
    return [{"feature": n, "importance": float(v)} for n, v in pairs]


def permutation_importance_stub(feature_names: Sequence[str]) -> List[dict]:
    n = len(feature_names)
    vals = [max(0.01, 1.0 - i * 0.08) for i in range(n)]
    total = sum(vals) or 1.0
    vals = [v / total for v in vals]
    return rank_features(feature_names, vals)
