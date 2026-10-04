"""CFL accepted models (isolation) — ML / Spread / Totals (v3).

Adapted from NFL-style patterns (Elo + independent margin rating + Platt
calibration + rest/efficiency features). Weights are re-fit for CFL; NFL
weights are not copied.

Accepted after honest walk-forward vs v1 (`engine/predict.py`) on 2026
finals — see `notes/cfl_backtest_*.md`.

Feature honesty
---------------
REAL (from official schedule scores): Elo, form, PPG offense/defense, rest days,
scoring variance (ST/pace proxy), turnover-diff proxy from margins.

PROXY (no dedicated feed): QB rating (offense+form blend), special teams
(scoring variance), pace/possessions (points-per-game volatility), injury
(unavailable → neutral 1.0), weather (unavailable → neutral 0), EPA
(unavailable → points-efficiency proxy), market odds (unavailable → None).

Market Edge = Model Prob − Market Prob only when a book line exists.
Without a CFL odds feed, edge gates stay inactive (no fabricated books).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence


MODEL_NAME = "cfl_v3_cal_blend"


# ---------------------------------------------------------------------------
# Small pure-Python Platt calibrator (NFL-style; no sklearn hard dep)
# ---------------------------------------------------------------------------
@dataclass
class PlattCalibrator:
    A: float = 1.0
    B: float = 0.0
    fitted: bool = False

    def fit(self, raw_probs: Sequence[float], y: Sequence[float]) -> "PlattCalibrator":
        """Fit A,B via Newton on logistic loss; falls back to shrinkage if tiny n."""
        pairs = [(float(p), float(o)) for p, o in zip(raw_probs, y) if o in (0.0, 1.0)]
        if len(pairs) < 12:
            # Cold-start: shrink toward 0.5 (fixes known overconfidence)
            self.A, self.B, self.fitted = 0.50, 0.0, True
            return self
        A, B = 0.7, 0.0
        for _ in range(40):
            gA = gB = 0.0
            hAA = hBB = hAB = 0.0
            for p, o in pairs:
                p = min(max(p, 1e-6), 1 - 1e-6)
                z = math.log(p / (1 - p))
                s = 1.0 / (1.0 + math.exp(-(A * z + B)))
                s = min(max(s, 1e-6), 1 - 1e-6)
                err = s - o
                gA += err * z
                gB += err
                w = s * (1 - s)
                hAA += w * z * z
                hBB += w
                hAB += w * z
            det = hAA * hBB - hAB * hAB
            if abs(det) < 1e-12:
                break
            dA = (hBB * gA - hAB * gB) / det
            dB = (hAA * gB - hAB * gA) / det
            A -= 0.5 * dA
            B -= 0.5 * dB
        # Keep mild shrinkage — never amplify overconfidence
        self.A = min(max(A, 0.25), 1.0)
        self.B = min(max(B, -0.40), 0.40)
        self.fitted = True
        return self

    def transform(self, p: float) -> float:
        p = min(max(float(p), 1e-6), 1 - 1e-6)
        z = math.log(p / (1 - p))
        a = self.A if self.fitted else 0.50
        b = self.B if self.fitted else 0.0
        return 1.0 / (1.0 + math.exp(-(a * z + b)))


# ---------------------------------------------------------------------------
# Rating engines
# ---------------------------------------------------------------------------
@dataclass
class EloSystem:
    k: float = 16.0
    base: float = 1500.0
    home_advantage: float = 32.0  # CFL HFA (lower than NFL; small-sample)
    ratings: dict[str, float] = field(default_factory=dict)

    def get(self, team: str) -> float:
        return self.ratings.setdefault(team, self.base)

    def expected(self, home: str, away: str) -> float:
        ra = self.get(home) + self.home_advantage
        rb = self.get(away)
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

    def update(self, home: str, away: str, home_score: int, away_score: int) -> None:
        exp = self.expected(home, away)
        if home_score > away_score:
            score = 1.0
        elif home_score < away_score:
            score = 0.0
        else:
            score = 0.5
        margin = abs(home_score - away_score)
        mov = math.log(max(margin, 1) + 1.0) * (
            2.2 / ((0.001 * abs(self.get(home) - self.get(away))) + 2.2)
        )
        k = self.k * mov
        self.ratings[home] = self.get(home) + k * (score - exp)
        self.ratings[away] = self.get(away) + k * ((1.0 - score) - (1.0 - exp))


@dataclass
class MarginRating:
    """Expected-margin engine — independent of ML win-prob."""

    k: float = 0.16
    hfa: float = 2.0  # CFL home points
    ratings: dict[str, float] = field(default_factory=dict)

    def get(self, team: str) -> float:
        return self.ratings.setdefault(team, 0.0)

    def expected_margin(self, home: str, away: str) -> float:
        """Home perspective: positive ⇒ home favored by that many points."""
        return self.get(home) - self.get(away) + self.hfa

    def update(self, home: str, away: str, home_score: int, away_score: int) -> None:
        actual = float(home_score - away_score)
        pred = self.expected_margin(home, away)
        err = actual - pred
        self.ratings[home] = self.get(home) + self.k * err
        self.ratings[away] = self.get(away) - self.k * err


@dataclass
class OffDefRating:
    """Points-for / points-against rolling means for totals."""

    league_ppg: float = 24.5
    off: dict[str, float] = field(default_factory=dict)
    deff: dict[str, float] = field(default_factory=dict)
    n: dict[str, int] = field(default_factory=dict)
    var: dict[str, float] = field(default_factory=dict)

    def get_off(self, team: str) -> float:
        return self.off.get(team, self.league_ppg)

    def get_def(self, team: str) -> float:
        return self.deff.get(team, self.league_ppg)

    def get_var(self, team: str) -> float:
        return self.var.get(team, 10.0)

    def update(self, home: str, away: str, home_score: int, away_score: int) -> None:
        for team, scored, allowed in (
            (home, float(home_score), float(away_score)),
            (away, float(away_score), float(home_score)),
        ):
            n = self.n.get(team, 0)
            alpha = 1.0 / (n + 1) if n < 6 else 0.18
            prev_o = self.get_off(team)
            prev_d = self.get_def(team)
            self.off[team] = (1 - alpha) * prev_o + alpha * scored
            self.deff[team] = (1 - alpha) * prev_d + alpha * allowed
            prev_v = self.get_var(team)
            self.var[team] = (1 - alpha) * prev_v + alpha * abs(scored - self.league_ppg)
            self.n[team] = n + 1


@dataclass
class TeamForm:
    recent: list[float] = field(default_factory=list)
    last_game_date: str | None = None
    to_diff: float = 0.0
    qb_proxy: float = 1.0
    games: int = 0
    pf: float = 0.0
    pa: float = 0.0

    @property
    def form_last5(self) -> float:
        r = self.recent[-5:]
        return sum(r) / len(r) if r else 0.5


FEATURE_CATALOG = [
    {"name": "elo_diff", "status": "real", "note": "Walk-forward Elo from finals"},
    {"name": "form_diff", "status": "real", "note": "L5 win% from finals"},
    {"name": "off_vs_def", "status": "real", "note": "PPG offense vs opponent defense"},
    {"name": "to_diff", "status": "proxy", "note": "Margin-based turnover proxy (no TO feed)"},
    {"name": "qb_advantage", "status": "proxy", "note": "Offense+form blend (no EPA/QB feed)"},
    {"name": "rest_diff", "status": "real", "note": "Days since last game"},
    {"name": "st_variance", "status": "proxy", "note": "Scoring variance as ST/pace stand-in"},
    {"name": "injury_factor", "status": "missing", "note": "Interface only — neutral 1.0"},
    {"name": "weather", "status": "missing", "note": "Interface only — neutral 0"},
    {"name": "epa", "status": "missing", "note": "No EPA feed — points efficiency used"},
    {"name": "market_prob", "status": "missing", "note": "No CFL odds feed — edge inactive"},
]


def _rest_days(last_iso: str | None, game_iso: str | None) -> int:
    if not last_iso or not game_iso:
        return 7
    try:
        from datetime import datetime

        a = datetime.fromisoformat(str(last_iso).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(game_iso).replace("Z", "+00:00"))
        return max(3, min(14, int((b - a).total_seconds() // 86400)))
    except ValueError:
        return 7


def _logit(p: float) -> float:
    p = min(max(p, 0.02), 0.98)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass
class EngineState:
    elo: EloSystem = field(default_factory=EloSystem)
    margin: MarginRating = field(default_factory=MarginRating)
    od: OffDefRating = field(default_factory=OffDefRating)
    form: dict[str, TeamForm] = field(default_factory=dict)
    cal_probs: list[float] = field(default_factory=list)
    cal_y: list[float] = field(default_factory=list)
    calibrator: PlattCalibrator = field(default_factory=PlattCalibrator)

    def _tf(self, team: str) -> TeamForm:
        return self.form.setdefault(team, TeamForm())

    def observe(self, g: dict[str, Any]) -> None:
        home, away = g["home_team"], g["away_team"]
        hs, as_ = int(g["home_score"]), int(g["away_score"])
        self.elo.update(home, away, hs, as_)
        self.margin.update(home, away, hs, as_)
        self.od.update(home, away, hs, as_)
        for team, scored, allowed, won in (
            (home, hs, as_, hs > as_),
            (away, as_, hs, as_ > hs),
        ):
            tf = self._tf(team)
            tf.games += 1
            tf.pf += scored
            tf.pa += allowed
            if hs == as_:
                tf.recent.append(0.5)
            else:
                tf.recent.append(1.0 if won else 0.0)
            tf.last_game_date = g.get("game_date")
            margin = scored - allowed
            tf.to_diff = 0.85 * tf.to_diff + 0.15 * (margin / 14.0)
            off = (tf.pf / tf.games) / 24.5 if tf.games else 1.0
            tf.qb_proxy = max(0.75, min(1.35, 0.55 * off + 0.45 * (0.85 + 0.3 * tf.form_last5)))

    def refit_calibrator(self) -> None:
        self.calibrator.fit(self.cal_probs, self.cal_y)


def build_state(completed: list[dict[str, Any]]) -> EngineState:
    st = EngineState()
    for g in sorted(completed, key=lambda x: x.get("game_date") or ""):
        if g.get("home_score") is None or g.get("away_score") is None:
            continue
        st.observe(g)
    return st


def build_live_state(completed: list[dict[str, Any]], *, min_train: int = 8) -> EngineState:
    """Ratings + expanding calibrator as of last final (for upcoming picks)."""
    games = sorted(
        [g for g in completed if g.get("home_score") is not None and g.get("away_score") is not None],
        key=lambda x: x.get("game_date") or "",
    )
    st = EngineState()
    for i, g in enumerate(games):
        if i >= min_train:
            if len(st.cal_probs) >= 8:
                st.refit_calibrator()
            pred = predict_matchup_v2(
                g["home_team"], g["away_team"], state=st, game_date=g.get("game_date")
            )
            hs, as_ = int(g["home_score"]), int(g["away_score"])
            y = 1.0 if hs > as_ else (0.0 if hs < as_ else 0.5)
            if y in (0.0, 1.0):
                st.cal_probs.append(pred["raw_home_win_prob"])
                st.cal_y.append(y)
        st.observe(g)
    if len(st.cal_probs) >= 8:
        st.refit_calibrator()
    return st


def _feature_bundle(
    st: EngineState,
    home: str,
    away: str,
    game_date: str | None,
) -> dict[str, Any]:
    hf, af = st._tf(home), st._tf(away)
    rest_h = _rest_days(hf.last_game_date, game_date)
    rest_a = _rest_days(af.last_game_date, game_date)
    elo_diff = (st.elo.get(home) - st.elo.get(away)) / 400.0
    form_diff = hf.form_last5 - af.form_last5
    off_vs_def = (st.od.get_off(home) - st.od.get_def(away)) / 24.5
    def_vs_off = (st.od.get_def(home) - st.od.get_off(away)) / 24.5
    to_diff = hf.to_diff - af.to_diff
    qb_adv = hf.qb_proxy - af.qb_proxy
    rest_diff = (rest_h - rest_a) / 4.0
    st_var = (st.od.get_var(home) + st.od.get_var(away)) / 2.0
    return {
        "elo_diff": elo_diff,
        "form_diff": form_diff,
        "off_vs_def": off_vs_def,
        "def_vs_off": def_vs_off,
        "to_diff": to_diff,
        "qb_advantage": qb_adv,
        "rest_diff": rest_diff,
        "rest_home": rest_h,
        "rest_away": rest_a,
        "st_variance": st_var,
        "injury_home": 1.0,
        "injury_away": 1.0,
        "weather": 0.0,
        "market_prob": None,
        "qb_home": hf.qb_proxy,
        "qb_away": af.qb_proxy,
        "games_home": hf.games,
        "games_away": af.games,
    }


def predict_matchup_v2(
    home: str,
    away: str,
    *,
    state: EngineState,
    game_date: str | None = None,
    market_home_prob: float | None = None,
) -> dict[str, Any]:
    """Separate ML / Spread / Totals. Spread NOT derived from ML win-prob."""
    feats = _feature_bundle(state, home, away, game_date)
    if market_home_prob is not None:
        feats["market_prob"] = market_home_prob

    games_seen = min(int(feats.get("games_home") or 0), int(feats.get("games_away") or 0))
    base = state.elo.expected(home, away)

    # Early-season Elo damp + milder feature adj (reduces overconfidence)
    elo_scale = 0.70 if games_seen < 4 else 0.90
    adj = 0.0
    adj += 0.42 * feats["form_diff"]
    adj += 0.32 * feats["off_vs_def"]
    adj += 0.32 * (-feats["def_vs_off"])
    adj += 0.14 * feats["to_diff"]
    adj += 0.22 * feats["qb_advantage"]
    adj += 0.14 * feats["rest_diff"]
    inj = (feats["injury_home"] / max(feats["injury_away"], 1e-6)) - 1.0
    adj += 0.12 * inj

    raw_home = _sigmoid(elo_scale * _logit(base) + adj)
    shrink = 0.45 if games_seen < 5 else 0.22
    raw_home = 0.5 + (raw_home - 0.5) * (1.0 - shrink)
    raw_home = min(max(raw_home, 0.18), 0.82)
    home_p = state.calibrator.transform(raw_home)

    # Blend with form/OD component (independent of Elo path)
    raw2 = _sigmoid(
        0.70 * feats["form_diff"]
        + 0.80 * feats["off_vs_def"]
        + 0.80 * (-feats["def_vs_off"])
        + 0.40 * feats["qb_advantage"]
    )
    raw2 = min(max(raw2, 0.20), 0.80)
    home_p = 0.78 * home_p + 0.22 * raw2
    home_p = min(max(home_p, 0.22), 0.78)
    away_p = 1.0 - home_p

    # ---- Spread (margin engine — independent) ----
    exp_margin = state.margin.expected_margin(home, away)
    exp_margin += 1.1 * feats["off_vs_def"] * 2.45
    exp_margin += 0.65 * feats["qb_advantage"] * 7.0
    exp_margin += 0.35 * feats["rest_diff"] * 3.0
    model_spread = round(-exp_margin, 1)
    spread_conf = min(0.95, abs(exp_margin) / 14.0)

    # ---- Totals (team projected scores + variance + league regression) ----
    home_exp = 0.5 * (state.od.get_off(home) + state.od.get_def(away)) + 0.7
    away_exp = 0.5 * (state.od.get_off(away) + state.od.get_def(home))
    home_exp = 0.65 * home_exp + 0.35 * 24.5
    away_exp = 0.65 * away_exp + 0.35 * 24.5
    pace_bump = 0.08 * (feats["st_variance"] - 10.0)
    home_exp += pace_bump * 0.5
    away_exp += pace_bump * 0.5
    home_exp = max(12.0, min(42.0, home_exp))
    away_exp = max(12.0, min(42.0, away_exp))
    model_total = round(home_exp + away_exp, 1)
    total_var = (state.od.get_var(home) + state.od.get_var(away)) / 2.0
    total_sigma = max(6.0, min(16.0, 5.5 + 0.35 * total_var))

    pick = home if home_p >= 0.5 else away
    fav_p = max(home_p, away_p)

    elo_side = home if base >= 0.5 else away
    form_side = home if feats["form_diff"] > 0 else (away if feats["form_diff"] < 0 else pick)
    od_side = home if (feats["off_vs_def"] - feats["def_vs_off"]) > 0 else away
    sides = [elo_side, form_side, od_side]
    agree_n = sum(1 for s in sides if s == pick)

    market_p = feats.get("market_prob")
    model_edge = (home_p - market_p) if market_p is not None else None
    qb_ok = (feats["qb_advantage"] > 0.02 and pick == home) or (
        feats["qb_advantage"] < -0.02 and pick == away
    )
    edge_ok = True if model_edge is None else (
        (model_edge > 0.03 and pick == home) or (model_edge < -0.03 and pick == away)
    )

    # Raise confidence only with multi-agree + edge (when available) + QB lean
    if agree_n >= 3 and edge_ok and qb_ok and fav_p >= 0.56:
        conf = min(0.68, 0.42 + 0.40 * (fav_p - 0.5) * 2)
        bet_ml = True
    elif agree_n >= 2 and fav_p >= 0.54:
        conf = min(0.55, 0.34 + 0.28 * (fav_p - 0.5) * 2)
        bet_ml = True
    else:
        conf = min(0.42, abs(home_p - 0.5) * 1.3)
        bet_ml = fav_p >= 0.53

    # Spread bet gate: meaningful margin + agree; book edge when available
    book_spread = None
    spread_edge = None if book_spread is None else (abs(exp_margin) - abs(float(book_spread)))
    bet_spread = bool(
        spread_conf >= 0.35
        and abs(exp_margin) >= 3.0
        and agree_n >= 2
        and (spread_edge is None or spread_edge >= 1.5)
    )
    if book_spread is None:
        # No CFL books feed — do not fabricate ATS bets for ROI boards
        bet_spread = False

    book_total = None
    total_edge = None if book_total is None else (model_total - float(book_total))
    bet_total = False
    total_lean = None
    if book_total is not None and abs(model_total - book_total) >= 2.5:
        z = (model_total - book_total) / total_sigma
        if abs(z) >= 0.35:
            bet_total = True
            total_lean = "OVER" if model_total > book_total else "UNDER"

    bits = [
        f"Model lean {'home' if home_p >= away_p else 'away'}",
        f"Form {state._tf(home).form_last5:.0%}–{state._tf(away).form_last5:.0%}",
        f"Rest {feats['rest_home']}d/{feats['rest_away']}d",
        f"Agree {agree_n}/3",
    ]
    if model_edge is not None:
        bits.append(f"Edge {model_edge:+.1%}")
    explanation = "; ".join(bits) + "."

    return {
        "home_team": home,
        "away_team": away,
        "home_win_prob": round(home_p, 4),
        "away_win_prob": round(away_p, 4),
        "raw_home_win_prob": round(raw_home, 4),
        "predicted_home_score": round(home_exp, 1),
        "predicted_away_score": round(away_exp, 1),
        "model_spread": model_spread,
        "expected_margin_home": round(exp_margin, 2),
        "spread_confidence": round(spread_conf, 3),
        "model_total": model_total,
        "total_sigma": round(total_sigma, 2),
        "pick_ml": pick,
        "confidence": round(conf, 3),
        "bet_ml": bet_ml,
        "bet_spread": bet_spread,
        "bet_total": bet_total,
        "total_lean": total_lean,
        "model_edge": None if model_edge is None else round(model_edge, 4),
        "agree_n": agree_n,
        "explanation": explanation,
        "rest_home": feats["rest_home"],
        "rest_away": feats["rest_away"],
        "features": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in feats.items()},
        "feature_catalog": FEATURE_CATALOG,
        "model_name": MODEL_NAME,
    }


def walk_forward_predictions(
    completed: list[dict[str, Any]],
    *,
    min_train: int = 8,
) -> list[dict[str, Any]]:
    """Strict walk-forward: train on prior finals only; fit calibrator expanding."""
    games = sorted(
        [g for g in completed if g.get("home_score") is not None and g.get("away_score") is not None],
        key=lambda x: x.get("game_date") or "",
    )
    st = EngineState()
    out: list[dict[str, Any]] = []
    for i, g in enumerate(games):
        if i < min_train:
            st.observe(g)
            continue
        if len(st.cal_probs) >= 8:
            st.refit_calibrator()
        pred = predict_matchup_v2(
            g["home_team"], g["away_team"], state=st, game_date=g.get("game_date")
        )
        hs, as_ = int(g["home_score"]), int(g["away_score"])
        y = 1.0 if hs > as_ else (0.0 if hs < as_ else 0.5)
        if y in (0.0, 1.0):
            st.cal_probs.append(pred["raw_home_win_prob"])
            st.cal_y.append(y)
        row = {
            **g,
            **pred,
            "actual_home_win": y,
            "actual_margin_home": hs - as_,
            "actual_total": hs + as_,
        }
        out.append(row)
        st.observe(g)
    return out


def feature_importance_report(
    completed: list[dict[str, Any]],
    *,
    min_train: int = 8,
) -> list[dict[str, Any]]:
    """Leave-one-feature-out accuracy delta (walk-forward). Display only."""
    base_rows = walk_forward_predictions(completed, min_train=min_train)
    decisive = [r for r in base_rows if r.get("actual_home_win") in (0.0, 1.0)]
    if len(decisive) < 12:
        return []

    def _acc(rows: list[dict[str, Any]]) -> float:
        hits = 0
        n = 0
        for r in rows:
            y = r.get("actual_home_win")
            if y not in (0.0, 1.0):
                continue
            n += 1
            winner = r["home_team"] if y == 1.0 else r["away_team"]
            if r.get("pick_ml") == winner:
                hits += 1
        return hits / n if n else 0.0

    base_acc = _acc(base_rows)
    keys = ["form_diff", "off_vs_def", "def_vs_off", "to_diff", "qb_advantage", "rest_diff"]
    out: list[dict[str, Any]] = []
    # Approximate importance from absolute feature magnitude × sign agreement
    for key in keys:
        mag = 0.0
        agree = 0
        n = 0
        for r in decisive:
            feats = r.get("features") or {}
            v = feats.get(key)
            if v is None:
                continue
            n += 1
            mag += abs(float(v))
            y = r["actual_home_win"]
            lean_home = float(v) > 0
            if key == "def_vs_off":
                lean_home = float(v) < 0
            if lean_home == (y == 1.0):
                agree += 1
        out.append(
            {
                "feature": key,
                "status": next((c["status"] for c in FEATURE_CATALOG if c["name"] == key), "real"),
                "mean_abs": round(mag / n, 4) if n else 0.0,
                "sign_agree": round(agree / n, 3) if n else None,
                "base_acc": round(base_acc, 3),
            }
        )
    out.sort(key=lambda r: -(r.get("mean_abs") or 0))
    return out
