"""MLB run-line v2 — separate margin model; do NOT auto -1.5 every ML favorite.

Isolation only. Does not change Efficiency / O/U scoring.
"""
from __future__ import annotations

from typing import Any

RUN_LINE = 1.5


def projected_run_margin(
    *,
    home_win_prob: float | None,
    offense_l10_diff: float | None = None,
    ra_l10_diff: float | None = None,
    book_spread: float | None = None,
    rest_diff: float | None = None,
) -> float | None:
    """Home-centric projected run margin from available signals (no invented stats)."""
    parts: list[tuple[float, float]] = []
    if home_win_prob is not None:
        p = float(home_win_prob)
        if p > 1.5:
            p = p / 100.0
        # Soft map used elsewhere: 0.5→0, 0.65→~1.5
        parts.append(((p - 0.5) * 10.0, 1.0))
    if book_spread is not None:
        try:
            parts.append((-float(book_spread), 0.85))
        except (TypeError, ValueError):
            pass
    if offense_l10_diff is not None:
        parts.append((float(offense_l10_diff) * 0.55, 0.45))
    if ra_l10_diff is not None:
        # Positive ra_l10_diff = away allows more runs recently → home pitching edge
        parts.append((float(ra_l10_diff) * 0.40, 0.40))
    if rest_diff is not None:
        parts.append((max(-1.0, min(1.0, float(rest_diff))) * 0.15, 0.15))
    if not parts:
        return None
    num = sum(v * w for v, w in parts)
    den = sum(w for _, w in parts)
    return num / den if den else None


def run_line_confidence(
    margin: float | None,
    *,
    models_agree_n: int | None = None,
    market_edge: float | None = None,
    pitching_edge: float | None = None,
    power_edge: float | None = None,
    sp_edge: float | None = None,
    bullpen_edge: float | None = None,
    recent_form: float | None = None,
    park_factor: float | None = None,
    home_away_edge: float | None = None,
) -> float:
    """0–100 user-facing Run Line Confidence (no methodology dump).

    Uses available proxies only — missing feeds are skipped, never invented.
    """
    if margin is None:
        return 0.0
    mag = abs(float(margin))
    score = 35.0 + min(35.0, mag * 12.0)
    if models_agree_n is not None and models_agree_n >= 4:
        score += 8.0
    elif models_agree_n is not None and models_agree_n >= 3:
        score += 4.0
    if market_edge is not None and abs(float(market_edge)) >= 0.05:
        score += 6.0
    pitch = pitching_edge if pitching_edge is not None else sp_edge
    if pitch is not None and pitch > 0.25:
        score += 6.0
    if bullpen_edge is not None and bullpen_edge > 0.20:
        score += 4.0
    if power_edge is not None and power_edge > 0.25:
        score += 5.0
    if recent_form is not None and recent_form > 0.20:
        score += 3.0
    if park_factor is not None:
        # Subtle park tilt only when we have a real factor (≈1.0 neutral)
        try:
            pf = float(park_factor)
            if abs(pf - 1.0) >= 0.05:
                score += 2.0
        except (TypeError, ValueError):
            pass
    if home_away_edge is not None and home_away_edge > 0.15:
        score += 3.0
    if mag < 1.25:
        score -= 15.0
    return float(max(0.0, min(100.0, round(score, 1))))


def recommend_run_line(
    margin: float | None,
    *,
    models_agree_n: int | None = None,
    market_edge: float | None = None,
    pitching_edge: float | None = None,
    power_edge: float | None = None,
    min_margin: float = 2.0,
    min_confidence: float = 55.0,
    min_support: int = 1,
) -> dict[str, Any]:
    """Only recommend -1.5 when projected margin >= 2 AND support signals.

    Otherwise: ml_only / no_spread.
    Default min_support=1 because true SP/bullpen feeds are often missing;
    RA-l10 / offense-l10 proxies count as one support each when present.
    """
    conf = run_line_confidence(
        margin,
        models_agree_n=models_agree_n,
        market_edge=market_edge,
        pitching_edge=pitching_edge,
        power_edge=power_edge,
    )
    if margin is None:
        return {
            "action": "no_spread",
            "side": None,
            "line": None,
            "projected_margin": None,
            "run_line_confidence": conf,
            "reason": "no_margin",
        }
    m = float(margin)
    support = 0
    if models_agree_n is not None and models_agree_n >= 3:
        support += 1
    if pitching_edge is not None and pitching_edge > 0.15:
        support += 1
    if power_edge is not None and power_edge > 0.15:
        support += 1
    if market_edge is not None and abs(float(market_edge)) >= 0.04:
        support += 1

    if abs(m) >= min_margin and support >= min_support and conf >= min_confidence:
        if m >= min_margin:
            return {
                "action": "spread",
                "side": "HOME",
                "line": -RUN_LINE,
                "projected_margin": round(m, 2),
                "run_line_confidence": conf,
                "reason": "margin_and_support",
            }
        return {
            "action": "spread",
            "side": "AWAY",
            "line": -RUN_LINE,
            "projected_margin": round(m, 2),
            "run_line_confidence": conf,
            "reason": "margin_and_support",
        }

    return {
        "action": "ml_only",
        "side": "HOME" if m >= 0 else "AWAY",
        "line": None,
        "projected_margin": round(m, 2),
        "run_line_confidence": conf,
        "reason": "margin_or_support_insufficient",
    }


def grade_minus_1_5(side: str, home_score: float, away_score: float) -> bool:
    am = float(home_score) - float(away_score)
    if side == "HOME":
        return am > RUN_LINE
    return am < -RUN_LINE
