#!/usr/bin/env python3
"""Grade player-prop results from ESPN posted lines + box scores.

Used by /player-props-api/results when a date has no stored HIT/MISS rows.
Does not invent players, markets, or lines. Pick side comes from a stored
player_prop_picks row when one exists; otherwise uses the same slight OVER
lean as backfill_nfl_props.py.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROPS_BACKEND = os.path.join(BASE_DIR, "standalone-player-props", "backend")
DATABASE = os.path.join(BASE_DIR, "sports_predictions_original.db")

_IN_SEASON_BACKFILL = ("NFL", "NCAAF", "CFL", "MLB", "WNBA")

for _k in list(sys.modules.keys()):
    if _k == "app" or _k.startswith("app."):
        del sys.modules[_k]
sys.path = [PROPS_BACKEND] + [p for p in sys.path if os.path.abspath(p) != BASE_DIR]

from app.config import LEAGUE_CONFIG  # noqa: E402
from app.data_sources import _espn_to_internal_prop  # noqa: E402
from app.engine import _fetch_event_actuals  # noqa: E402

if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

REQUEST_DELAY = 0.2
ATHLETE_CACHE: Dict[Tuple[str, str], str] = {}


def _cfg(league: str) -> Dict[str, str]:
    return LEAGUE_CONFIG.get((league or "").upper()) or {}


def _final_events(league: str, day: date) -> List[Dict]:
    cfg = _cfg(league)
    sport = cfg.get("espn_sport")
    lg = cfg.get("espn_league")
    if not sport or not lg:
        return []
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{lg}/scoreboard"
    try:
        body = requests.get(url, params={"dates": day.strftime("%Y%m%d")}, timeout=15).json() or {}
    except Exception:
        return []
    rows = []
    for ev in body.get("events") or []:
        st = (((ev.get("status") or {}).get("type")) or {})
        if not (st.get("completed") or "FINAL" in str(st.get("name") or "").upper()):
            continue
        eid = str(ev.get("id") or "")
        if not eid:
            continue
        rows.append({"event_id": eid, "name": ev.get("name") or "", "date": str(day)})
    return rows


def _athlete_name(league: str, athlete_id: str) -> str:
    cfg = _cfg(league)
    key = (league, athlete_id)
    if key in ATHLETE_CACHE:
        return ATHLETE_CACHE[key]
    sport = cfg.get("espn_sport") or ""
    lg = cfg.get("espn_league") or ""
    year = datetime.now().year
    name = ""
    url = (
        f"https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{lg}/"
        f"seasons/{year}/athletes/{athlete_id}"
    )
    try:
        body = requests.get(url, timeout=10).json() or {}
        name = (body.get("displayName") or body.get("fullName") or "").strip()
    except Exception:
        name = ""
    ATHLETE_CACHE[key] = name
    time.sleep(0.04)
    return name


def _prop_rows_for_event(league: str, event_id: str) -> List[Dict]:
    cfg = _cfg(league)
    sport = cfg.get("espn_sport") or ""
    lg = cfg.get("espn_league") or ""
    core = (
        f"https://sports.core.api.espn.com/v2/sports/{sport}/leagues/{lg}/"
        f"events/{event_id}/competitions/{event_id}/odds/100/propBets"
    )
    agg: Dict[Tuple[str, str], float] = {}
    page = 1
    page_count = 1
    while page <= page_count and page <= 60:
        try:
            body = requests.get(
                core,
                params={"limit": 100, "page": page, "lang": "en", "region": "us"},
                timeout=15,
            ).json() or {}
        except Exception:
            break
        try:
            page_count = int(body.get("pageCount") or 1)
        except Exception:
            page_count = 1
        for it in body.get("items") or []:
            aref = ((it.get("athlete") or {}).get("$ref")) or ""
            m = re.search(r"/athletes/(\d+)", aref)
            if not m:
                continue
            aid = m.group(1)
            type_name = ((it.get("type") or {}).get("name")) or it.get("name") or ""
            prop_type = _espn_to_internal_prop(league, type_name)
            if not prop_type:
                continue
            target = ((it.get("current") or {}).get("target") or {}).get("value")
            if target is None:
                continue
            try:
                line = float(target)
            except Exception:
                continue
            key = (aid, prop_type)
            prev = agg.get(key)
            half = abs((line * 2) - round(line * 2)) < 1e-9 and int(round(line * 2)) % 2 == 1
            if prev is None:
                agg[key] = line
            else:
                prev_half = abs((prev * 2) - round(prev * 2)) < 1e-9 and int(round(prev * 2)) % 2 == 1
                if half and not prev_half:
                    agg[key] = line
        page += 1
        time.sleep(0.04)

    rows = []
    for (aid, prop_type), line in agg.items():
        name = _athlete_name(league, aid)
        if not name:
            continue
        rows.append(
            {
                "player_id": aid,
                "player_name": name,
                "prop_type": prop_type,
                "line": round(round(line * 2.0) / 2.0, 1),
            }
        )
    return rows


def _team_for_players(league: str, event_id: str) -> Dict[str, str]:
    cfg = _cfg(league)
    sport = cfg.get("espn_sport") or ""
    lg = cfg.get("espn_league") or ""
    url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{lg}/summary"
    try:
        body = requests.get(url, params={"event": event_id}, timeout=15).json() or {}
    except Exception:
        return {}
    out: Dict[str, str] = {}
    for sec in (body.get("boxscore") or {}).get("players") or []:
        team = ((sec.get("team") or {}).get("displayName") or "").strip()
        if not team:
            continue
        for grp in sec.get("statistics") or []:
            for ath in grp.get("athletes") or []:
                nm = (((ath.get("athlete") or {}).get("displayName") or "").strip().lower())
                if nm:
                    out[nm] = team
    return out


def _pick_side(line: float, stored: Optional[str] = None) -> Tuple[str, float]:
    if stored in ("OVER", "UNDER"):
        return stored, float(line)
    proj = float(line) * 1.015
    return ("OVER" if proj >= float(line) else "UNDER"), round(proj, 1)


def _stored_picks(conn: sqlite3.Connection, league: str, day: str) -> Dict[Tuple[str, str], str]:
    try:
        rows = conn.execute(
            "SELECT player_name, prop_type, pick FROM player_prop_picks "
            "WHERE league=? AND pick_date=?",
            (league, day),
        ).fetchall()
    except Exception:
        return {}
    out: Dict[Tuple[str, str], str] = {}
    for r in rows:
        pick = str(r["pick"] if isinstance(r, sqlite3.Row) else r[2] or "").upper()
        if pick in ("OVER", "UNDER"):
            pname = str(r["player_name"] if isinstance(r, sqlite3.Row) else r[0] or "")
            pt = str(r["prop_type"] if isinstance(r, sqlite3.Row) else r[1] or "")
            out[(pname.lower(), pt)] = pick
    return out


def backfill_league_date(league: str, day, db_path: Optional[str] = None) -> int:
    """Grade one calendar date for one league. Returns rows written."""
    league = (league or "").upper()
    if league not in _IN_SEASON_BACKFILL:
        return 0
    if isinstance(day, str):
        day = date.fromisoformat(day[:10])
    path = db_path or DATABASE
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    inserted = 0
    try:
        stored = _stored_picks(conn, league, str(day))
        events = _final_events(league, day)
        for ev in events:
            eid = ev["event_id"]
            actuals = _fetch_event_actuals(league, eid)
            if not actuals:
                continue
            teams = _team_for_players(league, eid)
            props = _prop_rows_for_event(league, eid)
            for prop in props:
                nm = prop["player_name"]
                pt = prop["prop_type"]
                line = float(prop["line"])
                actual = (actuals.get(nm.lower()) or {}).get(pt)
                if actual is None:
                    continue
                actual_f = float(actual)
                if actual_f == line:
                    continue
                pick, proj = _pick_side(line, stored.get((nm.lower(), pt)))
                hit = (actual_f > line and pick == "OVER") or (actual_f < line and pick == "UNDER")
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO player_prop_results
                           (league, result_date, player_name, team,
                            prop_type, pick, line, projection, actual, result)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            league,
                            ev["date"],
                            nm,
                            teams.get(nm.lower(), ""),
                            pt,
                            pick,
                            line,
                            proj,
                            round(actual_f, 2),
                            "HIT" if hit else "MISS",
                        ),
                    )
                    inserted += 1
                except Exception:
                    continue
            conn.commit()
            time.sleep(REQUEST_DELAY)
    finally:
        conn.close()
    return inserted


def backfill_range(league: str, start: date, end: date, db_path: Optional[str] = None) -> int:
    total = 0
    cur = start
    while cur <= end:
        total += backfill_league_date(league, cur, db_path=db_path)
        cur += timedelta(days=1)
    return total
