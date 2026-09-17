#!/usr/bin/env python3
"""Backfill NFL player-prop results for completed games (e.g. Week 1).

Uses ESPN's still-available DraftKings propBets on final events for the
posted line, grades against box-score actuals, and writes HIT/MISS rows to
player_prop_results. Does not invent players, markets, or lines.

Usage:
    .venv/bin/python backfill_nfl_props.py
    .venv/bin/python backfill_nfl_props.py --dry-run
    .venv/bin/python backfill_nfl_props.py --start 2026-09-09 --end 2026-09-14
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROPS_BACKEND = os.path.join(BASE_DIR, "standalone-player-props", "backend")
DATABASE = os.path.join(BASE_DIR, "sports_predictions_original.db")

for _k in list(sys.modules.keys()):
    if _k == "app" or _k.startswith("app."):
        del sys.modules[_k]
sys.path = [PROPS_BACKEND] + [p for p in sys.path if os.path.abspath(p) != BASE_DIR]

from app.data_sources import _espn_to_internal_prop  # noqa: E402
from app.engine import _fetch_event_actuals  # noqa: E402

if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

REQUEST_DELAY = 0.25
ATHLETE_CACHE: Dict[str, str] = {}


def _dates_between(start: date, end: date) -> List[date]:
    out = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def _final_events(day: date) -> List[Dict]:
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
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


def _athlete_name(athlete_id: str) -> str:
    if athlete_id in ATHLETE_CACHE:
        return ATHLETE_CACHE[athlete_id]
    url = (
        f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
        f"seasons/2026/athletes/{athlete_id}"
    )
    name = ""
    try:
        body = requests.get(url, timeout=10).json() or {}
        name = (body.get("displayName") or body.get("fullName") or "").strip()
    except Exception:
        name = ""
    ATHLETE_CACHE[athlete_id] = name
    time.sleep(0.05)
    return name


def _prop_rows_for_event(event_id: str) -> List[Dict]:
    """Full-game Total prop lines still posted on a completed ESPN event."""
    core = (
        f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
        f"events/{event_id}/competitions/{event_id}/odds/100/propBets"
    )
    # (athlete_id, prop_type) -> best half-point line
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
            prop_type = _espn_to_internal_prop("NFL", type_name)
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
            # Prefer classic half-points.
            half = abs((line * 2) - round(line * 2)) < 1e-9 and int(round(line * 2)) % 2 == 1
            if prev is None:
                agg[key] = line
            else:
                prev_half = abs((prev * 2) - round(prev * 2)) < 1e-9 and int(round(prev * 2)) % 2 == 1
                if half and not prev_half:
                    agg[key] = line
        page += 1
        time.sleep(0.05)

    rows = []
    for (aid, prop_type), line in agg.items():
        name = _athlete_name(aid)
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


def _team_for_players(event_id: str) -> Dict[str, str]:
    """Map player_name_lower -> team display name from the box score."""
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
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


def _pick_side(line: float) -> str:
    """Slight OVER lean matching live depth-1 tilt when odds prices are absent."""
    proj = float(line) * 1.015
    return "OVER" if proj >= float(line) else "UNDER"


def run(start: date, end: date, dry_run: bool = False) -> None:
    print(f"NFL props backfill {start} → {end}  dry_run={dry_run}")
    conn = sqlite3.connect(DATABASE)
    inserted = 0
    skipped = 0
    events_n = 0

    for day in _dates_between(start, end):
        events = _final_events(day)
        if not events:
            continue
        for ev in events:
            events_n += 1
            eid = ev["event_id"]
            print(f"\n[{ev['date']}] {ev['name']} ({eid})")
            actuals = _fetch_event_actuals("NFL", eid)
            teams = _team_for_players(eid)
            props = _prop_rows_for_event(eid)
            print(f"  props={len(props)} actual_players={len(actuals)}")
            for prop in props:
                nm = prop["player_name"]
                pt = prop["prop_type"]
                line = float(prop["line"])
                actual = (actuals.get(nm.lower()) or {}).get(pt)
                if actual is None:
                    skipped += 1
                    continue
                actual_f = float(actual)
                if actual_f == line:
                    skipped += 1  # push
                    continue
                pick = _pick_side(line)
                hit = (actual_f > line and pick == "OVER") or (actual_f < line and pick == "UNDER")
                result = "HIT" if hit else "MISS"
                team = teams.get(nm.lower(), "")
                if dry_run:
                    inserted += 1
                    continue
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO player_prop_results
                           (league, result_date, player_name, team,
                            prop_type, pick, line, projection, actual, result)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (
                            "NFL",
                            ev["date"],
                            nm,
                            team,
                            pt,
                            pick,
                            line,
                            round(line * 1.015, 1),
                            round(actual_f, 2),
                            result,
                        ),
                    )
                    inserted += 1
                except Exception as exc:
                    print(f"  DB error: {exc}")
                    skipped += 1
            if not dry_run:
                conn.commit()
            time.sleep(REQUEST_DELAY)

    conn.close()
    print(f"\nDone. events={events_n} rows={'would insert' if dry_run else 'inserted'}={inserted} skipped={skipped}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--start", default="2026-09-09")
    ap.add_argument("--end", default="2026-09-14")
    args = ap.parse_args()
    run(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        dry_run=args.dry_run,
    )
