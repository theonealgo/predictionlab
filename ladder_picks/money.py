"""Odds and wager math. Money is kept in whole cents."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


def parse_american(raw) -> int | None:
    """American odds as an int, or None when the value is not a real price."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        text = str(raw).strip().replace("\u2212", "-").replace(",", "")
        if not text or text.upper() in {"EVEN", "EV"}:
            return 100 if text else None
        val = float(text)
    except (TypeError, ValueError):
        return None
    if val != val or abs(val) < 100 or abs(val) > 100000:
        return None
    if val != int(val):
        return None
    return int(val)


def american_to_decimal(american: int) -> Decimal:
    a = Decimal(int(american))
    if a > 0:
        return Decimal(1) + a / Decimal(100)
    return Decimal(1) + Decimal(100) / (-a)


def implied_prob(american: int) -> float:
    return float(Decimal(1) / american_to_decimal(american))


def no_vig_prob(pick_american: int, other_american: int) -> float | None:
    """Pick side's market probability with the bookmaker margin removed (two-way market)."""
    p = implied_prob(pick_american)
    q = implied_prob(other_american)
    total = p + q
    if total <= 0:
        return None
    return p / total


def profit_cents(stake_cents: int, american: int) -> int:
    """Winning profit on a stake, rounded to the cent."""
    stake = Decimal(int(stake_cents))
    gain = stake * (american_to_decimal(american) - Decimal(1))
    return int(gain.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fmt_money(cents: int | None) -> str:
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def fmt_american(american: int | None) -> str:
    if american is None:
        return "—"
    return f"+{american}" if american > 0 else str(american)
