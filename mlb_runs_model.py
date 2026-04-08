#!/usr/bin/env python3
"""
MLB Run Prediction Model
========================
Specialized XGBoost-based model for predicting MLB run totals.

Improvements over the generic XGBSpreadTotalPredictor:
  - Ballpark run factors (hardcoded for all 30 MLB parks)
  - Home/away performance splits computed from DB
  - Rest/fatigue (days since last game, capped at ±7)
  - Rolling last-5 game scoring (offense & defense)
  - Vegas-calibrated output: corrects systematic model bias vs the market
    (per user rule: avg model_total − vegas_total is subtracted from future predictions)

Feature vector (15 features):
  [0]  elo_diff          Elo rating difference (home − away)
  [1]  home_rpg          Home team season runs/game
  [2]  away_rpg          Away team season runs/game
  [3]  home_rapg         Home team season runs-allowed/game
  [4]  away_rapg         Away team season runs-allowed/game
  [5]  park_factor       Ballpark run factor (home park; 1.0 = neutral)
  [6]  rest_diff         Days-rest diff (home − away), capped at ±7
  [7]  home_roll5_off    Home rolling-5 game offense (runs scored)
  [8]  away_roll5_off    Away rolling-5 game offense
  [9]  home_roll5_def    Home rolling-5 game defense (runs allowed)
  [10] away_roll5_def    Away rolling-5 game defense
  [11] home_home_rpg     Home team runs/game specifically at home
  [12] away_road_rpg     Away team runs/game specifically on the road
  [13] home_home_rapg    Home team runs-allowed/game at home
  [14] away_road_rapg    Away team runs-allowed/game on the road
"""

import numpy as np
import logging
import sqlite3
import time
from collections import deque, defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Ballpark run factors (2024-25 estimates; 1.0 = neutral park) ─────────────
# Source: Baseball Reference multi-year park factors, rounded to 2 decimal places.
# Coors Field is the most extreme environment (+30% runs vs neutral).
PARK_FACTORS: dict = {
    'Arizona Diamondbacks':  1.08,   # Chase Field — retractable roof, hitter-friendly
    'Atlanta Braves':        1.02,   # Truist Park
    'Baltimore Orioles':     1.05,   # Camden Yards — hitter-friendly dimensions
    'Boston Red Sox':        1.05,   # Fenway Park — Green Monster
    'Chicago Cubs':          1.06,   # Wrigley Field — wind factor
    'Chicago White Sox':     1.05,   # Guaranteed Rate Field
    'Cincinnati Reds':       1.09,   # Great American Ball Park
    'Cleveland Guardians':   0.96,   # Progressive Field — pitcher-friendly
    'Colorado Rockies':      1.30,   # Coors Field — extreme altitude
    'Detroit Tigers':        0.92,   # Comerica Park — spacious outfield
    'Houston Astros':        0.99,   # Minute Maid Park — neutral
    'Kansas City Royals':    0.93,   # Kauffman Stadium
    'Los Angeles Angels':    0.98,   # Angel Stadium
    'Los Angeles Dodgers':   0.98,   # Dodger Stadium
    'Miami Marlins':         0.94,   # loanDepot park
    'Milwaukee Brewers':     1.02,   # American Family Field
    'Minnesota Twins':       0.93,   # Target Field
    'New York Mets':         0.97,   # Citi Field
    'New York Yankees':      1.04,   # Yankee Stadium — short porch
    'Athletics':             0.97,   # (Sacramento Rivercats/Sutter Health Park 2025)
    'Oakland Athletics':     0.97,   # legacy entry
    'Philadelphia Phillies': 1.08,   # Citizens Bank Park
    'Pittsburgh Pirates':    0.91,   # PNC Park
    'San Diego Padres':      0.89,   # Petco Park — pitcher-friendly
    'San Francisco Giants':  0.88,   # Oracle Park — coldest/windiest in NL
    'Seattle Mariners':      0.90,   # T-Mobile Park
    'St. Louis Cardinals':   0.97,   # Busch Stadium
    'Tampa Bay Rays':        0.95,   # Tropicana Field
    'Texas Rangers':         1.04,   # Globe Life Field
    'Toronto Blue Jays':     1.04,   # Rogers Centre — artificial turf
    'Washington Nationals':  1.00,   # Nationals Park — neutral
}

# ── MLB model constants ───────────────────────────────────────────────────────
HOME_ADV_MLB        = 0.15   # historical home-field run advantage
LEAGUE_MEAN_TOTAL   = 8.8    # from 2,626-game DB average
TOTAL_BOUNDS        = (5.0, 16.0)
SPREAD_BOUNDS       = (-8.0, 8.0)
TOTAL_REGRESSION_ALPHA  = 0.85   # pulls toward league mean (15 % shrinkage)
SPREAD_REGRESSION_ALPHA = 0.90
N_FEATURES          = 15
K_FACTOR_MLB        = 14     # Elo K-factor for MLB (lower than basketball/hockey)

# ── Module-level cache ────────────────────────────────────────────────────────
_MODEL_CACHE: dict = {}
_CACHE_TTL         = 3600    # retrain at most once per hour
_MODEL_VERSION     = 'mlb_v1_parkfactors'


# ─────────────────────────────────────────────────────────────────────────────
class MLBRunsModel:
    """
    XGBoost-based MLB run predictor with:
      • Ballpark factors for every MLB park
      • Home/away performance splits
      • Rolling last-5 game form
      • Vegas-calibration constant (model bias vs market)
    """

    def __init__(self):
        self.spread_model = None
        self.total_model  = None
        self.elo_ratings: dict  = {}   # {team: final Elo after all training games}
        self.team_stats:  dict  = {}   # {team: {offense, defense, home_offense, …}}
        self.team_roll5:  dict  = {}   # {team: list of (scored, allowed)}
        self.calibration_bias_total:  float = 0.0
        self.calibration_bias_spread: float = 0.0
        self.vegas_cal_constant:      float = 0.0   # avg(model_total − vegas_total)
        self._trained: bool = False

    # ── Elo helpers ───────────────────────────────────────────────────────────
    def _train_elo(self, games: list) -> list:
        """Progressive Elo; returns (elo_home, elo_away) snapshots at game time."""
        elo: dict = {}
        snapshots = []
        for g in games:
            h, a = g['home_team_id'], g['away_team_id']
            hr = elo.get(h, 1500)
            ar = elo.get(a, 1500)
            snapshots.append((hr, ar))
            exp    = 1 / (1 + 10 ** ((ar - hr) / 400))
            actual = 1 if g['home_score'] > g['away_score'] else 0
            elo[h] = hr + K_FACTOR_MLB * (actual - exp)
            elo[a] = ar + K_FACTOR_MLB * ((1 - actual) - (1 - exp))
        self.elo_ratings = elo
        return snapshots

    # ── Team stats builder ───────────────────────────────────────────────────
    def _build_team_stats(self, games: list) -> dict:
        """
        Compute season-level and home/away split stats from all completed games.
        Returns {team: {offense, defense, home_offense, road_offense,
                         home_defense, road_defense}}.
        Requires >= 5 games to include a team (early-season stability).
        """
        totals = defaultdict(lambda: {
            'scored': 0.0, 'allowed': 0.0, 'games': 0,
            'home_scored': 0.0, 'home_allowed': 0.0, 'home_games': 0,
            'road_scored': 0.0, 'road_allowed': 0.0, 'road_games': 0,
        })
        for g in games:
            h, a   = g['home_team_id'], g['away_team_id']
            hs, as_ = float(g['home_score']), float(g['away_score'])
            # Season totals
            totals[h]['scored']  += hs;  totals[h]['allowed'] += as_;  totals[h]['games'] += 1
            totals[a]['scored']  += as_; totals[a]['allowed'] += hs;   totals[a]['games'] += 1
            # Home splits
            totals[h]['home_scored']  += hs;  totals[h]['home_allowed']  += as_; totals[h]['home_games'] += 1
            # Road splits
            totals[a]['road_scored']  += as_; totals[a]['road_allowed']  += hs;  totals[a]['road_games'] += 1

        result = {}
        for team, d in totals.items():
            if d['games'] < 5:
                continue
            g  = d['games']
            hg = d['home_games'] or 1
            rg = d['road_games'] or 1
            result[team] = {
                'offense':       d['scored']       / g,
                'defense':       d['allowed']      / g,
                'home_offense':  d['home_scored']  / hg,
                'road_offense':  d['road_scored']  / rg,
                'home_defense':  d['home_allowed'] / hg,
                'road_defense':  d['road_allowed'] / rg,
            }
        return result

    # ── Build inference feature vector ───────────────────────────────────────
    def _build_features(
        self,
        home_team: str,
        away_team: str,
        elo_diff: float,
        rest_days_diff: float = 0.0,
    ) -> 'np.ndarray | None':
        h_st = self.team_stats.get(home_team)
        a_st = self.team_stats.get(away_team)
        if not h_st or not a_st:
            return None

        park_factor = PARK_FACTORS.get(home_team, 1.0)

        h_roll = self.team_roll5.get(home_team, [])
        a_roll = self.team_roll5.get(away_team, [])

        def _r5(roll, fb_off, fb_def):
            if len(roll) >= 2:
                return (
                    sum(r[0] for r in roll) / len(roll),
                    sum(r[1] for r in roll) / len(roll),
                )
            return fb_off, fb_def

        h_r5_off, h_r5_def = _r5(h_roll, h_st['offense'], h_st['defense'])
        a_r5_off, a_r5_def = _r5(a_roll, a_st['offense'], a_st['defense'])

        feats = [
            elo_diff,
            h_st['offense'],
            a_st['offense'],
            h_st['defense'],
            a_st['defense'],
            park_factor,
            float(rest_days_diff),
            h_r5_off,
            a_r5_off,
            h_r5_def,
            a_r5_def,
            h_st['home_offense'],
            a_st['road_offense'],
            h_st['home_defense'],
            a_st['road_defense'],
        ]
        assert len(feats) == N_FEATURES
        return np.array([feats], dtype=np.float32)

    # ── Training ──────────────────────────────────────────────────────────────
    def train(self, db_path: str) -> bool:
        """
        Train spread and total models on completed MLB games from the DB.
        Also computes Vegas calibration constant from games with betting lines.
        """
        try:
            import xgboost as xgb
        except ImportError:
            logger.error("[MLB] xgboost not installed; MLBRunsModel unavailable")
            return False

        # ── Load data from DB ─────────────────────────────────────────────────
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                'SELECT home_team_id, away_team_id, home_score, away_score, game_date '
                'FROM games WHERE sport=? AND home_score IS NOT NULL ORDER BY game_date',
                ('MLB',)
            ).fetchall()
            games = [dict(r) for r in rows]

            # Vegas totals for calibration (keyed by home|away to allow duplicate lookup)
            vegas_rows = conn.execute('''
                SELECT g.home_team_id, g.away_team_id, g.game_date, bl.total AS vegas_total
                FROM games g
                JOIN betting_lines bl ON g.game_id = bl.game_id
                WHERE g.sport = 'MLB' AND g.home_score IS NOT NULL AND bl.total IS NOT NULL
            ''').fetchall()
            # Use (home, away, date) as key for uniqueness
            vegas_map = {
                (r['home_team_id'], r['away_team_id'], r['game_date']): float(r['vegas_total'])
                for r in vegas_rows
            }
            conn.close()
        except Exception as e:
            logger.error(f"[MLB] DB load failed: {e}")
            return False

        if len(games) < 20:
            logger.warning(f"[MLB] Not enough games to train: {len(games)}")
            return False

        # ── Build season stats and Elo snapshots ──────────────────────────────
        self.team_stats = self._build_team_stats(games)
        snapshots       = self._train_elo(games)

        roll5: dict      = {}
        game_dates: dict = {}
        X, y_spread, y_total = [], [], []

        for i, g in enumerate(games):
            h, a    = g['home_team_id'], g['away_team_id']
            hs, as_ = float(g['home_score']), float(g['away_score'])
            gdate   = g.get('game_date', '')

            if not self.team_stats.get(h) or not self.team_stats.get(a):
                continue

            hr_elo, ar_elo = snapshots[i]
            elo_diff = hr_elo - ar_elo

            # Rest days
            rest_diff = 0.0
            if gdate:
                h_last = game_dates.get(h)
                a_last = game_dates.get(a)
                try:
                    if h_last and a_last:
                        d      = datetime.strptime(gdate[:10], '%Y-%m-%d')
                        h_rest = (d - datetime.strptime(h_last[:10], '%Y-%m-%d')).days
                        a_rest = (d - datetime.strptime(a_last[:10], '%Y-%m-%d')).days
                        rest_diff = float(min(h_rest, 7) - min(a_rest, 7))
                except Exception:
                    pass
                game_dates[h] = gdate
                game_dates[a] = gdate

            # Rolling-5 (uses PRIOR games only — no look-ahead leakage)
            h_roll = list(roll5.get(h, []))
            a_roll = list(roll5.get(a, []))

            def _r5(roll, fb_off, fb_def):
                if len(roll) >= 3:
                    return (
                        sum(r[0] for r in roll) / len(roll),
                        sum(r[1] for r in roll) / len(roll),
                    )
                return fb_off, fb_def

            h_st = self.team_stats[h]
            a_st = self.team_stats[a]
            h_r5_off, h_r5_def = _r5(h_roll, h_st['offense'], h_st['defense'])
            a_r5_off, a_r5_def = _r5(a_roll, a_st['offense'], a_st['defense'])

            feats = [
                elo_diff,
                h_st['offense'],       a_st['offense'],
                h_st['defense'],       a_st['defense'],
                PARK_FACTORS.get(h, 1.0),
                rest_diff,
                h_r5_off,              a_r5_off,
                h_r5_def,              a_r5_def,
                h_st['home_offense'],  a_st['road_offense'],
                h_st['home_defense'],  a_st['road_defense'],
            ]
            assert len(feats) == N_FEATURES
            X.append(feats)
            y_spread.append(hs - as_)
            y_total.append(hs + as_)

            # Update rolling windows AFTER feature construction (no leakage)
            roll5.setdefault(h, deque(maxlen=5)).append((hs,  as_))
            roll5.setdefault(a, deque(maxlen=5)).append((as_, hs))

        if len(X) < 20:
            logger.warning(f"[MLB] Too few usable training rows: {len(X)}")
            return False

        # Persist final rolling state for inference
        self.team_roll5 = {t: list(v) for t, v in roll5.items()}

        X_arr   = np.array(X,        dtype=np.float32)
        y_s_arr = np.array(y_spread, dtype=np.float32)
        y_t_arr = np.array(y_total,  dtype=np.float32)

        # ── XGBoost hyperparameters ───────────────────────────────────────────
        spread_params = dict(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, reg_lambda=1.5,
            random_state=42, verbosity=0,
        )
        total_params = dict(
            n_estimators=150, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.7,
            reg_lambda=2.5, reg_alpha=0.5,
            random_state=42, verbosity=0,
        )

        self.spread_model = xgb.XGBRegressor(**spread_params)
        self.spread_model.fit(X_arr, y_s_arr)

        self.total_model = xgb.XGBRegressor(**total_params)
        self.total_model.fit(X_arr, y_t_arr)

        # In-sample calibration bias (corrects systematic offset)
        self.calibration_bias_spread = float(
            np.mean(y_s_arr - self.spread_model.predict(X_arr))
        )
        self.calibration_bias_total = float(
            np.mean(y_t_arr - self.total_model.predict(X_arr))
        )

        # ── Vegas calibration constant ────────────────────────────────────────
        # Per rule: calibration_constant = mean(model_total − vegas_total).
        # At inference time: adjusted_total = model_total − vegas_cal_constant.
        self._trained = True   # allow _predict_raw() to work
        if vegas_map:
            cal_diffs = []
            for g in games:
                key = (g['home_team_id'], g['away_team_id'], g.get('game_date', ''))
                if key not in vegas_map:
                    continue
                _, _, _, raw_total = self._predict_raw(g['home_team_id'], g['away_team_id'])
                if raw_total is not None:
                    cal_diffs.append(raw_total - vegas_map[key])
            if cal_diffs:
                self.vegas_cal_constant = float(np.mean(cal_diffs))
                logger.info(
                    f"[MLB] Vegas calibration constant: {self.vegas_cal_constant:+.3f} "
                    f"(from {len(cal_diffs)} games with betting lines)"
                )
            else:
                logger.info("[MLB] No matching Vegas lines found for calibration; constant=0.0")
        else:
            logger.info("[MLB] No Vegas lines in DB; Vegas calibration skipped")

        logger.info(
            f"[MLB] MLBRunsModel trained | games={len(X)} | "
            f"league_mean={np.mean(y_t_arr):.2f} | "
            f"bias_total={self.calibration_bias_total:+.3f} | "
            f"vegas_cal={self.vegas_cal_constant:+.3f} | "
            f"teams={len(self.team_stats)}"
        )
        return True

    # ── Raw prediction (no Vegas cal / no regression; used for calibration) ──
    def _predict_raw(
        self,
        home_team: str,
        away_team: str,
        rest_days_diff: float = 0.0,
    ):
        """Return (home, away, spread, total) with only in-sample bias correction applied."""
        if not self._trained:
            return None, None, None, None
        hr_elo = self.elo_ratings.get(home_team, 1500)
        ar_elo = self.elo_ratings.get(away_team, 1500)
        X = self._build_features(home_team, away_team, hr_elo - ar_elo, rest_days_diff)
        if X is None:
            return None, None, None, None
        raw_spread = float(self.spread_model.predict(X)[0]) + self.calibration_bias_spread
        raw_total  = float(self.total_model.predict(X)[0])  + self.calibration_bias_total
        home = (raw_total + raw_spread) / 2.0
        away = (raw_total - raw_spread) / 2.0
        return round(home, 1), round(away, 1), round(raw_spread, 1), round(raw_total, 1)

    # ── Public inference ──────────────────────────────────────────────────────
    def predict(
        self,
        home_team: str,
        away_team: str,
        rest_days_diff: float = 0.0,
    ):
        """
        Predict MLB run totals with ballpark factors and full post-processing.

        Post-processing pipeline:
          1. Apply in-sample calibration bias
          2. Subtract Vegas calibration constant (model bias vs market)
          3. Regression to league mean (15 % shrinkage)
          4. Hard-clip to TOTAL_BOUNDS and SPREAD_BOUNDS
          5. Back-derive individual scores preserving raw run-share ratio

        Returns (home_score, away_score, spread, total) or (None, None, None, None).
        """
        if not self._trained:
            return None, None, None, None

        hr_elo = self.elo_ratings.get(home_team, 1500)
        ar_elo = self.elo_ratings.get(away_team, 1500)
        elo_diff = hr_elo - ar_elo

        X = self._build_features(home_team, away_team, elo_diff, rest_days_diff)
        if X is None:
            return None, None, None, None

        raw_spread = float(self.spread_model.predict(X)[0])
        raw_total  = float(self.total_model.predict(X)[0])

        # 1. In-sample calibration bias
        spread = raw_spread + self.calibration_bias_spread
        total  = raw_total  + self.calibration_bias_total

        # 2. Vegas calibration (subtract model-vs-market bias)
        total = total - self.vegas_cal_constant

        # 3. Regression to league mean
        total  = TOTAL_REGRESSION_ALPHA  * total  + (1 - TOTAL_REGRESSION_ALPHA)  * LEAGUE_MEAN_TOTAL
        spread = SPREAD_REGRESSION_ALPHA * spread  # mean ≈ 0, regression toward 0

        # 4. Hard clip
        tlo, thi = TOTAL_BOUNDS
        total  = max(tlo, min(thi, total))
        slo, shi = SPREAD_BOUNDS
        spread = max(slo, min(shi, spread))

        # 5. Back-derive individual scores preserving raw run-share
        # Use raw (pre-post-processing) home/away to get the ratio right
        raw_home = (raw_total + raw_spread) / 2.0
        raw_away = (raw_total - raw_spread) / 2.0
        denom = raw_home + raw_away
        home_share = raw_home / denom if denom > 0 else 0.5
        home_score = total * home_share
        away_score = total * (1.0 - home_share)

        return (
            round(home_score, 1),
            round(away_score, 1),
            round(spread, 1),
            round(total, 1),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Public factory — cached per process, retrained at most once per hour
# ─────────────────────────────────────────────────────────────────────────────
def get_or_train_mlb_model(db_path: str = 'sports_predictions_original.db'):
    """
    Return a trained MLBRunsModel.
    Re-trains if the cache is missing, stale (> 1 hour), or the model version changed.
    Returns None if training fails (caller should fall back to generic model).
    """
    now = time.time()
    cached = _MODEL_CACHE.get('MLB')
    if (
        cached
        and (now - cached['ts']) < _CACHE_TTL
        and cached.get('ver') == _MODEL_VERSION
    ):
        return cached['model']

    model = MLBRunsModel()
    ok = model.train(db_path)
    if ok:
        _MODEL_CACHE['MLB'] = {'ts': now, 'model': model, 'ver': _MODEL_VERSION}
        return model
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test — run directly to verify training and sample predictions
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import os
    _db = os.path.join(os.path.dirname(__file__), 'sports_predictions_original.db')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    print("Training MLB Runs Model...")
    m = get_or_train_mlb_model(_db)
    if m is None:
        print("ERROR: Model failed to train.")
    else:
        sample_games = [
            ('New York Yankees',    'Boston Red Sox'),
            ('Colorado Rockies',    'San Diego Padres'),
            ('Los Angeles Dodgers', 'San Francisco Giants'),
            ('Houston Astros',      'Cleveland Guardians'),
            ('Chicago Cubs',        'Atlanta Braves'),
        ]
        print(f"\n{'Matchup':<45} {'Home':>6} {'Away':>6} {'Spread':>7} {'Total':>6}")
        print('-' * 75)
        for home, away in sample_games:
            result = m.predict(home, away)
            if result[0] is not None:
                print(f"{away} @ {home:<30} {result[0]:>6.1f} {result[1]:>6.1f} "
                      f"{result[2]:>+7.1f} {result[3]:>6.1f}")
            else:
                print(f"{away} @ {home:<30}  N/A (teams not in training data)")
        print(f"\nVegas cal constant: {m.vegas_cal_constant:+.3f}")
        print(f"Bias total:         {m.calibration_bias_total:+.3f}")
        print(f"Bias spread:        {m.calibration_bias_spread:+.3f}")
        print(f"Teams in model:     {len(m.team_stats)}")
