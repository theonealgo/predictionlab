"""Pure helpers for MLB run-line slate / grade checkers (no Elo, no live writes)."""


def pick_side_from_label(pick: str, home: str, away: str) -> str | None:
    text = (pick or "").strip()
    if not text:
        return None
    home = (home or "").strip()
    away = (away or "").strip()
    if home and home in text:
        return "HOME"
    if away and away in text:
        return "AWAY"
    h_last = home.split()[-1] if home else ""
    a_last = away.split()[-1] if away else ""
    if h_last and h_last in text:
        return "HOME"
    if a_last and a_last in text:
        return "AWAY"
    return None


def score_grade_minus_1_5(side: str, home_score, away_score) -> bool | None:
    """Product fade is +1.5: covers unless that side loses by 2+."""
    try:
        am = float(home_score) - float(away_score)
    except (TypeError, ValueError):
        return None
    if side == "HOME":
        return am >= -1.5
    if side == "AWAY":
        return am <= 1.5
    return None


def fail_all_same_side(rows: list, *, min_n: int = 10) -> bool:
    """True (checker fail) if n>=min_n and every pick is HOME or every pick is AWAY."""
    sides = [r.get("side") for r in rows if r.get("side") in ("HOME", "AWAY")]
    if len(sides) < min_n:
        return False
    return len(set(sides)) == 1


def fail_grade_mismatch(rows: list) -> list[dict]:
    """Rows whose displayed Correct/Wrong contradicts score-based +1.5."""
    bad = []
    for r in rows:
        side = r.get("side")
        shown = r.get("ok")
        if side not in ("HOME", "AWAY") or shown is None:
            continue
        expect = score_grade_minus_1_5(side, r.get("home_score"), r.get("away_score"))
        if expect is None:
            continue
        if bool(shown) != bool(expect):
            bad.append(r)
    return bad


def fail_last_night_not_subset_of_last7(last_night_ids, last7_ids) -> list:
    """Missing last-night graded IDs that are not in last-7 (same dates)."""
    night = {i for i in (last_night_ids or []) if i}
    week = {i for i in (last7_ids or []) if i}
    return sorted(night - week, key=lambda x: str(x))


def fail_season_label_xsharp_spread(label: str | None) -> bool:
    """True (fail) if the product season spread face is labeled XSharp."""
    text = (label or "").strip().lower()
    if not text:
        return False
    if "prediction lab" in text or text in ("pl", "pl run line"):
        return False
    return "xsharp" in text


def fail_forced_bet_all_games(rows: list, *, min_n: int = 10, pickem_share: float = 0.25) -> bool:
    """True (fail) if every game has a spread pick while many are pick'ems."""
    usable = [r for r in rows if r.get("our_spread") is not None]
    if len(usable) < min_n:
        return False
    pickems = []
    for r in usable:
        try:
            if abs(float(r["our_spread"])) < 1.5:
                pickems.append(r)
        except (TypeError, ValueError):
            continue
    if len(pickems) < max(2, int(len(usable) * pickem_share)):
        return False
    forced = [r for r in pickems if r.get("side") in ("HOME", "AWAY") or r.get("action") == "BET"]
    return len(forced) == len(pickems) and len(pickems) > 0
