"""League strength for European club competitions.

Each club's rating = its league's rating + the club's rating inside that league.
Domestic games move only club ratings (the league cancels out); European games,
where two leagues meet, also move the two league ratings. Clubs from leagues
we do not track share one "other leagues" rating.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

OTHER = "other leagues"


def is_european_competition(league: str | None) -> bool:
    return str(league or "").startswith("UEFA")


class LeagueElo:
    def __init__(self, k_team: float = 20.0, k_league: float = 4.0, home_adv: float = 60.0,
                 other_start: float = -120.0, min_league_games: int = 0):
        self.k_team = k_team
        self.k_league = k_league
        self.home_adv = home_adv
        self.other_start = other_start
        self.min_league_games = min_league_games
        self.league: dict[str, float] = defaultdict(float)
        self.club: dict[str, float] = defaultdict(float)
        self.home_league: dict[str, str] = {}

    def league_of(self, team: str) -> str:
        return self.home_league.get(team, OTHER)

    def rating(self, team: str) -> float:
        return self.league[self.league_of(team)] + self.club[team]

    def fit(self, games: list[dict]) -> "LeagueElo":
        """games: dicts with league, home, away, home_score, away_score, oldest first."""
        seen: dict[str, Counter] = defaultdict(Counter)
        for g in games:
            if not is_european_competition(g["league"]):
                seen[g["home"]][g["league"]] += 1
                seen[g["away"]][g["league"]] += 1
        size = Counter()
        for c in seen.values():
            size.update(c)
        # A league with only a handful of stored games cannot be rated on its own.
        self.home_league = {t: lg for t, c in seen.items()
                            for lg in [c.most_common(1)[0][0]] if size[lg] >= self.min_league_games}
        self.league[OTHER] = self.other_start
        for g in games:
            h, a = g["home"], g["away"]
            hs, as_ = float(g["home_score"]), float(g["away_score"])
            diff = self.rating(h) + self.home_adv - self.rating(a)
            exp = 1.0 / (1.0 + 10 ** (-diff / 400.0))
            act = 1.0 if hs > as_ else 0.0 if hs < as_ else 0.5
            d = (1.0 + 0.5 * math.log1p(abs(hs - as_))) * (act - exp)
            self.club[h] += self.k_team * d
            self.club[a] -= self.k_team * d
            lh, la = self.league_of(h), self.league_of(a)
            if lh != la:
                self.league[lh] += self.k_league * d
                self.league[la] -= self.k_league * d
        return self

    def offset(self, home: str, away: str) -> float:
        """League part of the rating gap (Elo points), home minus away."""
        return self.league[self.league_of(home)] - self.league[self.league_of(away)]


def shift_three_way(hw: float, dw: float, aw: float, elo_pts: float) -> tuple[float, float, float]:
    """Move a 1X2 by `elo_pts` of strength toward home, keeping the draw share."""
    s = hw + aw
    if s <= 0:
        return hw, dw, aw
    p = min(max(hw / s, 1e-6), 1 - 1e-6)
    logit = math.log(p / (1 - p)) + elo_pts * math.log(10) / 400.0
    p2 = 1.0 / (1.0 + math.exp(-logit))
    return p2 * s, dw, (1 - p2) * s
