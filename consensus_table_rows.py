"""The graded games the results-page consensus table was built from.

The season Consensus Historical Record chip counts these same games (plus
stored finals dated before them), so a card's season record always holds the
table's Past 7 / Past 30 games.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

SPORTS = ("NFL", "NCAAF", "NHL")
_MAX_AGE = 2 * 24 * 3600


def _path(sport: str) -> Path:
    return Path(__file__).resolve().parent / ".cache" / f"consensus_table_rows_{sport.upper()}.json"


def save(sport: str, rows: list[dict[str, Any]]) -> None:
    sport_u = (sport or "").strip().upper()
    if sport_u not in SPORTS or not rows or len(rows) < 10:
        return
    path = _path(sport_u)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, default=str), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def label_key(label: str) -> tuple[int, tuple[str, ...]] | None:
    """'5/6 — all but Edge and Efficiency' -> (5, ('Edge', 'Efficiency')); splits and unanimous -> (n, ())."""
    import re
    from html import unescape

    text = re.sub(r"\s+", " ", unescape(label or "")).strip()
    m = re.match(r"^(\d+)/(\d+)\s*(?:[—–-]\s*)?(.*)$", text)
    if not m:
        return None
    folded, rest = int(m.group(1)), m.group(3).strip().lower()
    if rest.startswith("all but "):
        names = re.split(r"\s+and\s+|\s*,\s*", m.group(3).strip()[len("all but "):])
        return folded, tuple(n.strip() for n in names if n.strip())
    return folded, ()


def _grades_by_key(games: list[dict[str, Any]]) -> dict[tuple[int, tuple[str, ...]], list[int]]:
    out: dict[tuple[int, tuple[str, ...]], list[int]] = {}
    for g in games:
        key = label_key(g.get("label") or "")
        if key is None:
            continue
        rec = out.setdefault(key, [0, 0, 0])
        grade = g.get("grade")
        if grade == "WIN":
            rec[0] += 1
        elif grade == "LOSS":
            rec[1] += 1
        elif grade == "PUSH":
            rec[2] += 1
    return out


def save_past7_swap(sport: str, card_games: list[dict[str, Any]], old7: dict[str, tuple[int, int, int]]) -> None:
    """The Past 7 games a results table swapped in (card grades) and the builder's ones it replaced."""
    sport_u = (sport or "").strip().upper()
    try:
        path = _path(sport_u).with_name(f"consensus_past7_swap_{sport_u}.json")
        card = _grades_by_key(card_games)
        same = all(
            list(old7.get(label, (0, 0, 0)))[:3] == card.get(label_key(label) or (0, ()), [0, 0, 0])
            for label in old7
        ) and all(
            any(label_key(label) == key for label in old7) for key in card
        )
        # A second pass over an already-swapped table sees no change; keep the first pass's record.
        if same and path.exists() and time.time() - path.stat().st_mtime < 60:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"card": [{"label": g.get("label"), "grade": g.get("grade")} for g in card_games],
                        "old7": {k: list(v) for k, v in old7.items()}}),
            encoding="utf-8",
        )
    except Exception:
        pass


def past7_swap_deltas(sport: str) -> dict[tuple[int, tuple[str, ...]], list[int]]:
    """Per bucket, card-graded Past 7 minus the builder's Past 7 (what the table changed)."""
    sport_u = (sport or "").strip().upper()
    path = _path(sport_u).with_name(f"consensus_past7_swap_{sport_u}.json")
    try:
        if time.time() - path.stat().st_mtime > _MAX_AGE:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    deltas = _grades_by_key(data.get("card") or [])
    for label, rec in (data.get("old7") or {}).items():
        key = label_key(label)
        if key is None:
            continue
        cur = deltas.setdefault(key, [0, 0, 0])
        for i in range(3):
            cur[i] -= int(rec[i] if i < len(rec) else 0)
    return deltas


def card_season_counts(games: list[dict[str, Any]], start: str) -> dict[tuple[int, tuple[str, ...]], list[int]]:
    """Season W-L-P per bucket from card-graded games dated on or after ``start``."""
    return _grades_by_key([g for g in games if str(g.get("date") or "")[:10] >= start])


def load(sport: str) -> list[dict[str, Any]]:
    sport_u = (sport or "").strip().upper()
    if sport_u not in SPORTS:
        return []
    path = _path(sport_u)
    try:
        if time.time() - path.stat().st_mtime > _MAX_AGE:
            return []
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return rows if isinstance(rows, list) else []
