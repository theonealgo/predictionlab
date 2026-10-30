"""/historic-results: season records per model, one saved JSON per season.

The records are built offline by tools/build_historic_results.py; this module
never grades games on a request.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "data" / "historic_results"
_CACHE: dict = {}
_SEASON_RE = re.compile(r"^\d{4}-\d{2}$")

_NAMES = {
    "MLB": "MLB", "NHL": "NHL", "NBA": "NBA", "NFL": "NFL", "NCAAF": "NCAAF",
    "NCAAB": "NCAAB", "NCAAW": "NCAAW", "WNBA": "WNBA", "CFL": "CFL",
    "SOCCER": "Soccer", "TENNIS": "Tennis", "UFC": "UFC", "GOLF": "Golf",
}
_ROWS = (
    "Grinder2", "Takedown", "Edge", "XSharp", "Sharp Consensus", "Efficiency",
    "Prediction Lab",
)


def season_label(season: str) -> str:
    return season.replace("-", "/")


def seasons() -> list[str]:
    """Seasons with a saved file, newest first."""
    try:
        names = [p.stem for p in _DIR.glob("*.json") if _SEASON_RE.match(p.stem)]
    except OSError:
        return []
    return sorted(names, reverse=True)


def _cell(rec):
    if not isinstance(rec, dict):
        return None
    w, l = int(rec.get("w") or 0), int(rec.get("l") or 0)
    if w + l <= 0:
        return None
    return {"w": w, "l": l, "pct": round(100.0 * w / (w + l), 1)}


def season_sports(season: str) -> list[dict] | None:
    """Sport cards for one season, or None when the season has no file."""
    if not _SEASON_RE.match(season or ""):
        return None
    path = _DIR / f"{season}.json"
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _CACHE.get(season)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    sports = []
    for s in data.get("sports") or []:
        ml, sp, tot = s.get("moneyline") or {}, s.get("spread") or {}, s.get("total") or {}
        rows = []
        for label in _ROWS:
            row = {
                "label": label,
                "ml": _cell(ml.get(label)),
                "spread": _cell(sp.get(label)),
                "total": _cell(tot.get(label)),
            }
            if row["ml"] or row["spread"] or row["total"]:
                rows.append(row)
        graded = [
            c["w"] + c["l"]
            for r in rows for c in (r["ml"], r["spread"], r["total"]) if c
        ]
        sports.append({
            "sport": s.get("sport") or "",
            "name": _NAMES.get(s.get("sport") or "", s.get("sport") or ""),
            "season": s.get("season") or "",
            "games": max([int(s.get("games") or 0)] + graded) if rows else 0,
            "rows": rows,
            "has_spread": any(r["spread"] for r in rows),
            "has_total": any(r["total"] for r in rows),
        })
    _CACHE[season] = (mtime, sports)
    return sports
