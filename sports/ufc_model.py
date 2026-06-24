"""
UFC Prediction Model
====================

Uses the existing PredictionLab model framework with UFC-specific configuration:

  Grinder2       → Glicko-2 rating system
  Takedown       → TrueSkill rating system
  Edge           → Elo rating system
  XSharp         → XGBoost (rating features + fight history)
  Sharp Consensus → Calibrated stacked ensemble

Public model names are unchanged.  No new model brands created.
"""

from __future__ import annotations

import logging
import math
import os
import pickle
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── paths ──────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent.parent          # predictionmay/
_MODEL_DIR = _HERE / 'models' / 'UFC_v2'
_HISTORY_CACHE_FILE = _HERE / 'models' / 'ufc_fight_history.pkl'
_HISTORY_CACHE_TTL = 86400 * 7   # re-fetch ESPN history once a week

# ── training constants ──────────────────────────────────────────────────────
_MIN_FIGHTS_TO_TRAIN = 50        # refuse to train on tiny datasets
_YEARS_BACK = 4                  # how far back to pull ESPN history
_UFC_K = 40                      # Elo K-factor for MMA (higher than team sports)


# ===========================================================================
# Rating helpers (self-contained — no dependency on prediction_system_v2)
# ===========================================================================

class _EloRatings:
    """Simple Elo for fast baseline predictions (Edge model)."""

    def __init__(self, k: float = _UFC_K, default: float = 1500.0):
        self.k = k
        self.default = default
        self.ratings: Dict[str, float] = {}
        self.fight_count: Dict[str, int] = {}
        # Serialized with model so inference has real fighter state
        self.win_streak: Dict[str, int] = {}
        self.loss_streak: Dict[str, int] = {}
        self.last_fight_date: Dict[str, str] = {}

    def _expected(self, ra: float, rb: float) -> float:
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

    def get(self, name: str) -> float:
        return self.ratings.get(name, self.default)

    def update(self, winner: str, loser: str, date_str: str = '') -> None:
        rw, rl = self.get(winner), self.get(loser)
        ew = self._expected(rw, rl)
        self.ratings[winner] = rw + self.k * (1 - ew)
        self.ratings[loser]  = rl + self.k * (0 - (1 - ew))
        self.fight_count[winner] = self.fight_count.get(winner, 0) + 1
        self.fight_count[loser]  = self.fight_count.get(loser,  0) + 1
        self.win_streak[winner]  = self.win_streak.get(winner, 0) + 1
        self.loss_streak[winner] = 0
        self.win_streak[loser]   = 0
        self.loss_streak[loser]  = self.loss_streak.get(loser, 0) + 1
        if date_str:
            self.last_fight_date[winner] = date_str
            self.last_fight_date[loser]  = date_str

    def win_prob(self, a: str, b: str) -> float:
        return self._expected(self.get(a), self.get(b))

    def n_fights(self, name: str) -> int:
        return self.fight_count.get(name, 0)


class _Glicko2Ratings:
    """Glicko-2 implementation (Grinder2 model)."""

    TAU = 0.5
    SCALE = 173.7178

    def __init__(self, mu0: float = 1500.0, rd0: float = 350.0, vol0: float = 0.06):
        self.mu0, self.rd0, self.vol0 = mu0, rd0, vol0
        self.mu:  Dict[str, float] = {}
        self.rd:  Dict[str, float] = {}
        self.vol: Dict[str, float] = {}

    def _get(self, name: str) -> Tuple[float, float, float]:
        return (
            self.mu.get(name, self.mu0),
            self.rd.get(name, self.rd0),
            self.vol.get(name, self.vol0),
        )

    def _g(self, rd: float) -> float:
        return 1.0 / math.sqrt(1 + 3 * (rd / self.SCALE) ** 2 / math.pi ** 2)

    def _e(self, mu: float, mj: float, rd_j: float) -> float:
        return 1.0 / (1 + math.exp(-self._g(rd_j) * (mu - mj) / self.SCALE))

    def update(self, winner: str, loser: str) -> None:
        for (a_name, a_score), (b_name, b_score) in [
            ((winner, 1.0), (loser, 0.0)),
            ((loser, 0.0), (winner, 1.0)),
        ]:
            mu_a, rd_a, vol_a = self._get(a_name)
            mu_b, rd_b, _     = self._get(b_name)

            mu_a_s  = (mu_a - 1500) / self.SCALE
            mu_b_s  = (mu_b - 1500) / self.SCALE
            rd_a_s  = rd_a / self.SCALE
            rd_b_s  = rd_b / self.SCALE

            g_b = self._g(rd_b)
            e_ab = 1.0 / (1 + math.exp(-g_b * (mu_a_s - mu_b_s)))
            v = 1.0 / (g_b ** 2 * e_ab * (1 - e_ab) + 1e-9)
            delta = v * g_b * (a_score - e_ab)

            # volatility update (simplified)
            a_sq = (delta / (rd_a_s ** 2 + v)) ** 2
            eps = 0.000001
            f = lambda x: (
                math.exp(x) * (a_sq - rd_a_s ** 2 - v - math.exp(x))
                / (2 * (rd_a_s ** 2 + v + math.exp(x)) ** 2)
                - (x - math.log(vol_a ** 2)) / self.TAU ** 2
            )
            A = math.log(vol_a ** 2)
            if a_sq > rd_a_s ** 2 + v:
                B = math.log(a_sq - rd_a_s ** 2 - v)
            else:
                k = 1
                while f(A - k * self.TAU) < 0:
                    k += 1
                B = A - k * self.TAU
            fA, fB = f(A), f(B)
            for _ in range(100):
                C = A + (A - B) * fA / (fB - fA + 1e-12)
                fC = f(C)
                if fC * fB < 0:
                    A, fA = B, fB
                else:
                    fA /= 2
                B, fB = C, fC
                if abs(B - A) < eps:
                    break
            vol_new = math.exp(A / 2)

            rd_star = math.sqrt(rd_a_s ** 2 + vol_new ** 2)
            rd_new_s = 1.0 / math.sqrt(1.0 / rd_star ** 2 + 1.0 / v)
            mu_new_s = mu_a_s + rd_new_s ** 2 * g_b * (a_score - e_ab)

            self.mu[a_name]  = mu_new_s * self.SCALE + 1500
            self.rd[a_name]  = rd_new_s * self.SCALE
            self.vol[a_name] = vol_new

    def win_prob(self, a: str, b: str) -> float:
        mu_a, rd_a, _ = self._get(a)
        mu_b, rd_b, _ = self._get(b)
        combined_rd = math.sqrt(rd_a ** 2 + rd_b ** 2)
        g = self._g(combined_rd)
        return 1.0 / (1 + math.exp(-g * (mu_a - mu_b) / self.SCALE))

    def get_mu(self, name: str) -> float:
        return self.mu.get(name, self.mu0)

    def get_rd(self, name: str) -> float:
        return self.rd.get(name, self.rd0)


class _TrueSkillRatings:
    """TrueSkill implementation (Takedown model)."""

    BETA  = 25.0 / 6.0
    TAU   = 25.0 / 300.0
    DRAW_PROB = 0.0

    def __init__(self, mu0: float = 25.0, sigma0: float = 25.0 / 3.0):
        self.mu0, self.sigma0 = mu0, sigma0
        self.mu:    Dict[str, float] = {}
        self.sigma: Dict[str, float] = {}

    def get(self, name: str) -> Tuple[float, float]:
        return (self.mu.get(name, self.mu0), self.sigma.get(name, self.sigma0))

    def _v(self, t: float, eps: float) -> float:
        from scipy.stats import norm
        return norm.pdf(t - eps) / (norm.cdf(t - eps) + 1e-12)

    def _w(self, t: float, eps: float) -> float:
        v = self._v(t, eps)
        return v * (v + t - eps)

    def update(self, winner: str, loser: str, margin: float = 1.0) -> None:
        try:
            from scipy.stats import norm
        except ImportError:
            # Fallback: simple update without scipy
            mu_w, sig_w = self.get(winner)
            mu_l, sig_l = self.get(loser)
            c2 = 2 * self.BETA ** 2 + sig_w ** 2 + sig_l ** 2
            c  = math.sqrt(c2)
            t  = (mu_w - mu_l) / c
            eps = 0.0
            try:
                v_val = math.exp(-0.5 * t ** 2) / (math.sqrt(2 * math.pi) * max(0.5 + 0.5 * math.erf(t / math.sqrt(2)), 1e-12))
                w_val = v_val * (v_val + t)
            except Exception:
                v_val, w_val = 0.5, 0.25
            self.mu[winner]    = mu_w + sig_w ** 2 / c * v_val
            self.mu[loser]     = mu_l - sig_l ** 2 / c * v_val
            self.sigma[winner] = math.sqrt(sig_w ** 2 * (1 - sig_w ** 2 / c2 * w_val))
            self.sigma[loser]  = math.sqrt(sig_l ** 2 * (1 - sig_l ** 2 / c2 * w_val))
            # Dynamics noise
            self.sigma[winner] = math.sqrt(self.sigma[winner] ** 2 + self.TAU ** 2)
            self.sigma[loser]  = math.sqrt(self.sigma[loser]  ** 2 + self.TAU ** 2)
            return

        mu_w, sig_w = self.get(winner)
        mu_l, sig_l = self.get(loser)
        c2 = 2 * self.BETA ** 2 + sig_w ** 2 + sig_l ** 2
        c  = math.sqrt(c2)
        t  = (mu_w - mu_l) / c
        eps = 0.0

        v_val = norm.pdf(t - eps) / max(norm.cdf(t - eps), 1e-12)
        w_val = v_val * (v_val + t - eps)

        self.mu[winner]    = mu_w + sig_w ** 2 / c * v_val
        self.mu[loser]     = mu_l - sig_l ** 2 / c * v_val
        self.sigma[winner] = math.sqrt(sig_w ** 2 * (1 - sig_w ** 2 / c2 * w_val))
        self.sigma[loser]  = math.sqrt(sig_l ** 2 * (1 - sig_l ** 2 / c2 * w_val))
        self.sigma[winner] = math.sqrt(self.sigma[winner] ** 2 + self.TAU ** 2)
        self.sigma[loser]  = math.sqrt(self.sigma[loser]  ** 2 + self.TAU ** 2)

    def win_prob(self, a: str, b: str) -> float:
        mu_a, sig_a = self.get(a)
        mu_b, sig_b = self.get(b)
        denom = math.sqrt(2 * self.BETA ** 2 + sig_a ** 2 + sig_b ** 2)
        z = (mu_a - mu_b) / denom
        return 0.5 * (1 + math.erf(z / math.sqrt(2)))

    def conservative(self, name: str) -> float:
        mu, sig = self.get(name)
        return mu - 3 * sig


# ===========================================================================
# Feature engineering
# ===========================================================================

def _build_features(
    fights_df: pd.DataFrame,
    elo: _EloRatings,
    glicko: _Glicko2Ratings,
    ts: _TrueSkillRatings,
) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    Walk-forward feature extraction — ratings updated BEFORE each fight is used
    as a training sample (no data leakage).

    Returns X (features), y (labels: 1=home_team won), feature_names.
    """
    rows: List[Dict] = []
    labels: List[float] = []

    # streak/days state lives in elo so it's available at inference

    for _, row in fights_df.iterrows():
        home = row['home_team']
        away = row['away_team']
        home_won = float(row['home_score']) > float(row['away_score'])
        date_str = str(row['date'])[:10]

        # ── pre-fight ratings (BEFORE update) ──────────────────────────────
        g2_home = glicko.get_mu(home)
        g2_away = glicko.get_mu(away)
        g2_rd_home = glicko.get_rd(home)
        g2_rd_away = glicko.get_rd(away)
        g2_prob = glicko.win_prob(home, away)

        ts_mu_home, ts_sig_home = ts.get(home)
        ts_mu_away, ts_sig_away = ts.get(away)
        ts_prob = ts.win_prob(home, away)
        ts_con_home = ts.conservative(home)
        ts_con_away = ts.conservative(away)

        elo_home = elo.get(home)
        elo_away = elo.get(away)
        elo_prob = elo.win_prob(home, away)

        n_home = elo.n_fights(home)
        n_away = elo.n_fights(away)

        # days since last fight (from elo state, which persists to inference)
        try:
            cur_dt = datetime.strptime(date_str, '%Y-%m-%d')
            if home in elo.last_fight_date:
                lh = datetime.strptime(elo.last_fight_date[home], '%Y-%m-%d')
                days_home = (cur_dt - lh).days
            else:
                days_home = 365
            if away in elo.last_fight_date:
                la = datetime.strptime(elo.last_fight_date[away], '%Y-%m-%d')
                days_away = (cur_dt - la).days
            else:
                days_away = 365
        except Exception:
            days_home = days_away = 180

        ws_home = elo.win_streak.get(home, 0)
        ws_away = elo.win_streak.get(away, 0)
        ls_home = elo.loss_streak.get(home, 0)
        ls_away = elo.loss_streak.get(away, 0)

        feat = {
            # Grinder2 (Glicko-2) features
            'g2_diff':          g2_home - g2_away,
            'g2_home':          g2_home,
            'g2_away':          g2_away,
            'g2_rd_diff':       g2_rd_home - g2_rd_away,
            'g2_rd_home':       g2_rd_home,
            'g2_rd_away':       g2_rd_away,
            'g2_prob':          g2_prob,

            # Takedown (TrueSkill) features
            'ts_diff':          ts_mu_home - ts_mu_away,
            'ts_home':          ts_mu_home,
            'ts_away':          ts_mu_away,
            'ts_sig_diff':      ts_sig_home - ts_sig_away,
            'ts_con_diff':      ts_con_home - ts_con_away,
            'ts_prob':          ts_prob,

            # Edge (Elo) features
            'elo_diff':         elo_home - elo_away,
            'elo_home':         elo_home,
            'elo_away':         elo_away,
            'elo_prob':         elo_prob,

            # Experience / activity
            'fights_home':      n_home,
            'fights_away':      n_away,
            'fights_diff':      n_home - n_away,
            'days_home':        days_home,
            'days_away':        days_away,
            'days_diff':        days_home - days_away,
            'inactivity_home':  min(days_home, 730) / 730.0,
            'inactivity_away':  min(days_away, 730) / 730.0,

            # Streak features
            'win_streak_home':  ws_home,
            'win_streak_away':  ws_away,
            'loss_streak_home': ls_home,
            'loss_streak_away': ls_away,
            'streak_diff':      ws_home - ws_away,

            # Uncertainty interaction
            'uncertainty_home': min(g2_rd_home / 350.0, 1.0),
            'uncertainty_away': min(g2_rd_away / 350.0, 1.0),
            'debut_home':       1.0 if n_home == 0 else 0.0,
            'debut_away':       1.0 if n_away == 0 else 0.0,

            # Model agreement
            'model_agreement':  abs(g2_prob - ts_prob) + abs(g2_prob - elo_prob),
        }

        rows.append(feat)
        labels.append(1.0 if home_won else 0.0)

        # ── post-fight updates ──────────────────────────────────────────────
        winner = home if home_won else away
        loser  = away if home_won else home

        glicko.update(winner, loser)
        ts.update(winner, loser)
        elo.update(winner, loser, date_str)   # date_str stores last_fight_date + streaks

    if not rows:
        return np.array([]), np.array([]), []

    df = pd.DataFrame(rows)
    feature_names = list(df.columns)
    return df.values.astype(np.float32), np.array(labels, dtype=np.float32), feature_names


# ===========================================================================
# Data fetching
# ===========================================================================

def _fetch_ufc_history(years_back: int = _YEARS_BACK) -> pd.DataFrame:
    """
    Pull completed UFC fight results from ESPN scoreboard.
    Caches to disk (refreshed weekly).
    """
    # Check on-disk cache
    if _HISTORY_CACHE_FILE.exists():
        try:
            with open(_HISTORY_CACHE_FILE, 'rb') as fh:
                cached = pickle.load(fh)
            if time.time() - cached.get('ts', 0) < _HISTORY_CACHE_TTL:
                df = cached.get('df')
                if isinstance(df, pd.DataFrame) and len(df) >= _MIN_FIGHTS_TO_TRAIN:
                    logger.info(f"[UFC] Loaded {len(df)} fights from cache")
                    return df
        except Exception:
            pass

    rows: List[Dict] = []
    now = datetime.now()

    from sports._individual_sport import _fetch_scoreboard_events, _competitor_name, _athlete_id
    from sports._individual_sport import _ordered_pair, _event_datetime, _is_final_status
    from sports._individual_sport import _parse_match_scores

    def _main():
        import sys
        mod = sys.modules.get('__main__')
        if mod and hasattr(mod, '_cached_get'):
            return mod
        import NHL77FINAL as m
        return m

    m = _main()

    seen: set = set()
    for year_offset in range(years_back + 1):
        year = now.year - year_offset
        start_dt = datetime(year, 1, 1)
        end_dt = datetime(year, 12, 31) if year < now.year else now - timedelta(days=1)
        if end_dt < start_dt:
            continue

        start_str = start_dt.strftime('%Y%m%d')
        end_str   = end_dt.strftime('%Y%m%d')

        try:
            events = _fetch_scoreboard_events(m, 'mma', 'ufc',
                                              start_str=start_str, end_str=end_str)
        except Exception as exc:
            logger.warning(f"[UFC] history fetch {year}: {exc}")
            continue

        for event in events:
            for comp in (event.get('competitions') or []):
                competitors = comp.get('competitors') or []
                pair = _ordered_pair(competitors)
                if not pair:
                    continue
                p1, p2 = pair
                home_name = _competitor_name(p1)
                away_name = _competitor_name(p2)
                if not home_name or not away_name or home_name == 'TBD' or away_name == 'TBD':
                    continue

                status_name = (
                    (comp.get('status') or event.get('status') or {})
                    .get('type', {})
                    .get('name', '')
                )
                hs, aws = _parse_match_scores(status_name, p1, p2)
                if hs is None or aws is None:
                    continue   # not finished

                raw_dt = _event_datetime(event, comp)
                try:
                    game_date = m._espn_event_date_to_local(raw_dt) or str(now.date())
                except Exception:
                    game_date = str(now.date())

                key = (game_date, home_name, away_name)
                if key in seen:
                    continue
                seen.add(key)

                rows.append({
                    'home_team': home_name,
                    'away_team': away_name,
                    'home_score': float(hs),
                    'away_score': float(aws),
                    'date': game_date,
                })

    if not rows:
        logger.warning("[UFC] No historical fight data fetched from ESPN")
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values('date').reset_index(drop=True)
    logger.info(f"[UFC] Fetched {len(df)} completed fights from ESPN")

    # Persist cache
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    _HISTORY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_HISTORY_CACHE_FILE, 'wb') as fh:
            pickle.dump({'ts': time.time(), 'df': df}, fh)
    except Exception:
        pass

    return df


# ===========================================================================
# UFCPredictor
# ===========================================================================

class UFCPredictor:
    """
    Prediction system for UFC fights.

    Exposes the same interface as AdvancedPredictor so it slots directly
    into V2_PREDICTORS and get_v2_prediction() without modification.

    Internal models:
        glicko  → Grinder2
        ts      → Takedown
        elo     → Edge
        xgb     → XSharp
        ensemble (calibrated) → Sharp Consensus
    """

    def __init__(self):
        self.glicko  = _Glicko2Ratings()
        self.ts      = _TrueSkillRatings()
        self.elo     = _EloRatings()
        self.xgb     = None          # sklearn-compatible XGBoost model
        self.calibrator = None       # Platt / isotonic calibrator
        self.feature_names: List[str] = []
        self.trained = False
        self.training_metrics: Dict = {}

    # ── public API (mirrors AdvancedPredictor) ─────────────────────────────

    def predict(self, games: pd.DataFrame) -> pd.DataFrame:
        """
        Generate predictions for upcoming fights.

        games must have columns: home_team, away_team, date
        Returns DataFrame with home_win_prob, glicko2_prob, trueskill_prob,
        xgboost_prob, confidence, model_agreement, predicted_winner, is_v2
        """
        results = []
        for _, game in games.iterrows():
            home = str(game.get('home_team') or game.get('home_team_id') or '')
            away = str(game.get('away_team') or game.get('away_team_id') or '')

            g2_prob  = self.glicko.win_prob(home, away)
            ts_prob  = self.ts.win_prob(home, away)
            elo_prob = self.elo.win_prob(home, away)

            xgb_prob = None
            if self.trained and self.calibrator is not None:
                try:
                    feat = self._make_predict_features(home, away, str(game.get('date', '')))
                    xp = float(self.calibrator.predict_proba(feat)[0, 1])
                    xgb_prob = xp
                except Exception as exc:
                    logger.debug(f"[UFC] XSharp predict failed ({home} vs {away}): {exc}")
            elif self.trained and self.xgb is not None:
                try:
                    feat = self._make_predict_features(home, away, str(game.get('date', '')))
                    xgb_prob = float(self.xgb.predict_proba(feat)[0, 1])
                except Exception as exc:
                    logger.debug(f"[UFC] XSharp predict failed ({home} vs {away}): {exc}")

            # Sharp Consensus: rating models carry 90% by default;
            # XSharp gets a smaller slice (0.10) since XGBoost can be miscalibrated
            # on unseen fighters — ratings are more robust out-of-sample.
            probs = [g2_prob, ts_prob, elo_prob]
            weights = [0.35, 0.35, 0.20]
            if xgb_prob is not None:
                probs.append(xgb_prob)
                weights.append(0.10)
            wt = sum(weights)
            ensemble = sum(p * w for p, w in zip(probs, weights)) / wt

            # Clamp to avoid degenerate extremes
            ensemble  = max(0.05, min(0.95, ensemble))
            g2_prob   = max(0.05, min(0.95, g2_prob))
            ts_prob   = max(0.05, min(0.95, ts_prob))
            elo_prob  = max(0.05, min(0.95, elo_prob))
            if xgb_prob is not None:
                xgb_prob = max(0.05, min(0.95, xgb_prob))

            # Confidence: how far from 50% the consensus is
            confidence = abs(ensemble - 0.5) * 2.0

            # Model agreement: 1 if all models agree on winner, 0 if split
            plist = [g2_prob, ts_prob, elo_prob]
            if xgb_prob is not None:
                plist.append(xgb_prob)
            picks = [1 if p > 0.5 else 0 for p in plist]
            agreement = sum(picks) / len(picks) if picks else 0.5
            # Map to agreement score: 1.0 = unanimous, 0.0 = split
            agreement = abs(agreement - 0.5) * 2.0

            results.append({
                'home_team':        home,
                'away_team':        away,
                'date':             str(game.get('date', '')),
                'home_win_prob':    round(ensemble, 4),
                'away_win_prob':    round(1 - ensemble, 4),
                'predicted_winner': home if ensemble > 0.5 else away,
                'confidence':       round(confidence, 3),
                'model_agreement':  round(agreement, 2),
                'glicko2_prob':     round(g2_prob, 4),
                'trueskill_prob':   round(ts_prob, 4),
                'xgboost_prob':     round(xgb_prob, 4) if xgb_prob is not None else round(elo_prob, 4),
                'is_v2':            True,
                # Ratings for diagnostics
                'home_glicko2':         round(self.glicko.get_mu(home), 1),
                'away_glicko2':         round(self.glicko.get_mu(away), 1),
                'home_glicko2_uncertainty': round(self.glicko.get_rd(home), 1),
                'away_glicko2_uncertainty': round(self.glicko.get_rd(away), 1),
                'home_trueskill_mu':    round(self.ts.get(home)[0], 2),
                'away_trueskill_mu':    round(self.ts.get(away)[0], 2),
                'home_trueskill_sigma': round(self.ts.get(home)[1], 2),
                'away_trueskill_sigma': round(self.ts.get(away)[1], 2),
            })

        return pd.DataFrame(results) if results else pd.DataFrame()

    def train(self, fights_df: pd.DataFrame) -> Dict:
        """Train all models on historical fights."""
        if len(fights_df) < _MIN_FIGHTS_TO_TRAIN:
            raise ValueError(f"Need ≥ {_MIN_FIGHTS_TO_TRAIN} completed fights, got {len(fights_df)}")

        fights_df = fights_df.sort_values('date').reset_index(drop=True)
        logger.info(f"[UFC] Training on {len(fights_df)} fights")

        # Walk-forward feature extraction (updates ratings in place)
        self.glicko  = _Glicko2Ratings()
        self.ts      = _TrueSkillRatings()
        self.elo     = _EloRatings()

        X, y, feat_names = _build_features(fights_df, self.elo, self.glicko, self.ts)
        self.feature_names = feat_names

        if len(X) < _MIN_FIGHTS_TO_TRAIN:
            raise ValueError("Not enough complete feature rows after build")

        # ── XGBoost (XSharp) ────────────────────────────────────────────────
        try:
            import xgboost as xgb
            xgb_model = xgb.XGBClassifier(
                n_estimators=300,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                reg_alpha=0.5,
                reg_lambda=1.0,
                eval_metric='logloss',
                random_state=42,
            )
            xgb_model.fit(X, y)
            self.xgb = xgb_model
            logger.info("[UFC] XSharp (XGBoost) trained")
        except ImportError:
            logger.warning("[UFC] xgboost not installed — XSharp model skipped")
            self.xgb = None
        except Exception as exc:
            logger.warning(f"[UFC] XGBoost training failed: {exc}")
            self.xgb = None

        # ── Platt calibration (cross-validated to avoid in-sample overfitting) ─
        if self.xgb is not None:
            try:
                from sklearn.calibration import CalibratedClassifierCV
                import xgboost as xgb
                base = xgb.XGBClassifier(
                    n_estimators=300,
                    max_depth=4,
                    learning_rate=0.05,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    reg_alpha=0.5,
                    reg_lambda=1.0,
                    eval_metric='logloss',
                    random_state=42,
                )
                cal_model = CalibratedClassifierCV(base, cv=5, method='isotonic')
                cal_model.fit(X, y)
                self.calibrator = cal_model
            except Exception as exc:
                logger.warning(f"[UFC] CV calibration failed, falling back: {exc}")
                # Fallback: simple in-sample logistic scaling
                try:
                    from sklearn.linear_model import LogisticRegression
                    raw_probs = self.xgb.predict_proba(X)[:, 1].reshape(-1, 1)
                    cal = LogisticRegression()
                    cal.fit(raw_probs, y)
                    self.calibrator = cal
                except Exception:
                    self.calibrator = None

        # ── Evaluation ──────────────────────────────────────────────────────
        try:
            g2_preds  = np.array([self.glicko.win_prob(r['home_team'], r['away_team']) for _, r in fights_df.iterrows()])
            ts_preds  = np.array([self.ts.win_prob(r['home_team'], r['away_team'])     for _, r in fights_df.iterrows()])
            elo_preds = np.array([self.elo.win_prob(r['home_team'], r['away_team'])    for _, r in fights_df.iterrows()])
            true_y    = (fights_df['home_score'] > fights_df['away_score']).astype(float).values

            def _acc(preds): return float(np.mean((preds > 0.5) == true_y))

            self.training_metrics = {
                'n_fights':      len(fights_df),
                'glicko2_acc':   round(_acc(g2_preds),  3),
                'trueskill_acc': round(_acc(ts_preds),  3),
                'elo_acc':       round(_acc(elo_preds), 3),
            }
            if self.calibrator is not None and len(X) > 0:
                try:
                    xgb_preds = self.calibrator.predict_proba(X)[:, 1]
                    self.training_metrics['xgboost_acc'] = round(_acc(xgb_preds), 3)
                except Exception:
                    pass
            elif self.xgb is not None and len(X) > 0:
                xgb_preds = self.xgb.predict_proba(X)[:, 1]
                self.training_metrics['xgboost_acc'] = round(_acc(xgb_preds), 3)
            logger.info(f"[UFC] Training metrics: {self.training_metrics}")
        except Exception as exc:
            logger.debug(f"[UFC] Eval failed: {exc}")

        self.trained = True
        return self.training_metrics

    # ── serialisation ──────────────────────────────────────────────────────

    def save(self, model_dir: Optional[Path] = None) -> None:
        path = Path(model_dir) if model_dir else _MODEL_DIR
        path.mkdir(parents=True, exist_ok=True)

        with open(path / 'glicko2.pkl',      'wb') as f: pickle.dump(self.glicko, f)
        with open(path / 'trueskill.pkl',    'wb') as f: pickle.dump(self.ts,     f)
        with open(path / 'elo.pkl',          'wb') as f: pickle.dump(self.elo,    f)
        with open(path / 'xgboost.pkl',      'wb') as f: pickle.dump(self.xgb,    f)
        with open(path / 'calibrator.pkl',   'wb') as f: pickle.dump(self.calibrator, f)
        with open(path / 'feature_names.pkl','wb') as f: pickle.dump(self.feature_names, f)

        import json
        with open(path / 'training_metrics.json', 'w') as f:
            json.dump(self.training_metrics, f, indent=2)

        # Write sentinel so AdvancedPredictor.load() can detect UFC model type
        with open(path / 'ensemble.pkl', 'wb') as f:
            pickle.dump({'type': 'UFC', 'version': 1}, f)

        logger.info(f"[UFC] Model saved to {path}")

    @classmethod
    def load(cls, model_dir: Optional[Path] = None) -> 'UFCPredictor':
        path = Path(model_dir) if model_dir else _MODEL_DIR
        inst = cls()
        with open(path / 'glicko2.pkl',      'rb') as f: inst.glicko        = pickle.load(f)
        with open(path / 'trueskill.pkl',    'rb') as f: inst.ts            = pickle.load(f)
        with open(path / 'elo.pkl',          'rb') as f: inst.elo           = pickle.load(f)
        with open(path / 'xgboost.pkl',      'rb') as f: inst.xgb          = pickle.load(f)
        with open(path / 'calibrator.pkl',   'rb') as f: inst.calibrator   = pickle.load(f)
        with open(path / 'feature_names.pkl','rb') as f: inst.feature_names = pickle.load(f)
        inst.trained = True
        logger.info(f"[UFC] Model loaded from {path}")
        return inst

    @classmethod
    def load_or_train(cls, force_retrain: bool = False) -> Optional['UFCPredictor']:
        """
        Load from disk if available; otherwise fetch history from ESPN and train.
        Called from NHL77FINAL._ensure_v2_predictor() for UFC.
        """
        ensemble_pkl = _MODEL_DIR / 'ensemble.pkl'

        if not force_retrain and ensemble_pkl.exists():
            try:
                inst = cls.load()
                return inst
            except Exception as exc:
                logger.warning(f"[UFC] Load failed, will retrain: {exc}")

        logger.info("[UFC] Fetching fight history to train model...")
        try:
            df = _fetch_ufc_history()
        except Exception as exc:
            logger.error(f"[UFC] History fetch failed: {exc}")
            return None

        if len(df) < _MIN_FIGHTS_TO_TRAIN:
            logger.warning(f"[UFC] Only {len(df)} fights available, need {_MIN_FIGHTS_TO_TRAIN}")
            return None

        inst = cls()
        try:
            inst.train(df)
            inst.save()
            return inst
        except Exception as exc:
            logger.error(f"[UFC] Training failed: {exc}")
            return None

    # ── private ────────────────────────────────────────────────────────────

    def _make_predict_features(self, home: str, away: str, date_str: str) -> np.ndarray:
        """Build a single-row feature matrix for inference."""
        try:
            cur_dt = datetime.strptime(date_str[:10], '%Y-%m-%d') if date_str else datetime.now()
        except Exception:
            cur_dt = datetime.now()

        # For prediction, we don't have access to last_fight_date or streaks
        # at inference time (those are only in the training walk-forward).
        # Use current ratings as proxy — all features from ratings are available.
        g2_home = self.glicko.get_mu(home)
        g2_away = self.glicko.get_mu(away)
        g2_rd_home = self.glicko.get_rd(home)
        g2_rd_away = self.glicko.get_rd(away)
        g2_prob  = self.glicko.win_prob(home, away)

        ts_mu_home, ts_sig_home = self.ts.get(home)
        ts_mu_away, ts_sig_away = self.ts.get(away)
        ts_prob  = self.ts.win_prob(home, away)
        ts_con_home = self.ts.conservative(home)
        ts_con_away = self.ts.conservative(away)

        elo_home = self.elo.get(home)
        elo_away = self.elo.get(away)
        elo_prob = self.elo.win_prob(home, away)

        n_home = self.elo.n_fights(home)
        n_away = self.elo.n_fights(away)

        # Real fight recency from stored elo state (no more neutral zeros)
        elo_lfd = getattr(self.elo, 'last_fight_date', {})
        if home in elo_lfd:
            try:
                days_home = (cur_dt - datetime.strptime(elo_lfd[home], '%Y-%m-%d')).days
            except Exception:
                days_home = 180
        else:
            days_home = 365
        if away in elo_lfd:
            try:
                days_away = (cur_dt - datetime.strptime(elo_lfd[away], '%Y-%m-%d')).days
            except Exception:
                days_away = 180
        else:
            days_away = 365

        elo_ws = getattr(self.elo, 'win_streak', {})
        elo_ls = getattr(self.elo, 'loss_streak', {})
        ws_home = elo_ws.get(home, 0)
        ws_away = elo_ws.get(away, 0)
        ls_home = elo_ls.get(home, 0)
        ls_away = elo_ls.get(away, 0)

        feat = {
            'g2_diff':          g2_home - g2_away,
            'g2_home':          g2_home,
            'g2_away':          g2_away,
            'g2_rd_diff':       g2_rd_home - g2_rd_away,
            'g2_rd_home':       g2_rd_home,
            'g2_rd_away':       g2_rd_away,
            'g2_prob':          g2_prob,
            'ts_diff':          ts_mu_home - ts_mu_away,
            'ts_home':          ts_mu_home,
            'ts_away':          ts_mu_away,
            'ts_sig_diff':      ts_sig_home - ts_sig_away,
            'ts_con_diff':      ts_con_home - ts_con_away,
            'ts_prob':          ts_prob,
            'elo_diff':         elo_home - elo_away,
            'elo_home':         elo_home,
            'elo_away':         elo_away,
            'elo_prob':         elo_prob,
            'fights_home':      n_home,
            'fights_away':      n_away,
            'fights_diff':      n_home - n_away,
            'days_home':        days_home,
            'days_away':        days_away,
            'days_diff':        days_home - days_away,
            'inactivity_home':  min(days_home, 730) / 730.0,
            'inactivity_away':  min(days_away, 730) / 730.0,
            'win_streak_home':  ws_home,
            'win_streak_away':  ws_away,
            'loss_streak_home': ls_home,
            'loss_streak_away': ls_away,
            'streak_diff':      ws_home - ws_away,
            'uncertainty_home': min(g2_rd_home / 350.0, 1.0),
            'uncertainty_away': min(g2_rd_away / 350.0, 1.0),
            'debut_home':       1.0 if n_home == 0 else 0.0,
            'debut_away':       1.0 if n_away == 0 else 0.0,
            'model_agreement':  abs(g2_prob - ts_prob) + abs(g2_prob - elo_prob),
        }

        if self.feature_names:
            row = [feat.get(name, 0.0) for name in self.feature_names]
        else:
            row = list(feat.values())

        return np.array(row, dtype=np.float32).reshape(1, -1)
