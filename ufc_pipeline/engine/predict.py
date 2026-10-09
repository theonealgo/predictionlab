"""UFC win probabilities — Elo + odds-implied + record prior. Never invent flat 50% for all."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any


def american_to_implied(ml: int | float) -> float:
    ml = float(ml)
    if ml < 0:
        return (-ml) / ((-ml) + 100.0)
    return 100.0 / (ml + 100.0)


def american_from_prob(p: float) -> int:
    p = max(0.01, min(0.99, float(p)))
    if p >= 0.5:
        return int(round(-100.0 * p / (1.0 - p)))
    return int(round(100.0 * (1.0 - p) / p))


def devig_two_way(home_ml: int, away_ml: int) -> tuple[float, float]:
    ih = american_to_implied(home_ml)
    ia = american_to_implied(away_ml)
    s = ih + ia
    if s <= 0:
        return 0.5, 0.5
    return ih / s, ia / s


def parse_record(summary: str | None) -> tuple[int, int, int]:
    if not summary:
        return 0, 0, 0
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)(?:\s*-\s*(\d+))?", str(summary))
    if not m:
        return 0, 0, 0
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def record_win_rate(summary: str | None) -> float | None:
    w, l, d = parse_record(summary)
    n = w + l + d
    if n < 3:
        return None
    return (w + 0.5 * d) / n


def elo_from_record(summary: str | None, base: float = 1500.0) -> float:
    wr = record_win_rate(summary)
    if wr is None:
        return base
    return base + (wr - 0.5) * 600.0


@dataclass
class EloSystem:
    k: float = 28.0
    base: float = 1500.0
    ratings: dict[str, float] = field(default_factory=dict)

    def get(self, name: str) -> float:
        return self.ratings.setdefault(name, self.base)

    def expected(self, a: str, b: str) -> float:
        ra, rb = self.get(a), self.get(b)
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

    def update(self, winner: str, loser: str) -> None:
        exp_w = self.expected(winner, loser)
        self.ratings[winner] = self.get(winner) + self.k * (1.0 - exp_w)
        self.ratings[loser] = self.get(loser) + self.k * (0.0 - (1.0 - exp_w))


def train_elo(completed: list[dict[str, Any]]) -> EloSystem:
    elo = EloSystem()
    for g in sorted(completed, key=lambda x: x.get("fight_date") or ""):
        home = g.get("home_fighter") or ""
        away = g.get("away_fighter") or ""
        winner = g.get("winner") or ""
        if not home or not away or not winner:
            continue
        if winner == home:
            elo.update(home, away)
        elif winner == away:
            elo.update(away, home)
    return elo


def _clamp(p: float, lo: float = 0.08, hi: float = 0.92) -> float:
    return max(lo, min(hi, p))


def predict_fight(
    home: str,
    away: str,
    *,
    elo: EloSystem | None = None,
    home_record: str | None = None,
    away_record: str | None = None,
    odds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    elo = elo or EloSystem()
    if home not in elo.ratings and home_record:
        elo.ratings[home] = elo_from_record(home_record)
    if away not in elo.ratings and away_record:
        elo.ratings[away] = elo_from_record(away_record)

    elo_home = elo.expected(home, away)
    has_trained = (home in elo.ratings) or (away in elo.ratings)

    odds_home = odds_away = None
    home_ml = away_ml = None
    books_count = 0
    if odds and odds.get("home_ml") is not None and odds.get("away_ml") is not None:
        home_ml = int(odds["home_ml"])
        away_ml = int(odds["away_ml"])
        odds_home, odds_away = devig_two_way(home_ml, away_ml)
        books_count = int(odds.get("books_count") or 0)

    source = "elo"
    if odds_home is not None and has_trained:
        hp = 0.72 * odds_home + 0.28 * elo_home
        source = "blend"
    elif odds_home is not None:
        hp = odds_home
        source = "odds"
    elif has_trained:
        hp = elo_home
        source = "elo"
    else:
        rh = record_win_rate(home_record) or 0.5
        ra = record_win_rate(away_record) or 0.5
        logit = math.log(max(rh, 0.05) / max(1 - rh, 0.05)) - math.log(
            max(ra, 0.05) / max(1 - ra, 0.05)
        )
        hp = 1.0 / (1.0 + math.exp(-0.85 * logit))
        source = "form"
        if abs(hp - 0.5) < 0.005:
            hp = elo_home
            source = "elo_seed"

    hp = _clamp(hp)
    ap = 1.0 - hp
    # Do NOT invent 50.5% / −108 coin-flip placeholders when models are tied.
    # Keep the true probability; UI hides empty/N/A model rows separately.

    pick = home if hp >= ap else away
    conf = abs(hp - 0.5) * 2.0
    # User-facing explanation only — no pipeline / IP vocabulary.
    bits = []
    if home_record or away_record:
        bits.append(f"Records {home_record or '—'} vs {away_record or '—'}")

    return {
        "home_win_prob": round(hp, 4),
        "away_win_prob": round(ap, 4),
        "pick_ml": pick,
        "confidence": round(conf, 3),
        "prob_source": source,
        "home_ml": home_ml,
        "away_ml": away_ml,
        "books_count": books_count,
        "elo_home": round(elo.get(home), 1),
        "elo_away": round(elo.get(away), 1),
        "explanation": "; ".join(bits) if bits else "Model pick",
        "pl_home_ml": american_from_prob(hp),
        "pl_away_ml": american_from_prob(ap),
    }
