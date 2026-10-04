"""Canonical MLB run-line pick + grade. Isolation source of truth.

Home-centric our_spread: positive = home favored (projected home − away).
Publish the favorite −1.5 whenever our_spread is available.
When ``|our_spread| < 1.5`` still publish (thin edge) with ``thin_edge=True``
so callers can apply lower EV — do not leave Published pick blank.

Do not import Efficiency / ML / totals / IP-gate from here.
"""
from __future__ import annotations

from typing import Any

RUN_LINE = 1.5
MIN_ABS_SPREAD = 1.5
# Cover-prob / EV haircut for thin publishes (|our_spread| < 1.5).
THIN_EDGE_EV_FACTOR = 0.35


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pick_spread_side(
    our_spread: Any,
    *,
    home: str | None = None,
    away: str | None = None,
) -> dict[str, Any]:
    """Select HOME −1.5 or AWAY −1.5 from stored PL our_spread.

    Always BET when our_spread is numeric. Thin edges (|xs| < 1.5) still
    publish the model favorite −1.5 and set ``thin_edge=True`` for lower EV.
    Missing our_spread → NO BET (no side to publish).
    """
    del home, away
    xs = _as_float(our_spread)
    if xs is None:
        return {
            "action": "NO BET",
            "side": None,
            "line": None,
            "thin_edge": False,
            "reason": "model score unavailable",
        }
    thin = abs(xs) < MIN_ABS_SPREAD
    side = "HOME" if xs >= 0 else "AWAY"
    return {
        "action": "BET",
        "side": side,
        "line": -RUN_LINE,
        "thin_edge": thin,
        "reason": (
            "thin edge — lower EV"
            if thin
            else ("away -1.5" if side == "AWAY" else "home -1.5")
        ),
    }


def grade_spread_cover(
    side: str | None,
    home_score: Any,
    away_score: Any,
    *,
    line: float | None = None,
) -> bool | None:
    """Score-based run-line cover. None if no pick or scores missing.

    Favorite −1.5: that side must win by 2+.
    HOME −1.5 covers when home wins by 2+.
    AWAY −1.5 covers when away wins by 2+.
    Positive ``line`` still grades the old +1.5 dog cover.
    """
    if side not in ("HOME", "AWAY"):
        return None
    try:
        am = float(home_score) - float(away_score)
    except (TypeError, ValueError):
        return None
    ln = _as_float(line)
    if ln is None:
        ln = RUN_LINE
    if ln > 0:
        if side == "HOME":
            return am >= -RUN_LINE
        return am <= RUN_LINE
    if side == "HOME":
        return am > RUN_LINE
    return am < -RUN_LINE


def apply_spread_pick_and_grade(
    our_spread: Any,
    home_score: Any = None,
    away_score: Any = None,
    *,
    home: str | None = None,
    away: str | None = None,
) -> dict[str, Any]:
    """One call used by last night / last 7 / season / cards / units."""
    picked = pick_spread_side(our_spread, home=home, away=away)
    ok = None
    if picked["action"] == "BET":
        ok = grade_spread_cover(
            picked["side"], home_score, away_score, line=picked["line"]
        )
    team = None
    if picked["side"] == "HOME":
        team = home
    elif picked["side"] == "AWAY":
        team = away
    label = None
    if team and picked["line"] is not None:
        label = f"{team} {picked['line']:+.1f}"
    return {
        **picked,
        "correct": ok,
        "team": team,
        "label": label,
    }


def haircut_spread_ev(ev: Any, *, thin_edge: bool) -> float | None:
    """Apply lower EV for thin-edge publishes."""
    if ev is None:
        return None
    try:
        val = float(ev)
    except (TypeError, ValueError):
        return None
    if not thin_edge:
        return round(val, 1)
    return round(val * THIN_EDGE_EV_FACTOR, 1)


def tally_spread_windows(games: list[dict[str, Any]]) -> dict[str, Any]:
    """Grade a list of stored games with the unified pick/grade.

    Each game needs game_id, date, our_spread, home_score, away_score,
    and optionally home/away names.
    """
    rows: list[dict[str, Any]] = []
    for g in games:
        if g.get("skip_grading"):
            continue
        hs, aws = g.get("home_score"), g.get("away_score")
        if hs is None or aws is None:
            continue
        applied = apply_spread_pick_and_grade(
            g.get("our_spread"),
            hs,
            aws,
            home=g.get("home") or g.get("home_team_id"),
            away=g.get("away") or g.get("away_team_id"),
        )
        row = {
            "game_id": g.get("game_id"),
            "date": (g.get("date") or g.get("game_date") or "")[:10],
            "home": g.get("home") or g.get("home_team_id"),
            "away": g.get("away") or g.get("away_team_id"),
            "home_score": hs,
            "away_score": aws,
            "our_spread": _as_float(g.get("our_spread")),
            **applied,
        }
        rows.append(row)

    def _window(pred) -> dict[str, Any]:
        picked = [r for r in rows if pred(r) and r["action"] == "BET" and r["correct"] is not None]
        w = sum(1 for r in picked if r["correct"])
        l = sum(1 for r in picked if r["correct"] is False)
        n = w + l
        ids = [r["game_id"] for r in picked if r.get("game_id")]
        return {
            "w": w,
            "l": l,
            "n": n,
            "pct": round(100.0 * w / n, 1) if n else None,
            "record": f"{w}-{l}",
            "game_ids": ids,
            "no_bet": sum(1 for r in rows if pred(r) and r["action"] == "NO BET"),
            "thin_edge": sum(1 for r in rows if pred(r) and r.get("thin_edge")),
            "rows": [r for r in rows if pred(r)],
        }

    return {
        "all": _window(lambda _r: True),
        "rows": rows,
    }


def last_night_subset_of_last7(
    last_night_ids: list[Any],
    last7_ids: list[Any],
) -> tuple[bool, list[Any]]:
    """True if every last-night graded id is in last-7. Returns (ok, missing)."""
    night = {i for i in last_night_ids if i}
    week = {i for i in last7_ids if i}
    missing = sorted(night - week, key=lambda x: str(x))
    return (not missing), missing
