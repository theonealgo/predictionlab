"""Keep scores and saved pages current with no visitor and no manual step.

Every cycle:
  1. Score sync — a sport with games in the last 3 days that still have no
     score gets its normal score sync (the same function its results page uses).
  2. Results pages — a sport whose stored finals changed, or whose saved
     results / chart page does not show the newest final night (for example
     after the server was off), gets each of its saved pages rebuilt once.
  3. Picks pages — a saved picks page stamped with an earlier day is refreshed.

Sports are picked from the stored games, not from a calendar, so a sport going
into or out of season needs no change here. PL_AUTOREFRESH=0 turns it off.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

CYCLE_S = 600
# A fresh deploy has no saved pages; visitors' first builds go before score syncs.
FIRST_DELAY_S = int(os.environ.get("PL_AUTOREFRESH_FIRST_DELAY", "900"))
SYNC_EVERY_UNSCORED_S = 1800
SYNC_EVERY_QUIET_S = 6 * 3600
PAGE_PAUSE_S = 2.0

_ROOT = Path(__file__).resolve().parent
_CACHE = _ROOT / ".cache"
_STATE_PATH = _CACHE / "autorefresh_state.json"

SLUGS = {
    "MLB": "mlb", "NHL": "nhl", "NBA": "nba", "NFL": "nfl", "NCAAF": "ncaaf",
    "NCAAB": "ncaab", "NCAAW": "ncaaw", "WNBA": "wnba", "SOCCER": "soccer",
}
# Soccer rebuilds its own results pages after its finals sync.
_SELF_REBUILDING = {"SOCCER"}
# (view, market) of every saved results page; cards before charts, since the
# charts read the cards page.
_PAGE_VARIANTS = (
    ("", ""), ("", "moneyline"), ("", "spread"), ("", "totals"),
    ("chart", ""), ("chart", "moneyline"), ("chart", "spread"), ("chart", "totals"),
)
_PICKS_SPORTS = ("MLB", "NHL", "NBA", "NFL", "NCAAF", "NCAAB", "NCAAW", "WNBA", "CFL", "TENNIS", "UFC", "GOLF")

_started = False
_last_sync: dict[str, float] = {}
_tried_pages: set[tuple[str, str]] = set()
_warmed: set[str] = set()


def _today_et() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _load_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _CACHE.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except Exception as exc:
        logger.debug(f"[autorefresh] state save failed: {exc}")


def _query(db: str, sql: str, args=()) -> list:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _sync_candidates(db: str) -> dict[str, int]:
    """Sport -> unscored games dated in the last 3 days (0 = games, all scored)."""
    today = _today_et()
    start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
    rows = _query(
        db,
        """
        SELECT upper(sport),
               SUM(CASE WHEN home_score IS NULL OR away_score IS NULL THEN 1 ELSE 0 END)
        FROM games
        WHERE date(game_date) BETWEEN ? AND ?
        GROUP BY upper(sport)
        """,
        (start, today),
    )
    return {s: int(n or 0) for s, n in rows if s in SLUGS}


def _finals_stamp(db: str) -> dict[str, list]:
    """Sport -> [newest final night before today, finals in the last 10 days]."""
    today = _today_et()
    rows = _query(
        db,
        """
        SELECT upper(sport), MAX(date(game_date)), COUNT(*)
        FROM games
        WHERE home_score IS NOT NULL
          AND date(game_date) >= date(?, '-10 day')
          AND date(game_date) < ?
        GROUP BY upper(sport)
        """,
        (today, today),
    )
    return {s: [d, int(n)] for s, d, n in rows if s in SLUGS and d}


def _run_sync(sport: str, sync_fns: dict) -> None:
    fn = sync_fns.get(sport)
    if fn is None:
        return
    try:
        import sport_variant
        with sport_variant.sport_scope(SLUGS[sport]):
            fn()
    except Exception as exc:
        logger.warning(f"[autorefresh] {sport} score sync failed: {exc}")


def _served_file(sport: str, view: str, market: str, free: bool = False) -> Path:
    key = "|".join([sport, view, market, "", "", ""])
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:180]
    return _CACHE / (f"served_{safe}__free.html" if free else f"served_{safe}.html")


def _rebuild_page(app, sport: str, view: str, market: str) -> bool:
    """One forced build of a saved results page (paid and free copies)."""
    slug = SLUGS.get(sport, sport.lower())
    query = "&".join(p for p in (f"view={view}" if view else "", f"market={market}" if market else "") if p)
    url = f"/{slug}-results" + (f"?{query}" if query else "")
    path = _served_file(sport, view, market)
    before = path.stat().st_mtime if path.is_file() else 0.0
    force = None
    try:
        import isolate_checker_fixes as icf
        import sport_variant
        with sport_variant.sport_scope(slug):
            force = icf._RESULTS_REFRESH_FORCE
    except Exception:
        force = None
    try:
        if force is not None:
            force.on = True
        client = app.test_client()
        for base in ("http://localhost", "http://results-refresh.local"):
            client.get(url, base_url=base, headers={"X-PL-Results-Rebuild": "1"})
    except Exception as exc:
        logger.warning(f"[autorefresh] rebuild {url} failed: {exc}")
    finally:
        if force is not None:
            force.on = False
    after = path.stat().st_mtime if path.is_file() else 0.0
    return after > before


def _saved_pages(sport: str) -> list[tuple[str, str]]:
    return [(v, m) for v, m in _PAGE_VARIANTS if _served_file(sport, v, m).is_file()]


def _page_missing_night(sport: str, view: str, market: str, night: str) -> bool:
    try:
        text = _served_file(sport, view, market).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return night not in text


def _refresh_results(app, db: str, state: dict) -> None:
    stamps = _finals_stamp(db)
    rebuilt = state.setdefault("rebuilt", {})
    for sport, stamp in stamps.items():
        if sport in _SELF_REBUILDING:
            continue
        night = stamp[0]
        pages = _saved_pages(sport)
        if not pages:
            continue
        changed = rebuilt.get(sport) != stamp
        if changed:
            todo = pages
        else:
            todo = [
                (v, m) for v, m in pages
                if (sport, f"{v}|{m}|{night}") not in _tried_pages
                and _page_missing_night(sport, v, m, night)
            ]
        if not todo:
            continue
        done = 0
        for view, market in todo:
            _tried_pages.add((sport, f"{view}|{market}|{night}"))
            if _rebuild_page(app, sport, view, market):
                done += 1
            if _page_missing_night(sport, view, market, night):
                logger.warning(
                    f"[autorefresh] {sport} results view={view or 'cards'} market={market or '-'} "
                    f"still has no {night} after a rebuild"
                )
            time.sleep(PAGE_PAUSE_S)
        if changed:
            # Picks cards carry season records (consensus chip, PL vs Books)
            # counted from the same finals.
            _refresh_sport_picks(sport)
        rebuilt[sport] = stamp
        _save_state(state)
        logger.info(f"[autorefresh] {sport} finals {stamp}: rebuilt {done}/{len(todo)} saved results pages")


def _results_before_picks(app) -> None:
    """A saved picks page with no saved results page (a fresh deploy) counted its
    season chips without the graded results cards: build results, then picks."""
    for sport in SLUGS:
        if sport in _SELF_REBUILDING or sport in _warmed:
            continue
        if not (_CACHE / f"served_picks_{sport}.html").is_file():
            continue
        if _served_file(sport, "", "").is_file():
            _warmed.add(sport)
            continue
        _warmed.add(sport)
        built = _rebuild_page(app, sport, "", "")
        _refresh_sport_picks(sport)
        logger.info(f"[autorefresh] {sport} results page built before picks: {built}")
        time.sleep(PAGE_PAUSE_S)


def _refresh_sport_picks(sport: str) -> None:
    if sport not in _PICKS_SPORTS or not (_CACHE / f"served_picks_{sport}.html").is_file():
        return
    try:
        import isolate_checker_fixes as icf
        import sport_variant

        with sport_variant.sport_scope(sport.lower()):
            icf.schedule_picks_refresh(sport)
    except Exception as exc:
        logger.debug(f"[autorefresh] {sport} picks refresh failed: {exc}")


def _refresh_picks() -> None:
    try:
        import isolate_checker_fixes as icf
        import sport_variant
    except Exception:
        return
    today = _today_et()
    for sport in _PICKS_SPORTS:
        path = _CACHE / f"served_picks_{sport}.html"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        m = re.search(r"const today = '(\d{4}-\d{2}-\d{2})'", text)
        if not m or m.group(1) >= today:
            continue
        try:
            with sport_variant.sport_scope(sport.lower()):
                icf.schedule_picks_refresh(sport)
            logger.info(f"[autorefresh] {sport} picks page from {m.group(1)} refreshing")
        except Exception as exc:
            logger.debug(f"[autorefresh] {sport} picks refresh failed: {exc}")
        time.sleep(PAGE_PAUSE_S)


CFL_SYNC_EVERY_S = 3 * 3600


def _sync_cfl(app, state: dict) -> None:
    """CFL lives in its own engine DB: pull the official schedule / finals and
    write picks for newly set matchups (published picks stay as written)."""
    if time.time() - _last_sync.get("CFL", 0.0) < CFL_SYNC_EVERY_S:
        return
    _last_sync["CFL"] = time.time()
    try:
        import importlib.util
        path = _ROOT / "engines" / "cfl" / "engine" / "pipeline.py"
        spec = importlib.util.spec_from_file_location("cfl_autorefresh_pipeline", path)
        pipe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pipe)
        meta = pipe.ensure_predictions(refresh=True)
    except Exception as exc:
        logger.warning(f"[autorefresh] CFL sync failed: {exc}")
        return
    stamp = [int(meta.get("completed") or 0), int(meta.get("predictions") or 0)]
    rebuilt = state.setdefault("rebuilt", {})
    if rebuilt.get("CFL") == stamp:
        return
    done = 0
    pages = _saved_pages("CFL")
    for view, market in pages:
        if _rebuild_page(app, "CFL", view, market):
            done += 1
        time.sleep(PAGE_PAUSE_S)
    try:
        import isolate_checker_fixes as icf
        import sport_variant
        with sport_variant.sport_scope("cfl"):
            icf.schedule_picks_refresh("CFL")
    except Exception as exc:
        logger.debug(f"[autorefresh] CFL picks refresh failed: {exc}")
    rebuilt["CFL"] = stamp
    _save_state(state)
    logger.info(f"[autorefresh] CFL finals/picks {stamp}: rebuilt {done}/{len(pages)} saved results pages")


def _cycle(app, db: str, sync_fns: dict, state: dict) -> None:
    now = time.time()
    try:
        _sync_cfl(app, state)
    except Exception as exc:
        logger.warning(f"[autorefresh] CFL step failed: {exc}")
    try:
        candidates = _sync_candidates(db)
    except Exception as exc:
        logger.debug(f"[autorefresh] candidates query failed: {exc}")
        candidates = {}
    for sport, unscored in candidates.items():
        every = SYNC_EVERY_UNSCORED_S if unscored else SYNC_EVERY_QUIET_S
        if now - _last_sync.get(sport, 0.0) < every:
            continue
        _last_sync[sport] = now
        _run_sync(sport, sync_fns)
    try:
        _refresh_results(app, db, state)
    except Exception as exc:
        logger.warning(f"[autorefresh] results refresh failed: {exc}")
    try:
        _results_before_picks(app)
    except Exception as exc:
        logger.warning(f"[autorefresh] results-before-picks failed: {exc}")
    try:
        _refresh_picks()
    except Exception as exc:
        logger.debug(f"[autorefresh] picks refresh failed: {exc}")
    logger.info(f"[autorefresh] cycle done in {time.time() - now:.0f}s · memory {_memory_text()}")


def _memory_text() -> str:
    """Current and peak memory of this server process, e.g. '812 MB (peak 1240 MB)'."""
    current = 0
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    current = int(line.split()[1]) // 1024
                    break
    except OSError:
        pass
    try:
        import resource
        import sys

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = peak // (1024 * 1024) if sys.platform == "darwin" else peak // 1024
    except Exception:
        peak = 0
    return f"{current} MB (peak {peak} MB)" if current else f"peak {peak} MB"


def _loop(app, db: str, sync_fns: dict) -> None:
    time.sleep(FIRST_DELAY_S)
    state = _load_state()
    while True:
        try:
            _cycle(app, db, sync_fns, state)
        except Exception as exc:
            logger.warning(f"[autorefresh] cycle failed: {exc}")
        time.sleep(CYCLE_S)


def start(app, db: str, sync_fns: dict) -> None:
    global _started
    if _started or os.environ.get("PL_AUTOREFRESH") == "0":
        return
    _started = True
    threading.Thread(target=_loop, args=(app, db, sync_fns), daemon=True, name="site-autorefresh").start()
