import os
from typing import Optional, Dict, Any, List

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
try:
    from .prop_math import calculate as calculate_prop
except ImportError:
    from prop_math import calculate as calculate_prop

app = FastAPI()

MONTE_CARLO_RUNS = int(os.getenv("MONTE_CARLO_RUNS", "10000"))

HOME_FIELD_ADV = {
    "NFL": 2.5,
    "NBA": 3.0,
    "WNBA": 3.0,
    "NCAAF": 3.0,
    "NCAAB": 3.0,
    "NCAAW": 3.0,
    "NHL": 0.3,
    "MLB": 0.15,
    "SOCCER": 0.2,
}

BASE_SCORING = {
    "NFL": 23.0,
    "NBA": 112.0,
    "NCAAB": 70.0,
    "NCAAW": 68.0,
    "WNBA": 79.0,
    "NHL": 3.1,
    "MLB": 4.4,
    "SOCCER": 1.4,
}

SCORE_STD = {
    "NFL": 10.0,
    "NBA": 12.0,
    "NCAAB": 12.0,
    "NCAAW": 11.0,
    "WNBA": 9.0,
    "NHL": 1.4,
    "MLB": 2.0,
    "SOCCER": 1.2,
}

LOW_SCORING = {"NHL", "MLB", "SOCCER"}


class PredictRequest(BaseModel):
    sport: str
    home_team: str
    away_team: str
    home_stats: Optional[Dict[str, Any]] = None
    away_stats: Optional[Dict[str, Any]] = None


class PropRequest(BaseModel):
    player: str
    player_id: Optional[str] = None
    request_id: Optional[str] = None
    sport: str
    prop_type: str
    player_stats: Dict[str, Any]
    real_line: Optional[float] = None


class PropBatchRequest(BaseModel):
    items: List[PropRequest]


def _get_stat(stats: Optional[Dict[str, Any]], key: str, fallback: float) -> float:
    if not stats:
        return fallback
    try:
        val = stats.get(key)
        return float(val) if val is not None else fallback
    except Exception:
        return fallback


def _expected_scores(sport: str, home_stats: Optional[Dict[str, Any]], away_stats: Optional[Dict[str, Any]]):
    base = BASE_SCORING.get(sport, 20.0)
    home_off = _get_stat(home_stats, "offense", base)
    home_def = _get_stat(home_stats, "defense", base)
    away_off = _get_stat(away_stats, "offense", base)
    away_def = _get_stat(away_stats, "defense", base)
    hfa = HOME_FIELD_ADV.get(sport, 0.0)
    home_score = (home_off + away_def) / 2 + hfa
    away_score = (away_off + home_def) / 2
    return max(home_score, 0.1), max(away_score, 0.1)


def _simulate_scores(sport: str, home_mu: float, away_mu: float, runs: int):
    if sport in LOW_SCORING:
        home_scores = np.random.poisson(lam=home_mu, size=runs)
        away_scores = np.random.poisson(lam=away_mu, size=runs)
    else:
        std = SCORE_STD.get(sport, 10.0)
        home_scores = np.random.normal(loc=home_mu, scale=std, size=runs)
        away_scores = np.random.normal(loc=away_mu, scale=std, size=runs)
        home_scores = np.clip(home_scores, 0, None)
        away_scores = np.clip(away_scores, 0, None)
    return home_scores, away_scores


def _safe_float(v: Any, fallback: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return fallback


def _to_half_step(v: float) -> float:
    return round(round(float(v) * 2.0) / 2.0, 1)


_SPORT_PROP_SCALE = {
    "NBA": {"points": 1.00, "rebounds": 0.36, "assists": 0.30, "threes": 0.12},
    "WNBA": {"points": 0.90, "rebounds": 0.34, "assists": 0.28, "threes": 0.10},
    "NCAAB": {"points": 0.82, "rebounds": 0.34, "assists": 0.28, "threes": 0.10},
    "NCAAW": {"points": 0.78, "rebounds": 0.33, "assists": 0.27, "threes": 0.09},
    "MLB": {"hits": 0.11, "runs": 0.07, "rbis": 0.07, "strikeouts": 0.45},
    "NFL": {"passing_yards": 9.8, "rushing_yards": 2.9, "receptions": 0.25, "receiving_yards": 3.2, "tds": 0.06},
    "NCAAF": {"passing_yards": 9.1, "rushing_yards": 2.8, "receptions": 0.24, "receiving_yards": 3.0, "tds": 0.06},
    "NHL": {"goals": 0.05, "assists": 0.07, "shots": 0.16},
    "SOCCER": {"goals": 0.03, "assists": 0.05, "shots": 0.14, "shots_on_target": 0.08},
}


def _ladder_line(prop_type: str, projection: float) -> float:
    p = prop_type.lower()
    if p == "points":
        return float(max(5, int(round(projection / 5.0)) * 5))
    if p in {"rebounds", "assists"}:
        lo = max(1, int(projection) - 1)
        return float(lo)
    if p in {"threes", "goals", "shots_on_goal", "receptions"}:
        return round(max(0.5, round(projection) - 0.5), 1)
    if p in {"passing_yards", "rushing_yards", "receiving_yards"}:
        return float(int(round(projection / 5.0)) * 5)
    if p in {"hits", "runs", "rbis", "strikeouts", "shots", "tds"}:
        return round(max(0.5, round(projection) - 0.5), 1)
    return round(projection, 1)


def generate_internal_prop_line(player_stats: Dict[str, Any], sport: str, prop_type: str, player: str) -> Dict[str, Any]:
    generated = calculate_prop(player_stats, sport.upper(), prop_type.lower())
    return {**generated, "player": player, "sport": sport.upper(), "prop_type": prop_type.lower()}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/predict")
def predict(req: PredictRequest):
    sport = req.sport.upper()
    home_mu, away_mu = _expected_scores(sport, req.home_stats, req.away_stats)
    spread = home_mu - away_mu
    total = home_mu + away_mu

    home_scores, away_scores = _simulate_scores(sport, home_mu, away_mu, MONTE_CARLO_RUNS)
    home_wins = home_scores > away_scores
    win_prob_home = float(home_wins.mean())
    win_prob_away = float(1 - win_prob_home)

    cover_prob_home = float((home_scores - away_scores > spread).mean())
    over_prob = float((home_scores + away_scores > total).mean())

    return {
        "win_prob_home": round(win_prob_home, 4),
        "win_prob_away": round(win_prob_away, 4),
        "expected_home_score": round(home_mu, 2),
        "expected_away_score": round(away_mu, 2),
        "spread": round(spread, 2),
        "total": round(total, 2),
        "cover_prob_home": round(cover_prob_home, 4),
        "over_prob": round(over_prob, 4),
        "sim_runs": MONTE_CARLO_RUNS,
    }


@app.post("/predict-prop")
def predict_prop(req: PropRequest):
    try:
        generated = calculate_prop(req.player_stats or {}, req.sport, req.prop_type, req.real_line)
        return {**generated, "player": req.player, "player_id": req.player_id, "request_id": req.request_id,
                "sport": req.sport.upper(), "prop_type": req.prop_type}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/predict-props")
def predict_props(req: PropBatchRequest):
    out = []
    for item in req.items:
        try:
            out.append(predict_prop(item))
        except HTTPException as error:
            out.append({"player_id": item.player_id, "player": item.player, "prop_type": item.prop_type,
                        "request_id": item.request_id,
                        "available": False, "reason": error.detail})
    return {"count": len(out), "items": out}
