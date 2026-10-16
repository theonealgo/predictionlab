#!/usr/bin/env python3
"""Golf sandbox — ranked tournament win-% board (multi-model, tournament picker)."""
from __future__ import annotations

import html as html_lib
import json
import math
import re
import ssl
import sys
import time
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
# Official entry list when the scoreboard has the event but no competitors yet.
PGA_SCHEDULE = "https://www.pgatour.com/schedule/{year}"
PGA_FIELD = "https://www.pgatour.com/tournaments/{year}/{slug}/{tid}/field"
_PGA_CACHE: dict[str, tuple[float, Any]] = {}
_PGA_TTL = 900.0

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


_HTTP_TEXT_CACHE: dict[str, tuple[float, str]] = {}
_HTTP_TTL_S = 90.0


def _http_json(url: str) -> Any | None:
    import time as _time

    hit = _HTTP_TEXT_CACHE.get(url)
    if hit and _time.time() - hit[0] < _HTTP_TTL_S:
        try:
            return json.loads(hit[1])
        except json.JSONDecodeError:
            _HTTP_TEXT_CACHE.pop(url, None)
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
            text = resp.read().decode()
        data = json.loads(text)
        _HTTP_TEXT_CACHE[url] = (_time.time(), text)
        return data
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
    """Picks default: live, else this week, else the last completed tournament."""
    for want in (_PHASE_LIVE, _PHASE_WEEK):
        for t in tournaments:
            if t.get("phase") == want:
                return t["id"]
    for t in tournaments:
        if t.get("phase") == _PHASE_DONE:
            return t["id"]
    return tournaments[0]["id"] if tournaments else None


def _default_results_event_id(tournaments: list[dict[str, Any]]) -> str | None:
    """Results default: most recent completed (how we did), never a scheduled field."""
    for t in tournaments:
        if t.get("phase") == _PHASE_DONE:
            return t["id"]
    return None


def _http_text(url: str) -> str:
    try:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html",
            },
        )
        with urlopen(req, timeout=25, context=ssl._create_unverified_context()) as resp:
            return resp.read().decode("utf-8", "replace")
    except (URLError, HTTPError, TimeoutError, OSError):
        return ""


def _next_data(html: str) -> dict[str, Any] | None:
    start = html.find('<script id="__NEXT_DATA__"')
    if start < 0:
        return None
    start = html.find(">", start)
    end = html.find("</script>", start)
    if start < 0 or end < 0:
        return None
    try:
        data = json.loads(html[start + 1:end])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _query_data(html: str, name: str) -> Any:
    data = _next_data(html)
    props = ((data or {}).get("props") or {}).get("pageProps") or {}
    state = props.get("dehydratedState") or {}
    for query in state.get("queries") or []:
        key = query.get("queryKey")
        if isinstance(key, list) and key and key[0] == name:
            return (query.get("state") or {}).get("data")
    return None


def _cached_load(key: str, loader):
    now = time.time()
    hit = _PGA_CACHE.get(key)
    if hit and now - hit[0] < _PGA_TTL and hit[1]:
        return hit[1]
    val = loader()
    if val:
        _PGA_CACHE[key] = (now, val)
    return val


def _name_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _pga_player_name(row: dict[str, Any]) -> str:
    first = str(row.get("firstName") or "").strip()
    last = str(row.get("lastName") or "").strip()
    if first and last:
        return f"{first} {last}"
    display = str(row.get("displayName") or "").strip()
    if "," in display:
        last_n, first_n = [part.strip() for part in display.split(",", 1)]
        if first_n and last_n:
            return f"{first_n} {last_n}"
    return display


def _owgr_order(row: dict[str, Any]) -> int:
    raw = row.get("owgr")
    if raw in (None, "", "-"):
        return 1000
    try:
        number = int(float(str(raw).replace(",", "")))
    except (TypeError, ValueError):
        return 1000
    return number if number > 0 else 1000


def _pga_schedule(year: int) -> list[dict[str, Any]]:
    def _load():
        html = _http_text(PGA_SCHEDULE.format(year=year))
        data = _query_data(html, "schedule")
        rows = (data or {}).get("tournaments") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("tournamentId") or "").strip()
            name = str(row.get("name") or "").strip()
            if tid and name:
                out.append({
                    "id": tid,
                    "name": name,
                    "year": str(row.get("year") or year),
                    "status": str(row.get("status") or ""),
                })
        return out

    return _cached_load(f"schedule:{year}", _load) or []


def _pga_field_rows(year: str, slug: str, tid: str) -> list[dict[str, Any]]:
    def _load():
        html = _http_text(PGA_FIELD.format(year=year, slug=slug, tid=tid))
        data = _query_data(html, "field")
        rows = (data or {}).get("players") if isinstance(data, dict) else None
        return rows if isinstance(rows, list) else []

    return _cached_load(f"field:{tid}", _load) or []


def _pga_field_players(event_name: str | None, start_iso: str | None) -> list[dict[str, Any]]:
    """Entry list for a scheduled event whose scoreboard has no competitors yet.

    Uses the published field and world rank only. Prices are not read.
    """
    name = str(event_name or "").strip()
    if not name:
        return []
    start = _parse_iso(start_iso)
    year = start.year if start else datetime.now(timezone.utc).year
    want = _name_key(name)
    match = None
    for row in _pga_schedule(year):
        key = _name_key(row.get("name") or "")
        if key and (key == want or key in want or want in key):
            match = row
            break
    if not match:
        return []
    slug = re.sub(r"[^a-z0-9]+", "-", str(match.get("name") or name).lower()).strip("-")
    rows = _pga_field_rows(str(match.get("year") or year), slug, match["id"])
    players: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("withdrawn") or str(row.get("status") or "").upper() not in {"", "IN"}:
            continue
        pname = _pga_player_name(row)
        if not pname:
            continue
        players.append({
            "name": pname,
            "athlete_id": None,
            "score": None,
            "order": _owgr_order(row),
            "status": None,
        })
    return players


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
    if not players:
        done = bool(status_info.get("completed")) or str(status_info.get("state") or "").lower() == "post"
        if not done and str((meta or {}).get("state") or "").lower() != "post":
            players = _pga_field_players(
                name or (meta or {}).get("name"),
                (meta or {}).get("start"),
            )
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


def _store_win_pct(prob: float) -> float:
    """Keep a positive probability positive. Five decimals is the usual face."""
    try:
        value = float(prob)
    except (TypeError, ValueError):
        return 0.0
    if value <= 0:
        return 0.0
    for places in (5, 8, 12):
        rounded = round(value, places)
        if rounded > 0:
            return rounded
    return value


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
                    "win_pct": _store_win_pct(probs[i]),
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
            event_name = "PGA Tour"
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


def _win_pct_label(win_pct: float) -> str:
    """Print a positive win% so a small probability is not shown as 0%."""
    pct = float(win_pct) * 100.0
    if pct <= 0:
        return "0%"
    for places in range(1, 10):
        text = f"{pct:.{places}f}"
        if float(text) > 0:
            return f"{text}%"
    return f"{pct}%"


def _headshot(athlete_id: Any) -> str:
    """80px face. The full file is ~300KB and the cell is 36px."""
    if not athlete_id:
        return ""
    aid = html_lib.escape(str(athlete_id), quote=True)
    return (
        "https://a.espncdn.com/combiner/i?img=/i/headshots/golf/players/full/"
        f"{aid}.png&w=80&h=80"
    )


def _headshot_img(athlete_id: Any) -> str:
    img = _headshot(athlete_id)
    if not img:
        return ""
    return (
        f'<img src="{img}" alt="" width="40" height="40" class="golf-headshot" '
        f'loading="lazy" decoding="async" '
        f'onerror="this.style.display=\'none\'"/>'
    )


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


def _results_page_href(event_id: str) -> str:
    """Results link for one stored tournament. Keeps chart view when that is the page."""
    view = ""
    try:
        from flask import has_request_context, request

        if has_request_context():
            view = (request.args.get("view") or "").strip()
    except Exception:
        view = ""
    eid = html_lib.escape(str(event_id), quote=True)
    if view:
        return f"/golf-results?view={html_lib.escape(view, quote=True)}&event={eid}"
    return f"/golf-results?event={eid}"


def _results_date_nav(
    tournaments: list[dict[str, Any]],
    selected_id: str | None,
) -> str:
    """Date bubbles for tournaments already on this page. No extra events."""
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for tournament in tournaments or []:
        start = str(tournament.get("start") or "")[:10]
        eid = str(tournament.get("id") or "").strip()
        name = str(tournament.get("name") or "").strip()
        if not re.fullmatch(r"20\d\d-\d\d-\d\d", start) or not eid or not name:
            continue
        if eid in seen:
            continue
        seen.add(eid)
        rows.append((start, eid, name))
    if not rows:
        return ""
    rows.sort(key=lambda row: (row[0], row[2].lower()))
    bubbles: list[str] = []
    sections: list[str] = []
    options: list[str] = []
    dates: list[str] = []
    stamped: set[str] = set()
    for start, eid, name in rows:
        if start not in dates:
            dates.append(start)
        try:
            stamp = datetime.fromisoformat(start)
            label = f"{stamp:%a}, {stamp:%b} {stamp.day}"
        except ValueError:
            label = start
        href = _results_page_href(eid)
        active = " active" if selected_id and str(selected_id) == eid else ""
        if start not in stamped:
            stamped.add(start)
            sections.append(
                f'<div class="date-section{" visible" if active else ""}" id="date-{start}">'
                f'{html_lib.escape(name)}</div>'
            )
        bubbles.append(
            f'<a class="date-bubble{active}" data-date="{start}" '
            f'href="{href}" title="{html_lib.escape(name, quote=True)}">{html_lib.escape(label)}</a>'
        )
        options.append(
            f'<option value="{start}"'
            f'{" selected" if active else ""}>'
            f'{html_lib.escape(label)} — {html_lib.escape(name)}</option>'
        )
    payload = json.dumps(dates)
    return (
        '<div class="date-nav" aria-label="Golf date picker">'
        '<button type="button" class="nav-arrow" aria-label="Earlier dates" '
        "onclick=\"var c=document.getElementById('dateBubbles');"
        "if(c)c.scrollBy({left:-240,behavior:'smooth'})\">&#8249;</button>"
        f'<div class="date-bubbles" id="dateBubbles">{"".join(bubbles)}</div>'
        '<button type="button" class="nav-arrow" aria-label="Later dates" '
        "onclick=\"var c=document.getElementById('dateBubbles');"
        "if(c)c.scrollBy({left:240,behavior:'smooth'})\">&#8250;</button>"
        '<label for="datePicker">Date</label>'
        '<select id="datePicker" onchange="pickGolfResultDate(this.value)">'
        f'{"".join(options)}</select>'
        f'{"".join(sections)}'
        "</div>"
        "<script>function pickGolfResultDate(day){"
        "document.querySelectorAll('.date-section').forEach(function(s){s.classList.remove('visible');});"
        "var el=document.getElementById('date-'+day); if(el) el.classList.add('visible');"
        "var link=document.querySelector('.date-bubble[data-date=\"'+day+'\"]');"
        "if(link&&link.getAttribute('href')) location.href=link.getAttribute('href');"
        "}</script>"
        f"<script>const allDates = {payload};</script>"
        "<style id=\"golf-date-nav\">"
        ".date-section{display:none}.date-section.visible{display:block;padding:8px 0;color:#0f172a;font-weight:700}"
        ".date-nav{display:flex;align-items:center;justify-content:center;gap:12px;"
        "margin:16px 0;padding:12px 16px;background:#fff;border:1px solid rgba(15,23,42,.12);"
        "border-radius:12px;flex-wrap:wrap}"
        ".date-nav .nav-arrow{font-size:1.4rem;color:#94a3b8;background:transparent;border:0;"
        "cursor:pointer;padding:0 4px}"
        ".date-bubbles{display:flex;gap:8px;overflow-x:auto;padding:4px;max-width:860px}"
        ".date-bubble{background:#fff;border:2px solid rgba(15,23,42,.18);border-radius:22px;"
        "padding:9px 16px;min-width:105px;text-align:center;cursor:pointer;white-space:nowrap;"
        "font-weight:500;font-size:.86em;color:#0f172a;text-decoration:none}"
        ".date-bubble.active{background:#f59e0b;border-color:#d97706;color:#0f172a;font-weight:700}"
        ".date-nav label{font-size:.78rem;font-weight:700;color:#334155}"
        ".date-nav select{border:1px solid rgba(15,23,42,.18);border-radius:10px;"
        "padding:8px 10px;background:#fff;color:#0f172a;max-width:100%}"
        ".share-strip{max-width:1200px;margin:12px auto;padding:10px 16px;display:flex;"
        "align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;"
        "background:rgba(244,247,249,.7);border:1px solid rgba(15,23,42,.1);border-radius:12px}"
        ".share-strip-label{font-size:.82em;font-weight:800;color:#0f172a}"
        ".share-icons{display:flex;align-items:center;gap:8px;flex-wrap:wrap}"
        ".share-icon{width:30px;height:30px;display:inline-flex;align-items:center;"
        "justify-content:center;border-radius:999px;border:1px solid rgba(15,23,42,.14);background:#fff}"
        ".share-icon img{width:16px;height:16px;display:block}"
        "</style>"
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
    if which == "results":
        desc = "Golf results: our picks graded against the official finish."
    else:
        desc = "Golf picks: win, top 10 and top 20 picks made before the first round."
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="description" content="{html_lib.escape(desc)}"/>
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
<main id="page-main" class="golf-wrap">{tabs}{body}</main>
</body></html>"""


_REPO_ROOT = Path(__file__).resolve().parents[3]

_GPM_CSS = (
    "<style id=\"golf-model-css\">"
    ".gpm-tally{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:10px 0 18px}"
    ".gpm-tcard{background:#fff;border:1px solid #dbe3ee;border-radius:14px;padding:14px 16px}"
    ".gpm-tcard .lbl{font-size:.72rem;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#64748b}"
    ".gpm-tcard .rec{font-size:1.6rem;font-weight:800;color:#0f172a;margin-top:2px}"
    ".gpm-tcard .pct{font-size:.85rem;color:#475569}"
    ".gpm-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:10px 0 18px}"
    ".gpm-card{background:#fff;border:1px solid #dbe3ee;border-radius:14px;padding:14px}"
    ".gpm-card h3{margin:0 0 2px;font-size:1rem;color:#0f172a}"
    ".gpm-card .sub{color:#64748b;font-size:.8rem;margin-bottom:8px}"
    ".gpm-card table{width:100%;border-collapse:collapse;font-size:.88rem}"
    ".gpm-card td,.gpm-card th{padding:5px 4px;border-top:1px solid #eef2f7;text-align:left}"
    ".gpm-card th{border-top:0;color:#64748b;font-size:.7rem;text-transform:uppercase;letter-spacing:.05em}"
    ".gpm-ok{color:#067647;font-weight:800}.gpm-no{color:#D93025;font-weight:800}.gpm-void{color:#94a3b8;font-weight:700}"
    ".gpm-chart{background:#fff;border:1px solid #dbe3ee;border-radius:14px;padding:12px;overflow-x:auto;margin:10px 0 18px}"
    ".gpm-legend{display:flex;gap:16px;font-size:.82rem;color:#475569;margin:0 0 6px}"
    ".gpm-sw{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}"
    ".gpm-h2{font-size:1.15rem;margin:22px 0 6px;color:#0f172a}"
    ".gpm-winner{background:#fff;border:1px solid #dbe3ee;border-radius:14px;padding:12px 16px;margin:8px 0}"
    "@media(max-width:800px){.gpm-tally{grid-template-columns:repeat(2,1fr)}.gpm-grid{grid-template-columns:1fr}}"
    "</style>"
)


def _golf_model():
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import golf_pl_model

    return golf_pl_model


def _golf_model_state(tournaments: list[dict[str, Any]]) -> tuple[Any, dict[str, Any]] | tuple[None, None]:
    try:
        gm = _golf_model()
        done = [str(t["id"]) for t in tournaments or [] if t.get("phase") == _PHASE_DONE and t.get("id")]
        state = gm.model_state(lambda eid: _http_json(ESPN_EVENT.format(event_id=eid)), done)
        return gm, state
    except Exception:
        return None, None


def _pct1(p: float) -> str:
    v = float(p) * 100.0
    if v <= 0:
        return "<0.1%"
    return "<0.1%" if v < 0.05 else f"{v:.1f}%"


def _gpm_tally(gm, state, year: int | None, title: str) -> str:
    rec = gm.record(state, year)
    cards = []
    for market, label in (("Win", "Win"), ("Top 10", "Top 10"), ("Top 20", "Top 20"), ("All", "All picks")):
        w, l = rec.get(market, (0, 0))
        pct = f"{100.0 * w / (w + l):.1f}% hit" if w + l else "—"
        cards.append(
            f'<div class="gpm-tcard"><div class="lbl">{html_lib.escape(label)}</div>'
            f'<div class="rec">{w}-{l}</div><div class="pct">{pct}</div></div>'
        )
    return f'<h2 class="gpm-h2">{html_lib.escape(title)}</h2><div class="gpm-tally">{"".join(cards)}</div>'


def _gpm_chart(state, upto: str | None = None, last: int = 20) -> str:
    events = sorted(state["graded"].values(), key=lambda e: e["start"])
    if upto:
        events = [e for e in events if e["start"] <= upto]
    events = events[-last:]
    if not events:
        return ""
    bar_w, gap, half, top, left = 24, 12, 90, 14, 30
    width = left + len(events) * (bar_w + gap) + 8
    height = top + 2 * half + 58
    scale = half / 16.0
    mid = top + half
    parts = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Picks won and lost by event">',
        f'<line x1="{left}" y1="{mid}" x2="{width}" y2="{mid}" stroke="#94a3b8"/>',
    ]
    for v in (8, 16):
        for sign in (-1, 1):
            y = mid - sign * v * scale
            parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width}" y2="{y:.1f}" stroke="#eef2f7"/>')
            parts.append(f'<text x="{left - 5}" y="{y + 4:.1f}" font-size="10" fill="#94a3b8" text-anchor="end">{v}</text>')
    for i, ev in enumerate(events):
        grades = [p["grade"] for p in ev["picks"]]
        w, l = grades.count("WIN"), grades.count("LOSS")
        x = left + 6 + i * (bar_w + gap)
        name = html_lib.escape(ev["name"])
        parts.append(
            f'<rect x="{x}" y="{mid - w * scale:.1f}" width="{bar_w}" height="{w * scale:.1f}" fill="#00C076" rx="3" '
            f'aria-label="{name}: {w} won"></rect>'
            f'<rect x="{x}" y="{mid}" width="{bar_w}" height="{l * scale:.1f}" fill="#ef4444" rx="3" '
            f'aria-label="{name}: {l} lost"></rect>'
            f'<text x="{x + bar_w / 2}" y="{mid - w * scale - 4:.1f}" font-size="10" text-anchor="middle" fill="#0f172a">{w}</text>'
            f'<text x="{x + bar_w / 2}" y="{mid + l * scale + 12:.1f}" font-size="10" text-anchor="middle" fill="#0f172a">{l}</text>'
        )
        lx, ly = x + bar_w / 2, top + 2 * half + 14
        parts.append(
            f'<text x="{lx}" y="{ly}" font-size="10" fill="#64748b" text-anchor="end" '
            f'transform="rotate(-45 {lx} {ly})">{ev["start"][5:].replace("-", "/")}</text>'
        )
    parts.append("</svg>")
    return (
        '<div class="gpm-chart"><div class="gpm-legend">'
        '<span><span class="gpm-sw" style="background:#00C076"></span>Picks won</span>'
        '<span><span class="gpm-sw" style="background:#ef4444"></span>Picks lost</span></div>'
        + "".join(parts) + "</div>"
    )


_MARKET_SUB = {
    "Win": "Pick to win",
    "Top 10": "Picks to finish top 10 (ties count)",
    "Top 20": "Picks to finish top 20 (ties count)",
}


def _gpm_mark(grade: str | None) -> str:
    return {
        "WIN": '<span class="gpm-ok" aria-label="won">✓</span>',
        "LOSS": '<span class="gpm-no" aria-label="lost">✗</span>',
        "VOID": '<span class="gpm-void">Void</span>',
    }.get(grade or "", "")


def _gpm_pick_cards(picks: list[dict[str, Any]], graded: bool) -> str:
    cards = []
    for market in ("Win", "Top 10", "Top 20"):
        rows = [p for p in picks if p["market"] == market]
        head = market
        if graded:
            g = [p["grade"] for p in rows]
            head = f"{market} · {g.count('WIN')}-{g.count('LOSS')}"
        cols = "<th></th><th>Player</th><th>Chance</th><th>Finish</th>" if graded else "<th>Player</th><th>Chance</th>"
        body = "".join(
            "<tr>"
            + (f"<td>{_gpm_mark(p.get('grade'))}</td>" if graded else "")
            + f"<td>{html_lib.escape(p['name'])}</td><td>{_pct1(p['prob'])}</td>"
            + (f"<td>{html_lib.escape(str(p.get('finish') or '—'))}</td>" if graded else "")
            + "</tr>"
            for p in rows
        )
        cards.append(
            f'<div class="gpm-card" data-market="{html_lib.escape(market)}"><h3>{html_lib.escape(head)}</h3>'
            f'<div class="sub">{_MARKET_SUB[market]}</div>'
            f"<table><thead><tr>{cols}</tr></thead><tbody>{body}</tbody></table></div>"
        )
    return f'<div class="gpm-grid">{"".join(cards)}</div>'


def render_golf_board_html(event_id: str | None = None) -> tuple[str, dict[str, Any]]:
    tournaments = list_espn_tournaments()
    name, eid, players, status_info = _espn_event_payload(
        (event_id or "").strip() or None, tournaments=tournaments, default_for="picks"
    )
    picker = _tournament_picker_html(tournaments, eid, "/golf-picks", which="picks")
    meta = next((t for t in tournaments if str(t.get("id")) == str(eid)), None) or {}
    status = status_info.get("description")
    phase = status_info.get("phase")
    status_bit = f' <span class="golf-status">({html_lib.escape(str(status))})</span>' if status else ""
    gm, state = _golf_model_state(tournaments)

    graded = (state or {}).get("graded", {}).get(str(eid)) if state else None
    board: list[dict[str, Any]] = []
    if gm and players:
        board = gm.board_for_field(state, players, meta.get("start"), eid)
    picks = graded["picks"] if graded else (gm.picks_from_board(board) if gm and board else [])

    recent = ""
    if gm and state and state.get("graded"):
        last = max(state["graded"].values(), key=lambda e: e["start"])
        g = [p["grade"] for p in last["picks"]]
        year = int(last["start"][:4])
        rec = gm.record(state, year).get("All", (0, 0))
        recent = (
            '<section id="golf-recent" class="gpm-recent">'
            '<h2 class="gpm-h2">Recent results</h2><div class="gpm-tally">'
            f'<div class="gpm-tcard"><div class="lbl">Last event</div><div class="rec">{g.count("WIN")}-{g.count("LOSS")}</div>'
            f'<div class="pct">{html_lib.escape(last["name"])}</div></div>'
            f'<div class="gpm-tcard"><div class="lbl">{year} season</div><div class="rec">{rec[0]}-{rec[1]}</div>'
            f'<div class="pct">{(f"{100.0 * rec[0] / sum(rec):.1f}% hit") if sum(rec) else "—"}</div></div>'
            "</div>"
            f'<p class="golf-lede"><a href="/golf-results?event={html_lib.escape(last["id"])}">See every graded pick</a></p>'
            "</section>"
        )

    rows = []
    for p in board[:80]:
        rows.append(
            "<tr>"
            f'<td class="golf-rank">#{p["rank"]}</td>'
            f'<td class="golf-player">{_headshot_img(p.get("athlete_id"))}<strong>{html_lib.escape(str(p["name"]))}</strong></td>'
            f'<td class="golf-winpct"><span class="golf-mpct">{_pct1(p["win"])}</span></td>'
            f'<td><span class="golf-mpct">{_pct1(p["top10"])}</span></td>'
            f'<td><span class="golf-mpct">{_pct1(p["top20"])}</span></td>'
            "</tr>"
        )
    if phase == _PHASE_DONE or str(status_info.get("state") or "").lower() == "post":
        waiting = "This event is final. See how our picks did on Results."
    else:
        waiting = (
            "The field is set after 5 p.m. ET on the Friday before the event. "
            "Our picks post here as soon as it is out."
        )
    empty = f'<tr><td colspan="5" class="golf-empty">{html_lib.escape(waiting)}</td></tr>'
    picks_html = ""
    if picks:
        picks_html = (
            '<h2 class="gpm-h2">Our picks</h2>'
            + _gpm_pick_cards(picks, graded=bool(graded))
        )
    body = (
        _GPM_CSS
        + f"<h1>{html_lib.escape(str(name))}{status_bit}</h1>"
        '<p class="golf-lede">Made before the first round: one player to win, five to finish top 10, '
        "ten to finish top 20. Chances come from every player's recent rounds against the field.</p>"
        f"{picker}{picks_html}"
        '<h2 class="gpm-h2">Field</h2>'
        '<div class="golf-table-wrap"><table class="golf-table">'
        '<thead><tr><th scope="col">Rank</th><th scope="col">Player</th><th scope="col">Win</th>'
        '<th scope="col">Top 10</th><th scope="col">Top 20</th></tr></thead><tbody>'
        + ("\n".join(rows) or empty)
        + "</tbody></table></div>"
        + recent
    )
    page = _golf_page_shell(f"{name} Golf Picks | Prediction Lab", body, "picks")
    return page, {
        "ok": True,
        "players": len(board),
        "source": "golf-model" if board else "no-field",
        "event": name,
        "event_id": eid,
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
    date_nav = _results_date_nav(tournaments, eid)
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
            '<p class="golf-lede">Pick a <strong>completed</strong> tournament to see the official '
            "finishing order and score.</p>"
            f"{date_nav}{picker}"
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
    names = [str(a.get("name") or "") for a in actual if a.get("name")]
    rows_html = []
    shown = [a for a in actual if a.get("name")][:80]
    for i, act in enumerate(shown):
        finish = act.get("finish")
        score = act.get("score")
        score_s = html_lib.escape(str(score)) if score is not None and str(score) != "" else "—"
        if finish is None:
            place = "—"
        else:
            first = i
            while first > 0 and str(shown[first - 1].get("score")) == str(score):
                first -= 1
            last = i
            while last + 1 < len(shown) and str(shown[last + 1].get("score")) == str(score):
                last += 1
            pos = shown[first].get("finish") or finish
            place = f"T{pos}" if last > first else str(pos)
        rows_html.append(
            "<tr>"
            f"<td>{html_lib.escape(place)}</td>"
            f'<td class="golf-player">{_headshot_img(act.get("athlete_id"))}'
            f"<strong>{html_lib.escape(str(act.get('name')))}</strong></td>"
            f"<td>{score_s}</td>"
            "</tr>"
        )

    empty = (
        '<tr><td colspan="3" class="golf-empty">No official finishes posted for this event yet.</td></tr>'
    )
    scores = [str(a.get("score") or "") for a in actual[:40]]
    all_e = bool(actual) and all(s.upper() in ("E", "EVEN", "") for s in scores)
    warn = ""
    if all_e:
        warn = (
            '<p class="golf-banner" role="status">Scores look incomplete (everyone even). '
            "If this event just wrapped, refresh shortly.</p>"
        )
    gm, state = _golf_model_state(tournaments)
    model_html = ""
    if gm and state:
        graded = state["graded"].get(str(eid))
        year = int(str((graded or {}).get("start") or datetime.now(timezone.utc).year)[:4])
        model_html = _GPM_CSS + _gpm_tally(gm, state, year, f"Our {year} record")
        model_html += '<h2 class="gpm-h2">Picks won and lost by event</h2>' + _gpm_chart(
            state, (graded or {}).get("start")
        )
        if graded:
            winner = graded.get("winner")
            w_line = ""
            if winner:
                tp = winner.get("to_par")
                tp_s = "E" if tp == 0 else (f"{tp:+d}" if isinstance(tp, int) else "")
                w_line = (
                    f'<div class="gpm-winner"><strong>Winner:</strong> '
                    f"{html_lib.escape(str(winner['name']))} {tp_s}</div>"
                )
            model_html += (
                f'<h2 class="gpm-h2">Our picks · {html_lib.escape(event)}</h2>{w_line}'
                + _gpm_pick_cards(graded["picks"], graded=True)
            )
        else:
            model_html += (
                '<p class="golf-lede">We did not make picks for this event '
                "(team events and events before 2025 are not picked).</p>"
            )
    body = (
        f"<h1>{html_lib.escape(event)} — Results{status_bit}</h1>"
        '<p class="golf-lede">Every pick is graded against the official finish: '
        "✓ the pick came in, ✗ it did not.</p>"
        f"{date_nav}{picker}"
        f"{warn}"
        f"{model_html}"
        '<h2 class="gpm-h2">Official finish</h2>'
        '<div class="golf-table-wrap"><table class="golf-table golf-results-table">'
        "<thead><tr>"
        '<th scope="col">Finish</th>'
        '<th scope="col">Player</th>'
        '<th scope="col">Score</th>'
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
        "source": "espn-finish",
        "grading": "official_finish_only",
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
