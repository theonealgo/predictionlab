"""Pick types and grading shared by the history check, today's picker and settlement.

A pick type is (sport, market, model, side class, gap bucket):
- moneyline: our ensemble side; gap = our win probability for that side.
- spread: PL or XSharp projected home margin against the book's home line;
  side class fav / dog by the line we would take.
- total: PL or XSharp projected total against the book total; side class OVER / UNDER.

Gap buckets are cumulative ("gap of at least X"), so a pick with a large gap is
judged by every past pick of the same type whose gap was at least as large as
its bucket.
"""
from __future__ import annotations

POINT_SPORTS = {"NFL", "NCAAF", "NBA", "NCAAB", "NCAAW", "WNBA"}
_SPREAD_NAME = {"NHL": "puck line", "MLB": "run line"}


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def gap_edges(sport: str, market: str) -> tuple[float, ...]:
    if market == "moneyline":
        return (0.5, 0.55, 0.6)
    return (0.0, 2.0, 4.0, 7.0) if sport in POINT_SPORTS else (0.0, 0.5, 1.0, 1.5)


def bucket(sport: str, market: str, gap: float) -> float:
    return max(e for e in gap_edges(sport, market) if abs(gap) >= e - 1e-9)


def options(sport: str, model: dict, book: dict) -> list[dict]:
    """Every pick our models make against one book line.

    model: home_prob (0-1), pl_spread / xs_spread (projected home margin),
           pl_total / xs_total.
    book:  home_ml, away_ml, spread (home line), total, spread_price_home,
           spread_price_away, over_price, under_price.
    """
    out: list[dict] = []
    hp = _num(model.get("home_prob"))
    if hp is not None and 0 < hp < 1 and abs(hp - 0.5) > 1e-9:
        home = hp > 0.5
        p = hp if home else 1 - hp
        out.append({
            "market": "moneyline", "model": "Consensus", "side": "HOME" if home else "AWAY",
            "side_class": "all", "gap": p, "line": None,
            "price": book.get("home_ml" if home else "away_ml"),
            "other_price": book.get("away_ml" if home else "home_ml"),
            "model_prob": p,
        })
    spread = _num(book.get("spread"))
    if spread is not None:
        for name, key in (("PL", "pl_spread"), ("XSharp", "xs_spread")):
            m = _num(model.get(key))
            if m is None:
                continue
            gap = m + spread
            if abs(gap) < 1e-9:
                continue
            home = gap > 0
            line = spread if home else -spread
            out.append({
                "market": "spread", "model": name, "side": "HOME" if home else "AWAY",
                "side_class": "fav" if line < 0 else "dog", "gap": abs(gap), "line": line,
                "price": book.get("spread_price_home" if home else "spread_price_away"),
                "other_price": book.get("spread_price_away" if home else "spread_price_home"),
                "model_prob": None,
            })
    total = _num(book.get("total"))
    if total is not None:
        for name, key in (("PL", "pl_total"), ("XSharp", "xs_total")):
            m = _num(model.get(key))
            if m is None or abs(m - total) < 1e-9:
                continue
            over = m > total
            out.append({
                "market": "total", "model": name, "side": "OVER" if over else "UNDER",
                "side_class": "OVER" if over else "UNDER", "gap": abs(m - total), "line": total,
                "price": book.get("over_price" if over else "under_price"),
                "other_price": book.get("under_price" if over else "over_price"),
                "model_prob": None,
            })
    for o in out:
        o["bucket"] = bucket(sport, o["market"], o["gap"])
    return out


def grade(market: str, side: str, line, home_score: int, away_score: int) -> str:
    """win | loss | push for a stored pick."""
    if market == "total":
        act, ln = home_score + away_score, float(line)
        if act == ln:
            return "push"
        return "win" if (act > ln) == (side == "OVER") else "loss"
    margin = home_score - away_score if side == "HOME" else away_score - home_score
    if market == "spread":
        diff = margin + float(line)
    else:
        diff = margin
    if diff == 0:
        return "push"
    return "win" if diff > 0 else "loss"


def label(o: dict, home_team: str, away_team: str) -> str:
    """Bet as a customer would place it: 'Team ML', 'Team +1.5', 'Under 6.5'."""
    if o["market"] == "total":
        return f"{'Over' if o['side'] == 'OVER' else 'Under'} {_fmt_line(o['line'], signed=False)}"
    team = home_team if o["side"] == "HOME" else away_team
    if o["market"] == "spread":
        return f"{team} {_fmt_line(o['line'], signed=True)}"
    return f"{team} ML"


def market_name(sport: str, market: str) -> str:
    if market == "spread":
        return _SPREAD_NAME.get(sport, "spread").capitalize()
    return "Total" if market == "total" else "Moneyline"


def type_label(sport: str, o: dict) -> str:
    """Plain-language pick type, e.g. 'NHL puck line · underdog · PL'."""
    side = {"fav": "favorite", "dog": "underdog", "OVER": "over", "UNDER": "under"}.get(o["side_class"])
    parts = [f"{sport} {market_name(sport, o['market']).lower()}"]
    if side:
        parts.append(side)
    parts.append(o["model"])
    return " · ".join(parts)


def _fmt_line(v, signed: bool) -> str:
    x = float(v)
    txt = f"{x:g}" if x != int(x) else f"{int(x)}"
    if signed:
        return "PK" if x == 0 else (f"+{txt}" if x > 0 else txt)
    return txt
