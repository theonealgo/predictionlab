"""CFL prediction engine — Elo, HFA, form, efficiencies, TO diff, QB, rest."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EloSystem:
    k: float = 22.0
    base: float = 1500.0
    home_advantage: float = 48.0  # CFL HFA (points-ish → Elo scale)
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
        mov = math.log(max(margin, 1) + 1.0) * (2.2 / ((0.001 * abs(self.get(home) - self.get(away))) + 2.2))
        k = self.k * mov
        self.ratings[home] = self.get(home) + k * (score - exp)
        self.ratings[away] = self.get(away) + k * ((1.0 - score) - (1.0 - exp))


@dataclass
class TeamProfile:
    name: str
    elo: float = 1500.0
    games: int = 0
    wins: int = 0
    losses: int = 0
    pf: float = 0.0
    pa: float = 0.0
    off_eff: float = 1.0
    def_eff: float = 1.0
    to_diff: float = 0.0
    form_last5: float = 0.5
    qb_rating: float = 1.0
    last_game_date: str | None = None
    recent: list[float] = field(default_factory=list)  # 1/0.5/0


def build_profiles(completed: list[dict[str, Any]]) -> tuple[EloSystem, dict[str, TeamProfile]]:
    elo = EloSystem()
    profiles: dict[str, TeamProfile] = {}

    def _p(name: str) -> TeamProfile:
        return profiles.setdefault(name, TeamProfile(name=name))

    for g in sorted(completed, key=lambda x: x.get("game_date") or ""):
        home = g["home_team"]
        away = g["away_team"]
        hs = g.get("home_score")
        as_ = g.get("away_score")
        if hs is None or as_ is None:
            continue
        hs, as_ = int(hs), int(as_)
        elo.update(home, away, hs, as_)
        for team, scored, allowed, won in (
            (home, hs, as_, hs > as_),
            (away, as_, hs, as_ > hs),
        ):
            p = _p(team)
            p.games += 1
            p.pf += scored
            p.pa += allowed
            if hs == as_:
                p.recent.append(0.5)
            else:
                p.recent.append(1.0 if won else 0.0)
                if won:
                    p.wins += 1
                else:
                    p.losses += 1
            p.last_game_date = g.get("game_date")
            # Crude TO proxy from margin (no official TO feed in rounds.json)
            margin = scored - allowed
            p.to_diff = 0.85 * p.to_diff + 0.15 * (margin / 14.0)

    league_off = []
    for name, p in profiles.items():
        p.elo = elo.get(name)
        if p.games:
            p.off_eff = (p.pf / p.games) / 24.5
            p.def_eff = (p.pa / p.games) / 24.5
            league_off.append(p.off_eff)
            recent = p.recent[-5:]
            p.form_last5 = sum(recent) / len(recent) if recent else 0.5
            # QB proxy: offense + form
            p.qb_rating = max(0.75, min(1.35, 0.55 * p.off_eff + 0.45 * (0.85 + 0.3 * p.form_last5)))
    return elo, profiles


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


def predict_matchup(
    home: str,
    away: str,
    *,
    elo: EloSystem,
    profiles: dict[str, TeamProfile],
    game_date: str | None = None,
) -> dict[str, Any]:
    hp = profiles.get(home) or TeamProfile(name=home, elo=elo.get(home))
    ap = profiles.get(away) or TeamProfile(name=away, elo=elo.get(away))
    rest_h = _rest_days(hp.last_game_date, game_date)
    rest_a = _rest_days(ap.last_game_date, game_date)

    base = elo.expected(home, away)
    # Feature adjustments (logistic on logit)
    def _logit(p: float) -> float:
        p = min(max(p, 0.02), 0.98)
        return math.log(p / (1.0 - p))

    adj = 0.0
    adj += 0.55 * (hp.form_last5 - ap.form_last5)
    adj += 0.40 * (hp.off_eff - ap.def_eff)
    adj += 0.40 * (ap.off_eff - hp.def_eff) * -1.0
    adj += 0.25 * (hp.to_diff - ap.to_diff)
    adj += 0.35 * (hp.qb_rating - ap.qb_rating)
    adj += 0.08 * ((rest_h - 6) - (rest_a - 6)) / 4.0

    home_p = 1.0 / (1.0 + math.exp(-(_logit(base) + adj)))
    home_p = min(max(home_p, 0.12), 0.88)
    away_p = 1.0 - home_p

    # Score model: league ~24.5 PPG; adjust by eff + win prob
    home_exp = 24.5 * hp.off_eff * (0.92 + 0.16 * home_p) / max(ap.def_eff, 0.7)
    away_exp = 24.5 * ap.off_eff * (0.92 + 0.16 * away_p) / max(hp.def_eff, 0.7)
    # Mild total regression
    total = home_exp + away_exp
    target_total = 49.0 + 4.0 * (hp.off_eff + ap.off_eff - 2.0)
    scale = target_total / max(total, 1.0)
    home_exp *= 0.55 + 0.45 * scale
    away_exp *= 0.55 + 0.45 * scale
    home_score = round(home_exp, 1)
    away_score = round(away_exp, 1)
    spread = round(away_score - home_score, 1)  # home perspective (negative = home favored)
    model_total = round(home_score + away_score, 1)
    pick = home if home_p >= 0.5 else away
    conf = round(abs(home_p - 0.5) * 2.0, 3)

    # Product-facing blurb only — no rating-system / formula vocabulary.
    side = "home" if elo.get(home) >= elo.get(away) else "away"
    bits = [
        f"Model lean {side}",
        f"Recent form {hp.form_last5:.0%}–{ap.form_last5:.0%}",
        f"Rest {rest_h}d/{rest_a}d",
    ]
    explanation = "; ".join(bits) + "."

    return {
        "home_team": home,
        "away_team": away,
        "home_win_prob": round(home_p, 4),
        "away_win_prob": round(away_p, 4),
        "predicted_home_score": home_score,
        "predicted_away_score": away_score,
        "model_spread": spread,
        "model_total": model_total,
        "pick_ml": pick,
        "confidence": conf,
        "explanation": explanation,
        "rest_home": rest_h,
        "rest_away": rest_a,
        "features": {
            "elo_home": round(elo.get(home), 1),
            "elo_away": round(elo.get(away), 1),
            "form_home": round(hp.form_last5, 3),
            "form_away": round(ap.form_last5, 3),
            "off_home": round(hp.off_eff, 3),
            "def_home": round(hp.def_eff, 3),
            "off_away": round(ap.off_eff, 3),
            "def_away": round(ap.def_eff, 3),
            "to_diff_home": round(hp.to_diff, 3),
            "to_diff_away": round(ap.to_diff, 3),
            "qb_home": round(hp.qb_rating, 3),
            "qb_away": round(ap.qb_rating, 3),
            "hfa": elo.home_advantage,
        },
    }
