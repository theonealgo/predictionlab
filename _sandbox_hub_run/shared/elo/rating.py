"""Classic Elo with optional home-field advantage."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class EloSystem:
    k: float = 20.0
    base: float = 1500.0
    home_advantage: float = 55.0
    ratings: Dict[str, float] = field(default_factory=dict)

    def get(self, team: str) -> float:
        return self.ratings.setdefault(team, self.base)

    def expected(self, a: str, b: str, *, home: bool = False) -> float:
        ra = self.get(a) + (self.home_advantage if home else 0.0)
        rb = self.get(b)
        return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

    def update(self, winner: str, loser: str, *, home_winner: bool | None = None) -> None:
        # ML note: treat draws via update_draw; margin-adjusted K can wrap this upstream.
        exp_w = self.expected(winner, loser, home=bool(home_winner))
        self.ratings[winner] = self.get(winner) + self.k * (1.0 - exp_w)
        self.ratings[loser] = self.get(loser) + self.k * (0.0 - (1.0 - exp_w))

    def update_draw(self, a: str, b: str, *, a_home: bool = True) -> None:
        exp_a = self.expected(a, b, home=a_home)
        self.ratings[a] = self.get(a) + self.k * (0.5 - exp_a)
        self.ratings[b] = self.get(b) + self.k * (0.5 - (1.0 - exp_a))
