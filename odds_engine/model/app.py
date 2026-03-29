import os
from typing import Optional, Dict, Any

import numpy as np
from fastapi import FastAPI
from pydantic import BaseModel

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
