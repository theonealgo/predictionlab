"""Probability calibration helpers."""
from __future__ import annotations

from typing import List, Sequence


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    if not probs:
        return 0.0
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def reliability_bins(probs: Sequence[float], outcomes: Sequence[int], n_bins: int = 10) -> List[dict]:
    bins = [{"n": 0, "sum_p": 0.0, "sum_o": 0.0} for _ in range(n_bins)]
    for p, o in zip(probs, outcomes):
        idx = min(n_bins - 1, max(0, int(p * n_bins)))
        bins[idx]["n"] += 1
        bins[idx]["sum_p"] += p
        bins[idx]["sum_o"] += o
    out = []
    for i, b in enumerate(bins):
        n = b["n"] or 1
        out.append({
            "bin": i,
            "count": b["n"],
            "avg_pred": (b["sum_p"] / n) if b["n"] else None,
            "avg_outcome": (b["sum_o"] / n) if b["n"] else None,
        })
    return out
