"""Golf picks model.

Player form = strokes gained per round vs the field average, adjusted for the
strength of that field, recency weighted and shrunk toward a below-average prior
for players with few rounds. Each event is simulated to get Win / Top 10 /
Top 20 chances. An event's picks use only rounds played before it started.

History: data/golf_history.json (final individual PGA TOUR events). Events that
finish later are pulled once from the event scoreboard and kept in .cache/.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

ROOT = Path(__file__).resolve().parent
HISTORY_PATH = ROOT / "data" / "golf_history.json"
CACHE_DIR = ROOT / ".cache" / "golf_events"

PARAMS = {"half_life_days": 300.0, "shrink_rounds": 3.0, "prior": -1.5, "sigma": 2.8}
MARKETS = (("Win", 1, 1), ("Top 10", 10, 5), ("Top 20", 20, 10))  # market, finish needed, picks per event
MARKET_KEY = {"Win": "win", "Top 10": "top10", "Top 20": "top20"}
FIRST_GRADED_YEAR = 2025
TEAM_EVENT_WORDS = ("zurich classic", "ryder cup", "presidents cup", "grant thornton", "q-school")
SIMS = 10000

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"key": None, "data": None, "at": 0.0}
_STATE_TTL = 1800.0


def name_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())


def _day(iso: str) -> float:
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() / 86400.0


def _to_par(raw: Any) -> int | None:
    s = str(raw or "").strip().upper()
    if s in ("E", "EVEN"):
        return 0
    try:
        return int(s.replace("+", ""))
    except ValueError:
        return None


def compact_event(ev: dict[str, Any]) -> dict[str, Any] | None:
    """Final individual stroke-play event from an ESPN event payload, or None."""
    if not isinstance(ev, dict):
        return None
    if "competitions" not in ev and ev.get("events"):
        ev = ev["events"][0]
    name = str(ev.get("name") or "")
    status = ((ev.get("status") or {}).get("type") or {}).get("name") or ""
    comps = ((ev.get("competitions") or [{}])[0]).get("competitors") or []
    if status != "STATUS_FINAL" or not comps or any(w in name.lower() for w in TEAM_EVENT_WORDS):
        return None
    if not all(c.get("type") == "athlete" for c in comps):
        return None
    rows = []
    for c in comps:
        strokes = [float(l.get("value") or 0) for l in (c.get("linescores") or [])[:4]]
        strokes = [int(s) if 55 <= s <= 95 else None for s in strokes] + [None] * (4 - len(strokes))
        rows.append([
            str(c.get("id")), ((c.get("athlete") or {}).get("displayName")) or "",
            *strokes[:4], _to_par(c.get("score")), c.get("order"),
        ])
    return {"id": str(ev.get("id")), "name": name, "start": ev.get("date"), "rows": rows}


def _finish_table(rows: list[list[Any]]) -> dict[str, tuple[int, int | None, str]]:
    """player id -> (rounds played, tied position or None, finish text)."""
    played = {r[0]: sum(1 for s in r[2:6] if s is not None) for r in rows}
    full = max(played.values(), default=0)
    finishers = [r for r in rows if played[r[0]] == full and r[6] is not None]
    out: dict[str, tuple[int, int | None, str]] = {}
    for r in rows:
        n = played[r[0]]
        pos, text = None, ("MC" if n else "WD")
        if r in finishers:
            better = sum(1 for o in finishers if o[6] < r[6])
            tied = sum(1 for o in finishers if o[6] == r[6]) > 1
            pos = better + 1
            if pos == 1 and r[7] == 1:
                text = "1"
            elif pos == 1 and tied:
                pos, text = 2, "T2"
            else:
                text = f"T{pos}" if tied else str(pos)
        out[r[0]] = (n, pos, text)
    return out


class FormBook:
    def __init__(self, p: dict[str, float]):
        self.p = p
        self.days: dict[str, list[float]] = {}
        self.sg: dict[str, list[float]] = {}
        self.level = 0.0
        self.levels: list[tuple[float, float]] = []

    def level_at(self, day: float) -> float:
        before = [lv for d, lv in self.levels if d < day]
        return before[-1] if before else 0.0

    def skill(self, pid: str, day: float) -> float:
        """Form from rounds played strictly before `day` only."""
        prior = self.level_at(day) + self.p["prior"]
        d = self.days.get(pid)
        if not d:
            return prior
        ages = day - np.asarray(d)
        keep = ages > 0
        if not keep.any():
            return prior
        w = 0.5 ** (ages[keep] / self.p["half_life_days"])
        k = self.p["shrink_rounds"]
        return (float((w * np.asarray(self.sg[pid])[keep]).sum()) + k * prior) / (float(w.sum()) + k)

    def add_event(self, day: float, rows: list[list[Any]], field_strength: float) -> None:
        self.level += 0.1 * (field_strength - self.level)
        self.levels.append((day, self.level))
        for i in range(2, 6):
            vals = [r[i] for r in rows if r[i] is not None]
            if len(vals) < 10:
                continue
            mean = float(np.mean(vals))
            for r in rows:
                if r[i] is not None:
                    self.days.setdefault(r[0], []).append(day)
                    self.sg.setdefault(r[0], []).append(mean - r[i] + field_strength)


def _simulate(skills: np.ndarray, sigma: float, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    n = len(skills)
    total = rng.normal(-skills[None, :] * 4, sigma * 2.0, size=(SIMS, n))
    idx = np.argsort(total, axis=1)
    return {
        "win": np.bincount(idx[:, 0], minlength=n) / SIMS,
        "top10": np.bincount(idx[:, :10].ravel(), minlength=n) / SIMS,
        "top20": np.bincount(idx[:, :20].ravel(), minlength=n) / SIMS,
    }


def _board(skills: list[float], players: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    probs = _simulate(np.asarray(skills, dtype=float), PARAMS["sigma"], seed)
    rows = [
        {**pl, "skill": skills[i], "win": float(probs["win"][i]),
         "top10": float(probs["top10"][i]), "top20": float(probs["top20"][i])}
        for i, pl in enumerate(players)
    ]
    rows.sort(key=lambda r: (-r["win"], -r["top20"], -r["skill"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def picks_from_board(board: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for market, _need, count in MARKETS:
        key = MARKET_KEY[market]
        for r in sorted(board, key=lambda r: (-r[key], r["rank"]))[:count]:
            out.append({"market": market, "player_id": r.get("player_id"), "name": r["name"],
                        "athlete_id": r.get("athlete_id"), "prob": r[key], "rank": r["rank"]})
    return out


def _load_events(fetch_final: Callable[[str], dict | None] | None, completed_ids: list[str]) -> list[dict[str, Any]]:
    try:
        events = json.loads(HISTORY_PATH.read_text(encoding="utf-8")).get("events") or []
    except Exception:
        events = []
    have = {e["id"] for e in events}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for path in CACHE_DIR.glob("*.json"):
        try:
            ev = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if ev.get("id") and ev["id"] not in have:
            events.append(ev)
            have.add(ev["id"])
    for eid in completed_ids:
        eid = str(eid)
        if eid in have or fetch_final is None or (CACHE_DIR / f"{eid}.skip").exists():
            continue
        try:
            ev = compact_event(fetch_final(eid) or {})
        except Exception:
            ev = None
        if ev:
            (CACHE_DIR / f"{eid}.json").write_text(json.dumps(ev, separators=(",", ":")), encoding="utf-8")
            events.append(ev)
            have.add(eid)
        else:
            (CACHE_DIR / f"{eid}.skip").write_text("", encoding="utf-8")
    events.sort(key=lambda e: e["start"])
    return events


def _build(events: list[dict[str, Any]]) -> dict[str, Any]:
    book = FormBook(PARAMS)
    graded_path = CACHE_DIR / "_graded.json"
    try:
        saved = json.loads(graded_path.read_text(encoding="utf-8"))
        if saved.get("params") != PARAMS:
            saved = {}
    except Exception:
        saved = {}
    old_graded = saved.get("events") or {}
    graded: dict[str, dict[str, Any]] = {}
    for ev in events:
        day = _day(ev["start"])
        rows = ev["rows"]
        skills = [book.skill(r[0], day) for r in rows]
        field_strength = float(np.mean(skills)) if skills else 0.0
        if ev["id"] in old_graded:
            graded[ev["id"]] = old_graded[ev["id"]]
        elif int(ev["start"][:4]) >= FIRST_GRADED_YEAR:
            finish = _finish_table(rows)
            players = [{"player_id": r[0], "name": r[1], "athlete_id": r[0]} for r in rows]
            board = _board(skills, players, seed=int(re.sub(r"\D", "", ev["id"]) or 7) % (2**31))
            picks = []
            for pk in picks_from_board(board):
                n, pos, text = finish.get(pk["player_id"], (0, None, "DNP"))
                need = {m: k for m, k, _ in MARKETS}[pk["market"]]
                grade = "VOID" if not n else ("WIN" if pos is not None and pos <= need else "LOSS")
                picks.append({**pk, "grade": grade, "finish": text})
            winner = next((r for r in rows if finish.get(r[0], (0, None, ""))[2] == "1"), None)
            graded[ev["id"]] = {
                "id": ev["id"], "name": ev["name"], "start": ev["start"][:10], "picks": picks,
                "winner": {"name": winner[1], "to_par": winner[6]} if winner else None,
            }
        book.add_event(day, rows, field_strength)
    if graded.keys() != old_graded.keys():
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            graded_path.write_text(json.dumps({"params": PARAMS, "events": graded}), encoding="utf-8")
        except OSError:
            pass
    by_name: dict[str, str] = {}
    for ev in events:
        for r in ev["rows"]:
            by_name[name_key(r[1])] = r[0]
    return {"book": book, "graded": graded, "by_name": by_name, "last_day": _day(events[-1]["start"]) if events else 0.0}


def model_state(fetch_final: Callable[[str], dict | None] | None = None,
                completed_ids: list[str] | None = None) -> dict[str, Any]:
    """Form book + graded picks for every final event; rebuilt when a new event finishes."""
    ids = sorted(str(i) for i in (completed_ids or []))
    with _LOCK:
        key = tuple(ids)
        if _STATE["data"] is not None and _STATE["key"] == key and time.time() - _STATE["at"] < _STATE_TTL:
            return _STATE["data"]
        data = _build(_load_events(fetch_final, ids))
        _STATE.update(key=key, data=data, at=time.time())
        return data


def board_for_field(state: dict[str, Any], players: list[dict[str, Any]], start_iso: str | None,
                    event_id: str | None = None) -> list[dict[str, Any]]:
    """Pre-event board for a posted field. players: dicts with name / athlete_id."""
    if not players:
        return []
    day = _day(start_iso) if start_iso else state["last_day"] + 7
    book: FormBook = state["book"]
    known = set(book.days)
    rows, skills = [], []
    for p in players:
        aid = str(p.get("athlete_id") or "")
        pid = aid if aid in known else state["by_name"].get(name_key(p.get("name") or ""), aid or None)
        rows.append({"player_id": pid, "name": p.get("name") or "", "athlete_id": p.get("athlete_id") or pid})
        skills.append(book.skill(pid, day) if pid else book.level_at(day) + PARAMS["prior"])
    seed = int(re.sub(r"\D", "", str(event_id or "")) or 7) % (2**31)
    return _board(skills, rows, seed)


def record(state: dict[str, Any], year: int | None = None) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    events = [e for e in state["graded"].values() if year is None or e["start"].startswith(str(year))]
    for market in [m for m, _, _ in MARKETS] + ["All"]:
        grades = [p["grade"] for e in events for p in e["picks"] if market == "All" or p["market"] == market]
        out[market] = (grades.count("WIN"), grades.count("LOSS"))
    return out
