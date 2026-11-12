"""Full-field NCAAW final scores. Never reads or writes prediction/grade tables."""
from datetime import datetime, timedelta
import logging
import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard"


def fetch_day(day):
    response = requests.get(URL, params={"dates": day.replace("-", ""), "groups": 50, "limit": 1000},
                            timeout=(3, 12))
    response.raise_for_status()
    return response.json()


def final_rows(payload, day):
    rows = {}
    for event in payload.get("events") or []:
        comp = (event.get("competitions") or [{}])[0]
        status = ((comp.get("status") or event.get("status") or {}).get("type") or {})
        if not status.get("completed") or "FINAL" not in str(status.get("name", "")).upper():
            continue
        try:
            stamp = datetime.fromisoformat(str(comp.get("date") or event["date"]).replace("Z", "+00:00"))
            if not stamp.tzinfo or stamp.astimezone(ET).date().isoformat() != day:
                continue
            event_id = str(event["id"])
            if not event_id.isdigit():
                continue
            sides = comp.get("competitors") or []
            home = next(c for c in sides if c.get("homeAway") == "home")
            away = next(c for c in sides if c.get("homeAway") == "away")
            hs, aws = int(home["score"]), int(away["score"])
            if str(home["score"]).strip() != str(hs) or str(away["score"]).strip() != str(aws) or min(hs, aws) < 0:
                continue
            hn, an = home["team"]["displayName"].strip(), away["team"]["displayName"].strip()
            if not hn or not an:
                continue
            rows[event_id] = (f"NCAAW_{event_id}", day, hn, an, hs, aws)
        except (KeyError, ValueError, TypeError, StopIteration):
            continue
    return list(rows.values())


def sync_scores(database, days, fetch=None):
    fetch = fetch or fetch_day
    verified, unavailable = [], []
    # No database locks are held while waiting for the provider.
    for day in days:
        try:
            verified.extend(final_rows(fetch(day), day))
        except Exception:
            unavailable.append(day)
    changed = 0
    conn = sqlite3.connect(str(database), timeout=15)
    try:
        with conn:
            for gid, day, home, away, hs, aws in verified:
                previous = conn.execute(
                    "SELECT game_date, home_team_id, away_team_id FROM games WHERE sport='NCAAW' AND game_id=?",
                    (gid,),
                ).fetchone()
                if previous:
                    # An event ID must still describe the same participants.
                    if previous[1] != home or previous[2] != away:
                        logging.getLogger(__name__).warning("NCAAW score identity mismatch; update withheld")
                        continue
                    cursor = conn.execute(
                        "UPDATE games SET home_score=?,away_score=?,status='final',game_date=? "
                        "WHERE sport='NCAAW' AND game_id=? AND "
                        "(home_score IS NULL OR away_score IS NULL OR home_score!=? OR away_score!=? "
                        "OR status IS NULL OR status!='final' OR date(game_date)!=?)",
                        (hs, aws, day, gid, hs, aws, day),
                    )
                else:
                    cursor = conn.execute(
                        "INSERT INTO games(sport,league,game_id,season,game_date,home_team_id,away_team_id,"
                        "home_score,away_score,status) VALUES('NCAAW','NCAAW',?,?,?,?,?,?,?,'final')",
                        (gid, int(day[:4]), day, home, away, hs, aws),
                    )
                changed += cursor.rowcount
        signature = hashlib.sha256(repr(conn.execute(
            "SELECT game_id,game_date,home_score,away_score,status FROM games "
            "WHERE sport='NCAAW' ORDER BY game_id"
        ).fetchall()).encode()).hexdigest()
    finally:
        conn.close()
    return {"verified": len(verified), "changed": changed, "unavailable_days": unavailable, "signature": signature}

def invalidate_site(site):
    for key in list(site._SPORT_RESULTS_CACHE):
        if "ncaaw" in str(key).lower():
            site._SPORT_RESULTS_CACHE.pop(key, None)
    from sport_variant import sport_scope
    with sport_scope("ncaaw"):
        import isolate_checker_fixes
        import picks_recent_results
        import team_results_charts
        for module, name in ((isolate_checker_fixes, "_SERVED"), (picks_recent_results, "_CACHE")):
            cache = getattr(module, name, None)
            if isinstance(cache, dict):
                for key in list(cache):
                    if "ncaaw" in str(key).lower():
                        dict.pop(cache, key, None)
        source = getattr(team_results_charts, "_CHART_SOURCE_HTML", {})
        source.pop("NCAAW", None)
    from served_cache import cache_dir
    directory = cache_dir()
    paths = list(directory.glob("served_NCAAW*.html")) + [
        directory / "chart_src_NCAAW.html", directory / "results_summary/NCAAW.json",
    ]
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            site.app.logger.warning("NCAAW derived page cache could not be invalidated")
    # Public response snapshots are another derived cache; keep other sports intact.
    pending, visited = [site.app.wsgi_app], set()
    while pending:
        node = pending.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(getattr(node, "_pages", None), dict) and hasattr(node, "_bytes"):
            with node._lock:
                for key in list(node._pages):
                    if isinstance(key, tuple) and len(key) > 2 and key[2] in (
                            "/ncaaw-results", "/ncaaw-picks", "/ncaaw-predictions"):
                        entry = node._pages.pop(key)
                        node._bytes -= len(entry[3])
        for name in ("inner", "wsgi_app"):
            child = getattr(node, name, None)
            if child is not None:
                pending.append(child)


def start_worker(app, database, changed_callback):
    if app.testing or os.getenv("PL_NCAAW_SCORES", "1") != "1":
        return
    if app.extensions.get("ncaaw_score_worker"):
        return
    stop = threading.Event()
    app.extensions["ncaaw_score_worker"] = stop

    def run():
        first = True
        previous_signature = None
        while not stop.is_set():
            try:
                today = datetime.now(ET).date()
                days = [(today - timedelta(days=n)).isoformat() for n in range(8 if first else 2)]
                result = sync_scores(database, days)
                if result["signature"] != previous_signature:
                    with app.app_context():
                        changed_callback()
                    previous_signature = result["signature"]
                first = False
                app.logger.info("NCAAW score sync: %s", result)
            except Exception:
                app.logger.exception("NCAAW score sync unavailable; existing records preserved")
            stop.wait(300)
    threading.Thread(target=run, daemon=True, name="ncaaw-final-scores").start()
