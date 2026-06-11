"""
prop_calibration.py
===================
Per-prop-type calibration & decision layer for the player-props engine.

The raw model emits a projection + confidence + EV for every prop, but it was
treating all prop types with one global confidence system. That made it
overconfident on rare, high-variance hitter props (HR / RBI / Runs) while the
stable, pitcher-driven Strikeout props and NHL Points/Assists carried the real
signal.

This module turns raw props into GATED, CALIBRATED, TIERED picks:

  1. Each prop type has its own profile (frequency class, confidence cap,
     minimum sample size before its graded hit-rate is trusted).
  2. Category status is computed from the REAL graded history (wins/losses in
     player_prop_results), using a Wilson lower bound so small samples can't
     masquerade as 0% / 100%.
  3. A "NO BET ZONE" blocks categories that are below threshold or unproven.
  4. Confidence is capped per category; rare events are capped hard.
  5. Gold tier is only assignable inside an APPROVED category, with grounded
     EV + confidence + top-percentile — otherwise "No Gold props today".

The module is pure logic: callers pass in graded history, it returns decisions.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional


# ── Sample-size thresholds ─────────────────────────────────────────────────
MIN_SAMPLES_NORMAL = 25     # normal categories: trust hit-rate at 25+
MIN_SAMPLES_RARE   = 40     # rare/high-variance events need 40+
BLOCK_ACCURACY     = 0.45   # below this (with enough samples) → NO BET
CAUTION_ACCURACY   = 0.50   # 0.45–0.50 → allowed but confidence downgraded

# Gold-tier requirements (only inside approved categories)
GOLD_MIN_EV   = 8.0
GOLD_MIN_CONF = 63.0
GOLD_TOP_PCT  = 0.15        # must be in best 15% of the slate by EV


# ── Per-(league, prop_type) profile ────────────────────────────────────────
# frequency: "high" = reliable, "low" = rare/high-variance (needs more samples,
# lower confidence cap). max_conf = hard confidence ceiling for that category.
_DEFAULT_PROFILE = {"frequency": "high", "max_conf": 70.0}

_PROFILES: Dict[tuple, Dict] = {
    # ── MLB ── pitcher Ks are the only stable hitter-independent signal
    ("MLB", "strikeouts"): {"frequency": "high", "max_conf": 75.0},
    ("MLB", "hits"):       {"frequency": "high", "max_conf": 64.0},
    ("MLB", "runs"):       {"frequency": "low",  "max_conf": 55.0},
    ("MLB", "rbis"):       {"frequency": "low",  "max_conf": 55.0},
    ("MLB", "home_runs"):  {"frequency": "low",  "max_conf": 53.0},
    # ── NHL ── points/assists are stable; goals are rare
    ("NHL", "points"):        {"frequency": "high", "max_conf": 72.0},
    ("NHL", "assists"):       {"frequency": "high", "max_conf": 70.0},
    ("NHL", "shots_on_goal"): {"frequency": "high", "max_conf": 70.0},
    ("NHL", "goals"):         {"frequency": "low",  "max_conf": 55.0},
    # ── NBA / WNBA / NCAA hoops ── high-frequency box-score stats
    ("NBA", "points"):   {"frequency": "high", "max_conf": 75.0},
    ("NBA", "rebounds"): {"frequency": "high", "max_conf": 72.0},
    ("NBA", "assists"):  {"frequency": "high", "max_conf": 72.0},
    ("NBA", "threes"):   {"frequency": "low",  "max_conf": 60.0},
    # ── Soccer ── goals/assists are rare
    ("SOCCER", "goals"):           {"frequency": "low", "max_conf": 55.0},
    ("SOCCER", "assists"):         {"frequency": "low", "max_conf": 55.0},
    ("SOCCER", "shots"):           {"frequency": "high", "max_conf": 66.0},
    ("SOCCER", "shots_on_target"): {"frequency": "low", "max_conf": 58.0},
    # ── Golf ── moderate-frequency round stats
    ("GOLF", "birdies"): {"frequency": "high", "max_conf": 66.0},
    ("GOLF", "bogeys"):  {"frequency": "high", "max_conf": 64.0},
    # ── UFC ── significant strikes stable, takedowns rare
    ("UFC", "significant_strikes"): {"frequency": "high", "max_conf": 66.0},
    ("UFC", "takedowns"):           {"frequency": "low",  "max_conf": 55.0},
    # ── Tennis ──
    ("TENNIS", "aces"):          {"frequency": "high", "max_conf": 66.0},
    ("TENNIS", "double_faults"): {"frequency": "low",  "max_conf": 56.0},
}


def get_profile(league: str, prop_type: str) -> Dict:
    p = _PROFILES.get((league.upper(), prop_type))
    if p:
        return p
    # NBA hoops defaults apply to WNBA / NCAAB / NCAAW
    if league.upper() in ("WNBA", "NCAAB", "NCAAW"):
        p = _PROFILES.get(("NBA", prop_type))
        if p:
            return p
    return dict(_DEFAULT_PROFILE)


def min_samples_for(league: str, prop_type: str) -> int:
    return (MIN_SAMPLES_RARE
            if get_profile(league, prop_type)["frequency"] == "low"
            else MIN_SAMPLES_NORMAL)


# ── Wilson score lower bound — honest small-sample accuracy ────────────────
def wilson_lower_bound(wins: int, n: int, z: float = 1.96) -> Optional[float]:
    """Lower bound of a Wilson score interval (0..1). None if n == 0.
    A 7-0 record gives a lower bound near 0.59, not 1.0 — which is why we use
    it instead of raw wins/n for small samples."""
    if n <= 0:
        return None
    phat = wins / n
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


# ── Category status from graded history ────────────────────────────────────
def category_status(league: str, prop_type: str, wins: int, losses: int) -> Dict:
    """Return a decision record for one (league, prop_type) category.

    status ∈ {approved, caution, blocked, insufficient}
      insufficient : not enough graded games to trust → never show 0%/100%
      blocked      : enough games but < 45% accuracy → NO BET ZONE
      caution      : 45–50% → allowed, confidence downgraded
      approved     : ≥ 50% over the sample threshold
    """
    n = wins + losses
    profile = get_profile(league, prop_type)
    need = min_samples_for(league, prop_type)
    acc = (wins / n) if n else None
    ci_low = wilson_lower_bound(wins, n)

    if n < need:
        status = "insufficient"
        reason = f"Only {n} graded ({need}+ needed) — unproven, not bettable"
        display_pct = None          # caller must show "INSUFFICIENT DATA"
    elif acc < BLOCK_ACCURACY:
        status = "blocked"
        reason = f"{acc*100:.0f}% over {n} graded (below {int(BLOCK_ACCURACY*100)}%) — NO BET ZONE"
        display_pct = round(acc * 100, 1)
    elif acc < CAUTION_ACCURACY:
        status = "caution"
        reason = f"{acc*100:.0f}% over {n} graded — marginal, confidence capped"
        display_pct = round(acc * 100, 1)
    else:
        status = "approved"
        reason = f"{acc*100:.0f}% over {n} graded — validated edge"
        display_pct = round(acc * 100, 1)

    return {
        "league": league.upper(),
        "prop_type": prop_type,
        "status": status,
        "samples": n,
        "wins": wins,
        "losses": losses,
        "accuracy": display_pct,            # None when insufficient
        "ci_low": round(ci_low * 100, 1) if ci_low is not None else None,
        "min_samples": need,
        "max_conf": profile["max_conf"],
        "frequency": profile["frequency"],
        "reason": reason,
        "bettable": status in ("approved", "caution"),
    }


# ── Apply calibration + tiering to a slate of prop rows ────────────────────
def calibrate_slate(league: str, rows: List[Dict],
                    graded_by_type: Dict[str, Dict]) -> Dict:
    """
    rows            : list of prop dicts from the engine (filter_props output)
    graded_by_type  : {prop_type: {"wins": int, "losses": int}} from DB history

    Returns {items, category_status, approved_categories, blocked_categories,
             gold_count}. Each row is annotated with:
        category_status, blocked (bool), calibrated_confidence, tier
    """
    # 1. Compute status for every prop type present (or with history)
    types = set(r.get("prop_type") for r in rows) | set(graded_by_type.keys())
    status_by_type: Dict[str, Dict] = {}
    for pt in types:
        if not pt:
            continue
        g = graded_by_type.get(pt, {})
        status_by_type[pt] = category_status(
            league, pt, int(g.get("wins", 0)), int(g.get("losses", 0)))

    # 2. EV percentile cutoff for Gold (top GOLD_TOP_PCT of slate by pick EV)
    def _pick_ev(r):
        ev = r.get("pick_ev")
        if ev is None:
            ev = (r.get("ev_over_percent") if r.get("picked_side") == "OVER"
                  else r.get("ev_under_percent"))
        return ev if ev is not None else -999.0

    approved_evs = sorted(
        (_pick_ev(r) for r in rows
         if status_by_type.get(r.get("prop_type"), {}).get("status") == "approved"),
        reverse=True)
    gold_ev_cutoff = None
    if approved_evs:
        idx = max(0, int(len(approved_evs) * GOLD_TOP_PCT) - 1)
        gold_ev_cutoff = approved_evs[idx]

    # 3. Annotate each row
    out_rows = []
    for r in rows:
        pt = r.get("prop_type")
        st = status_by_type.get(pt, category_status(league, pt or "", 0, 0))
        conf = float(r.get("confidence_score") or 0.0)
        cap = st["max_conf"]
        blocked = False
        new_tier = r.get("tier", "neutral")

        if st["status"] == "blocked":
            blocked = True
            conf = min(conf, 50.0)
            new_tier = "blocked"
        elif st["status"] == "insufficient":
            blocked = True              # not bettable until proven
            conf = min(conf, 52.0)
            new_tier = "unproven"
        elif st["status"] == "caution":
            conf = min(conf, cap - 10.0)
            if new_tier == "gold":
                new_tier = "green"
        else:  # approved
            conf = min(conf, cap)

        # Gold only inside approved categories + grounded EV/conf + top percentile
        ev = _pick_ev(r)
        if (st["status"] == "approved" and ev >= GOLD_MIN_EV
                and conf >= GOLD_MIN_CONF
                and gold_ev_cutoff is not None and ev >= gold_ev_cutoff):
            new_tier = "gold"
        elif new_tier == "gold":        # strip any pre-existing gold that no longer qualifies
            new_tier = "green" if ev >= 2.0 else "neutral"

        rr = dict(r)
        rr["calibrated_confidence"] = round(conf, 1)
        rr["confidence_score"] = round(conf, 1)
        rr["tier"] = new_tier
        rr["blocked"] = blocked
        rr["category_status"] = st["status"]
        out_rows.append(rr)

    # 4. Re-rank: bettable categories first, then by EV, then confidence.
    #    Blocked/unproven sink to the bottom (still visible, clearly labelled).
    def _rank_key(r):
        st = status_by_type.get(r.get("prop_type"), {})
        bettable = 0 if st.get("bettable") else 1
        return (bettable, -_pick_ev(r), -float(r.get("calibrated_confidence") or 0))
    out_rows.sort(key=_rank_key)

    approved = sorted(pt for pt, s in status_by_type.items()
                      if s["status"] == "approved")
    blocked = sorted(pt for pt, s in status_by_type.items()
                     if s["status"] in ("blocked", "insufficient"))
    gold_count = sum(1 for r in out_rows if r.get("tier") == "gold")

    return {
        "items": out_rows,
        "category_status": status_by_type,
        "approved_categories": approved,
        "blocked_categories": blocked,
        "gold_count": gold_count,
    }
