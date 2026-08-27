"""CFL display-layer fade — invert ML + spread when season accuracy is under 50%.

Totals / O/U / projected scores are never inverted. Does not write the DB.
Isolation only (hub :5081 + this folder).
"""
from __future__ import annotations

from typing import Any


def grade_spread_raw(row: dict[str, Any]) -> tuple[bool | None, bool]:
    """ATS grade vs home-centric model_spread. Matches hub CFL chart logic.

    Returns (correct, push). None/False = ungradable.
    """
    sp = row.get("model_spread")
    hs, as_ = row.get("home_score"), row.get("away_score")
    if sp is None or hs is None or as_ is None:
        return None, False
    try:
        sp_f = float(sp)
        margin = float(hs) - float(as_)
    except (TypeError, ValueError):
        return None, False
    if abs(margin - sp_f) < 1e-9:
        return None, True
    pick_home = sp_f >= 0
    cover_home = margin > sp_f
    return (cover_home if pick_home else not cover_home), False


def grade_total_raw(row: dict[str, Any]) -> tuple[bool | None, bool]:
    """O/U grade vs model_total. Lean over when predicted sum >= line."""
    tot = row.get("model_total")
    hs, as_ = row.get("home_score"), row.get("away_score")
    if tot is None or hs is None or as_ is None:
        return None, False
    try:
        line = float(tot)
        actual = float(hs) + float(as_)
    except (TypeError, ValueError):
        return None, False
    if abs(actual - line) < 1e-9:
        return None, True
    ph, pa = row.get("predicted_home_score"), row.get("predicted_away_score")
    lean_over = True
    if ph is not None and pa is not None:
        try:
            lean_over = (float(ph) + float(pa)) >= line
        except (TypeError, ValueError):
            lean_over = True
    return (actual > line) == lean_over, False


def season_fade_flags(rows: list[dict[str, Any]]) -> tuple[bool, bool]:
    """(fade_ml, fade_spread) from unfaded season records. Totals never fade."""
    w = l = 0
    for r in rows:
        g = r.get("grade")
        if g == "WIN":
            w += 1
        elif g == "LOSS":
            l += 1
    fade_ml = (w + l) > 0 and (w / (w + l)) < 0.5

    sw = sl = 0
    for r in rows:
        ok, push = grade_spread_raw(r)
        if ok is None:
            continue
        if ok:
            sw += 1
        else:
            sl += 1
    fade_spread = (sw + sl) > 0 and (sw / (sw + sl)) < 0.5
    return fade_ml, fade_spread


def other_side(home: str, away: str, pick: str | None) -> str | None:
    if not pick or not home or not away:
        return pick
    pl = str(pick).strip().lower()
    if pl == str(home).strip().lower():
        return away
    if pl == str(away).strip().lower():
        return home
    return pick


def spread_label(home: str, away: str, sp: float | None) -> str:
    if sp is None:
        return "—"
    try:
        sp_f = float(sp)
    except (TypeError, ValueError):
        return "—"
    if sp_f == 0:
        return "Pick'em"
    if sp_f < 0:
        return f"{home} {sp_f:.1f}"
    return f"{away} {-sp_f:.1f}"


def apply_display_fade(
    card: dict[str, Any],
    *,
    fade_ml: bool,
    fade_spread: bool,
) -> dict[str, Any]:
    """Copy a pick/result row with faded ML and/or spread for display + ML grade.

    Spread ATS invert is done by the caller (grade on raw, then flip boolean)
    so negating model_spread here is display-only.
    """
    out = dict(card)
    home = out.get("home_team") or ""
    away = out.get("away_team") or ""

    if fade_ml:
        hp = out.get("home_win_prob")
        ap = out.get("away_win_prob")
        if hp is not None:
            try:
                hp_f = float(hp)
                out["home_win_prob"] = 1.0 - hp_f
                if ap is not None:
                    out["away_win_prob"] = 1.0 - float(ap)
                else:
                    out["away_win_prob"] = hp_f
            except (TypeError, ValueError):
                pass
        out["pick_ml"] = other_side(home, away, out.get("pick_ml"))
        g = out.get("grade")
        if g == "WIN":
            out["grade"] = "LOSS"
        elif g == "LOSS":
            out["grade"] = "WIN"

    if fade_spread:
        sp = out.get("model_spread")
        if sp is not None:
            try:
                out["model_spread"] = -float(sp)
            except (TypeError, ValueError):
                pass

    return out
