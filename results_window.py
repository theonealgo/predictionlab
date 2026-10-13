"""Results pages ship only the latest nights of game cards and game-log rows.

Season / Last 7 / Last 30 / consensus / PL vs Books are already rendered from
every final before this runs, so cutting older cards changes no record.
Runs as the last after-request step; saved pages used for those records are
written earlier and stay whole.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

NIGHTS = 3

_RESULTS_PATH = re.compile(
    r"^/(mlb|nhl|nba|nfl|ncaaf|ncaab|ncaaw|wnba|cfl|soccer|tennis|ufc|golf)-results/?$",
    re.I,
)
_SECTION_OPEN = re.compile(
    r'<div\b(?=[^>]*\bclass="[^"]*\bdate-section\b)(?=[^>]*\bid="date-(\d{4}-\d{2}-\d{2})")[^>]*>',
    re.I,
)
_DIV_TAG = re.compile(r"<div\b|</div\s*>", re.I)
_DAYS_ARRAY = re.compile(r'(const\s+days\s*=\s*)\[((?:\s*"\d{4}-\d{2}-\d{2}"\s*,?)+)\]')
_PICKER = re.compile(r'(<select\b[^>]*\bid="datePicker"[^>]*>)([\s\S]*?)(</select>)', re.I)
_OPTION = re.compile(r'<option\b[^>]*\bvalue="(\d{4}-\d{2}-\d{2})"[^>]*>[\s\S]*?</option>', re.I)
_BUBBLE = re.compile(
    r'<(button|div|a)\b(?=[^>]*\bclass="[^"]*\bdate-bubble\b)[^>]*\bdata-date="(\d{4}-\d{2}-\d{2})"[^>]*>[\s\S]*?</\1>',
    re.I,
)
_TABLE = re.compile(r'<table\b[^>]*\bclass="[^"]*\bresults-table\b[^"]*"[^>]*>[\s\S]*?</table>', re.I)
_DATE_ROW = re.compile(r"<tr\b[^>]*>\s*<td\b[^>]*>\s*(\d{4}-\d{2}-\d{2})\s*</td>[\s\S]*?</tr>", re.I)
_COUNT_TAG = re.compile(
    r'(<span\b[^>]*\bclass="tag"[^>]*>\()(\d+)(\)</span>\s*</h2>\s*(?:<div[^>]*>\s*)*)$', re.I
)


def _today_et() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return datetime.now().date().isoformat()


def keep_dates(dates_with_finals, today: str) -> set[str]:
    """Today's finals plus the latest NIGHTS earlier nights that have finals."""
    dates = set(dates_with_finals)
    keep = set(sorted((d for d in dates if d < today), reverse=True)[:NIGHTS])
    if today in dates:
        keep.add(today)
    return keep


# Set by install(): sport -> dates with stored finals. Cards and chart pages
# must show the same nights even when one of them is missing a night.
_finals_dates = None
_STORED_TTL = 60.0
_stored_cache: dict[str, tuple[float, set[str]]] = {}


def stored_window(sport: str, today: str) -> set[str]:
    """The nights from stored finals; empty when the sport has none recently."""
    if not callable(_finals_dates) or not sport:
        return set()
    sport_u = sport.upper()
    now = time.time()
    hit = _stored_cache.get(sport_u)
    if hit and now - hit[0] < _STORED_TTL:
        return keep_dates(hit[1], today)
    try:
        dates = {d for d in (_finals_dates(sport_u, today) or ()) if d and d <= today}
    except Exception:
        dates = set()
    _stored_cache[sport_u] = (now, dates)
    return keep_dates(dates, today)


def window_dates(dates_with_finals, sport: str = "") -> set[str]:
    """The nights a results page shows today."""
    today = _today_et()
    return stored_window(sport, today) or keep_dates(dates_with_finals, today)


def _div_end(html: str, start: int) -> int:
    depth = 0
    for m in _DIV_TAG.finditer(html, start):
        if m.group(0)[1] == "/":
            depth -= 1
            if depth == 0:
                return m.end()
        else:
            depth += 1
    return -1


def trim_card_sections(html: str, today: str, window: set[str] | None = None) -> str:
    sections = []
    for m in _SECTION_OPEN.finditer(html):
        end = _div_end(html, m.start())
        if end < 0:
            return html
        sections.append((m.group(1), m.start(), end))
    if not sections:
        return html
    with_finals = [d for d, s, e in sections if "final-score" in html[s:e]]
    keep = set(with_finals) & (window or set()) or keep_dates(with_finals, today)
    if not keep:
        return html
    out, pos = [], 0
    for day, s, e in sections:
        if day in keep:
            continue
        out.append(html[pos:s])
        pos = e
    out.append(html[pos:])
    html = "".join(out)

    def _days(m: re.Match) -> str:
        days = [d for d in re.findall(r"\d{4}-\d{2}-\d{2}", m.group(2)) if d in keep]
        return m.group(1) + "[" + ", ".join(f'"{d}"' for d in days) + "]"

    html = _DAYS_ARRAY.sub(_days, html)

    def _picker(m: re.Match) -> str:
        body = _OPTION.sub(lambda o: o.group(0) if o.group(1) in keep else "", m.group(2))
        return m.group(1) + body + m.group(3)

    html = _PICKER.sub(_picker, html)
    return _BUBBLE.sub(lambda b: b.group(0) if b.group(2) in keep else "", html)


def trim_game_log_rows(html: str, today: str, window: set[str] | None = None) -> str:
    out, pos = [], 0
    for t in _TABLE.finditer(html):
        table = t.group(0)
        rows = list(_DATE_ROW.finditer(table))
        if not rows:
            continue
        row_dates = {r.group(1) for r in rows}
        keep = row_dates & (window or set()) or keep_dates(row_dates, today)
        if not keep:
            continue
        kept = sum(1 for r in rows if r.group(1) in keep)
        new_table = _DATE_ROW.sub(lambda r: r.group(0) if r.group(1) in keep else "", table)
        head = html[pos:t.start()]
        tail_start = max(0, len(head) - 600)
        head = head[:tail_start] + _COUNT_TAG.sub(
            lambda c: f"{c.group(1)}{kept}{c.group(3)}", head[tail_start:], count=1
        )
        out.append(head)
        out.append(new_table)
        pos = t.end()
    if not out:
        return html
    out.append(html[pos:])
    return "".join(out)


def trim_results_html(html: str, today: str | None = None, sport: str = "") -> str:
    today = today or _today_et()
    window = stored_window(sport, today)
    html = trim_card_sections(html, today, window)
    return trim_game_log_rows(html, today, window)


def install(app, finals_dates=None) -> None:
    """``finals_dates(sport, today)`` returns the dates with stored finals."""
    global _finals_dates
    from flask import request

    _finals_dates = finals_dates

    def _results_window(response):
        try:
            if request.method != "GET" or response.status_code != 200:
                return response
            if response.direct_passthrough or "html" not in (response.mimetype or ""):
                return response
            path_match = _RESULTS_PATH.match(request.path or "")
            if not path_match:
                return response
            if request.args.get("date") or request.args.get("week"):
                return response
            html = response.get_data(as_text=True)
            trimmed = trim_results_html(html, sport=path_match.group(1))
            if trimmed != html:
                response.set_data(trimmed)
        except Exception:
            pass
        return response

    # after_request hooks run last-registered first; index 0 runs after all of them.
    app.after_request_funcs.setdefault(None, []).insert(0, _results_window)
