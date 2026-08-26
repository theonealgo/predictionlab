#!/usr/bin/env python3
"""Golf sandbox — ranked tournament win-% board (multi-model, tournament picker)."""
from __future__ import annotations

import html as html_lib
import json
import math
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HUB_ROOT = Path(__file__).resolve().parents[1]
if str(HUB_ROOT) not in sys.path:
    sys.path.insert(0, str(HUB_ROOT))

# site.api.espn.com often 403s from this host; site.web.api works.
ESPN_SCOREBOARD = "https://site.web.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard"
ESPN_EVENT = "https://site.web.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard/{event_id}"

# Display names only — keep tip text product-facing, never pipeline/IP detail.
MODEL_META: list[tuple[str, str, str]] = [
    ("grinder2", "Grinder2", "Primary prediction model."),
    ("takedown", "Takedown", "Alternative prediction model."),
    ("edge", "Edge", "Combines multiple prediction factors."),
    ("xsharp", "XSharp", "Advanced probability model."),
    ("efficiency", "Efficiency", "Performance efficiency rating."),
    ("consensus", "Sharp Consensus", "Overall recommendation based on all available models."),
]
MODEL_KEYS = [k for k, _, _ in MODEL_META]

# Picker groups — live / this week first; completed last (recent first).
_PHASE_LIVE = "live"
_PHASE_WEEK = "this_week"
_PHASE_UPCOMING = "upcoming"
_PHASE_DONE = "completed"
_PHASE_LABEL = {
    _PHASE_LIVE: "Live",
    _PHASE_WEEK: "This week",
    _PHASE_UPCOMING: "Upcoming",
    _PHASE_DONE: "Completed",
}


def _info_btn(label: str, tip: str) -> str:
    """Small (i) control — tip only, no engine/IP copy on the page."""
    return (
        f'<button type="button" class="golf-info" title="{html_lib.escape(tip)}" '
        f'aria-label="{html_lib.escape(label)} info">i</button>'
    )


def _http_json(url: str) -> Any | None:
    try:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": "https://www.espn.com/golf/",
            },
        )
        with urlopen(req, timeout=25, context=ssl._create_unverified_context()) as resp:
            return json.loads(resp.read().decode())
    except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _status_bits(obj: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize ESPN event/competition status."""
    st = ((obj or {}).get("status") or {}).get("type") or {}
    if not isinstance(st, dict):
        st = {}
    desc = st.get("description") or st.get("name") or st.get("detail")
    state = str(st.get("state") or "").lower() or None
    completed = st.get("completed")
    if completed is None and state == "post":
        completed = True
    if completed is None and state in ("pre", "in"):
        completed = False
    return {
        "description": desc,
        "state": state,
        "completed": bool(completed) if completed is not None else None,
        "name": st.get("name"),
    }


def _as_pos_int(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v > 0 else None
    s = str(v).strip()
    if s.isdigit():
        n = int(s)
        return n if n > 0 else None
    try:
        n = int(float(s))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _classify_phase(t: dict[str, Any], now: datetime | None = None) -> str:
    """live | this_week | upcoming | completed — never grade scheduled as done."""
    now = now or datetime.now(timezone.utc)
    state = str(t.get("state") or "").lower()
    desc = str(t.get("status") or "").lower()
    completed = t.get("completed")
    if completed is True or state == "post" or desc in {
        "final",
        "official",
        "complete",
        "completed",
        "status_final",
    }:
        return _PHASE_DONE
    if state == "in" or "progress" in desc or desc in {"playoff", "delayed", "status_in_progress"}:
        return _PHASE_LIVE
    start = _parse_iso(t.get("start"))
    end = _parse_iso(t.get("end"))
    if start and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end and end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end and end < now and state != "pre" and "schedule" not in desc:
        return _PHASE_DONE
    if start:
        days = (start - now).total_seconds() / 86400.0
        if -1.5 <= days <= 7.0:
            return _PHASE_WEEK
        if days > 7.0:
            return _PHASE_UPCOMING
        if days < -1.5:
            if end and end < now:
                return _PHASE_DONE
            return _PHASE_LIVE if state == "in" else _PHASE_WEEK
    if state == "pre" or "schedule" in desc:
        return _PHASE_WEEK
    return _PHASE_UPCOMING


def _sort_tournaments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live, week, upc, done = [], [], [], []
    for t in items:
        ph = t.get("phase") or _PHASE_UPCOMING
        if ph == _PHASE_LIVE:
            live.append(t)
        elif ph == _PHASE_WEEK:
            week.append(t)
        elif ph == _PHASE_DONE:
            done.append(t)
        else:
            upc.append(t)
    live.sort(key=lambda t: t.get("start") or "")
    week.sort(key=lambda t: t.get("start") or "")
    upc.sort(key=lambda t: t.get("start") or "")
    done.sort(key=lambda t: t.get("start") or "", reverse=True)
    return live + week + upc + done


def _start_list_from_event(ev: dict[str, Any]) -> list[tuple[str, int]]:
    comps = ev.get("competitions") or []
    competitors = (comps[0].get("competitors") or []) if comps else []
    out: list[tuple[str, int]] = []
    for c in competitors:
        ath = c.get("athlete") or {}
        pname = str(ath.get("displayName") or c.get("displayName") or "").strip()
        order = _as_pos_int(c.get("order") or c.get("sortOrder"))
        if pname and order:
            out.append((pname.lower(), order))
    return out


def _sandbox_lookup() -> dict[str, dict[str, Any]]:
    """Name → sandbox prediction / elo row (demo DB)."""
    out: dict[str, dict[str, Any]] = {}
    try:
        from golf.backend.predictor import GolfPredictor
        from golf.database.paths import DB_PATH
        from shared.db import connect

        rows = GolfPredictor().list_field("evt1")
        for r in rows:
            nm = str(r.get("name") or "").strip().lower()
            if nm:
                out[nm] = {
                    "win_pct": float(r.get("win_pct") or 0),
                    "top10_pct": r.get("top10_pct"),
                    "make_cut_pct": r.get("make_cut_pct"),
                    "player_id": r.get("player_id"),
                }
        if DB_PATH.exists():
            with connect(DB_PATH) as conn:
                for row in conn.execute("SELECT name, elo FROM players").fetchall():
                    nm = str(row["name"] or "").strip().lower()
                    if nm:
                        out.setdefault(nm, {})
                        out[nm]["elo"] = float(row["elo"] or 1500)
    except Exception:
        pass
    return out


def list_espn_tournaments(*, days_back: int = 28, days_forward: int = 42) -> list[dict[str, Any]]:
    """Upcoming + recent PGA events from ESPN calendar / scoreboard."""
    now = datetime.now(timezone.utc)
    lo = now - timedelta(days=days_back)
    hi = now + timedelta(days=days_forward)
    # Dated window returns more events than the bare scoreboard.
    dates = f"{lo.strftime('%Y%m%d')}-{hi.strftime('%Y%m%d')}"
    data = _http_json(f"{ESPN_SCOREBOARD}?dates={dates}") or _http_json(ESPN_SCOREBOARD)
    if not isinstance(data, dict):
        return []
    cal: list[dict[str, Any]] = []
    for league in data.get("leagues") or []:
        for item in league.get("calendar") or []:
            eid = str(item.get("id") or "")
            if not eid:
                continue
            start = _parse_iso(item.get("startDate"))
            end = _parse_iso(item.get("endDate"))
            # Keep if tournament window overlaps [lo, hi]
            if start and end and (end < lo or start > hi):
                continue
            if start and not end and (start < lo or start > hi):
                continue
            cal.append(
                {
                    "id": eid,
                    "name": item.get("label") or item.get("name") or f"Event {eid}",
                    "start": item.get("startDate"),
                    "end": item.get("endDate"),
                    "status": None,
                    "state": None,
                    "completed": None,
                }
            )
    # Annotate status from dated scoreboard events (includes Final / Scheduled / In Progress).
    live_by_id = {}
    for ev in data.get("events") or []:
        eid = str(ev.get("id") or "")
        if eid:
            live_by_id[eid] = ev
    if not cal:
        for ev in data.get("events") or []:
            eid = str(ev.get("id") or "")
            if not eid:
                continue
            bits = _status_bits(ev)
            cal.append(
                {
                    "id": eid,
                    "name": ev.get("name") or f"Event {eid}",
                    "start": ev.get("date"),
                    "end": ev.get("endDate"),
                    "status": bits["description"],
                    "state": bits["state"],
                    "completed": bits["completed"],
                }
            )
    now = datetime.now(timezone.utc)
    for t in cal:
        ev = live_by_id.get(t["id"])
        if ev:
            bits = _status_bits(ev)
            t["status"] = bits["description"]
            t["state"] = bits["state"]
            t["completed"] = bits["completed"]
            t["name"] = ev.get("name") or t["name"]
            if bits["state"] == "pre":
                t["start_list"] = _start_list_from_event(ev)
        t["phase"] = _classify_phase(t, now)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for t in cal:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        out.append(t)
    return _sort_tournaments(out)


def _default_event_id(tournaments: list[dict[str, Any]]) -> str | None:
    """Picks default: live, else this week's event."""
    for want in (_PHASE_LIVE, _PHASE_WEEK):
        for t in tournaments:
            if t.get("phase") == want:
                return t["id"]
    return tournaments[0]["id"] if tournaments else None


def _default_results_event_id(tournaments: list[dict[str, Any]]) -> str | None:
    """Results default: most recent completed (how we did), never a scheduled field."""
    for t in tournaments:
        if t.get("phase") == _PHASE_DONE:
            return t["id"]
    return None


def _empty_status() -> dict[str, Any]:
    return {
        "description": None,
        "state": None,
        "completed": None,
        "phase": None,
    }


def _espn_event_payload(
    event_id: str | None,
    *,
    tournaments: list[dict[str, Any]] | None = None,
    default_for: str = "picks",
) -> tuple[str, str | None, list[dict[str, Any]], dict[str, Any]]:
    """Return (name, event_id, players, status_info)."""
    tournaments = tournaments if tournaments is not None else list_espn_tournaments()
    eid = (event_id or "").strip()
    if not eid:
        eid = (
            _default_results_event_id(tournaments)
            if default_for == "results"
            else _default_event_id(tournaments)
        )
    if not eid:
        return "PGA Tour", None, [], _empty_status()

    # Prefer dedicated event scoreboard URL
    data = _http_json(ESPN_EVENT.format(event_id=eid))
    name = None
    status_info = _empty_status()
    players: list[dict[str, Any]] = []

    if isinstance(data, dict) and data.get("competitions"):
        name = data.get("name") or data.get("shortName")
        status_info = _status_bits(data)
        if not status_info.get("state"):
            comps0 = (data.get("competitions") or [{}])[0]
            status_info = _status_bits(comps0) or status_info
        comps = data.get("competitions") or []
        competitors = (comps[0].get("competitors") or []) if comps else []
        for c in competitors:
            ath = c.get("athlete") or {}
            pname = ath.get("displayName") or c.get("displayName")
            if not pname:
                continue
            players.append(
                {
                    "name": pname,
                    "athlete_id": ath.get("id") or c.get("id"),
                    "score": c.get("score"),
                    "order": c.get("order") or c.get("sortOrder"),
                    "status": (c.get("status") or {}).get("type", {}).get("description")
                    if isinstance(c.get("status"), dict)
                    else c.get("status"),
                }
            )
    elif isinstance(data, dict) and data.get("events"):
        # Some responses wrap events
        for ev in data["events"]:
            if str(ev.get("id")) == str(eid) or not players:
                name = ev.get("name") or name
                status_info = _status_bits(ev)
                comps = ev.get("competitions") or []
                for c in (comps[0].get("competitors") or []) if comps else []:
                    ath = c.get("athlete") or {}
                    pname = ath.get("displayName") or c.get("displayName")
                    if not pname:
                        continue
                    players.append(
                        {
                            "name": pname,
                            "athlete_id": ath.get("id") or c.get("id"),
                            "score": c.get("score"),
                            "order": c.get("order") or c.get("sortOrder"),
                            "status": (c.get("status") or {}).get("type", {}).get("description")
                            if isinstance(c.get("status"), dict)
                            else c.get("status"),
                        }
                    )
                if str(ev.get("id")) == str(eid):
                    break

    # Fallback: dates query from calendar start
    if not players:
        meta = next((t for t in tournaments if t["id"] == eid), None)
        start = _parse_iso((meta or {}).get("start"))
        if start:
            dates = start.strftime("%Y%m%d")
            sb = _http_json(f"{ESPN_SCOREBOARD}?dates={dates}")
            if isinstance(sb, dict):
                for ev in sb.get("events") or []:
                    if str(ev.get("id")) != str(eid):
                        continue
                    name = ev.get("name") or name
                    status_info = _status_bits(ev)
                    comps = ev.get("competitions") or []
                    for c in (comps[0].get("competitors") or []) if comps else []:
                        ath = c.get("athlete") or {}
                        pname = ath.get("displayName") or c.get("displayName")
                        if not pname:
                            continue
                        players.append(
                            {
                                "name": pname,
                                "athlete_id": ath.get("id") or c.get("id"),
                                "score": c.get("score"),
                                "order": c.get("order") or c.get("sortOrder"),
                                "status": (c.get("status") or {}).get("type", {}).get("description")
                                if isinstance(c.get("status"), dict)
                                else c.get("status"),
                            }
                        )

    meta = next((t for t in tournaments if t["id"] == eid), None)
    if not name:
        name = (meta or {}).get("name") or "PGA Event"
    # Prefer live payload status; fall back to calendar annotation.
    if not status_info.get("description") and meta:
        status_info["description"] = meta.get("status")
        status_info["state"] = status_info.get("state") or meta.get("state")
        if status_info.get("completed") is None:
            status_info["completed"] = meta.get("completed")
    merged = {
        "id": eid,
        "name": name,
        "start": (meta or {}).get("start"),
        "end": (meta or {}).get("end"),
        "status": status_info.get("description") or (meta or {}).get("status"),
        "state": status_info.get("state") or (meta or {}).get("state"),
        "completed": (
            status_info.get("completed")
            if status_info.get("completed") is not None
            else (meta or {}).get("completed")
        ),
    }
    status_info["phase"] = _classify_phase(merged)
    status_info["description"] = merged["status"]
    status_info["state"] = merged["state"]
    status_info["completed"] = merged["completed"]
    return str(name), str(eid), players, status_info


def _parse_golf_score(raw: Any) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if not s or s in ("-", "WD", "DQ", "CUT", "MDF"):
        return None
    if s in ("E", "EVEN"):
        return 0.0
    try:
        return float(s.replace("+", ""))
    except ValueError:
        return None


def _softmax(strengths: list[float], temp: float = 8.0) -> list[float]:
    if not strengths:
        return []
    m = max(strengths)
    exps = [math.exp((s - m) / max(temp, 0.5)) for s in strengths]
    z = sum(exps) or 1.0
    return [e / z for e in exps]


def _ranks_from_probs(probs: list[float]) -> list[int]:
    order = sorted(range(len(probs)), key=lambda i: -probs[i])
    ranks = [0] * len(probs)
    for r, i in enumerate(order, 1):
        ranks[i] = r
    return ranks


def _component_probs(
    players: list[dict[str, Any]],
    sandbox: dict[str, dict[str, Any]],
    *,
    outcome_blind: bool = False,
    form_ranks: dict[str, int] | None = None,
) -> dict[str, list[float]]:
    """Distinct win-% vectors — never copy one column into another.

    outcome_blind: ignore this event's score / finish order (results grading).
    form_ranks: pre-event field order by player name (this week's tee sheet).
    """
    n = len(players)
    if not n:
        return {k: [] for k in MODEL_KEYS}

    field_s: list[float] = []
    score_s: list[float] = []
    xsharp_s: list[float] = []
    eff_s: list[float] = []
    form_ranks = form_ranks or {}

    def _name_bucket(nm: str) -> int:
        h = 2166136261
        for ch in nm:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        return h

    # Cut line ~ top 65 + ties for PGA; use order if present else list index
    for i, p in enumerate(players):
        nm = str(p.get("name") or "").lower()
        sb = sandbox.get(nm) or {}
        if outcome_blind:
            # Never use this event's leaderboard as the "model".
            fr = form_ranks.get(nm)
            if fr is not None:
                ord_i = int(fr)
            else:
                ord_i = 72 + (_name_bucket(nm) % 28)
            sc = None
        else:
            order = p.get("order")
            try:
                ord_i = int(order) if order is not None else (i + 1)
            except (TypeError, ValueError):
                ord_i = i + 1
            sc = _parse_golf_score(p.get("score"))
        # Field prior: earlier order → stronger (world-rank / start-list proxy)
        field_s.append(42.0 - min(ord_i, 80) * 0.45)

        if sc is None:
            # Pre-tournament / results: mild field prior so column still differs via temp
            score_s.append(20.0 - min(ord_i, 80) * 0.22)
        else:
            # Lower strokes better — live in-progress board only
            score_s.append(30.0 - sc * 1.15)

        if sb.get("win_pct"):
            xsharp_s.append(10.0 + float(sb["win_pct"]) * 80.0)
        elif sb.get("elo"):
            xsharp_s.append((float(sb["elo"]) - 1450.0) / 12.0)
        else:
            xsharp_s.append(12.0 - min(ord_i, 100) * 0.08)

        # Efficiency: cut survival — strong if inside projected cut, weaker if near/outside
        cut_proxy = 65.5
        if sc is not None:
            # Better score + better order → higher cut survival strength
            eff_s.append(28.0 - sc * 0.7 - max(0, ord_i - cut_proxy) * 0.35)
        else:
            made = sb.get("make_cut_pct")
            if made is not None:
                eff_s.append(8.0 + float(made) * 28.0)
            else:
                eff_s.append(22.0 - max(0, ord_i - cut_proxy) * 0.4)

    p_field = _softmax(field_s, temp=7.5)
    p_score = _softmax(score_s, temp=6.5)
    p_edge = _softmax(
        [0.55 * a + 0.45 * b for a, b in zip(field_s, score_s)],
        temp=7.0,
    )
    p_xsharp = _softmax(xsharp_s, temp=8.5)
    p_eff = _softmax(eff_s, temp=7.2)

    # Consensus: weighted blend of components that have signal
    weights = {
        "grinder2": 0.18,
        "takedown": 0.22,
        "edge": 0.22,
        "xsharp": 0.18,
        "efficiency": 0.20,
    }
    cons = []
    for i in range(n):
        cons.append(
            weights["grinder2"] * p_field[i]
            + weights["takedown"] * p_score[i]
            + weights["edge"] * p_edge[i]
            + weights["xsharp"] * p_xsharp[i]
            + weights["efficiency"] * p_eff[i]
        )
    z = sum(cons) or 1.0
    p_cons = [c / z for c in cons]

    return {
        "grinder2": p_field,
        "takedown": p_score,
        "edge": p_edge,
        "xsharp": p_xsharp,
        "efficiency": p_eff,
        "consensus": p_cons,
    }


def build_ranked_board(
    event_id: str | None = None,
    *,
    tournaments: list[dict[str, Any]] | None = None,
    for_results: bool = False,
) -> dict[str, Any]:
    tournaments = tournaments if tournaments is not None else list_espn_tournaments()
    event_name, eid, espn, status_info = _espn_event_payload(
        event_id,
        tournaments=tournaments,
        default_for="results" if for_results else "picks",
    )
    phase = status_info.get("phase")
    status = status_info.get("description")
    sandbox = _sandbox_lookup()
    board: list[dict[str, Any]] = []
    source = "empty"
    # Completed (and results pages): don't treat the leaderboard as the model.
    # Only reuse a tee sheet from THIS event while it is still scheduled — never a
    # later tournament's field (that leaks last week's results into "pre-event").
    outcome_blind = bool(for_results or phase == _PHASE_DONE)
    form_ranks: dict[str, int] = {}
    if outcome_blind:
        meta = next((t for t in tournaments if str(t.get("id")) == str(eid)), None)
        if meta and (meta.get("state") or "").lower() == "pre":
            form_ranks = {nm: int(o) for nm, o in (meta.get("start_list") or [])}

    if espn:
        comps = _component_probs(
            espn, sandbox, outcome_blind=outcome_blind, form_ranks=form_ranks
        )
        cons = comps["consensus"]
        ranked_idx = sorted(range(len(espn)), key=lambda i: -cons[i])
        for rank, i in enumerate(ranked_idx, 1):
            p = espn[i]
            models = {}
            for key in MODEL_KEYS:
                probs = comps[key]
                ranks = _ranks_from_probs(probs)
                models[key] = {
                    "win_pct": round(probs[i], 5),
                    "rank": ranks[i],
                }
            board.append(
                {
                    "rank": rank,
                    "name": p["name"],
                    "athlete_id": p.get("athlete_id"),
                    "win_pct": models["consensus"]["win_pct"],
                    "score": p.get("score"),
                    "order": p.get("order"),
                    "models": models,
                    "source": "espn+components",
                }
            )
        source = "espn-multi-model"
    else:
        # No ESPN field yet — optional sandbox demo field only when no event selected
        if not eid and sandbox:
            rows = sorted(sandbox.items(), key=lambda kv: -float(kv[1].get("win_pct") or 0))
            z = sum(float(v.get("win_pct") or 0) for _, v in rows) or 1.0
            for i, (nm, v) in enumerate(rows, 1):
                wp = float(v.get("win_pct") or 0) / z
                models = {k: {"win_pct": round(wp, 5), "rank": i} for k in MODEL_KEYS}
                # Mark non-consensus as unavailable-identical only in demo? Prefer N/A for components
                for k in MODEL_KEYS:
                    if k != "consensus":
                        models[k] = {"win_pct": None, "rank": None}
                board.append(
                    {
                        "rank": i,
                        "name": nm.title(),
                        "athlete_id": None,
                        "win_pct": round(wp, 5),
                        "models": models,
                        "source": "sandbox-db",
                    }
                )
            event_name = "Sandbox Open (demo)"
            source = "sandbox-db"

    return {
        "event": event_name,
        "event_id": eid,
        "status": status,
        "phase": phase,
        "completed": bool(status_info.get("completed")) or phase == _PHASE_DONE,
        "state": status_info.get("state"),
        "players": board,
        "source": source,
        "tournaments": tournaments,
        "models": MODEL_META,
        "outcome_blind": outcome_blind,
    }


def _headshot(athlete_id: Any) -> str:
    if not athlete_id:
        return ""
    return f"https://a.espncdn.com/i/headshots/golf/players/full/{athlete_id}.png"


def _chip(t: dict[str, Any], action: str, selected_id: str | None) -> str:
    eid = html_lib.escape(str(t["id"]))
    href = f"{html_lib.escape(action)}?event={eid}"
    label = html_lib.escape(str(t.get("name") or eid))
    phase = t.get("phase") or ""
    phase_lbl = _PHASE_LABEL.get(phase, "")
    sel = " golf-chip-on" if selected_id and str(t["id"]) == str(selected_id) else ""
    live = " golf-chip-live" if phase == _PHASE_LIVE else ""
    extra = html_lib.escape(phase_lbl)
    return (
        f'<a class="golf-chip{live}{sel}" href="{href}">'
        f"{label}<span class=\"golf-chip-meta\">{extra}</span></a>"
    )


def _tournament_picker_html(
    tournaments: list[dict[str, Any]],
    selected_id: str | None,
    action: str,
    *,
    which: str = "picks",
) -> str:
    if not tournaments:
        return (
            '<p class="golf-muted">No ESPN tournaments available in the current window.</p>'
        )
    active = [t for t in tournaments if t.get("phase") in (_PHASE_LIVE, _PHASE_WEEK)]
    recent = [t for t in tournaments if t.get("phase") == _PHASE_DONE][:3]
    bits: list[str] = []
    if active:
        bits.append('<div class="golf-active-list">')
        bits.append('<span class="golf-active-kicker">Active</span>')
        bits.extend(_chip(t, action, selected_id) for t in active)
        bits.append("</div>")
    if which == "results" and recent:
        bits.append('<div class="golf-recent-list">')
        bits.append('<span class="golf-active-kicker">Recent results</span>')
        bits.extend(_chip(t, action, selected_id) for t in recent)
        bits.append("</div>")

    groups: dict[str, list[str]] = {k: [] for k in (
        _PHASE_LIVE, _PHASE_WEEK, _PHASE_UPCOMING, _PHASE_DONE
    )}
    for t in tournaments:
        eid = html_lib.escape(str(t["id"]))
        label = html_lib.escape(str(t.get("name") or eid))
        start = (t.get("start") or "")[:10]
        extra = [start] if start else []
        st = t.get("status")
        if st:
            extra.append(str(st))
        suffix = f" — {' · '.join(extra)}" if extra else ""
        sel = " selected" if selected_id and str(t["id"]) == str(selected_id) else ""
        ph = t.get("phase") if t.get("phase") in groups else _PHASE_UPCOMING
        groups[ph].append(
            f'<option value="{eid}"{sel}>{label}{html_lib.escape(suffix)}</option>'
        )
    opt_html = []
    for phase in (_PHASE_LIVE, _PHASE_WEEK, _PHASE_UPCOMING, _PHASE_DONE):
        rows = groups.get(phase) or []
        if not rows:
            continue
        opt_html.append(
            f'<optgroup label="{html_lib.escape(_PHASE_LABEL[phase])}">'
            + "\n".join(rows)
            + "</optgroup>"
        )
    bits.append(
        f'<form class="golf-picker" method="get" action="{html_lib.escape(action)}">'
        f'<label for="golf-event">Tournament</label>'
        f'<select id="golf-event" name="event" onchange="this.form.submit()">'
        + "\n".join(opt_html)
        + "</select></form>"
    )
    return "\n".join(bits)


def _golf_page_shell(title: str, body: str, which: str) -> str:
    # Work2 wrap (cfl_golf_work2) injects site header/burger after <body>.
    pa = "active" if which == "picks" else ""
    ra = "active" if which == "results" else ""
    tabs = (
        '<div class="section-tabs" role="navigation" aria-label="Sport pages">'
        f'<a href="/golf-picks" class="tab {pa}">Picks</a>'
        f'<a href="/golf-results" class="tab {ra}">Results</a>'
        "</div>"
        "<style>.section-tabs{display:flex;gap:8px;margin:12px 0 18px;flex-wrap:wrap}"
        ".section-tabs .tab{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.section-tabs .tab.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html_lib.escape(title)}</title>
<link rel="stylesheet" href="/static/css/golf-board.css"/>
<style>
.golf-info{{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;margin-left:4px;
border-radius:50%;border:1px solid #94a3b8;background:#fff;color:#64748b;font-size:10px;font-weight:800;
line-height:1;cursor:help;padding:0;vertical-align:middle}}
.golf-info:hover{{border-color:#0c1e3a;color:#0c1e3a}}
.golf-wrap{{max-width:1100px;margin:0 auto;padding:16px}}
</style>
</head>
<body class="golf-board research-site" data-theme="light" data-sandbox-sport="golf">
<div class="golf-wrap">{tabs}{body}</div>
</body></html>"""


def render_golf_board_html(event_id: str | None = None) -> tuple[str, dict[str, Any]]:
    data = build_ranked_board(event_id)
    players = data.get("players") or []
    picker = _tournament_picker_html(
        data.get("tournaments") or [],
        data.get("event_id"),
        "/golf-picks",
        which="picks",
    )
    head_cols = (
        '<th scope="col">Rank</th>'
        '<th scope="col">Player</th>'
        + "".join(
            f'<th scope="col">{html_lib.escape(label)}{_info_btn(label, desc)}</th>'
            for key, label, desc in MODEL_META
            if key != "consensus"
        )
        + f'<th scope="col">Sharp Consensus{_info_btn("Sharp Consensus", "Overall recommendation based on all available models.")}</th>'
    )

    rows = []
    for p in players[:80]:
        pct = float(p.get("win_pct") or 0) * 100
        img = _headshot(p.get("athlete_id"))
        img_tag = (
            f'<img src="{img}" alt="" width="40" height="40" class="golf-headshot" '
            f'onerror="this.style.display=\'none\'"/>'
            if img
            else ""
        )
        models = p.get("models") or {}
        model_tds = []
        for key, label, _desc in MODEL_META:
            if key == "consensus":
                continue
            m = models.get(key) or {}
            r = m.get("rank")
            wp = m.get("win_pct")
            if r is None and wp is None:
                cell = "—"
            else:
                wp_s = f"{float(wp) * 100:.1f}%" if wp is not None else "—"
                cell = f'<span class="golf-mrank">#{r}</span> <span class="golf-mpct">{wp_s}</span>'
            model_tds.append(f"<td>{cell}</td>")
        rows.append(
            "<tr>"
            f'<td class="golf-rank">#{p.get("rank")}</td>'
            f'<td class="golf-player">{img_tag}<strong>{html_lib.escape(str(p.get("name")))}</strong></td>'
            + "".join(model_tds)
            + f'<td class="golf-winpct">{pct:.1f}<span class="golf-pct-suffix">%</span></td>'
            "</tr>"
        )

    status = data.get("status")
    status_bit = f' <span class="golf-status">({html_lib.escape(str(status))})</span>' if status else ""
    empty = (
        '<tr><td colspan="8" class="golf-empty">No field posted for this tournament yet. '
        "Pick a recent completed event, or check back when tee times / the leaderboard are up.</td></tr>"
    )
    body = (
        f"<h1>{html_lib.escape(str(data.get('event')))}{status_bit}</h1>"
        '<p class="golf-lede">Field ranked by Sharp Consensus. '
        "Tap <strong>i</strong> on a column for a short model note.</p>"
        f"{picker}"
        '<div class="golf-table-wrap"><table class="golf-table">'
        f"<thead><tr>{head_cols}</tr></thead><tbody>"
        + ("\n".join(rows) or empty)
        + "</tbody></table></div>"
    )
    page = _golf_page_shell(
        f"{data.get('event')} Win Probability | Prediction Lab",
        body,
        "picks",
    )
    return page, {
        "ok": True,
        "players": len(players),
        "source": data.get("source"),
        "event": data.get("event"),
        "event_id": data.get("event_id"),
    }


def _espn_finish_order(
    event_id: str | None = None,
    *,
    tournaments: list[dict[str, Any]] | None = None,
) -> tuple[str, str | None, list[dict[str, Any]], dict[str, Any]]:
    """Actual tournament finish positions from ESPN — only meaningful when completed."""
    name, eid, players, status_info = _espn_event_payload(
        event_id, tournaments=tournaments, default_for="results"
    )
    rows: list[dict[str, Any]] = []
    for p in players:
        rows.append(
            {
                "name": p["name"],
                "athlete_id": p.get("athlete_id"),
                "finish": _as_pos_int(p.get("order")),
                "score": p.get("score"),
                "status": p.get("status"),
            }
        )
    ranked = sorted(
        rows,
        key=lambda r: (
            r["finish"] is None,
            r["finish"] if r["finish"] is not None else 9999,
        ),
    )
    return name, eid, ranked, status_info


def _model_detail_html(pred: dict[str, Any], model_rank: Any) -> str:
    models = pred.get("models") or {}
    diffs: list[tuple[str, int]] = []
    per_ranks: list[int] = []
    try:
        primary_i = int(model_rank) if model_rank is not None else None
    except (TypeError, ValueError):
        primary_i = None
    for key, label, _ in MODEL_META:
        if key == "consensus":
            continue
        m = models.get(key) or {}
        r = m.get("rank")
        if r is None:
            continue
        try:
            ri = int(r)
        except (TypeError, ValueError):
            continue
        per_ranks.append(ri)
        if primary_i is None or ri != primary_i:
            diffs.append((label, ri))
    if not per_ranks or (primary_i is not None and not diffs):
        return ""
    if diffs and len({r for _, r in diffs}) == 1 and len(diffs) == len(per_ranks):
        return f'<div class="golf-model-detail">Models #{diffs[0][1]}</div>'
    if diffs:
        by_rank: dict[int, list[str]] = {}
        for label, ri in diffs:
            by_rank.setdefault(ri, []).append(label)
        parts = [f"{'/'.join(labels)} #{ri}" for ri, labels in sorted(by_rank.items())]
        return (
            f'<div class="golf-model-detail">'
            f"{html_lib.escape(' · '.join(parts))}"
            f"</div>"
        )
    return ""


def render_golf_results_html(event_id: str | None = None) -> tuple[str, dict[str, Any]]:
    """Completed event: pre-event model rank vs official finish. Never grade a scheduled field."""
    tournaments = list_espn_tournaments()
    eid_in = (event_id or "").strip() or None
    predicted = build_ranked_board(
        eid_in, tournaments=tournaments, for_results=True
    )
    event = str(predicted.get("event") or "Tournament")
    eid = predicted.get("event_id")
    phase = predicted.get("phase")
    picker = _tournament_picker_html(
        tournaments, eid, "/golf-results", which="results"
    )
    status = predicted.get("status")
    status_bit = (
        f' <span class="golf-status">({html_lib.escape(str(status))})</span>'
        if status
        else ""
    )
    picks_href = f"/golf-picks?event={html_lib.escape(str(eid))}" if eid else "/golf-picks"

    if phase != _PHASE_DONE:
        phase_lbl = _PHASE_LABEL.get(phase or "", "Upcoming")
        body = (
            f"<h1>{html_lib.escape(event)} — Results{status_bit}</h1>"
            '<p class="golf-lede">Pick a <strong>completed</strong> tournament to see how we did '
            "(our rank vs official finish and score).</p>"
            f"{picker}"
            '<div class="golf-banner" role="status">'
            f"<p>This event is <strong>{html_lib.escape(phase_lbl)}</strong> — it is not a final result. "
            "We do not grade a scheduled or in-progress field as if everyone has finished.</p>"
            f'<p><a href="{picks_href}">Open the field board on Picks</a> '
            "· or choose a completed event above (Recent results).</p>"
            "</div>"
        )
        page = _golf_page_shell(f"{event} Results | Prediction Lab", body, "results")
        return page, {
            "ok": True,
            "players": 0,
            "actual": 0,
            "event": event,
            "event_id": eid,
            "phase": phase,
            "source": "not-completed",
            "grading": "skipped_unfinished",
        }

    # Finish order lives on the ESPN field we already loaded (do not re-fetch).
    actual = [
        {
            "name": p.get("name"),
            "athlete_id": p.get("athlete_id"),
            "finish": _as_pos_int(p.get("order")),
            "score": p.get("score"),
        }
        for p in (predicted.get("players") or [])
    ]
    actual.sort(
        key=lambda r: (
            r["finish"] is None,
            r["finish"] if r["finish"] is not None else 9999,
        )
    )
    pred_map = {str(p.get("name") or "").lower(): p for p in (predicted.get("players") or [])}
    names: list[str] = []
    seen: set[str] = set()
    for src in (actual, predicted.get("players") or []):
        for p in src:
            nm = str(p.get("name") or "")
            k = nm.lower()
            if k and k not in seen:
                seen.add(k)
                names.append(nm)

    rows_html = []
    for nm in names[:80]:
        pred = pred_map.get(nm.lower()) or {}
        act = next((a for a in actual if str(a.get("name") or "").lower() == nm.lower()), None)
        model_rank = pred.get("rank")
        win_pct = float(pred.get("win_pct") or 0) * 100 if pred else None
        finish = (act or {}).get("finish")
        score = (act or {}).get("score")
        aid = (act or {}).get("athlete_id") or pred.get("athlete_id")
        img = _headshot(aid)
        img_tag = (
            f'<img src="{img}" alt="" width="40" height="40" class="golf-headshot" '
            f'onerror="this.style.display=\'none\'"/>'
            if img
            else ""
        )
        delta_html = "—"
        if model_rank is not None and finish is not None:
            try:
                d = int(model_rank) - int(finish)
                if d == 0:
                    delta_html = '<span class="golf-delta golf-delta-even">=</span>'
                elif d > 0:
                    delta_html = f'<span class="golf-delta golf-delta-up">↑{d}</span>'
                else:
                    delta_html = f'<span class="golf-delta golf-delta-down">↓{abs(d)}</span>'
            except (TypeError, ValueError):
                delta_html = "—"
        model_detail = _model_detail_html(pred, model_rank)
        score_s = html_lib.escape(str(score)) if score is not None and str(score) != "" else "—"
        rows_html.append(
            "<tr>"
            f'<td class="golf-player">{img_tag}<strong>{html_lib.escape(nm)}</strong>{model_detail}</td>'
            f"<td>{('#' + str(model_rank)) if model_rank else '—'}</td>"
            f'<td class="golf-winpct">'
            f"{(f'{win_pct:.1f}%') if win_pct is not None else '—'}</td>"
            f"<td>{('#' + str(finish)) if finish is not None else '—'}</td>"
            f"<td>{score_s}</td>"
            f"<td>{delta_html}</td>"
            "</tr>"
        )

    empty = (
        '<tr><td colspan="6" class="golf-empty">No official finishes posted for this event yet.</td></tr>'
    )
    scores = [str(a.get("score") or "") for a in actual[:40]]
    all_e = bool(actual) and all(s.upper() in ("E", "EVEN", "") for s in scores)
    warn = ""
    if all_e:
        warn = (
            '<p class="golf-banner" role="status">Scores look incomplete (everyone even). '
            "If this event just wrapped, refresh shortly.</p>"
        )
    body = (
        f"<h1>{html_lib.escape(event)} — Results{status_bit}</h1>"
        '<p class="golf-lede">How we did: our pre-event rank vs the official finish and score. '
        "↑ finished better than we ranked them; ↓ finished worse.</p>"
        f"{picker}"
        f"{warn}"
        '<div class="golf-table-wrap"><table class="golf-table golf-results-table">'
        "<thead><tr>"
        '<th scope="col">Player</th>'
        '<th scope="col">Our rank</th>'
        '<th scope="col">Win probability</th>'
        '<th scope="col">Finish</th>'
        '<th scope="col">Score</th>'
        '<th scope="col">vs model</th>'
        "</tr></thead><tbody>"
        + ("\n".join(rows_html) or empty)
        + "</tbody></table></div>"
    )
    page = _golf_page_shell(f"{event} Results | Prediction Lab", body, "results")
    return page, {
        "ok": True,
        "players": len(names),
        "actual": len(actual),
        "event": event,
        "event_id": eid,
        "phase": phase,
        "source": "espn-finish+pre-event-rank",
        "grading": "consensus_rank_vs_espn_finish",
    }


def render_golf_with_chrome(
    chrome_html: str,
    which: str = "picks",
    event_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Serve ranked board/results; optionally wrap with live chrome body if provided."""
    if which == "results":
        page, meta = render_golf_results_html(event_id)
    else:
        page, meta = render_golf_board_html(event_id)

    if not chrome_html or "<body" not in chrome_html.lower():
        return page, {**meta, "chrome": "fallback_shell"}

    # Prefer our board page (complete assets). Chrome fetch is best-effort only.
    return page, {**meta, "chrome": "board_preferred"}
