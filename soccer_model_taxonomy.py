"""Permanent soccer model taxonomy (labeling + analytics contract).

INDEPENDENT: Grinder2, Takedown, XSharp, Dixon-Coles (research),
             EdgeElo+ when no books are available
MARKET-AWARE: Edge when sportsbook odds are available;
              Sharp Consensus / Production Consensus when it includes Edge
STRATEGY: Efficiency

Do not present Edge-with-books as an independent model.
Do not put vendor / pipeline IP in user-facing tip strings (AGENTS Rule 9).
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Literal, Optional

ModelClass = Literal["INDEPENDENT", "MARKET-AWARE", "STRATEGY"]

INDEPENDENT_PUBLISHED: FrozenSet[str] = frozenset(
    {"Grinder2", "Takedown", "XSharp"}
)
MARKET_AWARE_PUBLISHED: FrozenSet[str] = frozenset(
    {"Edge", "Sharp Consensus", "Production Consensus"}
)
STRATEGY_PUBLISHED: FrozenSet[str] = frozenset({"Efficiency"})

USER_FACING_LEGEND = (
    "Independent models: Grinder2, Takedown, XSharp. "
    "Market-aware: Edge, Sharp Consensus. "
    "Strategy: Efficiency."
)

TIP_BY_DISPLAY_NAME: Dict[str, str] = {
    "Grinder2": (
        "Independent model. Estimates home / draw / away chances from team "
        "strength only — it does not use sportsbook prices."
    ),
    "Takedown": (
        "Independent model. Estimates home / draw / away chances from recent "
        "form and attack/defense signals — it does not use sportsbook prices."
    ),
    "XSharp": (
        "Independent model. Estimates home / draw / away chances from expected "
        "goal rates — it does not use sportsbook prices."
    ),
    "Edge": (
        "Market-aware when book prices are posted: Edge follows the sportsbook "
        "moneyline probabilities for that match. When no book price is available, "
        "Edge uses our independent strength model instead."
    ),
    "Sharp Consensus": (
        "Market-aware blend of the published model probabilities. When Edge is "
        "using book prices, that market signal is part of this consensus."
    ),
    "Efficiency": (
        "Strategy view based on our spread lean — not an independent home / "
        "draw / away probability model."
    ),
}


def tip_for_display_name(name: str) -> Optional[str]:
    cleaned = " ".join((name or "").split())
    return TIP_BY_DISPLAY_NAME.get(cleaned)


def classify(name: str, *, books_present: Optional[bool] = None) -> ModelClass:
    raw = (name or "").strip().lower().replace("-", " ")
    if "efficiency" in raw:
        return "STRATEGY"
    if "sharp consensus" in raw or "production consensus" in raw:
        return "MARKET-AWARE"
    if raw == "edge" or raw.endswith(" edge"):
        if books_present is False:
            return "INDEPENDENT"
        return "MARKET-AWARE"
    if any(k in raw for k in ("grinder2", "takedown", "xsharp", "dixon", "edgeelo")):
        return "INDEPENDENT"
    raise KeyError(f"unknown soccer model for taxonomy: {name!r}")
