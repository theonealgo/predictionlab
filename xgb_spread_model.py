#!/usr/bin/env python3
"""
XGBoost-based spread and total predictor — v2 (improved totals).

Enhancements over v1:
  - Rolling last-5 scoring averages per team (offense & defense & game total)
  - Pace proxy: rolling-5 game total avg per team
  - Offensive / defensive rating indices (vs league average = 100)
  - Market baseline blending (optional vegas_total in predict())
  - Calibration bias computed from training residuals
  - Regression to mean + hard clipping to cap extreme predictions
  - Separate XGBoost hyper-params tuned for spread vs total models
  - Model version key forces cache invalidation on feature changes

Feature vector (17 features):
  [0]  elo_diff              rolling Elo difference at game time
  [1]  off_diff              season PPG differential
  [2]  def_diff              season PAPG diff (positive = home def better)
  [3]  pace_diff_season      season-avg pace proxy diff
  [4]  rest_diff             days-rest differential (positive = home more rest)
  [5]  home_adv              sport-specific constant
  [6]  h_roll5_off           home rolling-5 PPG
  [7]  a_roll5_off           away rolling-5 PPG
  [8]  h_roll5_def           home rolling-5 PAPG
  [9]  a_roll5_def           away rolling-5 PAPG
  [10] h_roll5_pace          home rolling-5 game total (true pace proxy)
  [11] a_roll5_pace          away rolling-5 game total
  [12] total_roll5_baseline  sum of [10]+[11]: combined scoring baseline
  [13] h_off_rtg_idx         home off-rating index (100 = league average)
  [14] a_off_rtg_idx         away off-rating index
  [15] h_def_rtg_idx         home def-rating index
  [16] a_def_rtg_idx         away def-rating index
"""

import numpy as np
import logging
import time
from collections import deque
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Sport constants ───────────────────────────────────────────────
K_FACTORS = {
    'NBA': 18, 'NHL': 22, 'NFL': 35,
    'NCAAB': 25, 'MLB': 14, 'NCAAF': 30, 'WNBA': 18,
}
HOME_ADV = {
    'NBA': 3.0, 'NHL': 0.3, 'NFL': 2.5,
    'NCAAB': 3.0, 'MLB': 0.15, 'NCAAF': 3.0, 'WNBA': 3.0,
}

# ── Sport-specific total clipping bounds ────────────────────────────────
TOTAL_BOUNDS = {
    'NBA':   (200, 255),
    'NFL':   (32,  68),
    'NHL':   (5.5, 6.5),
    'MLB':   (5.0, 16.0),
    'NCAAB': (120, 185),
    'NCAAF': (28,  90),
    'WNBA':  (155, 205),
}

# ── Sport-specific spread clipping bounds ─────────────────────────────
SPREAD_BOUNDS = {
    'NBA':   (-28, 28),
    'NFL':   (-21, 21),
    'NHL':   (-1.5, 1.5),
    'MLB':   (-5,  5),
    'NCAAB': (-35, 35),
    'NCAAF': (-40, 40),
    'WNBA':  (-22, 22),
}

# ── League mean totals for regression-to-mean ──────────────────────────
LEAGUE_MEAN_TOTAL = {
    'NBA':   226.0,
    'NFL':    47.5,
    'NHL':     5.8,
    'MLB':     8.9,
    'NCAAB': 150.0,
    'NCAAF':  56.0,
    'WNBA':  176.0,
}

# ── Regression-to-mean alphas (1.0 = trust model fully, 0.0 = use league mean) ─
TOTAL_REGRESSION_ALPHA  = 0.82  # pulls extremes toward league mean
SPREAD_REGRESSION_ALPHA = 0.90  # less regression for spreads

# ── Vegas market blending weight (when vegas_total is provided) ───────────
MARKET_WEIGHT = 0.33  # 33 % market, 67 % model

# ── Cache version — bump to invalidate stale cached models ─────────────────
_MODEL_VERSION = 'v2_roll5_ratings'

# ── Module-level model cache  {sport: {'ts': float, 'model': ..., 'ver': str}} ──
_MODEL_CACHE: dict = {}
_CACHE_TTL = 3600  # retrain at most once per hour

N_FEATURES = 17  # must match feature vector built in train() and predict()


# ─────────────────────────────────────────────────────────────────────────────
class XGBSpreadTotalPredictor:
    """
    Trains two XGBoost regressors (spread, total) on historical games
    and exposes a predict() method for upcoming matchups.
    """

    def __init__(self, sport: str):
        self.sport = sport
        self.spread_model = None
        self.total_model  = None
        self.elo_ratings: dict  = {}   # {team: final Elo after all training games}
        self.team_stats:  dict  = {}   # {team: {'offense': ppg, 'defense': papg}}
        self.team_roll5:  dict  = {}   # {team: list of (pts_scored, pts_allowed, total)}
        self.calibration_bias_total:  float = 0.0
        self.calibration_bias_spread: float = 0.0
        self.league_mean_total: float = LEAGUE_MEAN_TOTAL.get(sport, 100.0)
        self._trained = False

    # ── Elo helpers ────────────────────────────────────────────────────
    def _train_elo(self, games: list) -> list:
        """Progressive Elo ratings; returns (elo_home, elo_away) snapshots."""
        K = K_FACTORS.get(self.sport, 20)
        elo: dict = {}
        snapshots = []
        for g in games:
            h, a = g['home_team_id'], g['away_team_id']
            hr = elo.get(h, 1500)
            ar = elo.get(a, 1500)
            snapshots.append((hr, ar))
            exp = 1 / (1 + 10 ** ((ar - hr) / 400))
            actual = 1 if g['home_score'] > g['away_score'] else 0
            elo[h] = hr + K * (actual - exp)
            elo[a] = ar + K * ((1 - actual) - (1 - exp))
        self.elo_ratings = elo
        return snapshots

    # ── League-average helper ────────────────────────────────────────────
    def _league_avgs(self, team_stats: dict):
        """Return (avg_ppg, avg_papg) across all teams with non-zero stats."""
        vals_off = [v['offense'] for v in team_stats.values() if v.get('offense', 0) > 0]
        vals_def = [v['defense'] for v in team_stats.values() if v.get('defense', 0) > 0]
        avg_off = float(np.mean(vals_off)) if vals_off else (self.league_mean_total / 2)
        avg_def = float(np.mean(vals_def)) if vals_def else (self.league_mean_total / 2)
        return avg_off, avg_def

    # ── Training ─────────────────────────────────────────────────────────
    def train(self, completed_games: list, team_stats: dict) -> bool:
        """
        Train spread and total models on completed games.

        Args:
            completed_games: list of dicts with keys:
                home_team_id, away_team_id, home_score, away_score, game_date
            team_stats: {team_name: {'offense': ppg, 'defense': papg}}
        """
        try:
            import xgboost as xgb
        except ImportError:
            logger.error("xgboost not installed; spread/total model unavailable")
            return False

        self.team_stats = team_stats
        if len(completed_games) < 20:
            logger.warning(f"[{self.sport}] Not enough games to train ({len(completed_games)})")
            return False

        # Sort chronologically (required for correct rolling windows & Elo)
        try:
            completed_games = sorted(
                completed_games,
                key=lambda g: g.get('game_date', '') or ''
            )
        except Exception:
            pass

        snapshots = self._train_elo(completed_games)
        league_avg_off, league_avg_def = self._league_avgs(team_stats)
        la_off = league_avg_off if league_avg_off > 0 else 1.0
        la_def = league_avg_def if league_avg_def > 0 else 1.0

        # ── Rolling-5 windows: deque of (pts_scored, pts_allowed, game_total) ──
        roll5: dict    = {}   # team -> deque(maxlen=5)
        game_dates: dict = {}

        # ── Build feature matrix ───────────────────────────────────────────
        X, y_spread, y_total = [], [], []
        home_adv = HOME_ADV.get(self.sport, 0.0)

        for i, g in enumerate(completed_games):
            h, a    = g['home_team_id'], g['away_team_id']
            hs, as_ = g['home_score'],   g['away_score']
            gdate   = g.get('game_date', '')

            h_stats = team_stats.get(h)
            a_stats = team_stats.get(a)
            if not h_stats or not a_stats:
                continue
            if h_stats.get('offense', 0) == 0 and h_stats.get('defense', 0) == 0:
                continue
            if a_stats.get('offense', 0) == 0 and a_stats.get('defense', 0) == 0:
                continue

            hr_elo, ar_elo = snapshots[i]
            elo_diff = hr_elo - ar_elo

            # ── Season-average features ─────────────────────────────────
            off_diff        = h_stats['offense'] - a_stats['offense']
            def_diff        = a_stats['defense'] - h_stats['defense']  # positive = home def better
            h_pace_s        = (h_stats['offense'] + h_stats['defense']) / 2
            a_pace_s        = (a_stats['offense'] + a_stats['defense']) / 2
            pace_diff_season = h_pace_s - a_pace_s

            # ── Rest days ───────────────────────────────────────────────
            rest_diff = 0.0
            if gdate:
                h_last = game_dates.get(h)
                a_last = game_dates.get(a)
                try:
                    if h_last and a_last:
                        d = datetime.strptime(gdate[:10], '%Y-%m-%d')
                        h_rest = (d - datetime.strptime(h_last[:10], '%Y-%m-%d')).days
                        a_rest = (d - datetime.strptime(a_last[:10], '%Y-%m-%d')).days
                        rest_diff = float(min(h_rest, 10) - min(a_rest, 10))
                except Exception:
                    pass
                game_dates[h] = gdate
                game_dates[a] = gdate

            # ── Rolling-5 features (use PRIOR games to avoid look-ahead leakage) ─
            h_roll = list(roll5.get(h, []))
            a_roll = list(roll5.get(a, []))

            def _r5(roll, fb_off, fb_def):
                if len(roll) >= 3:
                    r5_off  = sum(r[0] for r in roll) / len(roll)
                    r5_def  = sum(r[1] for r in roll) / len(roll)
                    r5_pace = sum(r[2] for r in roll) / len(roll)
                else:
                    r5_off  = fb_off
                    r5_def  = fb_def
                    r5_pace = fb_off + fb_def
                return r5_off, r5_def, r5_pace

            h_r5_off, h_r5_def, h_r5_pace = _r5(h_roll, h_stats['offense'], h_stats['defense'])
            a_r5_off, a_r5_def, a_r5_pace = _r5(a_roll, a_stats['offense'], a_stats['defense'])
            total_roll5_baseline = h_r5_pace + a_r5_pace

            # ── Off/Def rating indices vs league average (100 = league average) ──
            h_off_rtg = h_stats['offense'] / la_off * 100
            a_off_rtg = a_stats['offense'] / la_off * 100
            h_def_rtg = h_stats['defense'] / la_def * 100
            a_def_rtg = a_stats['defense'] / la_def * 100

            feats = [
                elo_diff, off_diff, def_diff, pace_diff_season, rest_diff, home_adv,
                h_r5_off, a_r5_off, h_r5_def, a_r5_def,
                h_r5_pace, a_r5_pace, total_roll5_baseline,
                h_off_rtg, a_off_rtg, h_def_rtg, a_def_rtg,
            ]
            assert len(feats) == N_FEATURES
            X.append(feats)
            y_spread.append(float(hs - as_))
            y_total.append(float(hs + as_))

            # Update rolling windows AFTER appending features (no leakage)
            roll5.setdefault(h, deque(maxlen=5)).append((hs,  as_, hs + as_))
            roll5.setdefault(a, deque(maxlen=5)).append((as_, hs,  hs + as_))

        if len(X) < 20:
            logger.warning(f"[{self.sport}] Too few usable rows after filtering: {len(X)}")
            return False

        # Persist final rolling state for inference
        self.team_roll5 = {t: list(v) for t, v in roll5.items()}

        X   = np.array(X,        dtype=np.float32)
        y_s = np.array(y_spread, dtype=np.float32)
        y_t = np.array(y_total,  dtype=np.float32)

        # Update league mean total from actual training data
        self.league_mean_total = float(np.mean(y_t))

        # ── XGBoost hyperparameters ───────────────────────────────────────
        # Spread: deeper tree, more estimators (higher variance target)
        spread_params = dict(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, reg_lambda=1.5,
            random_state=42, verbosity=0,
        )
        # Total: shallower tree, stronger L2 (reduce extreme total predictions)
        total_params = dict(
            n_estimators=150, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.7,
            reg_lambda=2.5, reg_alpha=0.5,
            random_state=42, verbosity=0,
        )

        import xgboost as xgb
        self.spread_model = xgb.XGBRegressor(**spread_params)
        self.spread_model.fit(X, y_s)

        self.total_model = xgb.XGBRegressor(**total_params)
        self.total_model.fit(X, y_t)

        # ── Calibration bias from in-sample residuals ──────────────────────
        self.calibration_bias_spread = float(np.mean(y_s - self.spread_model.predict(X)))
        self.calibration_bias_total  = float(np.mean(y_t - self.total_model.predict(X)))

        self._trained = True
        logger.info(
            f"[{self.sport}] XGBSpread v2 trained on {len(X)} games | "
            f"league_mean_total={self.league_mean_total:.1f} | "
            f"bias_total={self.calibration_bias_total:+.2f} | "
            f"roll5 teams={len(self.team_roll5)}"
        )
        return True

    # ── Inference ─────────────────────────────────────────────────────────
    def predict(
        self,
        home_team: str,
        away_team: str,
        rest_days_diff: float = 0.0,
        vegas_total: float = None,
    ):
        """
        Returns (pred_home_score, pred_away_score, pred_spread, pred_total)
        or (None, None, None, None) if model not ready.

        Args:
            home_team:      home team display name
            away_team:      away team display name
            rest_days_diff: positive = home team had more rest
            vegas_total:    optional Vegas O/U line; blended with model prediction
        """
        if not self._trained:
            return None, None, None, None
        if home_team not in self.team_stats or away_team not in self.team_stats:
            return None, None, None, None

        hr_elo = self.elo_ratings.get(home_team, 1500)
        ar_elo = self.elo_ratings.get(away_team, 1500)
        elo_diff = hr_elo - ar_elo

        h = self.team_stats[home_team]
        a = self.team_stats[away_team]

        off_diff        = h['offense'] - a['offense']
        def_diff        = a['defense'] - h['defense']
        h_pace_s        = (h['offense'] + h['defense']) / 2
        a_pace_s        = (a['offense'] + a['defense']) / 2
        pace_diff_season = h_pace_s - a_pace_s
        home_adv        = HOME_ADV.get(self.sport, 0.0)

        # ── Rolling-5 features ─────────────────────────────────────────
        h_roll = self.team_roll5.get(home_team, [])
        a_roll = self.team_roll5.get(away_team, [])

        def _r5(roll, fb_off, fb_def):
            if len(roll) >= 2:
                r5_off  = sum(r[0] for r in roll) / len(roll)
                r5_def  = sum(r[1] for r in roll) / len(roll)
                r5_pace = sum(r[2] for r in roll) / len(roll)
            else:
                r5_off  = fb_off
                r5_def  = fb_def
                r5_pace = fb_off + fb_def
            return r5_off, r5_def, r5_pace

        h_r5_off, h_r5_def, h_r5_pace = _r5(h_roll, h['offense'], h['defense'])
        a_r5_off, a_r5_def, a_r5_pace = _r5(a_roll, a['offense'], a['defense'])
        total_roll5_baseline = h_r5_pace + a_r5_pace

        # ── Off/Def rating indices ──────────────────────────────────────
        all_stats = list(self.team_stats.values())
        la_off = float(np.mean([v['offense'] for v in all_stats if v.get('offense', 0) > 0]) or 1)
        la_def = float(np.mean([v['defense'] for v in all_stats if v.get('defense', 0) > 0]) or 1)
        h_off_rtg = h['offense'] / la_off * 100
        a_off_rtg = a['offense'] / la_off * 100
        h_def_rtg = h['defense'] / la_def * 100
        a_def_rtg = a['defense'] / la_def * 100

        X = np.array([[
            elo_diff, off_diff, def_diff, pace_diff_season, rest_days_diff, home_adv,
            h_r5_off, a_r5_off, h_r5_def, a_r5_def,
            h_r5_pace, a_r5_pace, total_roll5_baseline,
            h_off_rtg, a_off_rtg, h_def_rtg, a_def_rtg,
        ]], dtype=np.float32)

        raw_spread = float(self.spread_model.predict(X)[0])
        raw_total  = float(self.total_model.predict(X)[0])

        # ── 1. Apply calibration bias (corrects systematic under/over-prediction) ─
        spread = raw_spread + self.calibration_bias_spread
        total  = raw_total  + self.calibration_bias_total

        # ── 2. Market baseline blending (when Vegas total is known) ───────────
        if vegas_total is not None:
            total = (1.0 - MARKET_WEIGHT) * total + MARKET_WEIGHT * float(vegas_total)

        # ── 3. Regression to mean (pulls extremes toward league average) ───────
        total  = TOTAL_REGRESSION_ALPHA  * total  + (1 - TOTAL_REGRESSION_ALPHA)  * self.league_mean_total
        spread = SPREAD_REGRESSION_ALPHA * spread  # spread mean = 0

        # ── 4. Hard clipping to sport-specific bounds ──────────────────────
        tlo, thi = TOTAL_BOUNDS.get(self.sport, (0, 999))
        total    = max(tlo, min(thi, total))
        slo, shi = SPREAD_BOUNDS.get(self.sport, (-50, 50))
        spread   = max(slo, min(shi, spread))

        # ── 5. Back-derive individual scores ─────────────────────────────
        home_score = (total + spread) / 2.0
        away_score = (total - spread) / 2.0

        return (
            round(home_score, 1),
            round(away_score, 1),
            round(spread, 1),
            round(total, 1),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Public factory — cached per sport, retrained at most once per hour
# ─────────────────────────────────────────────────────────────────────────────
def get_or_train_model(sport: str, completed_games: list, team_stats: dict):
    """
    Return a trained XGBSpreadTotalPredictor for `sport`.
    Re-trains if cache is missing, expired (>1 hour), or model version changed.
    """
    now = time.time()
    cached = _MODEL_CACHE.get(sport)
    if (
        cached
        and (now - cached['ts']) < _CACHE_TTL
        and cached.get('ver') == _MODEL_VERSION
    ):
        return cached['model']

    model = XGBSpreadTotalPredictor(sport)
    ok = model.train(completed_games, team_stats)
    if ok:
        _MODEL_CACHE[sport] = {'ts': now, 'model': model, 'ver': _MODEL_VERSION}
        return model
    return None
