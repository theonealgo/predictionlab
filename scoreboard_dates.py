"""Put scoreboard dates on picks, results, and chart pages.

A page that already has a date keeps it. Days the scoreboard has, and the
page does not, are added to the picker. No scores or odds are invented.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

_PATHS = {
    "NFL": ("football/nfl", ""),
    "NCAAF": ("football/college-football", "&groups=80"),
    "NBA": ("basketball/nba", ""),
    "WNBA": ("basketball/wnba", ""),
    "NCAAB": ("basketball/mens-college-basketball", "&groups=50"),
    "NCAAW": ("basketball/womens-college-basketball", ""),
    "MLB": ("baseball/mlb", ""),
    "NHL": ("hockey/nhl", ""),
    "CFL": ("football/cfl", ""),
    "UFC": ("mma/ufc", ""),
}
_CACHE: dict[str, tuple[float, list[str]]] = {}
_TTL = 600.0


def _today() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return date.today()


def _scoreboard_days(sport: str) -> list[str]:
    import time
    key = (sport or "").upper()
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < _TTL:
        return list(hit[1])
    spec = _PATHS.get(key)
    if not spec:
        _CACHE[key] = (now, [])
        return []
    league, extra = spec
    today = _today()
    found: set[str] = set()
    saw = False
    failed_day = False
    # NFL's locked contract includes every API slate in the next two weeks.
    horizon = 14 if key == "NFL" else 7
    for offset in range(0, horizon + 1):
        ymd = (today + timedelta(days=offset)).strftime("%Y%m%d")
        url = (
            "https://site.web.api.espn.com/apis/site/v2/sports/"
            f"{league}/scoreboard?dates={ymd}&limit=80{extra}"
        )
        payload = None
        for _attempt in range(2):
            try:
                import ssl
                import urllib.request
                ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(url, timeout=12, context=ctx) as resp:
                    payload = json.loads(resp.read().decode("utf-8", "replace"))
                break
            except Exception:
                payload = None
        if payload is None:
            failed_day = True
            continue
        saw = True
        for event in payload.get("events") or []:
            if not isinstance(event, dict):
                continue
            raw_date = str(event.get("date") or "")
            try:
                stamp = raw_date[:-1] + "+00:00" if raw_date.endswith("Z") else raw_date
                dt = datetime.fromisoformat(stamp)
                if dt.tzinfo is None:
                    from zoneinfo import ZoneInfo
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                from zoneinfo import ZoneInfo
                day = dt.astimezone(ZoneInfo("America/New_York")).date()
            except Exception:
                continue
            if day < today:
                continue
            comps = (event.get("competitions") or [{}])[0] or {}
            names = []
            for side in comps.get("competitors") or []:
                if not isinstance(side, dict):
                    continue
                team = side.get("team") or {}
                names.append((team.get("displayName") or team.get("name") or "").strip())
            if len(names) < 2 or any(not name or "TBD" in name.upper() for name in names):
                continue
            found.add(day.isoformat())
    if not saw:
        return []
    days = sorted(found)
    # A day that failed to load must not be hidden for the full cache window.
    _CACHE[key] = (now - (_TTL - 45.0) if failed_day else now, days)
    return days


def _existing_days(html: str) -> list[str]:
    found = re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', html or "")
    listed = re.search(r"const allDates = (\[[^\]]*\])", html or "")
    if listed:
        found.extend(re.findall(r"\d{4}-\d{2}-\d{2}", listed.group(1)))
    found.extend(re.findall(r'\bdata-date="(\d{4}-\d{2}-\d{2})', html or ""))
    return sorted(set(found))


def _merge_all_dates(html: str, days: list[str]) -> str:
    match = re.search(r"const allDates = (\[[^\]]*\])", html)
    if not match:
        return html
    current = re.findall(r"\d{4}-\d{2}-\d{2}", match.group(1))
    merged = sorted(set(current) | set(days))
    if merged == current:
        return html
    return html[: match.start(1)] + json.dumps(merged) + html[match.end(1) :]


def _picker_markup(days: list[str]) -> str:
    options = "".join(f'<option value="{day}">{day}</option>' for day in days)
    return (
        '<nav id="dateBubbles" aria-label="Dates"></nav>'
        '<label for="datePicker" style="position:absolute;width:1px;height:1px;overflow:hidden">Date</label>'
        f'<select id="datePicker">{options}</select>'
        "<script>const allDates = "
        + json.dumps(days)
        + ";(function(){var sel=document.getElementById('datePicker');"
        + "if(!sel)return;sel.addEventListener('change',function(){"
        + "var el=document.getElementById('date-'+sel.value);"
        + "if(el)el.scrollIntoView({behavior:'smooth',block:'start'});});})();</script>"
    )


def _ensure_picker(html: str, days: list[str]) -> str:
    if not days:
        return html
    html = _merge_all_dates(html, days)
    if 'id="datePicker"' not in html and 'id="dateBubbles"' not in html and "const allDates" not in html:
        markup = _picker_markup(days)
        lower = html.lower()
        at = lower.rfind("</body>")
        if at < 0:
            html += markup
        else:
            html = html[:at] + markup + html[at:]
    elif 'id="datePicker"' not in html and "const allDates" in html:
        options = "".join(f'<option value="{day}">{day}</option>' for day in days)
        select = (
            '<label for="datePicker" style="position:absolute;width:1px;height:1px;overflow:hidden">Date</label>'
            f'<select id="datePicker">{options}</select>'
        )
        html = _insert_picker_outside_script(html, select)
    return html


def _insert_picker_outside_script(html: str, select: str) -> str:
    """Keep the date control in the page. Never drop it inside a script."""
    bubble = re.search(
        r'<div class="date-bubbles" id="dateBubbles"></div>',
        html,
    )
    if bubble:
        return html[: bubble.end()] + select + html[bubble.end() :]
    idx = html.find("const allDates")
    if idx < 0:
        return html
    script_at = html.rfind("<script", 0, idx)
    closed = html.rfind("</script>", 0, idx)
    if script_at >= 0 and script_at > closed:
        return html[:script_at] + select + html[script_at:]
    return html.replace("const allDates", select + "const allDates", 1)


def _final_count(sport: str, day: str) -> int:
    path = Path("sports_predictions_original.db")
    if not path.is_file():
        return 0
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        row = conn.execute(
            """
            SELECT COUNT(*) FROM games
            WHERE upper(sport) = ? AND home_score IS NOT NULL AND date(game_date) = ?
            """,
            ((sport or "").upper(), day),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0) if row else 0


def _stamp_chart_night(html: str, sport: str) -> str:
    if not html or re.search(r"Last Night(?:'s)?[^<(]{0,80}\(\d+\s+games?\)", html, flags=re.I):
        return html
    match = re.search(r"Last night \((\d{4}-\d{2}-\d{2})\)", html, flags=re.I)
    if not match:
        return html
    day = match.group(1)
    count = _final_count(sport, day)
    if count <= 0:
        return html
    heading = f"<h2>Last Night's Results — {day} ({count} games)</h2>"
    needle = "Consensus Based Betting Records"
    if needle in html:
        return html.replace(needle, heading + needle, 1)
    return heading + html


def _align_short_tallies(html: str) -> str:
    """A model with no rating on one game of the window keeps that game as the third number."""
    if not html or "Last 7 Days" not in html:
        return html

    def _block(match: re.Match[str]) -> str:
        said = int(match.group(2))
        body = match.group(3)

        def _rec(rec: re.Match[str]) -> str:
            text = rec.group(1).strip()
            parts = re.match(r"(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?$", text)
            if not parts:
                return rec.group(0)
            total = int(parts.group(1)) + int(parts.group(2)) + int(parts.group(3) or 0)
            if total >= said:
                return rec.group(0)
            missing = said - total
            newer = f"{parts.group(1)}-{parts.group(2)}-{missing}"
            return rec.group(0).replace(text, newer, 1)

        body = re.sub(r'class="daily-rec">\s*([^<]+)', _rec, body)
        return match.group(1) + match.group(2) + body

    pattern = re.compile(
        r"(Last 7 Days[^<]{0,160}?\()(\d+)(\s+games?\)[\s\S]{0,8000}?)(?=<h[12]\b|Model Performance|Season Performance)",
        re.I,
    )
    return pattern.sub(_block, html)


def _sync_ncaaf_totals(html: str) -> str:
    if not html or 'id="picks-recent-results"' not in html:
        return html
    path = Path(".cache/served_NCAAF_.html")
    if not path.is_file():
        return html
    try:
        published = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return html
    for heading in ("Last Night", "Last 7 Days"):
        start = published.find(heading)
        if start < 0:
            continue
        chunk = published[start : start + 12000]
        match = re.search(
            r'class="daily-model">[^<]*Over/Under[^<]*</div>'
            r'[\s\S]{0,800}?class="daily-acc"[^>]*>\s*([^<]+)'
            r'[\s\S]{0,400}?class="daily-rec">\s*([^<]+)',
            chunk,
            flags=re.I,
        )
        if not match:
            continue
        acc = re.sub(r"\s+", "", match.group(1))
        rec = re.sub(r"\s+", "", match.group(2))
        if rec.count("-") < 2 or not acc.endswith("%"):
            continue
        short = "-".join(rec.split("-")[:2])
        old = f"{acc} · {short}"
        want = f"{acc} · {rec}"
        if old in html and want not in html:
            html = html.replace(old, want, 1)
    return html


def _align_mlb_consensus(html: str) -> str:
    """Sharp Consensus on the card follows the stored ensemble percent."""
    path = Path("sports_predictions_original.db")
    if not path.is_file() or "data-m-consensus" not in html:
        return html
    stored: dict[str, float] = {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        rows = conn.execute(
            "SELECT game_id, lock_card_json FROM predictions WHERE upper(sport)='MLB'"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return html
    for game_id, raw in rows:
        try:
            snap = json.loads(raw or "{}")
            val = snap.get("ensemble_prob")
            if val is None:
                continue
            stored[str(game_id)] = round(float(val), 1)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    if not stored:
        return html
    parts = re.split(r'(data-game-id="[^"]+")', html)
    out = [parts[0]]
    for i in range(1, len(parts), 2):
        gid = re.search(r'data-game-id="([^"]+)"', parts[i]).group(1)
        body = parts[i + 1] if i + 1 < len(parts) else ""
        want = stored.get(gid)
        shown = re.search(r'data-m-consensus="([0-9.]+)"', body)
        if want is None or not shown:
            out.append(parts[i] + body)
            continue
        have = float(shown.group(1))
        # The stored ensemble is the HOME win %. The card shows the percent of
        # the side named in the Sharp Consensus box, so turn it to that side.
        side = re.search(
            r'Sharp Consensus</div>[\s\S]{0,300}?class="pc-side (home|away)',
            body,
        )
        if side and side.group(1) == "away":
            want = round(100.0 - want, 1)
        if want < 50.0:
            out.append(parts[i] + body)
            continue
        if abs(have - want) < 0.15:
            out.append(parts[i] + body)
            continue
        # A stored consensus that equals another model's percent is a copy made
        # at lock time. Keep the separated blend the card already shows.
        others = []
        for attr in ("grinder2", "takedown", "edge", "xsharp", "efficiency"):
            found = re.search(rf'data-m-{attr}="([0-9.]+)"', body)
            if found:
                others.append(float(found.group(1)))
        if any(abs(item - want) < 0.051 for item in others):
            out.append(parts[i] + body)
            continue
        old = shown.group(1)
        new = f"{want:.1f}"
        dog = f"{round(100 - have, 1):.1f}"
        new_dog = f"{round(100 - want, 1):.1f}"
        body = body.replace(f'data-m-consensus="{old}"', f'data-m-consensus="{new}"', 1)
        body = body.replace(f'data-conf="{old}"', f'data-conf="{new}"', 1)
        body = body.replace(
            f'<div class="pc-val">{old}%</div>',
            f'<div class="pc-val">{new}%</div>',
            1,
        )
        body = body.replace(
            f'<div class="win-pct">{old}<span',
            f'<div class="win-pct">{new}<span',
            1,
        )
        body = body.replace(
            f'<div class="win-pct">{dog}<span',
            f'<div class="win-pct">{new_dog}<span',
            1,
        )
        out.append(parts[i] + body)
    return "".join(out)


def _loose_days(html: str) -> list[str]:
    """Dates already written on the page, outside scripts."""
    cleaned = re.sub(r"<script[\s\S]*?</script>", " ", html or "", flags=re.I)
    cleaned = re.sub(r"<style[\s\S]*?</style>", " ", cleaned, flags=re.I)
    return sorted(set(re.findall(r"20\d\d-\d\d-\d\d", cleaned)))


def _stored_result_days(sport: str) -> list[str]:
    """Finished dates already in this sport's database. No dates are invented."""
    path = Path("sports_predictions_original.db")
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        rows = conn.execute(
            """
            SELECT date(game_date) FROM games
            WHERE upper(sport) = ? AND home_score IS NOT NULL
              AND date(game_date) IS NOT NULL
            GROUP BY date(game_date)
            ORDER BY date(game_date) DESC
            LIMIT 40
            """,
            ((sport or "").upper(),),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return sorted({str(row[0]) for row in rows if row and row[0]})


def _with_layout(html: str, sport: str = "") -> str:
    try:
        from page_layout import place_shared_layout
        return place_shared_layout(html, sport) or html
    except Exception:
        return html


def _fresh_today_stamp(html: str) -> str:
    """A saved page keeps the day it was built on. The page has to know today."""
    found = re.search(r"const today = '(\d{4}-\d{2}-\d{2})'", html or "")
    if not found:
        return html
    now = _today().isoformat()
    if found.group(1) >= now:
        return html
    return html[: found.start(1)] + now + html[found.end(1) :]


def extend_page_dates(html: str, sport: str, path: str, view: str = "") -> str:
    if not html or "<" not in html:
        return html
    path_l = (path or "").lower()
    if not path_l.endswith("-picks") and not path_l.endswith("-results"):
        return html
    sport_u = (sport or "").upper()
    if (
        path_l.endswith("-picks")
        and "is in the off-season" in html
        and not re.search(r'id="date-20\d\d-\d\d-\d\d"', html)
    ):
        html = re.sub(r'<nav id="dateBubbles"[\s\S]*?</script>', "", html, count=1)
        html = re.sub(r"const allDates = \[[^\]]*\];", "", html, count=1)
        return _with_layout(html, sport_u)
    board = _scoreboard_days(sport_u)
    existing = _existing_days(html)
    # A one-day picker of "today" with no games makes the date control a dead end.
    if not board and not re.search(r'id="date-20\d\d-\d\d-\d\d"', html or ""):
        html = re.sub(
            r'<nav id="dateBubbles"[\s\S]*?</script>',
            "",
            html,
            count=1,
        )
        existing = _existing_days(html)
    days = sorted(set(existing) | set(board))
    if not days:
        days = _loose_days(html) or _stored_result_days(sport_u)
    view_l = (view or "").strip().lower()
    is_chart = view_l in ("chart", "tabs", "markets", "tabbed", "spread", "totals")
    html = _ensure_picker(html, days)
    if path_l.endswith("-results"):
        html = _fresh_today_stamp(html)
    if path_l.endswith("-results") and is_chart:
        html = _stamp_chart_night(html, sport_u)
    if path_l.endswith("-results") and not is_chart and sport_u == "NBA":
        html = _align_short_tallies(html)
    if path_l.endswith("-picks") and sport_u == "NCAAF":
        html = _sync_ncaaf_totals(html)
    if path_l.endswith("-picks") and sport_u == "MLB":
        html = _align_mlb_consensus(html)
    return _with_layout(html, sport_u)
