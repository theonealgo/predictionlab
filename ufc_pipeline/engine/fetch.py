"""Fetch UFC schedule/results (ESPN) + MMA h2h odds (The Odds API)."""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CACHE_DIR = Path(__file__).resolve().parents[1] / "database" / "cache"
# site.api often 403s; site.web.api works from this host.
SCOREBOARD_URL = "https://site.web.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
ODDS_SPORT = "mma_mixed_martial_arts"


def _decode(raw: bytes) -> str:
    if raw[:2] == b"\x1f\x8b":
        import gzip

        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def _http_json(url: str, *, timeout: float = 25.0) -> Any | None:
    try:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "Referer": "https://www.espn.com/mma/",
            },
        )
        with urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as resp:
            return json.loads(_decode(resp.read()))
    except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError) as e:
        print(f"[ufc.fetch] HTTP fail {url[:80]}… {e}", flush=True)
        return None


def _cache_get(key: str, max_age_s: float) -> Any | None:
    path = CACHE_DIR / f"{key}.json"
    if not path.is_file():
        return None
    if time.time() - path.stat().st_mtime > max_age_s:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_set(key: str, data: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(json.dumps(data), encoding="utf-8")


def norm_fighter(name: str) -> str:
    # Fold accents so Charrière ↔ Charriere, Syguła ↔ Sygula, etc.
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    return re.sub(r"\s+", " ", s)


def fighter_match(a: str, b: str) -> bool:
    na, nb = norm_fighter(a), norm_fighter(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    ta, tb = na.split(), nb.split()
    if not ta or not tb:
        return False
    # last-name + first initial / first token overlap (Diego Ferreira ↔ Carlos Diego Ferreira)
    if ta[-1] == tb[-1] and (
        ta[0][:1] == tb[0][:1]
        or ta[0] in tb
        or tb[0] in ta
        or any(x in tb for x in ta[:-1])
        or any(x in ta for x in tb[:-1])
    ):
        return True
    # DelValle ↔ del Valle
    if "".join(ta) == "".join(tb):
        return True
    return False


def _athlete_name(comp: dict[str, Any]) -> str:
    ath = comp.get("athlete") or {}
    return str(ath.get("displayName") or ath.get("fullName") or comp.get("displayName") or "").strip()


def _athlete_id(comp: dict[str, Any]) -> str:
    ath = comp.get("athlete") or {}
    return str(ath.get("id") or comp.get("id") or "").strip()


def _record(comp: dict[str, Any]) -> str:
    recs = comp.get("records") or []
    if recs and isinstance(recs[0], dict):
        return str(recs[0].get("summary") or "")
    return ""


def _parse_competitions(payload: dict[str, Any] | None, *, source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not payload:
        return out
    now = datetime.now(timezone.utc).isoformat()
    for ev in payload.get("events") or []:
        event_id = str(ev.get("id") or "")
        event_name = str(ev.get("name") or "")
        for comp in ev.get("competitions") or []:
            comps = list(comp.get("competitors") or [])
            if len(comps) < 2:
                continue
            # ESPN MMA: order 1 ≈ "home" slot on PL cards, order 2 ≈ away
            comps_sorted = sorted(comps, key=lambda z: int(z.get("order") or 99))
            home_c, away_c = comps_sorted[0], comps_sorted[1]
            home = _athlete_name(home_c)
            away = _athlete_name(away_c)
            if not home or not away:
                continue
            st = (comp.get("status") or {}).get("type") or {}
            completed = bool(st.get("completed"))
            state = str(st.get("state") or "").lower()
            status = "complete" if completed else ("live" if state == "in" else "scheduled")
            winner = None
            if home_c.get("winner"):
                winner = home
            elif away_c.get("winner"):
                winner = away
            fight_id = str(comp.get("id") or f"{event_id}-{_athlete_id(home_c)}-{_athlete_id(away_c)}")
            out.append(
                {
                    "fight_id": fight_id,
                    "event_id": event_id,
                    "event_name": event_name,
                    "fight_date": str(comp.get("date") or ev.get("date") or ""),
                    "home_fighter": home,
                    "away_fighter": away,
                    "home_id": _athlete_id(home_c),
                    "away_id": _athlete_id(away_c),
                    "winner": winner,
                    "status": status,
                    "home_record": _record(home_c),
                    "away_record": _record(away_c),
                    "source": source,
                    "updated_at": now,
                }
            )
    return out


def fetch_espn_scoreboard(dates: str, *, use_cache: bool = True, max_age_s: float = 600.0) -> list[dict[str, Any]]:
    key = f"espn_{dates}"
    if use_cache:
        cached = _cache_get(key, max_age_s)
        if isinstance(cached, list):
            return cached
    url = f"{SCOREBOARD_URL}?dates={dates}"
    data = _http_json(url)
    fights = _parse_competitions(data if isinstance(data, dict) else None, source="espn")
    _cache_set(key, fights)
    return fights


def fetch_espn_history(*, days_back: int = 220, use_cache: bool = True) -> list[dict[str, Any]]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days_back)
    dates = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    return fetch_espn_scoreboard(dates, use_cache=use_cache, max_age_s=3600.0)


def fetch_espn_upcoming(*, days_ahead: int = 14, use_cache: bool = True) -> list[dict[str, Any]]:
    start = datetime.now(timezone.utc).date() - timedelta(days=1)
    end = start + timedelta(days=days_ahead)
    dates = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    fights = fetch_espn_scoreboard(dates, use_cache=use_cache, max_age_s=300.0)
    return [f for f in fights if f.get("status") in ("scheduled", "live")]


def _load_odds_api_key() -> str:
    key = os.environ.get("ODDS_API_KEY") or ""
    if key:
        return key
    try:
        from dotenv import load_dotenv

        for env_path in (
            Path.home() / "Documents/Personal/predictionlabfix_work/.env",
            Path(__file__).resolve().parents[2] / "predictionlabfix_work" / ".env",
        ):
            if env_path.is_file():
                load_dotenv(env_path)
                break
    except Exception:
        pass
    return os.environ.get("ODDS_API_KEY") or ""


def fetch_odds_events(*, use_cache: bool = True, max_age_s: float = 300.0) -> list[dict[str, Any]]:
    key = "odds_mma_h2h"
    exhausted_flag = CACHE_DIR / "odds_api_exhausted.json"

    def _stale() -> list[dict[str, Any]]:
        raw = _cache_get(key, max_age_s=7 * 24 * 3600.0)
        return raw if isinstance(raw, list) and raw else []

    if use_cache:
        cached = _cache_get(key, max_age_s)
        if isinstance(cached, list) and cached:
            return cached

    stale = _stale()
    if exhausted_flag.is_file() and (time.time() - exhausted_flag.stat().st_mtime) < 6 * 3600:
        if stale:
            print(f"[ufc.fetch] Odds API exhausted — stale cache ({len(stale)} events)", flush=True)
            return stale
        print("[ufc.fetch] Odds API exhausted — no stale cache", flush=True)
        return []

    api_key = _load_odds_api_key()
    if not api_key:
        print("[ufc.fetch] ODDS_API_KEY missing — books/implied probs unavailable", flush=True)
        return stale

    qs = urlencode(
        {
            "apiKey": api_key,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american",
        }
    )
    url = f"https://api.the-odds-api.com/v4/sports/{ODDS_SPORT}/odds?{qs}"
    data = None
    try:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )
        with urlopen(req, timeout=25.0, context=ssl._create_unverified_context()) as resp:
            data = json.loads(_decode(resp.read()))
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        print(f"[ufc.fetch] Odds API HTTP {e.code}: {body[:160]}", flush=True)
        if e.code in (401, 402, 429) or "OUT_OF_USAGE" in body or "quota" in body.lower():
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            exhausted_flag.write_text(
                json.dumps({"at": time.time(), "code": e.code, "body": body[:200]}),
                encoding="utf-8",
            )
        return stale
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        print(f"[ufc.fetch] Odds API fail: {e}", flush=True)
        return stale

    events: list[dict[str, Any]] = []
    if isinstance(data, list):
        try:
            if exhausted_flag.is_file():
                exhausted_flag.unlink()
        except OSError:
            pass
        for g in data:
            home = str(g.get("home_team") or "")
            away = str(g.get("away_team") or "")
            book_lines: list[tuple[int, int, str]] = []
            for book in g.get("bookmakers") or []:
                home_ml = away_ml = None
                for market in book.get("markets") or []:
                    if market.get("key") != "h2h":
                        continue
                    for outcome in market.get("outcomes") or []:
                        nm = str(outcome.get("name") or "")
                        price = outcome.get("price")
                        if price is None:
                            continue
                        try:
                            price_i = int(price)
                        except (TypeError, ValueError):
                            continue
                        if fighter_match(nm, home):
                            home_ml = price_i
                        elif fighter_match(nm, away):
                            away_ml = price_i
                if home_ml is not None and away_ml is not None:
                    book_lines.append(
                        (home_ml, away_ml, str(book.get("title") or book.get("key") or ""))
                    )
            if not book_lines:
                continue
            homes = sorted(x[0] for x in book_lines)
            aways = sorted(x[1] for x in book_lines)
            mid = len(homes) // 2
            events.append(
                {
                    "home": home,
                    "away": away,
                    "home_ml": homes[mid],
                    "away_ml": aways[mid],
                    "books_count": len(book_lines),
                    "books": [x[2] for x in book_lines],
                    "source": "TheOddsAPI",
                }
            )
    if not events:
        print("[ufc.fetch] Odds API events with ML: 0", flush=True)
        return stale
    print(f"[ufc.fetch] Odds API events with ML: {len(events)}", flush=True)
    _cache_set(key, events)
    return events


def lookup_odds(home: str, away: str, events: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    events = events if events is not None else fetch_odds_events()
    for ev in events:
        eh, ea = ev.get("home") or "", ev.get("away") or ""
        if fighter_match(home, eh) and fighter_match(away, ea):
            return {
                "home_ml": ev.get("home_ml"),
                "away_ml": ev.get("away_ml"),
                "books_count": ev.get("books_count") or 0,
                "source": ev.get("source"),
            }
        if fighter_match(home, ea) and fighter_match(away, eh):
            return {
                "home_ml": ev.get("away_ml"),
                "away_ml": ev.get("home_ml"),
                "books_count": ev.get("books_count") or 0,
                "source": ev.get("source"),
            }
    return None
