"""Season window for the record lines on pick cards.

Consensus Historical Record and PL vs Books count every graded game of the
current season. A sport with no graded game yet this season (off-season, or
the first days of a new one) keeps showing the season that just ended.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

# Regular-season opening (month, day). Preseason games fall before these dates.
_SEASON_START_MD: dict[str, tuple[int, int]] = {
    "NFL": (9, 1),
    "NCAAF": (8, 15),
    "CFL": (6, 1),
    "MLB": (3, 20),
    "NHL": (9, 29),
    "NBA": (10, 1),
    "WNBA": (5, 1),
    "NCAAB": (11, 1),
    "NCAAW": (11, 1),
    "SOCCER": (8, 1),
    "UFC": (1, 1),
    "TENNIS": (1, 1),
}


def _today() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def _start_on_or_before(md: tuple[int, int], day: date) -> date:
    start = date(day.year, md[0], md[1])
    if start > day:
        start = date(day.year - 1, md[0], md[1])
    return start


def season_start_iso(sport: str, dates: Iterable[Any] | None = None) -> str:
    """First day of the season the card records count.

    ``dates`` are the graded game dates on hand. When none of them fall in the
    current season yet, the previous season's start is returned.
    """
    md = _SEASON_START_MD.get((sport or "").strip().upper())
    today = _today()
    if not md:
        return "0000-00-00"
    start = _start_on_or_before(md, today)
    if dates is not None:
        lo, hi = start.isoformat(), today.isoformat()
        if not any(lo <= str(d or "")[:10] < hi for d in dates):
            start = date(start.year - 1, md[0], md[1])
    return start.isoformat()


def season_rows(rows: Iterable[dict[str, Any]], sport: str, key: str = "game_date") -> list[dict[str, Any]]:
    """Rows dated this season (or the last one when this one has none), before today."""
    rows = list(rows or [])
    today = _today().isoformat()
    start = season_start_iso(sport, [r.get(key) for r in rows])
    return [r for r in rows if start <= str(r.get(key) or "")[:10] < today]
