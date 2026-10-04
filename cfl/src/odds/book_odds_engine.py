"""
Book reference layer — **parallel** to production (does not replace PL live lines).

Never imports or edits ``2025_sports/odds_engine/`` (locked).

Named layers (use on the site / in docs)
----------------------------------------
**PL production lines** — Everything already on predictionlab.io from your real
pricing pipeline. **Not produced here**; we do not change those.

**model_odds** — *PL model probability.* Home/away win % from your pick models
(same family as picks). Not DraftKings; not a moneyline.

**our_odds** — *Fair ML from model* (legacy JSON key). American ML with **no
vig**, math-only from model home win %. Not scraped from DK; not your full PL
production line object.

**book_odds** — *Synthetic book reference* (legacy JSON key). **This module.**
Vigged ML + heuristic spread/total so you can show a **second column** next to
PL lines that *behaves like* a board for comparison. Not live DK until you wire
a feed.

Compare DraftKings ↔ **book_odds** (synthetic). Compare model edge ↔ **our_odds**
(fair) vs **book_odds** (synthetic) or vs real book when wired.

Conventions
-----------
* ``p_home`` is the model's win probability for the **home** team (0–1).
* American odds are integers; extremes are clamped for stability.
* Vig is expressed as total overround on a two-way market (e.g. 0.045 ≈ 4.5%).
"""

from __future__ import annotations

import hashlib
from typing import Any, MutableMapping, Optional

# Short strings for JSON ``reference_legend`` (same meaning as module doc).
BOOK_REFERENCE_LEGEND: dict[str, str] = {
    "model_odds": (
        "PL model probability — home/away win % from your models; not a book line."
    ),
    "our_odds": (
        "Fair ML from model — no-vig American ML from model win %; not DK; "
        "not the full PL production line object."
    ),
    "book_odds": (
        "Synthetic book reference — vig + heuristics from book_odds_engine; "
        "for comparison until you attach a live book feed."
    ),
}

# ---------------------------------------------------------------------------
# League baselines for total estimation (conservative defaults)
# ---------------------------------------------------------------------------
LEAGUE_TOTAL_BASELINES: dict[str, float] = {
    # Center of gravity for *playoff-style* boards (~210–215). High-pace regular
    # season games will still be low vs live DK until we add pace or book feed.
    "NBA": 211.5,
    "WNBA": 165.0,
    "NCAAB": 140.0,
    "NCAAW": 130.0,
    "NCAAF": 52.0,
    "NFL": 43.5,
    "NHL": 6.0,
    # MLB main lines cluster around 9.0; 8.5 was low vs typical book boards.
    "MLB": 9.0,
    # Typical main-line cluster ~2.5 goals; noise can dip slightly below.
    "SOCCER": 2.65,
}


def _norm_sport(sport: str) -> str:
    return (sport or "").strip().upper() or "NBA"


def _clamp(p: float, lo: float = 1e-4, hi: float = 1.0 - 1e-4) -> float:
    return max(lo, min(hi, float(p)))


def prob_to_american_fair(p: float) -> int:
    """
    Convert win probability to *fair* American odds (no vig).

    Rules (per spec):
      p >= 0.5  ->  -100 * (p / (1 - p))
      p <  0.5  ->  +100 * ((1 - p) / p)
    """
    p = _clamp(p)
    if p >= 0.5:
        raw = -100.0 * (p / (1.0 - p))
    else:
        raw = 100.0 * ((1.0 - p) / p)
    return int(round(raw))


def american_to_implied_prob(odds: int) -> float:
    """Convert American odds to implied probability (includes vig if odds are vigged)."""
    o = float(odds)
    if o == 0:
        return 0.5
    if o < 0:
        return abs(o) / (abs(o) + 100.0)
    return 100.0 / (o + 100.0)


def vigged_implied_probs(p_home: float, vig: float = 0.045) -> tuple[float, float]:
    """
    Inflate each side's fair win probability by the total book hold.

    ``implied_home + implied_away ≈ 1 + vig`` for mid-range probabilities.
    Values are capped so they remain valid inputs to the American mapping.
    """
    z = 1.0 + float(vig)
    ph = _clamp(p_home)
    ih = min(0.985, ph * z)
    ia = min(0.985, (1.0 - ph) * z)
    return ih, ia


def _american_from_implied(p: float) -> int:
    """Map implied probability (0–1) to American (same piecewise rule as fair)."""
    p = _clamp(p)
    return prob_to_american_fair(p)


def estimate_spread_from_prob(p_home: float, sport: str = "NBA") -> float:
    """
    spread ≈ (p_home - 0.5) * scale, clamped to [-15, 15], rounded to nearest 0.5.

    Scale is sport-specific so MLB run lines sit closer to typical ±1.5 RL boards;
    NBA keeps a wider points scale.

    Sign: positive => home favored by that many points (or runs for MLB).
    """
    sp = _norm_sport(sport)
    scale = {
        "MLB": 12.0,
        "NHL": 2.6,
        # ~34 maps a ~63% home favorite to ≈ -4.5 board (see Cavs @ DET example).
        "NBA": 34.0,
        "WNBA": 18.0,
        "NCAAB": 18.0,
        "NCAAW": 18.0,
        "NFL": 14.0,
        "NCAAF": 14.0,
        "SOCCER": 1.8,
    }.get(sp, 20.0)
    raw = (float(p_home) - 0.5) * scale
    raw = max(-15.0, min(15.0, raw))
    return round(raw * 2.0) / 2.0


def _stable_unit_noise(game_id: str, salt: str = "total") -> float:
    """Deterministic noise in [-1, 1] from ``game_id`` (no ``random`` module)."""
    h = hashlib.sha256(f"{salt}:{game_id}".encode()).hexdigest()
    v = int(h[:8], 16) / 0xFFFFFFFF
    return v * 2.0 - 1.0


def estimate_total(
    sport: str,
    p_home: float,
    *,
    offensive_rating: Optional[float] = None,
    defensive_rating: Optional[float] = None,
    game_id: str = "",
    max_noise: float = 3.0,
) -> float:
    """
    Total = league baseline + optional rating adjustment + confidence-weighted noise.

    If both ratings are None: ``baseline + noise`` where noise amplitude is up to
    ``±max_noise`` scaled by how close ``p_home`` is to a coin flip (higher
    uncertainty near 0.5 => more noise). MLB/NHL use a smaller default cap so
    totals stay near the league anchor (e.g. MLB ~9). All sports: total is
    rounded to the nearest half point to match book ladders.
    """
    sp = _norm_sport(sport)
    baseline = LEAGUE_TOTAL_BASELINES.get(sp, 200.0)

    eff_max_noise = float(max_noise)
    if offensive_rating is None and defensive_rating is None:
        if sp == "MLB":
            eff_max_noise = min(eff_max_noise, 1.0)
        elif sp == "NBA":
            eff_max_noise = min(eff_max_noise, 2.25)
        elif sp == "NHL":
            eff_max_noise = min(eff_max_noise, 0.55)
        elif sp == "SOCCER":
            eff_max_noise = min(eff_max_noise, 0.4)

    adjustment = 0.0
    if offensive_rating is not None and defensive_rating is not None:
        # Small conservative tilt: better offense / worse defense nudge total up.
        adjustment = 0.12 * (float(offensive_rating) - float(defensive_rating))

    confidence = abs(float(p_home) - 0.5) * 2.0  # 0 at 50–50, 1 at rails
    noise_scale = (1.0 - confidence) * eff_max_noise
    noise = _stable_unit_noise(game_id or "unknown", salt="total") * noise_scale

    total = baseline + adjustment + noise
    # Reasonable global clamp (safety for odd sports keys)
    total = max(1.0, min(320.0, total))
    # Soccer: never collapse to 1.0 from global floor — keep plausible goal totals.
    if sp == "SOCCER":
        total = max(2.25, min(4.5, total))
    # Books only quote totals on the half (and whole); never .1 / .7 tails.
    return round(total * 2.0) / 2.0


def build_book_odds_bundle(
    game_id: str,
    sport: str,
    p_home: float,
    *,
    vig: float = 0.045,
    offensive_rating: Optional[float] = None,
    defensive_rating: Optional[float] = None,
) -> dict[str, Any]:
    """
    Build the unified comparison object (model vs our fair vs book reference).

    Parameters
    ----------
    game_id:
        Stable id for deterministic total noise when ratings are absent.
    sport:
        League key, e.g. ``"NBA"``, ``"NHL"``, ``"MLB"``.
    p_home:
        Model probability home wins (0–1).
    vig:
        Total two-way overround for ``book_odds`` (default 4.5%).
    offensive_rating, defensive_rating:
        Optional small adjustment layer for totals; omit for noise-only path.
    """
    ph = _clamp(float(p_home))
    pa = 1.0 - ph

    model_odds = {
        "home_win_prob": round(ph, 6),
        "away_win_prob": round(pa, 6),
    }

    our_home_ml = prob_to_american_fair(ph)
    our_away_ml = prob_to_american_fair(pa)
    implied_h_our = american_to_implied_prob(our_home_ml)
    implied_a_our = american_to_implied_prob(our_away_ml)

    our_odds = {
        "home_ml": our_home_ml,
        "away_ml": our_away_ml,
        "implied_edge_home": round(ph - implied_h_our, 6),
        "implied_edge_away": round(pa - implied_a_our, 6),
    }

    qh, qa = vigged_implied_probs(ph, vig=vig)
    book_home_ml = _american_from_implied(qh)
    book_away_ml = _american_from_implied(qa)
    spread = estimate_spread_from_prob(ph, sport=sport)
    total = estimate_total(
        sport,
        ph,
        offensive_rating=offensive_rating,
        defensive_rating=defensive_rating,
        game_id=game_id,
    )

    book_odds = {
        "home_ml": int(book_home_ml),
        "away_ml": int(book_away_ml),
        "spread": float(spread),
        "total": float(total),
        "vig": float(vig),
    }

    return {
        "game_id": str(game_id),
        "reference_legend": BOOK_REFERENCE_LEGEND,
        "model_odds": model_odds,
        "our_odds": our_odds,
        "book_odds": book_odds,
    }


def enrich_prediction_dict(
    pred: MutableMapping[str, Any],
    *,
    vig: float = 0.045,
    game_id_key: str = "game_id",
    sport_key: str = "sport",
    prob_key: str = "ensemble_prob",
) -> MutableMapping[str, Any]:
    """
    Non-destructive helper: attach ``book_odds_bundle`` under ``pred['book_odds_bundle']``.

    Expects ``pred[prob_key]`` as home win % in **0–100** (common in this codebase)
    or **0–1** if already fractional.
    """
    gid = str(pred.get(game_id_key) or pred.get("game_id") or "")
    sport = str(pred.get(sport_key) or pred.get("league") or "NBA")
    raw = pred.get(prob_key)
    try:
        p_pct = float(raw)
    except (TypeError, ValueError):
        p_pct = 50.0
    p_home = p_pct / 100.0 if p_pct > 1.0 else _clamp(p_pct)

    bundle = build_book_odds_bundle(
        gid,
        sport,
        p_home,
        vig=vig,
        offensive_rating=_maybe_float(pred.get("offensive_rating")),
        defensive_rating=_maybe_float(pred.get("defensive_rating")),
    )
    pred["book_odds_bundle"] = bundle
    return pred


def _maybe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    demo = build_book_odds_bundle(
        "demo-nba-1",
        "NBA",
        0.629,
        vig=0.045,
    )
    import json

    print(json.dumps(demo, indent=2))
