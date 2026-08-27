"""Fetch CFL schedule/results — official scoreboard + ESPN fallback. Never invent NFL."""
from __future__ import annotations

import gzip
import json
import re
import ssl
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CACHE_DIR = Path(__file__).resolve().parents[1] / "database" / "cache"

TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/cfl/teams"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/cfl/scoreboard"
OFFICIAL_ROUNDS_URL = "https://cflscoreboard.cfl.ca/json/scoreboard/rounds.json"

_NFL_DENY = {
    "panthers", "patriots", "cowboys", "packers", "chiefs", "bills", "ravens",
    "49ers", "eagles", "steelers", "dolphins", "jets", "giants", "bears",
    "vikings", "browns", "bengals", "titans", "colts", "jaguars", "texans",
    "broncos", "raiders", "chargers", "rams", "seahawks", "cardinals",
    "saints", "buccaneers", "falcons", "commanders",
}

CFL_TEAMS_FALLBACK = [
    ("cgy", "Calgary Stampeders", "CGY"),
    ("edm", "Edmonton Elks", "EDM"),
    ("ssk", "Saskatchewan Roughriders", "SSK"),
    ("wpg", "Winnipeg Blue Bombers", "WPG"),
    ("bc", "BC Lions", "BC"),
    ("ham", "Hamilton Tiger-Cats", "HAM"),
    ("tor", "Toronto Argonauts", "TOR"),
    ("mtl", "Montreal Alouettes", "MTL"),
    ("ott", "Ottawa RedBlacks", "OTT"),
]


def _decode_body(raw: bytes) -> bytes:
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


def _http_json(url: str, *, timeout: float = 20.0, ssl_unverified: bool = False) -> Any | None:
    try:
        req = Request(
            url,
            headers={
                "User-Agent": "predictionlab-cfl-sandbox/1.0",
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )
        kwargs: dict[str, Any] = {"timeout": timeout}
        if ssl_unverified:
            kwargs["context"] = ssl._create_unverified_context()
        with urlopen(req, **kwargs) as resp:
            return json.loads(_decode_body(resp.read()).decode())
    except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _http_text(url: str, *, timeout: float = 30.0) -> str | None:
    try:
        req = Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Encoding": "gzip",
            },
        )
        with urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as resp:
            return _decode_body(resp.read()).decode("utf-8", "replace")
    except (URLError, HTTPError, TimeoutError, OSError):
        return None


def _cache_get(key: str, max_age_s: float = 900.0) -> Any | None:
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


def _looks_like_nfl(name: str | None) -> bool:
    if not name:
        return False
    low = name.lower().strip()
    if "bc lions" in low or "british columbia" in low:
        return False
    if re.search(r"\bdetroit lions\b", low):
        return True
    for token in _NFL_DENY:
        if re.search(rf"\b{re.escape(token)}\b", low):
            return True
    return False


def _event_year(date_s: str | None) -> int | None:
    if not date_s:
        return None
    y = str(date_s)[:4]
    return int(y) if y.isdigit() else None


def is_regular_season_round(round_name: str | None) -> bool:
    """True for CFL.ca regular-season rounds ('Week 1' … 'Week 21').

    Preseason Week N, Grey Cup, and conference finals are excluded.
    """
    rn = str(round_name or "").strip().lower()
    return bool(re.fullmatch(r"week\s+\d+", rn))


def cfl_season_year(now: datetime | None = None) -> int:
    """Calendar year of the current CFL season (May–Nov). Jan–Apr → prior year."""
    et = ZoneInfo("America/New_York")
    if now is None:
        now = datetime.now(et)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=et)
    else:
        now = now.astimezone(et)
    return now.year if now.month >= 5 else now.year - 1


_TEAM_ABBR = {
    "BC": "BC Lions",
    "CGY": "Calgary Stampeders",
    "EDM": "Edmonton Elks",
    "ESK": "Edmonton Elks",
    "HAM": "Hamilton Tiger-Cats",
    "MTL": "Montreal Alouettes",
    "OTT": "Ottawa RedBlacks",
    "SSK": "Saskatchewan Roughriders",
    "TOR": "Toronto Argonauts",
    "WPG": "Winnipeg Blue Bombers",
}

_CFL_NAME_ALIASES = {
    "ottawa redblacks": "Ottawa RedBlacks",
    "ottawa red blacks": "Ottawa RedBlacks",
    "b.c. lions": "BC Lions",
    "bc lions": "BC Lions",
    "edmonton eskimos": "Edmonton Elks",
    "edmonton elks": "Edmonton Elks",
}


def normalize_team(name: str | None) -> str:
    if not name:
        return ""
    n = name.strip()
    if n.upper() in _TEAM_ABBR:
        return _TEAM_ABBR[n.upper()]
    return _CFL_NAME_ALIASES.get(n.lower(), n)


def team_name_variants(name: str | None) -> tuple[str, ...]:
    """Canonical name plus historical aliases for H2H matching."""
    canon = normalize_team(name)
    if not canon:
        return tuple()
    extras = {
        "Ottawa RedBlacks": ("Ottawa RedBlacks", "Ottawa Redblacks", "Ottawa Red Blacks"),
        "BC Lions": ("BC Lions", "B.C. Lions"),
        "Edmonton Elks": ("Edmonton Elks", "Edmonton Eskimos"),
        "Hamilton Tiger-Cats": ("Hamilton Tiger-Cats", "Hamilton Tiger Cats"),
    }
    out = extras.get(canon, (canon,))
    if canon not in out:
        out = (canon,) + tuple(x for x in out if x != canon)
    return out


def fetch_cfl_ca_season_games(year: int, *, use_cache: bool = True) -> list[dict[str, Any]]:
    """Parse CFL.ca /schedule/{year}/ into completed regular-season-style games.

    Official rounds.json is current-season only. Prior years are needed for H2H.
    May games are preseason and are skipped. Does not invent NFL.
    """
    cache_key = f"cfl_ca_schedule_{int(year)}"
    cached = _cache_get(cache_key, max_age_s=7 * 24 * 3600) if use_cache else None
    if isinstance(cached, list):
        return cached
    html = _http_text(f"https://www.cfl.ca/schedule/{int(year)}/")
    if not html:
        return []
    found: list[dict[str, Any]] = []
    for m in re.finditer(
        r'id="ad-schedule-game-(\d{4}-\d{2}-\d{2})-([a-z0-9]+)-([a-z0-9]+)"'
        r'[\s\S]{0,2500}?'
        r'<span class="visitor-score">\s*([^<]*?)\s*</span>'
        r'[\s\S]{0,400}?'
        r'<span class="host-score">\s*([^<]*?)\s*</span>',
        html,
        flags=re.I,
    ):
        date_s, vis, host, vs, hs = m.groups()
        vis_u, host_u = vis.upper(), host.upper()
        away = normalize_team(_TEAM_ABBR.get(vis_u, vis_u))
        home = normalize_team(_TEAM_ABBR.get(host_u, host_u))
        if not home or not away or _looks_like_nfl(home) or _looks_like_nfl(away):
            continue
        try:
            month = int(date_s[5:7])
        except (TypeError, ValueError):
            continue
        # CFL preseason is May; regular season starts in June.
        if month < 6:
            continue
        vs, hs = vs.strip(), hs.strip()
        try:
            away_score = int(vs) if vs.isdigit() else None
            home_score = int(hs) if hs.isdigit() else None
        except (TypeError, ValueError):
            away_score = home_score = None
        if away_score is None or home_score is None:
            continue
        gid = f"cfl_h2h_{date_s}_{vis_u}_{host_u}"
        found.append(
            {
                "game_id": gid,
                "cfl_id": None,
                "game_date": f"{date_s}T00:00:00+00:00",
                "home_team": home,
                "away_team": away,
                "home_score": home_score,
                "away_score": away_score,
                "status": "complete",
                "round_name": "Week hist",
                "source": "h2h-history",
            }
        )
    if found:
        _cache_set(cache_key, found)
    return found


def fetch_h2h_history(*, years: tuple[int, ...] | None = None, use_cache: bool = True) -> list[dict[str, Any]]:
    """Prior regular-season meetings for H2H L10 (not used for Elo / live picks)."""
    season = cfl_season_year()
    if years is None:
        years = tuple(y for y in range(season - 4, season) if y >= 2022)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for year in years:
        for g in fetch_cfl_ca_season_games(year, use_cache=use_cache):
            gid = str(g.get("game_id") or "")
            if not gid or gid in seen:
                continue
            seen.add(gid)
            out.append(g)
    return out


def _parse_american_odds(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    s = str(raw).strip().replace("\u2212", "-").replace("\u2013", "-").replace("+", "")
    try:
        n = int(round(float(s)))
    except (TypeError, ValueError):
        return None
    if n == 0:
        return None
    return n


def _parse_line(raw: Any) -> float | None:
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    # CFL game totals live roughly 40–80; reject junk / soccer-scale numbers.
    if n < 30.0 or n > 90.0:
        return None
    return n


def _parse_spread_line(raw: Any) -> float | None:
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    if abs(n) > 40.0:
        return None
    return n


def _decimal_or_american(side: dict[str, Any] | None) -> int | None:
    """Scorebet/BCLC use decimal `odds`; some rows use `american`. Never invent."""
    if not isinstance(side, dict):
        return None
    am = _parse_american_odds(side.get("american"))
    if am is not None:
        return am
    try:
        d = float(side.get("odds"))
    except (TypeError, ValueError):
        return None
    if d <= 1.0 or d > 100.0:
        return None
    if d >= 2.0:
        return int(round((d - 1.0) * 100.0))
    return int(round(-100.0 / (d - 1.0)))


def _avg_half(vals: list[float]) -> float:
    return round((sum(vals) / len(vals)) * 2.0) / 2.0


def extract_book_total(tournament: dict[str, Any] | None) -> dict[str, Any]:
    """Back-compat alias — totals plus ML/spread when CFL.ca posts them."""
    return extract_book_markets(tournament)


def extract_book_markets(tournament: dict[str, Any] | None) -> dict[str, Any]:
    """Scorebet + BCLC moneyline, spread, and total. Empty when books are missing."""
    if not isinstance(tournament, dict):
        return {}
    totals: list[float] = []
    over_odds: list[int] = []
    under_odds: list[int] = []
    spreads: list[float] = []
    home_ml: list[int] = []
    away_ml: list[int] = []
    for key in ("marketsScorebet", "marketsBCLC"):
        block = tournament.get(key)
        if not isinstance(block, dict):
            continue
        total = block.get("total")
        if isinstance(total, dict):
            over = total.get("over") if isinstance(total.get("over"), dict) else {}
            under = total.get("under") if isinstance(total.get("under"), dict) else {}
            line = _parse_line(over.get("line") if over.get("line") is not None else under.get("line"))
            if line is not None:
                totals.append(line)
                oa = _decimal_or_american(over)
                ua = _decimal_or_american(under)
                if oa is not None:
                    over_odds.append(oa)
                if ua is not None:
                    under_odds.append(ua)
        spread = block.get("spread")
        if isinstance(spread, dict):
            home = spread.get("home") if isinstance(spread.get("home"), dict) else {}
            sp = _parse_spread_line(home.get("line") if home else None)
            if sp is None:
                away = spread.get("away") if isinstance(spread.get("away"), dict) else {}
                away_sp = _parse_spread_line(away.get("line") if away else None)
                if away_sp is not None:
                    sp = -away_sp
            if sp is not None:
                spreads.append(sp)
        ml = block.get("moneyline")
        if isinstance(ml, dict):
            hm = _decimal_or_american(ml.get("home") if isinstance(ml.get("home"), dict) else None)
            aml = _decimal_or_american(ml.get("away") if isinstance(ml.get("away"), dict) else None)
            if hm is not None:
                home_ml.append(hm)
            if aml is not None:
                away_ml.append(aml)
    out: dict[str, Any] = {}
    if totals:
        out["book_total"] = _avg_half(totals)
        if over_odds:
            out["book_over_odds"] = int(round(sum(over_odds) / len(over_odds)))
        if under_odds:
            out["book_under_odds"] = int(round(sum(under_odds) / len(under_odds)))
    if spreads:
        out["book_spread"] = _avg_half(spreads)
    if home_ml:
        out["book_home_moneyline"] = int(round(sum(home_ml) / len(home_ml)))
    if away_ml:
        out["book_away_moneyline"] = int(round(sum(away_ml) / len(away_ml)))
    return out


def fetch_official_all_games(*, use_cache: bool = True) -> list[dict[str, Any]]:
    cached = _cache_get("official_rounds") if use_cache else None
    data = cached if isinstance(cached, list) else _http_json(OFFICIAL_ROUNDS_URL, ssl_unverified=True)
    if isinstance(data, list) and data and cached is None:
        _cache_set("official_rounds", data)
    if not isinstance(data, list):
        return []
    found: list[dict[str, Any]] = []
    for rnd in data:
        round_name = rnd.get("name")
        for g in rnd.get("tournaments") or []:
            home_sq = g.get("homeSquad") or {}
            away_sq = g.get("awaySquad") or {}
            home = normalize_team(home_sq.get("name"))
            away = normalize_team(away_sq.get("name"))
            if not home or not away:
                continue
            if home.upper() == "TBD" or away.upper() == "TBD":
                continue
            if _looks_like_nfl(home) or _looks_like_nfl(away):
                continue
            year = _event_year(g.get("date"))
            if year is not None and year < 2024:
                continue
            if not home or not away:
                continue
            status = (g.get("status") or "scheduled").lower()
            hs = home_sq.get("score")
            as_ = away_sq.get("score")
            try:
                home_score = int(hs) if hs is not None else None
                away_score = int(as_) if as_ is not None else None
            except (TypeError, ValueError):
                home_score = away_score = None
            # Official feed often zeros scores for unplayed games
            if status not in {"complete", "final", "closed"} and home_score == 0 and away_score == 0:
                home_score = away_score = None
            gid = str(g.get("cflId") or g.get("id") or f"{away}@{home}|{g.get('date')}")
            row: dict[str, Any] = {
                "game_id": f"cfl_{gid}",
                "cfl_id": g.get("cflId") or g.get("id"),
                "game_date": g.get("date"),
                "home_team": home,
                "away_team": away,
                "home_score": home_score,
                "away_score": away_score,
                "status": status,
                "round_name": round_name,
                "source": "cfl-official",
            }
            row.update(extract_book_total(g))
            found.append(row)
    found.sort(key=lambda r: r.get("game_date") or "")
    return found


def fetch_espn_teams() -> list[tuple[str, str, str]]:
    data = _http_json(TEAMS_URL, ssl_unverified=True)
    out: list[tuple[str, str, str]] = []
    if not isinstance(data, dict):
        return list(CFL_TEAMS_FALLBACK)
    for sport in data.get("sports") or []:
        for league in sport.get("leagues") or []:
            for row in league.get("teams") or []:
                team = row.get("team") or {}
                name = normalize_team(team.get("displayName") or team.get("name"))
                if not name or _looks_like_nfl(name):
                    continue
                tid = str(team.get("id") or name.lower().replace(" ", "_"))
                short = team.get("abbreviation") or team.get("shortDisplayName") or ""
                out.append((tid, name, short))
    return out or list(CFL_TEAMS_FALLBACK)


def games_in_window(
    games: list[dict[str, Any]],
    *,
    days_back: int = 3,
    days_fwd: int = 21,
) -> list[dict[str, Any]]:
    et = ZoneInfo("America/New_York")
    now = datetime.now(et)
    start = now - timedelta(days=days_back)
    end = now + timedelta(days=days_fwd)
    out: list[dict[str, Any]] = []
    for g in games:
        date_s = g.get("game_date")
        if not date_s:
            continue
        try:
            dt = datetime.fromisoformat(str(date_s).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_et = dt.astimezone(et)
        except ValueError:
            continue
        if start <= dt_et <= end:
            out.append(g)
    return out
