"""ROI / unit tracking for model-only lines."""
from __future__ import annotations

from typing import Iterable, Mapping


def american_to_decimal(odds: float) -> float:
    if odds >= 100:
        return 1.0 + odds / 100.0
    if odds <= -100:
        return 1.0 + 100.0 / abs(odds)
    return 1.91


def unit_profit(result: str, american_odds: float = -110.0, stake: float = 1.0) -> float:
    r = (result or "").upper()
    if r == "WIN":
        return stake * (american_to_decimal(american_odds) - 1.0)
    if r == "LOSS":
        return -stake
    return 0.0


def roi_from_bets(bets: Iterable[Mapping]) -> dict:
    rows = list(bets)
    profit = staked = 0.0
    wins = losses = pushes = 0
    for b in rows:
        stake = float(b.get("stake", 1.0))
        staked += stake
        res = str(b.get("result", "")).upper()
        profit += unit_profit(res, float(b.get("odds", -110)), stake)
        if res == "WIN":
            wins += 1
        elif res == "LOSS":
            losses += 1
        else:
            pushes += 1
    return {
        "n": len(rows),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "staked": round(staked, 2),
        "profit": round(profit, 2),
        "roi": round(profit / staked, 4) if staked else 0.0,
        "win_pct": round(wins / max(1, wins + losses), 4),
    }
