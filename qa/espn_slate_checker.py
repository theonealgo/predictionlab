"""Today's pick slates vs ESPN scoreboard — fail wrong opponents.

Compares every sport's today (ET) picks to ESPN. Catches a page that
still has Toronto vs Kansas City when ESPN has Toronto vs Athletics.
Soccer is checked per in-catalog league that ESPN has today. Tennis
reads ESPN tournament groupings. Golf checks the current PGA event name.
Does not edit sport UIs.
"""
from __future__ import annotations

import argparse
import html as html_lib
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests

_QA = Path(__file__).resolve().parent
_ROOT = _QA.parent
for _p in (str(_QA), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

ET = ZoneInfo("America/New_York")

# Sport → (picks path, ESPN scoreboard URL, extra query)
SLATES = (
    ("MLB", "/mlb-picks", "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard", ""),
    ("NFL", "/nfl-picks", "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard", ""),
    ("NBA", "/nba-picks", "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard", ""),
    ("NHL", "/nhl-picks", "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard", ""),
    ("WNBA", "/wnba-picks", "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard", ""),
    ("NCAAF", "/ncaaf-picks", "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard", "limit=300"),
    ("NCAAB", "/ncaab-picks", "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard", "limit=300"),
    ("NCAAW", "/ncaaw-picks", "https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard", "limit=300"),
    ("CFL", "/cfl-picks", "https://site.api.espn.com/apis/site/v2/sports/football/cfl/scoreboard", ""),
    ("Soccer", "/soccer-picks?region=all", "https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard", "limit=400"),
    ("Tennis", "/tennis-picks", "https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard", ""),
    ("UFC", "/ufc-picks", "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard", ""),
    ("Golf", "/golf-picks", "https://site.web.api.espn.com/apis/site/v2/sports/golf/pga/scoreboard", ""),
)

# Must list every ESPN game today (small slates). Wrong opponent always fails.
STRICT_COMPLETE = frozenset({"MLB", "NFL", "NBA", "NHL", "WNBA", "CFL"})
# ESPN has games today → the picks page must show at least one of them.
MUST_HAVE_TODAY = STRICT_COMPLETE | frozenset({"Tennis", "UFC", "NCAAF", "Soccer"})
_NICKNAME_SPORTS = frozenset({
    "NFL", "NBA", "NHL", "WNBA", "CFL", "TENNIS", "UFC",
})
_SOCCER_LEAGUE_CAP = 15

_STOP = frozenset({
    "the", "at", "fc", "cf", "sc", "ac", "afc", "cfc", "united", "city",
    "club", "de", "la", "el", "and", "of",
})

# City / nickname → one id so Toronto Blue Jays == Blue Jays == TOR.
_TEAM_ALIASES = {
    "toronto": "bluejays",
    "blue jays": "bluejays",
    "bluejays": "bluejays",
    "jays": "bluejays",
    "tor": "bluejays",
    "kansas city": "royals",
    "kansas city royals": "royals",
    "royals": "royals",
    "kc": "royals",
    "kcr": "royals",
    "athletics": "athletics",
    "oakland": "athletics",
    "oakland athletics": "athletics",
    "oakland a s": "athletics",
    "a s": "athletics",
    "ath": "athletics",
    "red sox": "redsox",
    "boston": "redsox",
    "yankees": "yankees",
    "new york yankees": "yankees",
    "mets": "mets",
    "new york mets": "mets",
    "cubs": "cubs",
    "white sox": "whitesox",
    "chi white sox": "whitesox",
    "guardians": "guardians",
    "cleveland": "guardians",
    "tigers": "tigers",
    "detroit": "tigers",
    "twins": "twins",
    "minnesota": "twins",
    "orioles": "orioles",
    "baltimore": "orioles",
    "rays": "rays",
    "tampa bay": "rays",
    "astros": "astros",
    "houston": "astros",
    "rangers": "rangers",
    "texas": "rangers",
    "angels": "angels",
    "los angeles angels": "angels",
    "mariners": "mariners",
    "seattle": "mariners",
    "dodgers": "dodgers",
    "los angeles dodgers": "dodgers",
    "giants": "giants",
    "san francisco": "giants",
    "padres": "padres",
    "san diego": "padres",
    "rockies": "rockies",
    "colorado": "rockies",
    "diamondbacks": "dbacks",
    "arizona": "dbacks",
    "braves": "braves",
    "atlanta": "braves",
    "phillies": "phillies",
    "philadelphia": "phillies",
    "nationals": "nationals",
    "washington": "nationals",
    "marlins": "marlins",
    "miami": "marlins",
    "pirates": "pirates",
    "pittsburgh": "pirates",
    "reds": "reds",
    "cincinnati": "reds",
    "brewers": "brewers",
    "milwaukee": "brewers",
    "cardinals": "cardinals",
    "st louis": "cardinals",
    "saint louis": "cardinals",
}


def today_et() -> str:
    return datetime.now(ET).date().isoformat()


def espn_date_param(day: str) -> str:
    return day.replace("-", "")


def _plain(name: str) -> str:
    s = html_lib.unescape(name or "").lower()
    s = s.replace("st.", "st").replace("'", "").replace("'", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_GENERIC_IDS = frozenset({
    "rangers", "united", "city", "athletic", "alianza", "sporting", "real",
    "giants", "jets",
})


def team_id(name: str, sport: str = "") -> str:
    """Stable id: MLB city aliases first, else last meaningful tokens."""
    plain = _plain(name)
    if not plain:
        return ""
    sport_u = (sport or "").upper()
    if sport_u == "MLB":
        if plain in _TEAM_ALIASES:
            return _TEAM_ALIASES[plain]
        words = [w for w in plain.split() if w not in _STOP]
        joined = " ".join(words)
        if joined in _TEAM_ALIASES:
            return _TEAM_ALIASES[joined]
        if len(words) >= 2:
            two = " ".join(words[:2])
            if two in _TEAM_ALIASES:
                return _TEAM_ALIASES[two]
            last2 = " ".join(words[-2:])
            if last2 in _TEAM_ALIASES:
                return _TEAM_ALIASES[last2]
        if words and words[-1] in _TEAM_ALIASES:
            return _TEAM_ALIASES[words[-1]]
        if words:
            return "".join(words[-2:] if len(words) >= 2 else words)
        return plain.replace(" ", "")
    words = [w for w in plain.split() if w not in _STOP]
    if not words:
        return plain.replace(" ", "")
    if sport_u in _NICKNAME_SPORTS:
        return words[-1]
    return "".join(words)


def _competitor_name(comp: dict) -> str:
    team = comp.get("team") or {}
    ath = comp.get("athlete") or {}
    return (
        team.get("displayName")
        or team.get("shortDisplayName")
        or ath.get("displayName")
        or comp.get("displayName")
        or ""
    )


def _et_day(iso: str) -> str:
    raw = (iso or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(ET).date().isoformat()
    except Exception:
        return raw[:10]


def _pairs_from_competitors(people: list) -> tuple[str, str] | None:
    if len(people) < 2:
        return None
    home = next((c for c in people if c.get("homeAway") == "home"), None)
    away = next((c for c in people if c.get("homeAway") == "away"), None)
    if not home or not away:
        home, away = people[0], people[1]
    hn, an = _competitor_name(home), _competitor_name(away)
    if hn and an:
        return (an, hn)
    return None


def espn_event_names(data: dict) -> list[str]:
    return [
        str(ev.get("name") or ev.get("shortName") or "").strip()
        for ev in (data.get("events") or [])
        if str(ev.get("name") or ev.get("shortName") or "").strip()
    ]


def espn_pairs_from_payload(data: dict, day: str | None = None) -> list[tuple[str, str]]:
    """(away, home) display names from an ESPN scoreboard JSON."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _keep(pair: tuple[str, str] | None, when: str = "") -> None:
        if not pair:
            return
        if day and when and when != day:
            return
        if pair in seen:
            return
        seen.add(pair)
        out.append(pair)

    for ev in data.get("events") or []:
        comps = list(ev.get("competitions") or [])
        for g in ev.get("groupings") or []:
            comps.extend(g.get("competitions") or [])
        grouped = bool(ev.get("groupings"))
        for comp in comps:
            when = _et_day(comp.get("date") or comp.get("startDate") or ev.get("date") or "")
            if grouped and day and when and when != day:
                continue
            _keep(
                _pairs_from_competitors(comp.get("competitors") or []),
                when if grouped else "",
            )
    return out


_ESPN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.espn.com/",
}


def _espn_get_json(session, url: str):
    urls = [url]
    if "site.api.espn.com" in url:
        urls.append(url.replace("://site.api.espn.com", "://site.web.api.espn.com", 1))
    last = None
    for try_url in urls:
        resp = session.get(try_url, timeout=20, headers=_ESPN_HEADERS)
        last = resp
        if resp.status_code == 200:
            return resp.json()
    last.raise_for_status()
    return {}


def fetch_espn_payload(session, url: str, day: str, extra: str = "", dated: bool = True) -> dict:
    if dated:
        q = f"dates={espn_date_param(day)}"
        if extra:
            q += f"&{extra}"
        full = f"{url}?{q}"
    else:
        full = f"{url}?{extra}" if extra else url
    return _espn_get_json(session, full)


def fetch_espn_pairs(session, url: str, day: str, extra: str = "") -> list[tuple[str, str]]:
    if "/tennis/" in url:
        pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for tour in ("atp", "wta"):
            tour_url = re.sub(r"/tennis/[^/]+/scoreboard", f"/tennis/{tour}/scoreboard", url)
            data = fetch_espn_payload(session, tour_url, day, extra)
            for pair in espn_pairs_from_payload(data, day):
                if pair not in seen:
                    seen.add(pair)
                    pairs.append(pair)
        return pairs
    data = fetch_espn_payload(session, url, day, extra)
    return espn_pairs_from_payload(data, day)


def _tag_attr(tag: str, name: str) -> str:
    m = re.search(rf'\b{re.escape(name)}="([^"]*)"', tag, flags=re.I)
    return html_lib.unescape((m.group(1) or "").strip()) if m else ""


def page_pairs_for_day(html: str, day: str, sport: str = "") -> list[tuple[str, str]]:
    """(away, home) from pick cards dated ``day``."""
    html = html or ""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(away: str, home: str) -> None:
        away, home = (away or "").strip(), (home or "").strip()
        if not away or not home:
            return
        key = (team_id(away, sport), team_id(home, sport))
        if not key[0] or not key[1] or key in seen:
            return
        seen.add(key)
        out.append((away, home))

    for m in re.finditer(r"<div\b[^>]*\bdata-pick-card\b[^>]*>", html, flags=re.I):
        tag = m.group(0)
        date = (_tag_attr(tag, "data-date") or "")[:10]
        if date != day:
            continue
        _add(
            _tag_attr(tag, "data-away-full") or _tag_attr(tag, "data-away"),
            _tag_attr(tag, "data-home-full") or _tag_attr(tag, "data-home"),
        )

    parts = re.split(r'id="date-(\d{4}-\d{2}-\d{2})"', html)
    it = iter(parts[1:])
    for date_key in it:
        body = next(it, "")
        if date_key != day:
            continue
        for a, h in re.findall(
            r'data-away="([^"]+)"[\s\S]{0,400}?data-home="([^"]+)"',
            body,
        ):
            _add(a, h)
        for h, a in re.findall(
            r'data-home="([^"]+)"[\s\S]{0,400}?data-away="([^"]+)"',
            body,
        ):
            _add(a, h)

    return out


def slate_mismatch_issues(
    espn_pairs: list[tuple[str, str]],
    page_pairs: list[tuple[str, str]],
    *,
    sport: str,
) -> list[str]:
    """Wrong opponent / extra game / (strict sports) missing ESPN game."""
    espn_opp: dict[str, str] = {}
    espn_label: dict[str, str] = {}
    for away, home in espn_pairs:
        aid, hid = team_id(away, sport), team_id(home, sport)
        if not aid or not hid:
            continue
        espn_opp[aid] = hid
        espn_opp[hid] = aid
        espn_label[aid] = away
        espn_label[hid] = home

    issues: list[str] = []
    page_ids: set[str] = set()
    for away, home in page_pairs:
        aid, hid = team_id(away, sport), team_id(home, sport)
        if not aid or not hid:
            continue
        page_ids.add(aid)
        page_ids.add(hid)
        if sport.upper() == "SOCCER" and (aid in _GENERIC_IDS or hid in _GENERIC_IDS):
            continue
        if aid in espn_opp and espn_opp[aid] != hid:
            issues.append(
                f"{away} is on {home} on the page; ESPN has "
                f"{espn_label.get(aid, away)} vs {espn_label.get(espn_opp[aid], espn_opp[aid])}"
            )
        elif hid in espn_opp and espn_opp[hid] != aid:
            issues.append(
                f"{home} is on {away} on the page; ESPN has "
                f"{espn_label.get(hid, home)} vs {espn_label.get(espn_opp[hid], espn_opp[hid])}"
            )
        elif (
            sport in STRICT_COMPLETE
            and aid not in espn_opp
            and hid not in espn_opp
        ):
            issues.append(f"{away} vs {home} is on the page but not on ESPN today")

    if espn_pairs and not page_pairs and sport in MUST_HAVE_TODAY:
        issues.append(
            f"ESPN has {len(espn_pairs)} game(s) today; the page has none"
        )
    elif sport in STRICT_COMPLETE and espn_pairs:
        missing = []
        for away, home in espn_pairs:
            aid, hid = team_id(away, sport), team_id(home, sport)
            if aid and hid and aid not in page_ids and hid not in page_ids:
                missing.append(f"{away} vs {home}")
        if missing:
            issues.append(
                "ESPN today missing from the page: " + "; ".join(missing[:6])
            )
    return issues


def soccer_league_games_today(events: list) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """Catalog leagues with ESPN games today: (label, espn_slug, pairs)."""
    try:
        from soccer_league_catalog import (
            SOCCER_LEAGUE_ENDPOINTS,
            SOCCER_LEAGUE_NUMERIC_IDS,
        )
    except Exception:
        return []
    by_id: dict[str, list[tuple[str, str]]] = {}
    for ev in events or []:
        uid = str(ev.get("uid") or "")
        m = re.search(r"~l:(\d+)", uid)
        if not m:
            continue
        lid = m.group(1)
        comps = ev.get("competitions") or [{}]
        pair = _pairs_from_competitors((comps[0] or {}).get("competitors") or [])
        if pair:
            by_id.setdefault(lid, []).append(pair)
    out: list[tuple[str, str, list[tuple[str, str]]]] = []
    for lid, pairs in by_id.items():
        name = SOCCER_LEAGUE_NUMERIC_IDS.get(lid)
        slug = SOCCER_LEAGUE_ENDPOINTS.get(name) if name else None
        if name and slug:
            out.append((name, slug, pairs))
    out.sort(key=lambda row: -len(row[2]))
    return out[:_SOCCER_LEAGUE_CAP]


def golf_name_issues(event_names: list[str], html: str) -> list[str]:
    page = (html or "").lower()
    missing = [n for n in event_names if n.lower() not in page]
    if event_names and missing == event_names:
        return [
            "ESPN golf event not on the page: " + ", ".join(event_names[:3])
        ]
    return []


class EspnSlateChecker:
    NAME = "espn_slate"

    def __init__(self, session, base: str, report, CheckResult):
        self.s = session
        self.base = base.rstrip("/")
        self.r = report
        self.CheckResult = CheckResult
        self.timeout = int(os.environ.get("AUDIT_TIMEOUT", "90"))
        self.day = today_et()

    def add(self, label, status, message, url="", detail=""):
        self.r.add(
            self.CheckResult(
                label=label,
                status=status,
                message=message,
                detail=detail,
                url=url,
                auditor=self.NAME,
            )
        )

    def fetch_page(self, path: str):
        url = urljoin(self.base + "/", path.lstrip("/"))
        try:
            resp = self.s.get(url, timeout=self.timeout, allow_redirects=True)
            return resp.status_code, resp.text or "", url
        except Exception as exc:
            return 0, "", url + f" ({exc})"

    def _report_pairs(self, sport: str, url: str, espn, page, extra=""):
        issues = slate_mismatch_issues(espn, page, sport=sport)
        if issues:
            self.add(
                f"{sport} today vs ESPN",
                FAIL,
                "; ".join(issues[:4])
                + (f" (+{len(issues) - 4} more)" if len(issues) > 4 else ""),
                url=url,
                detail=f"espn={len(espn)} page={len(page)} day={self.day} {extra}".strip(),
            )
        else:
            self.add(
                f"{sport} today vs ESPN",
                PASS,
                f"{len(page)} page game(s) match ESPN ({len(espn)} ESPN game(s))",
                url=url,
            )

    def _check_golf(self, espn_s, path, api, extra):
        st, html, url = self.fetch_page(path)
        if st != 200:
            self.add(
                "Golf today vs ESPN",
                FAIL,
                f"{path} won't open ({st}) — cannot compare today's slate",
                url=url,
            )
            return
        try:
            data = fetch_espn_payload(espn_s, api, self.day, extra)
            names = espn_event_names(data)
            if not names:
                data = fetch_espn_payload(espn_s, api, self.day, extra, dated=False)
                names = espn_event_names(data)
        except Exception as exc:
            self.add("Golf today vs ESPN", FAIL, f"ESPN scoreboard failed ({exc})", url=api)
            return
        issues = golf_name_issues(names, html)
        if not names:
            self.add(
                "Golf today vs ESPN",
                PASS,
                "ESPN has no current PGA event",
                url=url,
            )
        elif issues:
            self.add("Golf today vs ESPN", FAIL, issues[0], url=url)
        else:
            self.add(
                "Golf today vs ESPN",
                PASS,
                f"Page has ESPN event ({names[0]})",
                url=url,
            )

    def _check_soccer(self, espn_s, path, api, extra):
        try:
            data = fetch_espn_payload(espn_s, api, self.day, extra)
            events = data.get("events") or []
            leagues = soccer_league_games_today(events)
        except Exception as exc:
            self.add(
                "Soccer today vs ESPN",
                FAIL,
                f"ESPN scoreboard failed ({exc})",
                url=api,
            )
            return
        if not leagues:
            st, html, url = self.fetch_page(path)
            page = page_pairs_for_day(html, self.day, "Soccer") if st == 200 else []
            if st != 200:
                self.add(
                    "Soccer today vs ESPN",
                    FAIL,
                    f"{path} won't open ({st})",
                    url=url,
                )
            elif not page:
                self.add(
                    "Soccer today vs ESPN",
                    PASS,
                    f"ESPN and the page both have no catalog-league {self.day} games",
                    url=url,
                )
            else:
                self._report_pairs("Soccer", url, [], page)
            return
        issues: list[str] = []
        checked = 0

        def _one(row):
            name, slug, espn_pairs = row
            st, html, url = self.fetch_page(f"/soccer-picks?league={slug}")
            return name, slug, espn_pairs, st, html, url

        rows = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(_one, row) for row in leagues]
            for fut in as_completed(futs):
                rows.append(fut.result())
        for name, slug, espn_pairs, st, html, url in rows:
            if st != 200:
                issues.append(f"{name}: page won't open ({st})")
                continue
            page = page_pairs_for_day(html, self.day, "Soccer")
            off = "is in the off-season" in (html or "")
            if off and not page:
                continue
            league_issues = slate_mismatch_issues(
                espn_pairs, page, sport="Soccer"
            )
            if league_issues:
                issues.append(f"{name}: " + league_issues[0])
            checked += 1
        if issues:
            self.add(
                "Soccer today vs ESPN",
                FAIL,
                "; ".join(issues[:4])
                + (f" (+{len(issues) - 4} more)" if len(issues) > 4 else ""),
                url=urljoin(self.base + "/", "soccer-picks"),
                detail=f"leagues={checked} day={self.day}",
            )
        else:
            self.add(
                "Soccer today vs ESPN",
                PASS,
                f"{checked} in-catalog league(s) with ESPN games today match the pages",
                url=urljoin(self.base + "/", "soccer-picks"),
            )

    def run(self):
        print(f"  ESPN slate checker: today's games ({self.day} ET) vs ESPN scoreboard")
        espn_s = requests.Session()
        espn_s.headers.update(_ESPN_HEADERS)
        for sport, path, api, extra in SLATES:
            if sport == "Golf":
                self._check_golf(espn_s, path, api, extra)
                continue
            if sport == "Soccer":
                self._check_soccer(espn_s, path, api, extra)
                continue
            st, html, url = self.fetch_page(path)
            if st != 200:
                self.add(
                    f"{sport} today vs ESPN",
                    FAIL,
                    f"{path} won't open ({st}) — cannot compare today's slate",
                    url=url,
                )
                continue
            try:
                espn = fetch_espn_pairs(espn_s, api, self.day, extra)
            except Exception as exc:
                self.add(
                    f"{sport} today vs ESPN",
                    FAIL,
                    f"ESPN scoreboard failed ({exc})",
                    url=api,
                )
                continue
            page = page_pairs_for_day(html, self.day, sport)
            off = "is in the off-season" in (html or "")
            if not espn and not page:
                self.add(
                    f"{sport} today vs ESPN",
                    PASS,
                    f"ESPN and the page both have no {self.day} games",
                    url=url,
                )
                continue
            if not espn and page and off:
                self.add(
                    f"{sport} today vs ESPN",
                    PASS,
                    "Off-season page; ESPN has no games today",
                    url=url,
                )
                continue
            self._report_pairs(sport, url, espn, page)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Today's slates vs ESPN")
    parser.add_argument(
        "--url",
        default=os.environ.get("AUDIT_BASE_URL", "http://127.0.0.1:5052"),
    )
    args = parser.parse_args(argv)

    class _R:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)
            mark = {"PASS": "OK", "FAIL": "FAIL", "WARN": "WARN"}.get(
                row.status, row.status
            )
            print(f"  [{mark}] {row.label}: {row.message}")

    class _C:
        def __init__(self, label, status, message, detail="", url="", auditor="espn_slate"):
            self.label = label
            self.status = status
            self.message = message
            self.detail = detail
            self.url = url
            self.auditor = auditor

    session = requests.Session()
    session.headers["User-Agent"] = "predictionlab-espn-slate-checker"
    report = _R()
    EspnSlateChecker(session, args.url, report, _C).run()
    fails = sum(1 for r in report.rows if r.status == FAIL)
    print(f"\nESPN slate checker: {len(report.rows) - fails} pass / {fails} fail")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
