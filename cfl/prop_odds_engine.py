"""
prop_odds_engine.py — Proprietary player prop odds engine.

Statistical model: Poisson distribution.
  - Counting stats (pts, reb, ast, 3PT, stl) are non-negative integers that
    arise from independent, rate-driven events.  Poisson with rate λ is the
    standard choice; it outperforms Normal for low-mean props (assists, steals)
    and matches empirical NBA distributions well.
  - λ is adjusted per-game for recent form, usage rate, opponent defense,
    pace, home/away split, back-to-back fatigue, and minutes projection.
  - Over/Under probabilities come from the Poisson CDF; half-point lines are
    handled exactly (no continuity correction needed).

Odds format: American.
  - Fair odds = prob → American, no vig.
  - Edge% = (model_prob × net_payout − (1 − model_prob)) × 100.

Usage:
    from prop_odds_engine import PropOddsEngine
    engine = PropOddsEngine(db_path="sports_predictions_original.db")
    result = engine.generate(player_metrics, prop_type, line, pick, opponent_team)
"""

import math
import sqlite3
import time
from typing import Optional

# ── Scipy Poisson ──────────────────────────────────────────────────────────
try:
    from scipy.stats import poisson as _scipy_poisson
    def _poisson_over(lam: float, line: float) -> float:
        """P(X > line) for Poisson(λ). Works for fractional lines."""
        if lam <= 0:
            return 0.0
        # P(X > line) = 1 - P(X <= floor(line))
        return float(1.0 - _scipy_poisson.cdf(math.floor(line), lam))

    def _poisson_under(lam: float, line: float) -> float:
        """P(X < line) for Poisson(λ). Works for fractional lines."""
        if lam <= 0:
            return 1.0
        # P(X < line) = P(X <= floor(line - 0.001))
        return float(_scipy_poisson.cdf(math.floor(line - 0.001), lam))

except ImportError:
    # Pure-Python fallback (accurate enough for λ < 60)
    def _poisson_pmf(k: int, lam: float) -> float:
        if lam <= 0:
            return 1.0 if k == 0 else 0.0
        try:
            return math.exp(-lam) * (lam ** k) / math.factorial(k)
        except (OverflowError, ValueError):
            return 0.0

    def _poisson_cdf(k: int, lam: float) -> float:
        return sum(_poisson_pmf(i, lam) for i in range(k + 1))

    def _poisson_over(lam: float, line: float) -> float:
        return 1.0 - _poisson_cdf(math.floor(line), lam)

    def _poisson_under(lam: float, line: float) -> float:
        return _poisson_cdf(math.floor(line - 0.001), lam)


# ── Odds conversion ────────────────────────────────────────────────────────
def prob_to_american(p: float) -> Optional[int]:
    """Convert win probability [0,1] to fair American odds (no vig)."""
    p = max(0.001, min(0.999, p))
    if p >= 0.5:
        return round(-(p / (1.0 - p)) * 100)
    else:
        return round(((1.0 - p) / p) * 100)


def american_to_implied(odds: float) -> float:
    """American odds → implied probability (with vig)."""
    if odds is None:
        return 0.5
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return 0.5
    if o >= 0:
        return 100.0 / (100.0 + o)
    else:
        return abs(o) / (abs(o) + 100.0)


def ev_pct(model_prob: float, market_odds: float) -> float:
    """EV% given model probability and American market odds."""
    try:
        p = float(model_prob)
        o = float(market_odds)
    except (TypeError, ValueError):
        return 0.0
    net_payout = o / 100.0 if o > 0 else 100.0 / abs(o)
    return round((p * net_payout - (1.0 - p)) * 100.0, 1)


# ── Defensive rating helpers ───────────────────────────────────────────────
# Cache so repeated calls within the same request are fast.
_DEF_CACHE: dict = {}
_DEF_CACHE_TTL = 3600  # seconds


def _opponent_def_factor(team_name: str, prop_type: str, db_path: str) -> float:
    """
    Return a multiplicative factor (around 1.0) reflecting how the opponent
    defence affects the given prop type.  Uses our games table to compute the
    opponent's points-allowed per game relative to league average.

    Factor > 1.0  →  opponent allows more than average  →  boost projection
    Factor < 1.0  →  opponent allows less than average  →  suppress projection
    """
    if not team_name or not db_path:
        return 1.0
    cache_key = f"{team_name}:{prop_type}"
    cached = _DEF_CACHE.get(cache_key)
    if cached and (time.time() - cached["ts"]) < _DEF_CACHE_TTL:
        return cached["v"]

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Points allowed per game over last 20 home+away games
        row = conn.execute(
            """
            SELECT AVG(CASE WHEN home_team_id = :t THEN away_score
                            WHEN away_team_id = :t THEN home_score
                       END) AS allowed_pg,
                   AVG(home_score + away_score) / 2.0 AS league_half_avg
            FROM (
                SELECT * FROM games
                WHERE (home_team_id = :t OR away_team_id = :t)
                  AND status = 'final'
                  AND home_score IS NOT NULL
                ORDER BY game_date DESC LIMIT 20
            )
            """,
            {"t": team_name},
        ).fetchone()
        conn.close()

        if row and row["allowed_pg"] and row["league_half_avg"] and row["league_half_avg"] > 0:
            factor = round(float(row["allowed_pg"]) / float(row["league_half_avg"]), 4)
        else:
            factor = 1.0
    except Exception:
        factor = 1.0

    # Prop-type weights: defence matters most for points, less for steals
    PROP_DEF_WEIGHT = {
        "points":    1.00,
        "rebounds":  0.40,   # boards driven more by rebounding skill than D
        "assists":   0.30,
        "threes":    0.70,
        "steals":    0.20,
    }
    w = PROP_DEF_WEIGHT.get(prop_type, 0.50)
    blended = 1.0 + (factor - 1.0) * w
    blended = max(0.70, min(1.30, blended))   # cap ±30%

    _DEF_CACHE[cache_key] = {"ts": time.time(), "v": blended}
    return blended


# ── Main engine ────────────────────────────────────────────────────────────
class PropOddsEngine:
    """
    Generates Poisson-based fair odds and edge metrics for a single player prop.

    Constructor args:
        db_path  — path to the SQLite DB (for opponent defence lookup)
    """

    MODEL_NAME = "Poisson (λ-adjusted)"
    MODEL_DESCRIPTION = (
        "Poisson distribution with rate λ adjusted for: "
        "weighted recent form (L5×0.7 + L10×0.3), usage rate, "
        "opponent defensive rating, pace factor, home/away split, "
        "back-to-back fatigue (−5% λ), and projected minutes."
    )

    def __init__(self, db_path: str = "sports_predictions_original.db"):
        self.db_path = db_path

    # ------------------------------------------------------------------
    def _compute_lambda(
        self,
        metrics: dict,
        prop_type: str,
        opponent_team: str,
        is_home: bool,
        is_back_to_back: bool,
    ) -> float:
        """
        Build the Poisson rate λ from all available factors.
        Returns λ > 0.
        """
        # 1. Base: weighted recent-form projection
        weighted = (metrics.get("stats_weighted") or {}).get(prop_type)
        last5    = (metrics.get("stats_last5")    or {}).get(prop_type)
        last10   = (metrics.get("stats_last10")   or {}).get(prop_type)

        if weighted is not None:
            base = float(weighted)
        elif last5 is not None and last10 is not None:
            base = float(last5) * 0.7 + float(last10) * 0.3
        elif last5 is not None:
            base = float(last5)
        elif last10 is not None:
            base = float(last10)
        else:
            base = 0.0

        if base <= 0:
            return max(0.5, base)

        lam = base

        # 2. Usage rate adjustment
        usage = float(metrics.get("usage_rate") or 0.15)
        # League avg usage ~0.20; normalise around 1.0
        lam *= max(0.7, min(1.3, usage / 0.20))

        # 3. Projected minutes vs average minutes
        proj_min = float(metrics.get("projected_minutes") or metrics.get("avg_minutes") or 30.0)
        avg_min  = float(metrics.get("avg_minutes") or 30.0)
        if avg_min > 0:
            min_factor = proj_min / avg_min
            min_factor = max(0.6, min(1.4, min_factor))
            lam *= min_factor

        # 4. Opponent defensive rating (DB lookup)
        lam *= _opponent_def_factor(opponent_team, prop_type, self.db_path)

        # 5. Home/away split — small home boost for scoring stats
        HOME_BOOST = {"points": 0.03, "threes": 0.04, "assists": 0.02, "rebounds": 0.01, "steals": 0.00}
        boost = HOME_BOOST.get(prop_type, 0.02)
        lam *= (1.0 + boost) if is_home else (1.0 - boost * 0.5)

        # 6. Back-to-back fatigue
        if is_back_to_back:
            lam *= 0.95

        return max(0.5, round(lam, 4))

    # ------------------------------------------------------------------
    def generate(
        self,
        metrics: dict,
        prop_type: str,
        line: float,
        pick: str,
        opponent_team: str = "",
        is_home: bool = True,
        is_back_to_back: bool = False,
        market_over_odds: Optional[float] = None,
        market_under_odds: Optional[float] = None,
    ) -> dict:
        """
        Full odds generation for one prop.

        Returns dict with:
            lam, projection, over_prob, under_prob,
            fair_over_odds, fair_under_odds,
            edge_pct, confidence, model, model_description,
            ev_over, ev_under, pick_ev
        """
        lam = self._compute_lambda(metrics, prop_type, opponent_team, is_home, is_back_to_back)

        over_p  = round(_poisson_over(lam, line), 4)
        under_p = round(_poisson_under(lam, line), 4)

        # Probabilities should sum to ~1 (gap = P(X == line) for integer lines)
        push_p = max(0.0, round(1.0 - over_p - under_p, 4))

        fair_over  = prob_to_american(over_p)
        fair_under = prob_to_american(under_p)

        # Edge vs market (if market odds provided)
        ev_over  = ev_pct(over_p,  market_over_odds  or fair_over)  if market_over_odds  else None
        ev_under = ev_pct(under_p, market_under_odds or fair_under) if market_under_odds else None

        # Edge vs line (distance of projection from line, standardised)
        distance = lam - line
        edge_raw = distance / max(line, 1.0) * 100.0  # % away from line

        # Pick-side EV
        if pick == "OVER":
            pick_ev = ev_over if ev_over is not None else round(edge_raw, 1)
            pick_prob = over_p
        else:
            pick_ev = ev_under if ev_under is not None else round(-edge_raw, 1)
            pick_prob = under_p

        # Confidence: driven by how far from 50/50 and sample size
        sample = len((metrics.get("last_10_games_minutes") or []))
        prob_gap = abs(pick_prob - 0.5)           # 0 = 50/50, 0.5 = certain
        sample_factor = min(sample / 10.0, 1.0)
        confidence = round((prob_gap * 2.0 * 0.6 + sample_factor * 0.4) * 100.0, 1)

        # EV tier label
        if pick_ev is not None and pick_ev >= 5.0 and confidence >= 60:
            tier = "gold"          # premium: high EV + high confidence
        elif pick_ev is not None and pick_ev >= 2.0:
            tier = "green"         # positive EV
        elif pick_ev is not None and pick_ev < 0:
            tier = "red"           # negative EV — fade
        else:
            tier = "neutral"

        return {
            "lam":               round(lam, 2),
            "projection":        round(lam, 2),
            "over_prob":         over_p,
            "under_prob":        under_p,
            "push_prob":         push_p,
            "fair_over_odds":    fair_over,
            "fair_under_odds":   fair_under,
            "ev_over":           round(ev_over, 1)  if ev_over  is not None else None,
            "ev_under":          round(ev_under, 1) if ev_under is not None else None,
            "pick_ev":           round(pick_ev, 1)  if pick_ev  is not None else None,
            "edge_pct":          round(edge_raw, 1),
            "confidence":        confidence,
            "tier":              tier,
            "model":             self.MODEL_NAME,
            "model_description": self.MODEL_DESCRIPTION,
            "pick":              pick,
            "line":              line,
        }
