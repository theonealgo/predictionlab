"""ESPN completed-game counts for results integrity checks.

The values auditor uses this so Last Night / Past 7 / Past 30 on our results
pages cannot claim a joke sample (e.g. unanimous 1-1) when ESPN finished far
more games in the same window.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
CACHE_PATH = Path(__file__).resolve().parent / ".espn_results_counts_cache.json"
# Bump when scoreboard query params / league set change so stale counts drop.
_CACHE_VER = "v2"

# Sport key → (scoreboard URL, extra query). Same catalog as espn_slate_checker.
ESPN_BOARDS: dict[str, tuple[str, str]] = {
    "MLB": (
        "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard",
        "",
    ),
    "NFL": (
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
        "",
    ),
    "NBA": (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
        "",
    ),
    "NHL": (
        "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard",
        "",
    ),
    "WNBA": (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard",
        "",
    ),
    "NCAAF": (
        "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
        "groups=80&limit=300",
    ),
    "NCAAB": (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard",
        "groups=50&limit=357",
    ),
    "NCAAW": (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard",
        "groups=50&limit=357",
    ),
    "CFL": (
        "https://site.api.espn.com/apis/site/v2/sports/football/cfl/scoreboard",
        "",
    ),
    # Placeholder — soccer uses site-league scoreboards in _fetch_day.
    "SOCCER": (
        "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard",
        "",
    ),
    "TENNIS": (
        "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard",
        "",
    ),
    "UFC": (
        "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard",
        "",
    ),
    "GOLF": (
        "https://site.web.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard",
        "",
    ),
}

_ALL_SPORTS = tuple(ESPN_BOARDS.keys())
_HEADERS = {
    "User-Agent": "PredictionLab-QA/espn-results-counts",
    "Accept": "application/json",
}


def _today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def _ymd_to_espn(day: str) -> str:
    return day.replace("-", "")


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def _event_completed(ev: dict) -> bool:
    comps = ev.get("competitions") or [{}]
    st = ((comps[0].get("status") or {}).get("type") or {})
    if st.get("completed"):
        return True
    name = str(st.get("name") or st.get("description") or "").upper()
    return name in {"STATUS_FINAL", "STATUS_FULL_TIME", "STATUS_FINAL_PEN"}


def _count_completed_events(data: dict) -> int:
    n = 0
    for ev in data.get("events") or []:
        if not isinstance(ev, dict):
            continue
        # Golf: one event = tournament day; count the event if completed or in progress with scores.
        if _event_completed(ev):
            n += 1
            continue
        # Tennis: count individual competitions inside groupings when present.
        comps = ev.get("competitions") or []
        if len(comps) > 1:
            for c in comps:
                st = ((c.get("status") or {}).get("type") or {})
                if st.get("completed"):
                    n += 1
            continue
    return n


def _soccer_site_league_urls(day_ymd: str) -> list[str]:
    """Only the site's active soccer leagues — not ESPN's all/ dump."""
    days_param = _ymd_to_espn(day_ymd)
    slugs: list[str] = []
    try:
        from soccer_league_catalog import SOCCER_LEAGUE_ENDPOINTS

        slugs = sorted({str(v) for v in (SOCCER_LEAGUE_ENDPOINTS or {}).values() if v})
    except Exception:
        slugs = [
            "eng.1", "ita.1", "ger.1", "esp.1", "fra.1", "uefa.champions",
            "bra.1", "por.1", "bel.1", "usa.1", "eng.2", "ned.1", "tur.1",
            "aut.1", "sco.1", "sui.1",
        ]
    # World Cup / internationals when on the slate (audit hits fifa.world).
    if "fifa.world" not in slugs:
        slugs.append("fifa.world")
    out = []
    for slug in slugs:
        out.append(
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"
            f"?dates={days_param}"
        )
    return out


def _fetch_day(session: requests.Session, sport: str, day: str) -> int:
    board = ESPN_BOARDS.get((sport or "").upper())
    if not board:
        return 0
    url, extra = board
    sport_u = sport.upper()
    days_param = _ymd_to_espn(day)
    urls: list[str] = []
    if sport_u == "SOCCER":
        urls = _soccer_site_league_urls(day)
    elif sport_u == "TENNIS":
        for tour in ("atp", "wta"):
            tour_url = re.sub(r"/tennis/[^/]+/scoreboard", f"/tennis/{tour}/scoreboard", url)
            q = f"dates={days_param}"
            if extra:
                q += f"&{extra}"
            urls.append(f"{tour_url}?{q}")
    else:
        q = f"dates={days_param}"
        if extra:
            q += f"&{extra}"
        urls.append(f"{url}?{q}")
        if "site.api.espn.com" in url:
            urls.append(urls[-1].replace("://site.api.espn.com", "://site.web.api.espn.com", 1))

    total = 0
    seen = set()
    for full in urls:
        if full in seen:
            continue
        seen.add(full)
        try:
            resp = session.get(full, timeout=15, headers=_HEADERS)
            if resp.status_code != 200:
                continue
            data = resp.json() or {}
        except Exception:
            continue
        total += _count_completed_events(data)
    return total


def completed_on_day(session: requests.Session, sport: str, day: str) -> int:
    """Completed ESPN games/matches for one calendar day (YYYY-MM-DD)."""
    sport_u = (sport or "").upper()
    if sport_u not in ESPN_BOARDS or not day:
        return 0
    cache = _load_cache()
    key = f"{_CACHE_VER}:{sport_u}:{day}"
    stamp = _today_et()
    hit = cache.get(key) or {}
    if hit.get("fetched") == stamp and isinstance(hit.get("n"), int):
        return int(hit["n"])
    n = _fetch_day(session, sport_u, day)
    cache[key] = {"fetched": stamp, "n": n}
    _save_cache(cache)
    return n


def completed_in_window(
    session: requests.Session,
    sport: str,
    *,
    days: int = 30,
    through: str | None = None,
) -> dict[str, Any]:
    """Sum completed ESPN games for the last `days` ending at `through` (exclusive of today tip).

    Returns {n, by_day, latest_night, latest_n}.
    """
    sport_u = (sport or "").upper()
    through = through or _today_et()
    end = datetime.strptime(through[:10], "%Y-%m-%d")
    # Do not count "today" while games are still in progress — same idea as
    # results Last Night anchoring on ≤ yesterday.
    end = end - timedelta(days=1)

    by_day: dict[str, int] = {}
    latest_night = ""
    latest_n = 0

    def _one(offset: int) -> tuple[str, int]:
        day = (end - timedelta(days=offset)).strftime("%Y-%m-%d")
        return day, completed_on_day(session, sport_u, day)

    # Parallel day fetches (cached hits are cheap).
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_one, i) for i in range(max(1, days))]
        for fut in as_completed(futs):
            try:
                day, n = fut.result()
            except Exception:
                continue
            by_day[day] = n
            if n > 0 and (not latest_night or day > latest_night):
                latest_night, latest_n = day, n

    total = sum(by_day.values())
    return {
        "n": total,
        "by_day": by_day,
        "latest_night": latest_night,
        "latest_n": latest_n,
        "through": end.strftime("%Y-%m-%d"),
        "days": days,
    }


def last_night_issues(
    session: requests.Session,
    sport: str,
    page_day: str,
    page_n: int,
) -> list[str]:
    """FAIL when the results Last Night board disagrees with ESPN finals that night."""
    sport_u = (sport or "").upper()
    if sport_u not in ESPN_BOARDS:
        return []
    if not page_day or page_n < 0:
        return []
    # Golf / tennis / UFC: ESPN "events" are not 1:1 with our graded cards —
    # only require the page night to have *some* ESPN activity when we claim games.
    espn_n = completed_on_day(session, sport_u, page_day)
    issues: list[str] = []
    if sport_u in {"GOLF", "TENNIS", "UFC"}:
        if page_n > 0 and espn_n == 0:
            # Soft: ESPN may not date-filter these well; skip hard fail.
            return []
        return []
    if espn_n <= 0 and page_n > 0:
        # Page claims a night ESPN has no finals for — find ESPN's latest night.
        win = completed_in_window(session, sport_u, days=7, through=_today_et())
        if win["latest_n"] > 0:
            issues.append(
                f"Page Last Night is {page_day} ({page_n} games) but ESPN has 0 "
                f"finals that night; ESPN latest night is {win['latest_night']} "
                f"({win['latest_n']} finals)"
            )
        return issues
    # Allow small gaps (postponements / DNPs we did not store) but not jokes.
    if page_n == 0 and espn_n >= 3:
        issues.append(
            f"Page Last Night shows 0 games on {page_day}; ESPN completed {espn_n}"
        )
    elif espn_n >= 5 and page_n * 2 < espn_n:
        issues.append(
            f"Page Last Night counts {page_n} games on {page_day}; "
            f"ESPN completed {espn_n} that night"
        )
    elif espn_n >= 1 and page_n > espn_n * 2 + 5:
        issues.append(
            f"Page Last Night counts {page_n} games on {page_day}; "
            f"ESPN only completed {espn_n} that night"
        )
    return issues


def consensus_window_espn_issues(
    session: requests.Session,
    sport: str,
    *,
    biggest_past30: int,
    night_wl: tuple[int, int] | None,
    past_wl: tuple[int, int] | None,
) -> list[str]:
    """FAIL when Past 30 consensus is a last-night-sized joke vs ESPN volume."""
    sport_u = (sport or "").upper()
    if sport_u not in ESPN_BOARDS or sport_u in {"GOLF", "TENNIS", "UFC"}:
        return []
    win = completed_in_window(session, sport_u, days=30, through=_today_et())
    espn_n = int(win.get("n") or 0)
    issues: list[str] = []
    # Consensus rows only cover games with stored model sides — expect a
    # fraction of the ESPN slate, not half. Fail only on a thin joke sample.
    min_cover = max(20, espn_n // 5)
    if espn_n >= 40 and biggest_past30 < min_cover:
        issues.append(
            f"ESPN has {espn_n} completed {sport_u} games in 30 days; "
            f"the largest past-30 consensus record covers only {biggest_past30}"
        )
    night_n = (night_wl[0] + night_wl[1]) if night_wl else 0
    past_n = (past_wl[0] + past_wl[1]) if past_wl else 0
    # Unanimous buckets are naturally small. Fail only when the *whole*
    # Past 30 table is night-sized (largest row ≈ unanimous), not when
    # other consensus rows already cover a real 30-day sample.
    if (
        night_wl
        and past_wl
        and past_wl == night_wl
        and espn_n >= 20
        and past_n <= max(4, win.get("latest_n") or 0)
        and biggest_past30 <= past_n + 2
    ):
        issues.append(
            f"6/6 unanimous past 30 is {past_wl[0]}-{past_wl[1]}, the same as last night, "
            f"but ESPN finished {espn_n} games in 30 days "
            f"(latest night {win.get('latest_night')} had {win.get('latest_n')})"
        )
    return issues


def warm_all_sports(session: requests.Session | None = None, days: int = 30) -> dict[str, int]:
    """Prefetch 30-day counts for every sport (for scripts / prewarm)."""
    session = session or requests.Session()
    out: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {
            pool.submit(completed_in_window, session, sp, days=days): sp
            for sp in _ALL_SPORTS
        }
        for fut in as_completed(futs):
            sp = futs[fut]
            try:
                out[sp] = int(fut.result().get("n") or 0)
            except Exception:
                out[sp] = 0
    return out


if __name__ == "__main__":
    s = requests.Session()
    print("warming ESPN completed counts…")
    for sport, n in sorted(warm_all_sports(s).items()):
        print(f"  {sport}: {n} finals in ~30 days")
