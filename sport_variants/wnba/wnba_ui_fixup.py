#!/usr/bin/env python3
"""WNBA picks/results publish-layer UI (MLB template parity).

Display only. Does not change MLB v2 math, invent 50% / −100, or name vendors.
WNBA does not publish Grinder2 or Takedown — drop those tiles, do not fill
them from Elo fallback.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from mlb_ui_fixup import dedupe_game_card_stacks, enrich_mlb_chart_data_attrs

_WNBA_ESPN_TTL = 900.0
_WNBA_ESPN_CACHE: dict[str, tuple[float, Any]] = {}
_WNBA_LOGO_ABBR = {
    "Atlanta Dream": "atl",
    "Chicago Sky": "chi",
    "Connecticut Sun": "conn",
    "Dallas Wings": "dal",
    "Golden State Valkyries": "gsv",
    "Indiana Fever": "ind",
    "Las Vegas Aces": "lv",
    "Los Angeles Sparks": "la",
    "Minnesota Lynx": "min",
    "New York Liberty": "ny",
    "Phoenix Mercury": "phx",
    "Portland Fire": "por",
    "Seattle Storm": "sea",
    "Toronto Tempo": "tor",
    "Washington Mystics": "wsh",
}
_WNBA_ESPN_SLUG = {
    "Atlanta Dream": "atl",
    "Chicago Sky": "chi",
    "Connecticut Sun": "con",
    "Dallas Wings": "dal",
    "Golden State Valkyries": "gs",
    "Indiana Fever": "ind",
    "Las Vegas Aces": "lv",
    "Los Angeles Sparks": "la",
    "Minnesota Lynx": "min",
    "New York Liberty": "ny",
    "Phoenix Mercury": "phx",
    "Portland Fire": "por",
    "Seattle Storm": "sea",
    "Toronto Tempo": "tor",
    "Washington Mystics": "wsh",
}


def _wnba_h2h_db_paths() -> list[Path]:
    root = Path(__file__).resolve().parent
    return [
        root / "sports_predictions_original.db",
        root / "data" / "sports_predictions_original.db",
        Path.home() / "Documents/Personal/wnba" / "data" / "sandbox_results.db",
    ]


def _fmt_h2h_half(n: float) -> str:
    r = round(float(n) * 2) / 2
    return str(int(r)) if r == int(r) else str(r)


def _wnba_h2h_text(conn: sqlite3.Connection, home: str, away: str, *, n: int = 10, min_games: int = 2) -> str:
    """Last-N scored WNBA H2H as ``163 (8 games)``. Display only."""
    if not home or not away:
        return ""
    try:
        rows = conn.execute(
            """
            SELECT home_team_id, away_team_id, home_score, away_score
            FROM games
            WHERE sport = 'WNBA'
              AND home_score IS NOT NULL AND away_score IS NOT NULL
              AND (
                    (home_team_id = ? AND away_team_id = ?)
                 OR (home_team_id = ? AND away_team_id = ?)
              )
            ORDER BY game_date DESC
            LIMIT ?
            """,
            (home, away, away, home, int(n)),
        ).fetchall()
    except Exception:
        return ""
    if len(rows) < int(min_games):
        return ""
    totals: list[float] = []
    for ht, at, hs, a_s in rows:
        try:
            totals.append(float(hs) + float(a_s))
        except (TypeError, ValueError):
            continue
    if len(totals) < int(min_games):
        return ""
    g = len(totals)
    games = "1 game" if g == 1 else f"{g} games"
    return f"{_fmt_h2h_half(sum(totals) / g)} ({games})"


def _good_h2h(val: str) -> bool:
    t = (val or "").strip()
    if not t or t in {"—", "-", "–", "n/a", "N/A"}:
        return False
    return bool(re.search(r"\d", t))


def enrich_wnba_h2h_from_db(html: str) -> str:
    """Fill data-h2h + View Details H2H chip when scored history exists.

    Display-only safety net (soccer_ui_fixup style). First meetings stay empty
    so the chart shows a real ``—``, not a broken enrich.
    """
    if not html or "data-pick-card" not in html:
        return html
    conns: list[sqlite3.Connection] = []
    for path in _wnba_h2h_db_paths():
        if not path.is_file():
            continue
        try:
            conns.append(sqlite3.connect(str(path)))
        except Exception:
            continue
    if not conns:
        return html

    cache: dict[tuple[str, str], str] = {}

    def _lookup(home: str, away: str) -> str:
        key = (home, away)
        if key in cache:
            return cache[key]
        val = ""
        for conn in conns:
            try:
                val = _wnba_h2h_text(conn, home, away)
            except Exception:
                val = ""
            if _good_h2h(val):
                break
        cache[key] = val
        cache[(away, home)] = val
        return val

    def _set_attr(tag: str, name: str, value: str) -> str:
        if not value:
            return tag
        esc = (
            value.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
        )
        if re.search(rf'\b{name}="[^"]*"', tag, flags=re.I):
            return re.sub(
                rf'\b{name}="[^"]*"',
                f'{name}="{esc}"',
                tag,
                count=1,
                flags=re.I,
            )
        return tag[:-1] + f' {name}="{esc}">'

    def _chip_html(h2h: str) -> str:
        return (
            '<div class="sf-item">'
            '<span class="sf-label">H2H Last 10</span> '
            f'<span class="sf-val">{html_lib.escape(h2h)}</span>'
            "</div>"
        )

    def _ensure_chip(rest: str, h2h: str) -> str:
        if not _good_h2h(h2h):
            return rest
        chip_re = re.compile(
            r'<div\b[^>]*\bclass="[^"]*\bsf-item\b[^"]*"[^>]*>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*H2H\s*Last\s*10\s*</span>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]*?)\s*</span>\s*'
            r"</div>",
            flags=re.I,
        )
        m = chip_re.search(rest)
        if m:
            cur = (m.group(1) or "").strip()
            if cur == h2h:
                return rest
            return rest[: m.start()] + _chip_html(h2h) + rest[m.end() :]
        foot_m = re.search(
            r'(<div\b[^>]*\bclass="[^"]*\bodds-extras-footer\b[^"]*"[^>]*>)',
            rest,
            flags=re.I,
        )
        if foot_m:
            return rest[: foot_m.end()] + "\n        " + _chip_html(h2h) + rest[foot_m.end() :]
        details_m = re.search(
            r'(<div\b[^>]*\bclass="[^"]*\b(?:card-details|odds-pricing-table|pick-conf-bar)\b[^"]*"[^>]*>)',
            rest,
            flags=re.I,
        )
        if details_m:
            return rest[: details_m.start()] + _chip_html(h2h) + rest[details_m.start() :]
        return rest + _chip_html(h2h)

    def _chip_value(rest: str) -> str:
        chip_m = re.search(
            r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*H2H\s*Last\s*10\s*</span>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]+?)\s*</span>',
            rest,
            flags=re.I,
        )
        if not chip_m:
            return ""
        return (chip_m.group(1) or "").strip()

    def _attr(open_tag: str, *names: str) -> str:
        for name in names:
            m = re.search(rf'\b{name}="([^"]*)"', open_tag, flags=re.I)
            if m:
                return html_lib.unescape((m.group(1) or "").strip())
        return ""

    def _patch_stack(stack: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            return stack
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        existing = html_lib.unescape(_attr(open_tag, "data-h2h"))
        chip_val = _chip_value(rest)
        home = _attr(open_tag, "data-home-full", "data-home")
        away = _attr(open_tag, "data-away-full", "data-away")
        db_h2h = _lookup(home, away) if home and away else ""
        h2h = db_h2h if _good_h2h(db_h2h) else (
            existing if _good_h2h(existing) else (chip_val if _good_h2h(chip_val) else "")
        )
        if not _good_h2h(h2h):
            return stack
        open2 = _set_attr(open_tag, "data-h2h", h2h)
        rest2 = _ensure_chip(rest, h2h)
        if open2 == open_tag and rest2 == rest:
            return stack
        return open2 + rest2

    try:
        parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
        if len(parts) > 1:
            html = parts[0] + "".join(_patch_stack(p) for p in parts[1:])
    finally:
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
    return html


def _wnba_team_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _wnba_logo_url(team_name: str) -> str:
    abbr = _WNBA_LOGO_ABBR.get((team_name or "").strip())
    if not abbr:
        return ""
    return f"https://a.espncdn.com/i/teamlogos/wnba/500/{abbr}.png"


def _wnba_espn_json(url: str) -> dict[str, Any]:
    now = time.time()
    hit = _WNBA_ESPN_CACHE.get(url)
    if hit and (now - hit[0]) < _WNBA_ESPN_TTL:
        return hit[1] if isinstance(hit[1], dict) else {}
    try:
        import requests
    except Exception:
        return {}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, timeout=8, headers=headers)
        if getattr(r, "status_code", None) == 403 and "site.api.espn.com" in url:
            alt = url.replace("://site.api.espn.com", "://site.web.api.espn.com", 1)
            r = requests.get(alt, timeout=8, headers=headers)
        r.raise_for_status()
        data = r.json()
    except Exception:
        data = {}
    if isinstance(data, dict) and data:
        _WNBA_ESPN_CACHE[url] = (now, data)
    return data if isinstance(data, dict) else {}


def _wnba_page_dates(html: str) -> list[str]:
    dates = set(re.findall(r'"startDate"\s*:\s*"(\d{4}-\d{2}-\d{2})', html or ""))
    dates.update(re.findall(r"📅\s*(\d{4}-\d{2}-\d{2})", html or ""))
    return sorted(d for d in dates if d)


def _wnba_format_et_clock(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        local = dt.astimezone(ZoneInfo("America/New_York"))
        return f"{local.strftime('%I:%M %p').lstrip('0')} ET"
    except Exception:
        return ""


def _wnba_espn_clocks(html: str) -> dict[tuple[str, str], str]:
    dates = _wnba_page_dates(html)
    if dates:
        start = dates[0].replace("-", "")
        end = dates[-1].replace("-", "")
        q = f"dates={start}-{end}&limit=100"
    else:
        q = "limit=80"
    data = _wnba_espn_json(
        f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?{q}"
    )
    out: dict[tuple[str, str], str] = {}
    for ev in data.get("events") or []:
        if not isinstance(ev, dict):
            continue
        clock = _wnba_format_et_clock(str(ev.get("date") or ""))
        if not clock:
            continue
        comps = (ev.get("competitions") or [{}])[0] or {}
        home = away = ""
        ordered: list[str] = []
        for c in comps.get("competitors") or []:
            if not isinstance(c, dict):
                continue
            team = c.get("team") or {}
            name = (team.get("displayName") or team.get("name") or "").strip()
            if not name:
                continue
            ordered.append(name)
            if str(c.get("homeAway") or "") == "home":
                home = name
            elif str(c.get("homeAway") or "") == "away":
                away = name
        if (not home or not away) and len(ordered) == 2:
            home, away = ordered[0], ordered[1]
        if not home or not away:
            continue
        out[(_wnba_team_key(away), _wnba_team_key(home))] = clock
    return out


def _wnba_espn_score_val(raw: Any) -> float | None:
    if isinstance(raw, dict):
        raw = raw.get("value", raw.get("displayValue"))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _wnba_espn_h2h_from_events(events: list[Any], home: str, away: str) -> str:
    want = {_wnba_team_key(home), _wnba_team_key(away)}
    if len(want) < 2:
        return ""
    rows: list[tuple[str, float]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        comps = (ev.get("competitions") or [{}])[0] or {}
        status = (comps.get("status") or {}).get("type") or {}
        if not status.get("completed"):
            continue
        sides: list[tuple[str, float]] = []
        for c in comps.get("competitors") or []:
            if not isinstance(c, dict):
                continue
            team = c.get("team") or {}
            name = (team.get("displayName") or team.get("name") or "").strip()
            score = _wnba_espn_score_val(c.get("score"))
            if name and score is not None:
                sides.append((name, score))
        if len(sides) != 2:
            continue
        if {_wnba_team_key(n) for n, _ in sides} != want:
            continue
        rows.append((str(ev.get("date") or ""), sides[0][1] + sides[1][1]))
    rows.sort(key=lambda r: r[0], reverse=True)
    totals = [tot for _dt, tot in rows[:10]]
    if not totals:
        return ""
    g = len(totals)
    games = "1 game" if g == 1 else f"{g} games"
    return f"{_fmt_h2h_half(sum(totals) / g)} ({games})"


def _wnba_espn_h2h_text(home: str, away: str) -> str:
    slugs = []
    for name in (home, away):
        slug = _WNBA_ESPN_SLUG.get((name or "").strip())
        if slug and slug not in slugs:
            slugs.append(slug)
    slugs.sort(key=lambda s: 0 if s in {"por", "tor"} else 1)
    for slug in slugs:
        data = _wnba_espn_json(
            f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/teams/{slug}/schedule"
        )
        val = _wnba_espn_h2h_from_events(list(data.get("events") or []), home, away)
        if _good_h2h(val):
            return val
    return ""


def _wnba_set_attr(tag: str, name: str, value: str) -> str:
    if not value:
        return tag
    esc = (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
    )
    if re.search(rf'\b{name}="[^"]*"', tag, flags=re.I):
        return re.sub(
            rf'\b{name}="[^"]*"',
            f'{name}="{esc}"',
            tag,
            count=1,
            flags=re.I,
        )
    return tag[:-1] + f' {name}="{esc}">'


def _wnba_patch_h2h_chips(rest: str, h2h: str) -> str:
    if not _good_h2h(h2h):
        return rest
    esc = html_lib.escape(h2h)
    rest = re.sub(
        r'(H2H Last 10</span>\s*<span class="sf-val">)([\s\S]*?)(</span>)',
        rf"\g<1>{esc}\3",
        rest,
        count=1,
        flags=re.I,
    )
    rest = re.sub(
        r'(<div class="line-chip h2h-face-chip">\s*'
        r'<div class="line-chip-label">H2H Last 10</div>\s*'
        r'<div class="line-chip-val">)([\s\S]*?)(</div>)',
        rf"\g<1>{esc}\3",
        rest,
        count=1,
        flags=re.I,
    )
    return rest


def _wnba_patch_logos(rest: str) -> str:
    def _slot(m: re.Match[str]) -> str:
        block = m.group(0)
        name_m = re.search(r'class="team-name">([^<]+)', block)
        name = (name_m.group(1) if name_m else "").strip()
        url = _wnba_logo_url(name)
        if not url:
            return block
        src_m = re.search(r'(<img class="team-logo"[^>]*src=")([^"]*)(")', block, flags=re.I)
        if not src_m:
            return block
        cur = src_m.group(2)
        if "pl-logo" in cur.lower() or not cur.strip():
            return block[: src_m.start()] + src_m.group(1) + url + src_m.group(3) + block[src_m.end() :]
        return block

    return re.sub(
        r'<div class="team-slot[^"]*">[\s\S]*?(?=<div class="(?:model-tag|win-pct))',
        _slot,
        rest,
        flags=re.I,
    )


def fill_wnba_card_gaps(html: str) -> str:
    """Fill Upcoming clocks, Fire/Tempo logos, and ESPN H2H when the DB is short."""
    if not html or "data-pick-card" not in html:
        return html
    clocks: dict[tuple[str, str], str] = {}
    try:
        clocks = _wnba_espn_clocks(html)
    except Exception:
        clocks = {}
    h2h_cache: dict[tuple[str, str], str] = {}

    def _h2h_lookup(home: str, away: str) -> str:
        key = (home, away)
        if key in h2h_cache:
            return h2h_cache[key]
        try:
            val = _wnba_espn_h2h_text(home, away)
        except Exception:
            val = ""
        h2h_cache[key] = val
        h2h_cache[(away, home)] = val
        return val

    def _attr(open_tag: str, *names: str) -> str:
        for name in names:
            m = re.search(rf'\b{name}="([^"]*)"', open_tag, flags=re.I)
            if m:
                return html_lib.unescape((m.group(1) or "").strip())
        return ""

    def _patch_stack(stack: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            return stack
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        home = _attr(open_tag, "data-home-full", "data-home")
        away = _attr(open_tag, "data-away-full", "data-away")
        clock = clocks.get((_wnba_team_key(away), _wnba_team_key(home)), "")
        if clock:
            open_tag = _wnba_set_attr(open_tag, "data-time", clock)
            rest = re.sub(
                r'(class="game-time">)(?:Upcoming|TBD|TBA|—|–|-)?(</span>)',
                rf"\g<1>{html_lib.escape(clock)}\2",
                rest,
                count=1,
                flags=re.I,
            )
        rest = _wnba_patch_logos(rest)
        existing = html_lib.unescape(_attr(open_tag, "data-h2h"))
        if not _good_h2h(existing) and home and away:
            espn_h2h = _h2h_lookup(home, away)
            if _good_h2h(espn_h2h):
                open_tag = _wnba_set_attr(open_tag, "data-h2h", espn_h2h)
                rest = _wnba_patch_h2h_chips(rest, espn_h2h)
        return open_tag + rest

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) <= 1:
        return html
    return parts[0] + "".join(_patch_stack(p) for p in parts[1:])


def _balanced_div_at(html: str, start: int) -> tuple[str, int]:
    tag_end = html.find(">", start)
    if tag_end < 0:
        return "", -1
    j = tag_end + 1
    depth = 1
    while j < len(html) and depth > 0:
        no, nc = html.find("<div", j), html.find("</div>", j)
        if nc < 0:
            return "", -1
        if no >= 0 and no < nc:
            depth += 1
            j = no + 4
        else:
            depth -= 1
            if depth == 0:
                return html[start : nc + 6], nc + 6
            j = nc + 6
    return "", -1


def hide_unavailable_model_boxes(html: str) -> str:
    """Drop pick-confidence boxes whose value is N/A / empty (no fake 50%)."""
    if not html or "pc-box" not in html:
        return html
    empty_re = re.compile(
        r"^\s*(N/?A|null|undefined|NaN|—|–|-|\.|none)?\s*$",
        re.I,
    )
    parts: list[str] = []
    pos = 0
    while True:
        m = re.search(r'<div class="pc-box[^"]*">', html[pos:], re.I)
        if not m:
            parts.append(html[pos:])
            break
        abs_start = pos + m.start()
        box, end = _balanced_div_at(html, abs_start)
        if end < 0:
            parts.append(html[pos:])
            break
        parts.append(html[pos:abs_start])
        val_m = re.search(r'<div class="pc-val[^"]*"[^>]*>\s*([^<]*?)\s*</div>', box, re.I)
        side_m = re.search(r'<div class="pc-side[^"]*"[^>]*>\s*([^<]*?)\s*</div>', box, re.I)
        val = (val_m.group(1) if val_m else "").strip()
        side = (side_m.group(1) if side_m else "").strip()
        has_pct = bool(re.search(r"\d", val))
        emptyish = (
            empty_re.match(val or "") is not None
            or empty_re.match(side or "") is not None
            or val.lower() in ("n/a", "null", "undefined", "nan")
            or side.lower() in ("n/a", "null", "undefined", "nan")
            or (not val and not side)
        )
        if not (emptyish and not has_pct):
            parts.append(box)
        pos = end
    return "".join(parts)


def _pct_and_rec(w: int, l: int) -> tuple[str, str]:
    n = int(w) + int(l)
    rec = f"{int(w)}-{int(l)}"
    if n <= 0:
        return "—", "—"
    return f"{round(100.0 * int(w) / n, 1)}%", rec


def patch_wnba_last7_from_cards(html: str) -> str:
    """Rebuild Last 7 model cards from graded game cards in that date window.

    Live weekly_tally can shrink Edge/XSharp/Consensus to last-night's 3 games
    when mid-week rows lack those prob columns, while Efficiency (attached later)
    shows the full 18-game sample. Prefer the larger real card sample.
    """
    if not html or "Last 7" not in html:
        return html
    try:
        from mlb_results_ui import (
            MODEL_ORDER,
            _extract_game_rows,
            _extract_tally_sections,
            _tally_models_from_finals,
        )
    except Exception:
        return html

    sections = _extract_tally_sections(html)
    l7 = next((s for s in sections if s.get("kind") == "last_7"), None)
    if not l7:
        return html
    date_from = (l7.get("date_from") or "")[:10]
    date_to = (l7.get("date_to") or "")[:10]
    if not date_from or not date_to:
        return html

    finals = _extract_game_rows(html, limit=400)
    windowed = [
        row
        for row in finals
        if date_from <= str(row.get("game_date") or "")[:10] <= date_to
    ]
    if not windowed:
        return html
    from_cards = _tally_models_from_finals(windowed)
    if not from_cards:
        return html

    block_m = re.search(
        r'(<div class="daily-tally"[^>]*>\s*<h2>[^<]*Last 7 Days[\s\S]*?</h2>)([\s\S]*?)(</div>\s*(?:<div class="daily-tally"|<!--|</div>\s*<div class="date-section))',
        html,
        flags=re.I,
    )
    if not block_m:
        # Fallback: first Last 7 daily-tally through next daily-tally / date-section
        block_m = re.search(
            r'(<h2>[^<]*Last 7 Days[\s\S]*?</h2>)([\s\S]{0,8000}?)(?=<div class="daily-tally"|<div class="date-section"|<h2>)',
            html,
            flags=re.I,
        )
        if not block_m:
            return html
        prefix, body = block_m.group(1), block_m.group(2)
        suffix = ""
        start, end = block_m.start(2), block_m.end(2)
    else:
        prefix, body, suffix = block_m.group(1), block_m.group(2), block_m.group(3)
        start, end = block_m.start(2), block_m.end(2)

    published = {c["name"]: c for c in (l7.get("cards") or [])}
    wnba_models = ("Edge", "XSharp", "Sharp Consensus", "Efficiency")

    def _n_of(card: dict[str, Any] | None) -> int:
        if not card:
            return 0
        rec = card.get("record") or ""
        parts = [p for p in re.split(r"[-–]", rec) if p.strip().isdigit()]
        if len(parts) >= 2:
            return int(parts[0]) + int(parts[1])
        return int(card.get("n") or 0)

    new_body = body
    for name in wnba_models:
        src = from_cards.get(name)
        if not src:
            continue
        card_n = int(src.get("n") or 0)
        pub_n = _n_of(published.get(name))
        # Fill empty tiles only. Do not enlarge Efficiency off every card
        # while Edge/XSharp/SC stay on the published-ML sample.
        if pub_n > 0 or card_n <= 0:
            continue
        pct_s, rec_s = _pct_and_rec(int(src.get("w") or 0), int(src.get("l") or 0))
        name_pat = re.escape(name)
        new_body, nsub = re.subn(
            rf'(<div class="daily-model">[^<]*{name_pat}</div>\s*)'
            rf'(<div class="daily-acc"[^>]*>)([^<]*)(</div>\s*)'
            rf'(<div class="daily-rec">)([^<]*)(</div>)',
            rf"\g<1>\g<2>{pct_s}\g<4>\g<5>{rec_s}\g<7>",
            new_body,
            count=1,
            flags=re.I,
        )
        if nsub == 0:
            continue

    if new_body == body:
        return html
    return html[:start] + new_body + html[end:]


def patch_wnba_season_from_cards(html: str) -> str:
    """Fill Season / Moneyline Accuracy tiles from graded cards when server tiles are 0-0."""
    if not html or "Moneyline Accuracy by Model" not in html:
        return html
    try:
        from mlb_results_ui import _extract_game_rows, _tally_models_from_finals
    except Exception:
        return html
    finals = _extract_game_rows(html, limit=400)
    if len(finals) < 20:
        return html
    from_cards = _tally_models_from_finals(finals)
    wnba_models = ("Edge", "XSharp", "Sharp Consensus", "Efficiency")
    if not any(int((from_cards.get(n) or {}).get("n") or 0) > 0 for n in wnba_models):
        return html

    def _n_of_rec(rec: str) -> int:
        parts = [p for p in re.split(r"[-–]", rec or "") if p.strip().isdigit()]
        if len(parts) >= 2:
            return int(parts[0]) + int(parts[1])
        return 0

    new_html = html
    for name in wnba_models:
        src = from_cards.get(name)
        if not src or int(src.get("n") or 0) <= 0:
            continue
        pct_s, rec_s = _pct_and_rec(int(src.get("w") or 0), int(src.get("l") or 0))
        name_pat = re.escape(name)
        new_html, _ = re.subn(
            rf'(<div class="model-label">[^<]*{name_pat}</div>\s*)'
            rf'(<div class="model-acc"[^>]*>)([^<]*)(</div>\s*)'
            rf'(<div class="model-rec">)([^<]*)(</div>)',
            lambda m, p=pct_s, r=rec_s: (
                m.group(1) + m.group(2) + (p if _n_of_rec(m.group(6)) <= 0 else m.group(3))
                + m.group(4) + m.group(5)
                + (r if _n_of_rec(m.group(6)) <= 0 else m.group(6))
                + m.group(7)
            ),
            new_html,
            count=1,
            flags=re.I,
        )
    return new_html


def wnba_results_html_season_ml_empty(html: str, *, min_games: int = 20) -> bool:
    """True when a season slate with N>=min_games published all ML tiles as 0-0 / no picks."""
    if not html:
        return False
    games_m = re.search(
        r"Season[^\n<]{0,80}?\((\d+)\s+games?\)",
        html,
        flags=re.I,
    )
    if not games_m:
        games_m = re.search(
            r"Last 7 Days[^\n<]*\((\d+)\s+games?\)",
            html,
            flags=re.I,
        )
    n_games = int(games_m.group(1)) if games_m else 0
    if n_games < int(min_games):
        return False
    empty = 0
    seen = 0
    for name in ("Edge", "XSharp", "Sharp Consensus", "Efficiency"):
        m = re.search(
            rf"(?:daily-model|model-label)\">[^<]*{re.escape(name)}</div>\s*"
            rf"<div class=\"(?:daily-acc|model-acc)\"[^>]*>\s*([^<]*?)\s*</div>\s*"
            rf"<div class=\"(?:daily-rec|model-rec)\">\s*([^<]*?)\s*</div>",
            html,
            flags=re.I,
        )
        if not m:
            continue
        seen += 1
        rec = (m.group(2) or "").strip()
        acc = (m.group(1) or "").strip()
        if rec in {"0-0", "—", "-", "–", "0-0 · no picks"} or "no picks" in rec.lower():
            if acc in {"—", "-", "–", "0%", "0.0%", ""}:
                empty += 1
    return seen >= 3 and empty == seen


def wnba_results_view_toggle_html(*, active: str = "normal") -> str:
    n_cls = "active" if active == "normal" else ""
    c_cls = "active" if active == "chart" else ""
    return (
        '<div class="pl-view-toggle" role="navigation" aria-label="Results view">'
        f'<a class="pl-view-btn {n_cls}" href="/wnba-results">Cards</a>'
        f'<a class="pl-view-btn {c_cls}" href="/wnba-results?view=chart">Chart</a>'
        "</div>"
        "<style>.pl-view-toggle{display:flex;gap:8px;margin:12px 16px 18px;flex-wrap:wrap}"
        ".pl-view-btn{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.pl-view-btn.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def inject_wnba_results_view_toggle(html: str, *, active: str = "normal") -> str:
    if not html:
        return html
    if 'class="pl-view-toggle"' in html or "class='pl-view-toggle'" in html:
        return html
    bar = wnba_results_view_toggle_html(active=active)
    if re.search(r"<main\b", html, re.I):
        out = re.sub(r"(<main\b[^>]*>)", r"\1" + bar, html, count=1, flags=re.I)
        if out != html:
            return out
    if re.search(r'class="container\b', html, re.I):
        out = re.sub(
            r'(<div class="container\b[^"]*"[^>]*>)',
            r"\1" + bar,
            html,
            count=1,
            flags=re.I,
        )
        if out != html:
            return out
    if "daily-tally" in html:
        out = re.sub(
            r'(<!--[^\n]*Daily Tally[^\n]*-->|<div class="daily-tally")',
            bar + r"\1",
            html,
            count=1,
            flags=re.I,
        )
        if out != html:
            return out
    return bar + html


def _parse_american_ml(raw: str) -> int | None:
    text = (raw or "").replace("\u2212", "-").replace(",", "").strip()
    m = re.search(r"([+-]?\d+)", text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _american_implied(odds: int) -> float:
    if odds < 0:
        return abs(odds) / (abs(odds) + 100.0)
    return 100.0 / (odds + 100.0)


def _deveig_pair(away_ml: int, home_ml: int) -> tuple[float, float]:
    a, h = _american_implied(away_ml), _american_implied(home_ml)
    tot = a + h
    if tot <= 0:
        return 50.0, 50.0
    return round(100.0 * a / tot, 1), round(100.0 * h / tot, 1)


def _is_coinflip_pct(raw: str) -> bool:
    text = (raw or "").replace("%", "").strip()
    try:
        return abs(float(text) - 50.0) < 0.05
    except ValueError:
        return False


def _home_margin_from_side_line(text: str, home: str, away: str) -> float | None:
    raw = re.sub(r"\s+", " ", (text or "")).strip()
    m = re.match(r"(.+?)\s+([+-]\d+(?:\.\d+)?)\s*$", raw)
    if not m:
        return None
    team, line = m.group(1).strip(), float(m.group(2))
    if _wnba_names_match(team, home):
        return -line
    if _wnba_names_match(team, away):
        return line
    return None


def _home_margin_from_proj(text: str, home: str, away: str) -> float | None:
    m = _WNBA_PROJ_RE.search(text or "")
    if not m:
        return None
    n1, p1, n2, p2 = m.group(1).strip(), float(m.group(2)), m.group(3).strip(), float(m.group(4))
    if _wnba_names_match(n2, home) or _wnba_names_match(n1, away):
        return p2 - p1
    if _wnba_names_match(n1, home) or _wnba_names_match(n2, away):
        return p1 - p2
    return None


def _wnba_winpct_from_margin(margin: float) -> float:
    try:
        from sports.team_efficiency_attach import spread_to_home_prob_pct

        return float(spread_to_home_prob_pct(margin, "WNBA"))
    except Exception:
        return round(50.0 + 50.0 * max(-1.0, min(1.0, margin / 16.0)), 1)


def _fmt_pct(pct: float) -> str:
    return f"{pct:.1f}"


def _favorite_of(home: str, away: str, home_pct: float) -> tuple[str, str, float]:
    if home_pct >= 50.0:
        return home, "home", home_pct
    return away, "away", round(100.0 - home_pct, 1)


def _set_attr(tag: str, name: str, value: str) -> str:
    if re.search(rf'\b{re.escape(name)}="', tag):
        return re.sub(
            rf'({re.escape(name)}=")[^"]*(")',
            rf"\g<1>{value}\g<2>",
            tag,
            count=1,
        )
    return tag[:-1] + f' {name}="{value}">'


def _rewrite_pc_box(box: str, pct: float, side: str, team: str) -> str:
    box = re.sub(
        r'(<div class="pc-val"[^>]*>)[^<]*(</div>)',
        rf"\g<1>{_fmt_pct(pct)}%\g<2>",
        box,
        count=1,
    )
    box = re.sub(
        r'(<div class="pc-side)[^"]*("[^>]*>)[^<]*(</div>)',
        rf"\g<1> {side}\g<2>{(team or '').split()[-1] if team else team}\g<3>",
        box,
        count=1,
    )
    return box


def fill_wnba_placeholder_probs(html: str) -> str:
    """Replace coin-flip 50% face / Pick Confidence with lines already on the card.

    Sharp Consensus = Prediction Lab moneyline (devig).
    Edge = PL projected margin. XSharp = XSharp spread/proj.
    Efficiency = PL spread (published 4th model — do not leave it off the grid).
    """
    if not html or "data-pick-card" not in html:
        return html
    parts: list[str] = []
    pos = 0
    while True:
        m = re.search(r'<div class="game-card-stack"', html[pos:], flags=re.I)
        if not m:
            parts.append(html[pos:])
            break
        abs_start = pos + m.start()
        stack, end = _balanced_div_at(html, abs_start)
        if end < 0:
            parts.append(html[pos:])
            break
        parts.append(html[pos:abs_start])
        parts.append(_fill_one_wnba_stack(stack))
        pos = end
    return "".join(parts)


def _fmt_half(n: float) -> str:
    n = round(float(n) * 2) / 2.0
    if abs(n - round(n)) < 0.001:
        return str(int(round(n)))
    return f"{n:.1f}"


def _wnba_lock_pl_line(game_id: str) -> tuple[float, float] | None:
    """Stored Prediction Lab spread and total. Does not read the books line."""
    gid = (game_id or "").strip()
    if not gid:
        return None
    db = Path(__file__).resolve().parent / "sports_predictions_original.db"
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        row = conn.execute(
            "SELECT lock_pl_spread, lock_pl_total FROM predictions WHERE game_id=? LIMIT 1",
            (gid,),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if not row or row[0] is None or row[1] is None:
        return None
    return float(row[0]), float(row[1])


def _blank_cell(raw: str) -> bool:
    text = (raw or "").replace("&mdash;", "").replace("&ndash;", "").strip()
    return text in {"", "—", "–", "-", "N/A", "n/a"}


def _paint_missing_pl_line(stack: str, away: str, home: str) -> str:
    """Fill a blank Prediction Lab spread and score from the stored lock.

    Only when that line is empty. Does not move XSharp, books, or a line
    that is already on the card.
    """
    spread_attr = re.search(r'data-pl-spread="([^"]*)"', stack)
    if spread_attr and spread_attr.group(1).strip():
        return stack
    gid = re.search(r'data-game-id="([^"]*)"', stack)
    locked = _wnba_lock_pl_line(gid.group(1) if gid else "")
    if not locked:
        return stack
    margin, total = locked
    tot_m = re.search(
        r'class="market-k">\s*Total</td>[\s\S]*?class="val-pl">\s*([^<]+)',
        stack,
        flags=re.I,
    )
    if tot_m and not _blank_cell(tot_m.group(1)):
        try:
            visible = float(tot_m.group(1).strip())
        except ValueError:
            visible = 0.0
        if visible > 0:
            total = visible
    mag = abs(margin)
    if mag < 0.01:
        spread_txt = "PK"
    else:
        side = home if margin > 0 else away
        spread_txt = f"{side} -{_fmt_half(mag)}"
    home_pts = round(((total + margin) / 2.0) * 2) / 2.0
    away_pts = round((total - home_pts) * 2) / 2.0
    proj = f"{away} {_fmt_half(away_pts)} – {home} {_fmt_half(home_pts)}"
    open_end = stack.find(">")
    if open_end > 0:
        tag = stack[: open_end + 1]
        tag = _set_attr(tag, "data-pl-spread", spread_txt)
        tag = _set_attr(tag, "data-pl-proj", proj)
        stack = tag + stack[open_end + 1 :]

    def _spread_row(match: re.Match[str]) -> str:
        shown = match.group(1)
        if not _blank_cell(shown):
            return match.group(0)
        return match.group(0).replace(
            f'class="val-pl">{shown}',
            f'class="val-pl">{spread_txt}',
            1,
        )

    stack = re.sub(
        r'class="market-k">\s*Spread</td>[\s\S]*?class="val-pl">([^<]*)',
        _spread_row,
        stack,
        count=1,
        flags=re.I,
    )
    stack = re.sub(
        r'(class="proj-model pl"[^>]*>\s*Prediction Lab\s*</span>\s*'
        r'<span class="proj-val">)([^<]*)',
        lambda match: match.group(1) + proj
        if _blank_cell(match.group(2))
        else match.group(0),
        stack,
        count=1,
        flags=re.I,
    )
    return stack


def _stored_lock(game_id: str) -> dict[str, Any] | None:
    """Published pick for one game. 50% model probs in the lock are placeholders."""
    gid = (game_id or "").strip()
    if not gid:
        return None
    cache = getattr(_stored_lock, "_cache", None)
    if cache is None:
        cache = {}
        setattr(_stored_lock, "_cache", cache)
    if gid in cache:
        return cache[gid]
    db = Path(__file__).resolve().parent / "sports_predictions_original.db"
    row = None
    if db.is_file():
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
            row = conn.execute(
                "SELECT lock_card_json, lock_pl_spread, lock_xs_spread, game_date FROM predictions WHERE game_id=? LIMIT 1",
                (gid,),
            ).fetchone()
            conn.close()
        except sqlite3.Error:
            row = None
    snap: dict[str, Any] | None = None
    if row:
        try:
            parsed = json.loads(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, dict):
            snap = dict(parsed)
            if row[1] is not None:
                snap["lock_pl_spread"] = float(row[1])
            if row[2] is not None:
                snap["lock_xs_spread"] = float(row[2])
            snap["game_date"] = str(row[3] or "")[:10]
    cache[gid] = snap
    return snap


def _stored_winner_name(game_id: str, home: str, away: str) -> str:
    snap = _stored_lock(game_id) or {}
    winner = str(snap.get("predicted_winner") or "").strip()
    if _wnba_names_match(winner, home):
        return home
    if _wnba_names_match(winner, away):
        return away
    return ""


def _home_pct_from_margin(margin: float) -> float:
    try:
        from sports.team_efficiency_attach import spread_to_home_prob_pct

        return float(spread_to_home_prob_pct(float(margin), "WNBA"))
    except Exception:
        return round(50.0 + 50.0 * max(-1.0, min(1.0, float(margin) / 16.0)), 1)


def _fill_one_wnba_stack(stack: str) -> str:
    away_m = re.search(r'data-away="([^"]*)"', stack)
    home_m = re.search(r'data-home="([^"]*)"', stack)
    if not away_m or not home_m:
        return stack
    away, home = away_m.group(1).strip(), home_m.group(1).strip()
    face_pcts = re.findall(
        r'<div class="matchup-row">[\s\S]*?<div class="win-pct">([\d.]+)',
        stack,
    )
    pc_vals = re.findall(r'class="pc-val"[^>]*>\s*([^<]+)', stack)
    stuck = (not face_pcts or all(_is_coinflip_pct(v) for v in face_pcts[:2])) and (
        not pc_vals or all(_is_coinflip_pct(v) for v in pc_vals)
    )
    if not stuck:
        return stack
    stack = _paint_missing_pl_line(stack, away, home)

    pl_mls = re.findall(
        r'class="ml-src pl">\s*Prediction Lab\s*</span>\s*'
        r'<span class="ml-num[^"]*">\s*([^<]+)',
        stack,
        flags=re.I,
    )
    sc_away = sc_home = None
    if len(pl_mls) >= 2:
        a_ml, h_ml = _parse_american_ml(pl_mls[0]), _parse_american_ml(pl_mls[1])
        if a_ml is not None and h_ml is not None:
            sc_away, sc_home = _deveig_pair(a_ml, h_ml)
    pl_spread = ""
    sm = re.search(r'data-pl-spread="([^"]*)"', stack)
    if sm:
        pl_spread = sm.group(1)
    xs_spread = ""
    xm = re.search(r'data-xs-spread="([^"]*)"', stack)
    if xm:
        xs_spread = xm.group(1)
    pl_proj = ""
    pm = re.search(r'data-pl-proj="([^"]*)"', stack)
    if pm:
        pl_proj = pm.group(1)
    xs_proj = ""
    xpm = re.search(r'data-xs-proj="([^"]*)"', stack)
    if xpm:
        xs_proj = xpm.group(1)

    edge_home = None
    mg = _home_margin_from_proj(pl_proj, home, away)
    if mg is not None:
        edge_home = _wnba_winpct_from_margin(mg)
    xs_home = None
    mg = _home_margin_from_side_line(xs_spread, home, away)
    if mg is None:
        mg = _home_margin_from_proj(xs_proj, home, away)
    if mg is not None:
        xs_home = _wnba_winpct_from_margin(mg)
    eff_home = None
    mg = _home_margin_from_side_line(pl_spread, home, away)
    if mg is not None:
        eff_home = _wnba_winpct_from_margin(mg)

    if sc_home is None:
        sc_home = edge_home if edge_home is not None else eff_home
        if sc_home is not None:
            sc_away = round(100.0 - sc_home, 1)
    if sc_home is None:
        return stack
    if sc_away is None:
        sc_away = round(100.0 - sc_home, 1)
    if edge_home is None:
        edge_home = sc_home
    if xs_home is None:
        xs_home = sc_home
    if eff_home is None:
        eff_home = sc_home

    models = {
        "Edge": edge_home,
        "XSharp": xs_home,
        "Sharp Consensus": sc_home,
        "Efficiency": eff_home,
    }

    gid_m = re.search(r'data-game-id="([^"]*)"', stack)
    stored_winner = _stored_winner_name(gid_m.group(1) if gid_m else "", home, away)
    face_home = sc_home
    face_away = sc_away if sc_away is not None else round(100.0 - sc_home, 1)
    if stored_winner and _wnba_names_match(stored_winner, home):
        sc_team, sc_show = home, face_home
    elif stored_winner:
        sc_team, sc_show = stored_winner, face_away
    else:
        sc_team, _, sc_show = _favorite_of(home, away, sc_home)

    # Face highlight is the stored winner. Win percents stay the line's two sides.
    row_m = re.search(
        r'(<div class="matchup-row">)([\s\S]*?)(</div>\s*<div class="lines-strip")',
        stack,
    )
    if row_m:
        row = row_m.group(2)
        seen = {"n": 0}

        def _wp(m: re.Match[str]) -> str:
            seen["n"] += 1
            pct = face_away if seen["n"] == 1 else face_home
            return f"{m.group(1)}{_fmt_pct(pct)}"

        row = re.sub(r'(<div class="win-pct">)[\d.]+', _wp, row, count=2)
        slot_i = {"n": 0}

        def _fav(m: re.Match[str]) -> str:
            slot_i["n"] += 1
            if stored_winner:
                fav = (slot_i["n"] == 1 and _wnba_names_match(stored_winner, away)) or (
                    slot_i["n"] == 2 and _wnba_names_match(stored_winner, home)
                )
            else:
                fav = (slot_i["n"] == 1 and face_away >= 50.0) or (
                    slot_i["n"] == 2 and face_home >= 50.0
                )
            return f'class="team-slot{" favored" if fav else ""}"'

        row = re.sub(r'class="team-slot[^"]*"', _fav, row, count=2)
        stack = stack[: row_m.start(2)] + row + stack[row_m.end(2) :]

    def _pc_repl(m: re.Match[str]) -> str:
        box = m.group(0)
        name_m = re.search(r'class="pc-name">([^<]+)', box)
        name = (name_m.group(1) if name_m else "").strip()
        if name not in models:
            return box
        if name == "Sharp Consensus" and stored_winner:
            side = "home" if _wnba_names_match(stored_winner, home) else "away"
            return _rewrite_pc_box(box, sc_show, side, stored_winner)
        # Edge stays on the percent already on the card. Painting it from the
        # Prediction Lab margin copies Efficiency (that margin is Efficiency).
        if name == "Edge":
            return box
        team, side, pct = _favorite_of(home, away, models[name])
        return _rewrite_pc_box(box, pct, side, team)

    stack = re.sub(
        r'<div class="pc-box[^"]*">[\s\S]*?</div>\s*</div>',
        _pc_repl,
        stack,
    )
    if "Efficiency" not in stack:
        team, side, pct = _favorite_of(home, away, eff_home)
        extra = (
            f'<div class="pc-box ">'
            f'<div class="pc-name">Efficiency</div>'
            f'<div class="pc-val">{_fmt_pct(pct)}%</div>'
            f'<div class="pc-side {side}">{(team or "").split()[-1] if team else team}</div>'
            f"</div>"
        )
        stack = re.sub(
            r'(<div class="pc-box consensus">[\s\S]*?</div>\s*</div>)',
            r"\1" + extra,
            stack,
            count=1,
        )

    def _conf_of(home_pct: float) -> str:
        return _fmt_pct(home_pct if home_pct >= 50.0 else 100.0 - home_pct)

    open_end = stack.find(">")
    if open_end > 0:
        tag = stack[: open_end + 1]
        tag = _set_attr(tag, "data-conf", _fmt_pct(sc_show))
        tag = _set_attr(tag, "data-pick", sc_team)
        tag = _set_attr(tag, "data-m-xsharp", _conf_of(models["XSharp"]))
        tag = _set_attr(tag, "data-m-consensus", _fmt_pct(sc_show))
        tag = _set_attr(tag, "data-m-efficiency", _conf_of(models["Efficiency"]))
        stack = tag + stack[open_end + 1 :]
    return stack


def _pc_is_fifty(raw: str) -> bool:
    text = (raw or "").replace("%", "").strip()
    try:
        return abs(float(text) - 50.0) < 0.051
    except ValueError:
        return True


def _hist_label(sides: dict[str, str], record: str) -> str:
    from collections import Counter

    order = ("Edge", "XSharp", "Sharp Consensus", "Efficiency")
    counts = Counter(sides[n] for n in order if n in sides)
    if not counts:
        return ""
    top_side, top_n = counts.most_common(1)[0]
    rec = record if re.search(r"\d+\s*-\s*\d+", record or "") else "0-0"
    if top_n >= 4:
        return f"Unanimous: {rec}"
    if top_n == 3:
        dissent = [n for n in order if sides.get(n) and sides.get(n) != top_side]
        return "All but " + " and ".join(dissent) + f": {rec}"
    if top_n == 2:
        return f"Split: {rec}"
    return ""


def _visible_model_pct(rest: str, name: str) -> str:
    match = re.search(
        rf'<div class="pc-name">\s*{re.escape(name)}\s*</div>\s*'
        r'<div class="pc-val"[^>]*>\s*([\d.]+)\s*%',
        rest,
        flags=re.I,
    )
    if not match:
        return ""
    try:
        num = float(match.group(1))
    except ValueError:
        return ""
    if abs(num - 50.0) < 0.051:
        return ""
    return _fmt_pct(num)


def _upsert_line_box(rest: str, name: str, pct: float, side: str, team: str) -> str:
    """Replace a 50% box, or add one. Leave a box that already has a real percent."""
    if abs(pct - 50.0) < 0.051:
        return rest
    found = {"n": 0}

    def _repl(match: re.Match[str]) -> str:
        box = match.group(0)
        name_m = re.search(r'class="pc-name">\s*([^<]+)', box)
        if not name_m or name_m.group(1).strip() != name:
            return box
        found["n"] += 1
        val_m = re.search(r'class="pc-val"[^>]*>\s*([^<]*)', box)
        raw = val_m.group(1) if val_m else ""
        if not _pc_is_fifty(raw):
            return box
        return _rewrite_pc_box(box, pct, side, team)

    rest = re.sub(
        r'<div class="pc-box[^"]*">[\s\S]*?</div>\s*</div>',
        _repl,
        rest,
    )
    if found["n"]:
        return rest
    nick = (team or "").split()[-1] if team else ""
    extra = (
        f'<div class="pc-box ">'
        f'<div class="pc-name">{name}</div>'
        f'<div class="pc-val">{_fmt_pct(pct)}%</div>'
        f'<div class="pc-side {side}">{nick}</div>'
        f"</div>"
    )
    updated = re.sub(
        r'(<div class="pc-box consensus">[\s\S]*?</div>\s*</div>)',
        r"\1" + extra,
        rest,
        count=1,
    )
    if updated != rest:
        return updated
    return re.sub(
        r'(<div class="pick-conf-grid">)',
        r"\1" + extra,
        rest,
        count=1,
    )


def _wnba_slate_today() -> str:
    """Picks day. The 2026-09-29 slate is today even while the clock is still the 28th."""
    declared = "2026-09-29"
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        et = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        et = declared
    return et if et >= declared else declared


def _wnba_elo_probs(home: str, away: str, game_date: str) -> dict[str, float] | None:
    """Elo and ensemble home win % for today or a later game.

    Does not read the Prediction Lab spread, the book line, or Efficiency.
    Earlier dates stay on whatever is already stored.
    """
    if str(game_date or "")[:10] < _wnba_slate_today():
        return None
    try:
        import NHL77FINAL as N

        probs = N._wnba_elo_ml_probs_before(home, away, game_date)
    except Exception:
        return None
    if not isinstance(probs, dict):
        return None
    out: dict[str, float] = {}
    for key in ("elo", "ens"):
        try:
            num = float(probs.get(key))
        except (TypeError, ValueError):
            return None
        if num <= 1.0:
            num *= 100.0
        out[key] = round(num, 1)
    if abs(out["elo"] - 50.0) < 0.051 and abs(out["ens"] - 50.0) < 0.051:
        return None
    return out


def _wnba_model_edge_home_pct(home: str, away: str, game_date: str) -> float | None:
    """Home win % from the Elo path when the stored Edge is 50%."""
    probs = _wnba_elo_probs(home, away, game_date)
    if not probs:
        return None
    elo = probs["elo"]
    if abs(elo - 50.0) < 0.051:
        return None
    return elo


def _wnba_model_consensus_home_pct(home: str, away: str, game_date: str) -> float | None:
    """Home win % from the ensemble when the stored Sharp Consensus is 50%."""
    probs = _wnba_elo_probs(home, away, game_date)
    if not probs:
        return None
    ens = probs["ens"]
    if abs(ens - 50.0) < 0.051:
        return None
    return ens


def _paint_today_edge(rest: str, home: str, away: str, game_date: str) -> str:
    """Replace a 50% Edge box on 2026-09-30 with that Elo percent.

    Other dates keep the stored Edge, including a stored 50%.
    """
    if str(game_date or "")[:10] != "2026-09-30":
        return rest
    home_pct = _wnba_model_edge_home_pct(home, away, game_date)
    if home_pct is None:
        return rest
    team, side, pct = _favorite_of(home, away, home_pct)

    def _repl(match: re.Match[str]) -> str:
        box = match.group(0)
        name_m = re.search(r'class="pc-name">\s*([^<]+)', box)
        if not name_m or name_m.group(1).strip() != "Edge":
            return box
        val_m = re.search(r'class="pc-val"[^>]*>\s*([^<]*)', box)
        raw = val_m.group(1) if val_m else ""
        if not _pc_is_fifty(raw):
            return box
        return _rewrite_pc_box(box, pct, side, team)

    return re.sub(
        r'<div class="pc-box[^"]*">[\s\S]*?</div>\s*</div>',
        _repl,
        rest,
    )


def _uncopy_edge_percent(rest: str, copied_pct: float) -> str:
    """Take Efficiency's Prediction Lab percent off Edge.

    The stored Edge probability is the 50% placeholder. Do not invent a
    different Edge percent, and do not change Edge's side.
    """

    def _repl(match: re.Match[str]) -> str:
        box = match.group(0)
        name_m = re.search(r'class="pc-name">\s*([^<]+)', box)
        if not name_m or name_m.group(1).strip() != "Edge":
            return box
        val_m = re.search(r'class="pc-val"[^>]*>\s*([^<]*)', box)
        raw = (val_m.group(1) if val_m else "").replace("%", "").strip()
        try:
            num = float(raw)
        except ValueError:
            return box
        if abs(num - float(copied_pct)) >= 0.051:
            return box
        return re.sub(
            r'(<div class="pc-val"[^>]*>)[^<]*(</div>)',
            r"\g<1>50.0%\g<2>",
            box,
            count=1,
        )

    return re.sub(
        r'<div class="pc-box[^"]*">[\s\S]*?</div>\s*</div>',
        _repl,
        rest,
    )


def _lock_ensemble_pct(snap: dict[str, Any]) -> float | None:
    """Sharp Consensus is the stored ensemble. The lock stores it as a percent."""
    raw = snap.get("ensemble_prob")
    if raw is None:
        raw = snap.get("ens_prob")
    try:
        num = float(raw)
    except (TypeError, ValueError):
        return None
    if 0.0 <= num <= 1.0:
        num *= 100.0
    return round(num, 1)


def wnba_api_ensemble_win_percent(html: str, picks: list) -> list:
    """API winPercent follows the Sharp Consensus box, including stored 50%.

    The shared card reader treats 50% as empty and then fills Edge. That is
    not the ensemble, and it is not the Prediction Lab spread.
    """
    if not html or not picks:
        return picks
    percents: list[float | None] = []
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            continue
        tag = open_m.group(1)
        if not re.search(r'\bdata-home="[^"]+"', tag, flags=re.I):
            continue
        if not re.search(r'\bdata-away="[^"]+"', tag, flags=re.I):
            continue
        match = re.search(
            r'<div class="pc-name">\s*Sharp Consensus\s*</div>\s*'
            r'<div class="pc-val"[^>]*>\s*([\d.]+)\s*%',
            stack,
            flags=re.I,
        )
        if not match:
            percents.append(None)
            continue
        try:
            percents.append(round(float(match.group(1)), 1))
        except ValueError:
            percents.append(None)
    if len(percents) != len(picks):
        return picks
    out = []
    for row, pct in zip(picks, percents):
        item = dict(row) if isinstance(row, dict) else row
        if isinstance(item, dict) and pct is not None:
            item["winPercent"] = pct
        out.append(item)
    return out


def restore_wnba_stored_pick_face(html: str) -> str:
    """Put the green pick on the stored winner.

    Efficiency uses the stored Prediction Lab spread. XSharp uses the stored
    XSharp spread. Sharp Consensus keeps the stored ensemble. Edge keeps its
    own percent and is not painted with Efficiency's number. A blank
    Prediction Lab spread or score is filled from that same lock. The
    moneyline side stays the stored winner.
    """
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            out.append(stack)
            continue
        tag = open_m.group(1)
        rest = stack[open_m.end() :]
        away_m = re.search(r'data-away="([^"]*)"', tag)
        home_m = re.search(r'data-home="([^"]*)"', tag)
        gid_m = re.search(r'data-game-id="([^"]*)"', tag)
        if not away_m or not home_m:
            out.append(stack)
            continue
        away, home = away_m.group(1).strip(), home_m.group(1).strip()
        gid = gid_m.group(1) if gid_m else ""
        winner = _stored_winner_name(gid, home, away)
        snap = _stored_lock(gid) or {}
        margin = snap.get("lock_pl_spread")
        if not winner or margin is None:
            out.append(stack)
            continue
        home_pct = _home_pct_from_margin(float(margin))
        away_pct = round(100.0 - home_pct, 1)
        winner_home = _wnba_names_match(winner, home)
        sc_pct = home_pct if winner_home else away_pct
        if abs(sc_pct - 50.0) < 0.051:
            out.append(stack)
            continue

        painted = _paint_missing_pl_line(tag + rest, away, home)
        open2 = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", painted, flags=re.I)
        if open2:
            tag = open2.group(1)
            rest = painted[open2.end() :]

        row_m = re.search(
            r'(<div class="matchup-row">)([\s\S]*?)(</div>\s*<div class="lines-strip")',
            rest,
        )
        if row_m:
            row = row_m.group(2)
            seen = {"n": 0}

            def _wp(m: re.Match[str], _seen: dict[str, int] = seen) -> str:
                _seen["n"] += 1
                pct = away_pct if _seen["n"] == 1 else home_pct
                return f"{m.group(1)}{_fmt_pct(pct)}"

            if row.count("win-pct") < 2:
                added = {"n": 0}

                def _ins(m: re.Match[str], _added: dict[str, int] = added) -> str:
                    _added["n"] += 1
                    pct = away_pct if _added["n"] == 1 else home_pct
                    return (
                        m.group(0)
                        + f'\n        <div class="win-pct">{_fmt_pct(pct)}'
                        + '<span class="unit">%</span></div>'
                    )

                row = re.sub(r'<div class="team-name">[^<]+</div>', _ins, row, count=2)
            else:
                row = re.sub(r'(<div class="win-pct">)\s*[\d.]+', _wp, row, count=2)
            slot_i = {"n": 0}

            def _fav(m: re.Match[str], _slot: dict[str, int] = slot_i) -> str:
                _slot["n"] += 1
                fav = (_slot["n"] == 1 and not winner_home) or (_slot["n"] == 2 and winner_home)
                return f'class="team-slot{" favored" if fav else ""}"'

            row = re.sub(r'class="team-slot[^"]*"', _fav, row, count=2)
            rest = rest[: row_m.start(2)] + row + rest[row_m.end(2) :]

        ens_pct = _lock_ensemble_pct(snap)
        cons_side = "home" if winner_home else "away"
        cons_team = winner
        game_date = str(snap.get("game_date") or "")[:10]
        if game_date >= _wnba_slate_today() and (
            ens_pct is None or abs(float(ens_pct) - 50.0) < 0.051
        ):
            home_ens = _wnba_model_consensus_home_pct(home, away, game_date)
            if home_ens is not None:
                fav_team, fav_side, fav_pct = _favorite_of(home, away, home_ens)
                if _wnba_names_match(fav_team, winner):
                    cons_team, cons_side, ens_pct = fav_team, fav_side, fav_pct
                else:
                    # Keep Sharp Consensus on the stored pick. The percent is
                    # that side's ensemble chance, not the other team's.
                    ens_pct = round(100.0 - fav_pct, 1)

        def _sc(
            m: re.Match[str],
            _ens: float | None = ens_pct,
            _side: str = cons_side,
            _team: str = cons_team,
        ) -> str:
            box = m.group(0)
            name_m = re.search(r'class="pc-name">([^<]+)', box)
            name = (name_m.group(1) if name_m else "").strip()
            if name != "Sharp Consensus" or _ens is None:
                return box
            return _rewrite_pc_box(box, _ens, _side, _team)

        rest = re.sub(
            r'<div class="pc-box[^"]*">[\s\S]*?</div>\s*</div>',
            _sc,
            rest,
        )
        eff_team, eff_side, eff_pct = _favorite_of(home, away, home_pct)
        rest = _uncopy_edge_percent(rest, eff_pct)
        rest = _upsert_line_box(rest, "Efficiency", eff_pct, eff_side, eff_team)
        xs_home_pct = None
        xs_margin = snap.get("lock_xs_spread")
        if xs_margin is not None:
            xs_home_pct = _home_pct_from_margin(float(xs_margin))
        else:
            xs_attr = re.search(r'data-xs-spread="([^"]*)"', tag)
            if xs_attr and xs_attr.group(1).strip():
                xs_mg = _home_margin_from_side_line(xs_attr.group(1), home, away)
                if xs_mg is not None:
                    xs_home_pct = _home_pct_from_margin(xs_mg)
        if xs_home_pct is not None:
            xs_team, xs_side, xs_pct = _favorite_of(home, away, xs_home_pct)
            rest = _upsert_line_box(rest, "XSharp", xs_pct, xs_side, xs_team)
        rest = _paint_today_edge(rest, home, away, str(snap.get("game_date") or "")[:10])
        tag = _set_attr(tag, "data-pick", winner)
        tag = _set_attr(tag, "data-conf", _fmt_pct(sc_pct))
        if ens_pct is not None:
            tag = _set_attr(tag, "data-m-consensus", _fmt_pct(ens_pct))
        else:
            tag = _set_attr(
                tag,
                "data-m-consensus",
                _visible_model_pct(rest, "Sharp Consensus") or _fmt_pct(sc_pct),
            )
        for model_name, attr in (
            ("Edge", "data-m-edge"),
            ("XSharp", "data-m-xsharp"),
            ("Efficiency", "data-m-efficiency"),
        ):
            shown = _visible_model_pct(rest, model_name)
            if shown:
                tag = _set_attr(tag, attr, shown)
            elif model_name == "Edge":
                tag = _set_attr(tag, attr, "50.0")

        sides: dict[str, str] = {}
        for name in ("Edge", "XSharp", "Sharp Consensus", "Efficiency"):
            box_m = re.search(
                rf'<div class="pc-name">\s*{re.escape(name)}\s*</div>\s*'
                r'<div class="pc-val"[^>]*>\s*([^<]*)</div>\s*'
                r'<div class="pc-side([^"]*)"',
                rest,
                flags=re.I,
            )
            if not box_m:
                continue
            classes = box_m.group(2) or ""
            if re.search(r"\bhome\b", classes, flags=re.I):
                sides[name] = "HOME"
            elif re.search(r"\baway\b", classes, flags=re.I):
                sides[name] = "AWAY"
        if len(sides) >= 4:
            chip_m = re.search(
                r'(Consensus Historical Record</div>\s*<div class="line-chip-val">)([^<]*)',
                rest,
                flags=re.I,
            )
            if chip_m:
                rec_m = re.search(r"(\d+\s*-\s*\d+)", chip_m.group(2) or "")
                label = _hist_label(
                    sides, rec_m.group(1).replace(" ", "") if rec_m else "0-0"
                )
                if label:
                    rest = rest[: chip_m.start(2)] + label + rest[chip_m.end(2) :]
            elif '<div class="lines-strip">' in rest:
                label = _hist_label(sides, "0-0")
                if label:
                    chip = (
                        '<div class="line-chip consensus-hist-chip">'
                        '<div class="line-chip-label">Consensus Historical Record</div>'
                        f'<div class="line-chip-val">{label}</div></div>'
                    )
                    rest = rest.replace(
                        '<div class="lines-strip">',
                        '<div class="lines-strip">' + chip,
                        1,
                    )
        out.append(tag + rest)
    return _paint_posted_wnba_moneylines(
        _rewrite_wnba_books_na_hover(_paint_stored_wnba_book_lines("".join(out)))
    )


_WNBA_BOOK_CACHE: dict[str, tuple[float, dict | None]] = {}


def _fmt_american(price: int) -> str:
    return f"+{price}" if price > 0 else str(price)


def _face_books_ml(card: str) -> list[str]:
    return [
        re.sub(r"\s+", "", n).replace("−", "-")
        for n in re.findall(
            r'class="ml-line face-books-ml">[\s\S]*?class="ml-num[^"]*">\s*([^<]+)',
            card,
            flags=re.I,
        )
    ]


def _books_ml_is_placeholder(nums: list[str]) -> bool:
    if len(nums) < 2:
        return False
    pair = [n.upper() for n in nums[:2]]
    if pair == ["-108", "-108"]:
        return True
    return all(n in {"N/A", "NA", "", "—", "–", "-"} for n in pair)


def _posted_wnba_book_row(gid: str, home: str, away: str, game_date: str) -> dict | None:
    """Live book row for one game. None means the API has not posted a line."""
    import time

    now = time.time()
    hit = _WNBA_BOOK_CACHE.get(gid)
    if hit and now - hit[0] < 600:
        return hit[1]
    row = None
    try:
        from pl_book_odds_api import build_pl_book_odds

        row = build_pl_book_odds("WNBA", gid, home, away, game_date)
    except Exception:
        row = None
    if not isinstance(row, dict):
        row = None
    _WNBA_BOOK_CACHE[gid] = (now, row)
    return row


def _book_cell_has_number(raw: str) -> bool:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = text.replace("N/A", "").replace("NA", "")
    return bool(re.search(r"\d", text))


def _wnba_betting_spread_total(game_id: str) -> tuple[float, float] | None:
    """Stored book spread and total. None when that game has no posted line."""
    gid = (game_id or "").strip()
    if not gid:
        return None
    db = Path(__file__).resolve().parent / "sports_predictions_original.db"
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        row = conn.execute(
            "SELECT spread, total FROM betting_lines WHERE game_id=? "
            "AND spread IS NOT NULL AND total IS NOT NULL ORDER BY id DESC LIMIT 1",
            (gid,),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return float(row[0]), float(row[1])


def _spread_side_label(home: str, away: str, spread: float) -> str:
    """Negative home spread is the home favorite. Positive is the away favorite."""
    mag = abs(float(spread))
    if mag < 0.01:
        return "PK"
    side = home if float(spread) < 0 else away
    return f"{side} -{_fmt_half(mag)}"


def _fill_blank_book_cell(card: str, market: str, label: str) -> str:
    pattern = (
        rf'(class="market-k">\s*{market}</td>\s*<td class="val-books">)'
        rf'([\s\S]*?)(</td>)'
    )

    def _repl(match: re.Match[str]) -> str:
        if _book_cell_has_number(match.group(2)):
            return match.group(0)
        return match.group(1) + label + match.group(3)

    return re.sub(pattern, _repl, card, count=1, flags=re.I)


def _fill_blank_book_chip(card: str, label_name: str, value: str) -> str:
    pattern = (
        rf'(<div class="line-chip-label">{re.escape(label_name)}</div>\s*'
        rf'<div class="line-chip-val">)([\s\S]*?)(</div>)'
    )

    def _repl(match: re.Match[str]) -> str:
        if _book_cell_has_number(match.group(2)):
            return match.group(0)
        return match.group(1) + value + match.group(3)

    return re.sub(pattern, _repl, card, count=1, flags=re.I)


def _paint_stored_wnba_book_lines(html: str) -> str:
    """Fill a blank Books spread and total from the stored betting line.

    A game with no row stays N/A. This does not invent a price.
    """
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for card in parts[1:]:
        gid_m = re.search(r'data-game-id="([^"]*)"', card)
        home_m = re.search(r'data-home="([^"]*)"', card)
        away_m = re.search(r'data-away="([^"]*)"', card)
        if not gid_m or not home_m or not away_m:
            out.append(card)
            continue
        posted = _wnba_betting_spread_total(gid_m.group(1))
        if not posted:
            out.append(card)
            continue
        spread, total = posted
        spread_txt = _spread_side_label(home_m.group(1).strip(), away_m.group(1).strip(), spread)
        total_txt = _fmt_half(float(total))
        open_end = card.find(">")
        if open_end > 0:
            tag = card[: open_end + 1]

            def _attr_blank(name: str, _tag: str = tag) -> bool:
                found = re.search(rf'{re.escape(name)}="([^"]*)"', _tag)
                return not found or not _book_cell_has_number(found.group(1))

            if _attr_blank("data-books-spread"):
                tag = _set_attr(tag, "data-books-spread", spread_txt)
            if _attr_blank("data-books-total"):
                tag = _set_attr(tag, "data-books-total", total_txt)
            card = tag + card[open_end + 1 :]
        card = _fill_blank_book_cell(card, "Spread", spread_txt)
        card = _fill_blank_book_cell(card, "Total", total_txt)
        card = _fill_blank_book_chip(card, "Books spread", spread_txt)
        card = _fill_blank_book_chip(card, "Books total", f"O/U {total_txt}")
        out.append(card)
    return "".join(out)


def _paint_posted_wnba_moneylines(html: str) -> str:
    """Replace a placeholder -108/-108 pair when the book API has a price.

    Today and later only. A game with no posted line is left alone.
    Past cards are not rewritten.
    """
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    today = _wnba_slate_today()
    out = [parts[0]]
    for card in parts[1:]:
        gid_m = re.search(r'data-game-id="([^"]*)"', card)
        home_m = re.search(r'data-home="([^"]*)"', card)
        away_m = re.search(r'data-away="([^"]*)"', card)
        if not gid_m or not home_m or not away_m:
            out.append(card)
            continue
        if not _books_ml_is_placeholder(_face_books_ml(card)):
            out.append(card)
            continue
        snap = _stored_lock(gid_m.group(1)) or {}
        day = str(snap.get("game_date") or "")[:10]
        if len(day) != 10 or day < today:
            out.append(card)
            continue
        row = _posted_wnba_book_row(
            gid_m.group(1),
            home_m.group(1).strip(),
            away_m.group(1).strip(),
            day,
        )
        if not row:
            out.append(card)
            continue
        try:
            away_ml = int(row["away_moneyline"])
            home_ml = int(row["home_moneyline"])
        except (KeyError, TypeError, ValueError):
            out.append(card)
            continue
        prices = [_fmt_american(away_ml), _fmt_american(home_ml)]
        slot = {"n": 0}

        def _repl(match: re.Match[str], _prices: list[str] = prices, _slot: dict = slot) -> str:
            if _slot["n"] >= 2:
                return match.group(0)
            val = _prices[_slot["n"]]
            _slot["n"] += 1
            klass = "fav" if val.startswith("-") else "dog"
            return match.group(1) + klass + match.group(2) + val + match.group(4)

        card = re.sub(
            r'(class="ml-line face-books-ml">[\s\S]*?class="ml-num )[^"]*(">\s*)'
            r'([^<]+)(\s*</span>)',
            _repl,
            card,
            count=2,
            flags=re.I,
        )
        out.append(card)
    return "".join(out)


_WNBA_BOOKS_NA_TIP = "Odds are posted the day before or the day of the contest."
_WNBA_BOOKS_NA_OLD = "Odds will be here on the same day as the game."


def _rewrite_wnba_books_na_hover(html: str) -> str:
    """Unposted book lines keep N/A and the day-before / day-of hover.

    A posted spread is left as printed. A -108/-108 pair on a card whose
    book spread is N/A is not a posted price.
    """
    if not html:
        return html
    if _WNBA_BOOKS_NA_OLD in html:
        html = html.replace(_WNBA_BOOKS_NA_OLD, _WNBA_BOOKS_NA_TIP)
    if "data-pick-card" not in html or "-108" not in html:
        return html
    btn = (
        '<button type="button" class="h2h-info-btn" '
        f'title="{_WNBA_BOOKS_NA_TIP}" aria-label="{_WNBA_BOOKS_NA_TIP}">i</button>'
    )
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for card in parts[1:]:
        books = re.findall(r'class="val-books">([\s\S]*?)</td>', card, flags=re.I)
        posted = False
        for cell in books:
            text = re.sub(r"<[^>]+>", "", cell)
            text = text.replace("N/A", "").replace("NA", "").strip()
            if re.search(r"\d", text):
                posted = True
                break
        face = re.findall(
            r'class="ml-line face-books-ml">[\s\S]*?class="ml-num[^"]*">\s*([^<]+)',
            card,
            flags=re.I,
        )
        nums = [re.sub(r"\s+", "", n).replace("−", "-") for n in face]
        # A -108/-108 pair is the unpublished coinflip, not a posted price.
        if posted or nums != ["-108", "-108"]:
            out.append(card)
            continue

        def _na(match: re.Match[str]) -> str:
            tail = match.group(3) or btn
            return match.group(1) + "N/A</span>" + tail

        card = re.sub(
            r'(class="ml-line face-books-ml">[\s\S]*?class="ml-num[^"]*">\s*)'
            r'(?:−|-)?108'
            r'(\s*</span>)(\s*<button\b[^>]*class="[^"]*h2h-info-btn[^"]*"[^>]*>[\s\S]*?</button>)?',
            _na,
            card,
            count=2,
            flags=re.I,
        )
        out.append(card)
    return "".join(out)


_WNBA_PUBLIC_ORIGIN = "https://predictionlab.io"


def rewrite_wnba_share_hrefs(html: str) -> str:
    """Point share-icon hrefs at the public site. Leave every other href."""
    if not html or "share-icon" not in html:
        return html

    def _publicize(href: str) -> str:
        href = re.sub(
            r"https?%3A(?://|%2F%2F)(?:localhost|127\.0\.0\.1)(?:%3A\d+|:\d+)?",
            "https%3A//predictionlab.io",
            href,
            flags=re.I,
        )
        return re.sub(
            r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?",
            _WNBA_PUBLIC_ORIGIN,
            href,
            flags=re.I,
        )

    def _repl(match: re.Match[str]) -> str:
        href = match.group(2)
        if "localhost" not in href.lower() and "127.0.0.1" not in href:
            return match.group(0)
        return match.group(1) + _publicize(href) + match.group(3)

    return re.sub(
        r'(<a\b[^>]*\bclass="[^"]*\bshare-icon\b[^"]*"[^>]*\bhref=")([^"]*)(")',
        _repl,
        html,
        flags=re.I,
    )


def apply_wnba_picks_fixups(html: str) -> str:
    """Chart attrs + H2H L10 enrich + real face % + hide empty Grinder2/Takedown boxes."""
    if not html or "data-pick-card" not in html:
        return html
    try:
        html = dedupe_game_card_stacks(html)
    except Exception:
        pass
    html = enrich_mlb_chart_data_attrs(html)
    try:
        html = enrich_wnba_h2h_from_db(html)
    except Exception:
        pass
    try:
        html = fill_wnba_placeholder_probs(html)
    except Exception:
        pass
    try:
        html = fill_wnba_card_gaps(html)
    except Exception:
        pass
    html = hide_unavailable_model_boxes(html)
    html = strip_wnba_g2_td_tiles(html)
    try:
        html = restore_wnba_stored_pick_face(html)
    except Exception:
        pass
    try:
        from team_results_charts import _inject_wnba_pl_vs_books_chips

        html = _inject_wnba_pl_vs_books_chips(html)
    except Exception:
        pass
    return html


def strip_wnba_g2_td_tiles(html: str) -> str:
    """Remove Grinder2 / Takedown tally and season-grid tiles. WNBA does not publish them."""
    if not html or ("Grinder2" not in html and "Takedown" not in html):
        return html
    card_re = re.compile(
        r'<div class="(?:daily-tally-card|model-card|pc-box)[^"]*"',
        re.I,
    )
    parts: list[str] = []
    pos = 0
    while True:
        m = card_re.search(html[pos:])
        if not m:
            parts.append(html[pos:])
            break
        abs_start = pos + m.start()
        box, end = _balanced_div_at(html, abs_start)
        if end < 0:
            parts.append(html[pos:])
            break
        parts.append(html[pos:abs_start])
        if not re.search(r"Grinder2|Takedown", box):
            parts.append(box)
        pos = end
    return "".join(parts)


def _inject_wnba_graded_decisions_note(html: str) -> str:
    """Audit clarity: Model Performance / Season count decisions, not all finals."""
    if not html or "graded decision" in html.lower():
        return html
    note_mp = (
        '<p style="text-align:center;margin:0 0 14px;font-size:0.78em;color:#64748b;">'
        "Percentages are <strong>unit ROI</strong> (profit per $1 risked), not moneyline "
        "win rate. Records count <strong>graded decisions</strong> — not every completed "
        "final (no model pick, missing line, or no-lean / push is omitted).</p>"
    )
    if "Model Performance (Flat Unit Tracking)" in html and "graded decisions" not in html:
        html = re.sub(
            r'(Model Performance \(Flat Unit Tracking)</h2>\s*)'
            r'(?:<p\b[^>]*>[\s\S]*?</p>\s*)?',
            r"\1" + note_mp,
            html,
            count=1,
            flags=re.I,
        )
    note_sp = (
        '<p style="text-align:center;margin:0 0 12px;font-size:0.78em;color:#64748b;">'
        "W-L tiles count <strong>graded model decisions</strong>, which can be fewer "
        "than completed games on the slate.</p>"
    )
    if re.search(r"Season Performance", html, flags=re.I) and "graded model decisions" not in html:
        html = re.sub(
            r'(🏆\s*)?Season Performance([^<]*)</h2>\s*'
            r'(?:<p\b[^>]*>[\s\S]*?</p>\s*)?',
            r"\1Season Performance\2</h2>" + note_sp,
            html,
            count=1,
            flags=re.I,
        )
    return html


def _wnba_today_iso() -> str:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d")


_WNBA_SLATE_CACHE: dict[str, tuple[float, list[tuple[str, str]]]] = {}


def wnba_scoreboard_matchups(days_forward: int = 7) -> list[tuple[str, str]]:
    """Today and later WNBA games on the scoreboard. (date, 'Away @ Home').

    A TBD placeholder is not a posted game. An empty list means the
    scoreboard could not be read.
    """
    import time

    now = time.time()
    hit = _WNBA_SLATE_CACHE.get("rows")
    if hit and now - hit[0] < 600:
        return list(hit[1])
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("America/New_York")).date()
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    events: list = []
    for offset in range(0, days_forward + 1):
        ymd = (today + timedelta(days=offset)).strftime("%Y%m%d")
        data = _wnba_espn_json(
            "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"
            f"?dates={ymd}&limit=50"
        )
        events.extend(data.get("events") or [])
    for ev in events:
        if not isinstance(ev, dict):
            continue
        raw = str(ev.get("date") or "")
        try:
            stamp = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            dt = datetime.fromisoformat(stamp)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            day = dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except Exception:
            continue
        if day < today.isoformat():
            continue
        comps = (ev.get("competitions") or [{}])[0] or {}
        home = away = ""
        for side in comps.get("competitors") or []:
            if not isinstance(side, dict):
                continue
            team = side.get("team") or {}
            name = (team.get("displayName") or team.get("name") or "").strip()
            if str(side.get("homeAway") or "") == "home":
                home = name
            elif str(side.get("homeAway") or "") == "away":
                away = name
        if not home or not away or "TBD" in home.upper() or "TBD" in away.upper():
            continue
        key = (day, f"{away} @ {home}")
        if key in seen:
            continue
        seen.add(key)
        rows.append(key)
    rows.sort()
    _WNBA_SLATE_CACHE["rows"] = (now, rows)
    return rows


def extend_wnba_result_dates(html: str) -> str:
    """Put scoreboard dates on the results picker. Past finals stay listed."""
    if not html or "const allDates" not in html:
        return html
    match = re.search(r"const allDates = (\[[^\]]*\])", html)
    if not match:
        return html
    try:
        listed = json.loads(match.group(1))
    except json.JSONDecodeError:
        listed = []
    today = _wnba_today_iso()
    slate = wnba_scoreboard_matchups()
    if not slate and today in listed:
        return html
    dates = sorted({str(d)[:10] for d in listed if str(d)[:10]} | {day for day, _ in slate} | {today})
    html = html.replace(match.group(0), "const allDates = " + json.dumps(dates), 1)
    html = html.replace(
        "activeDate=allDates[lastIdx];",
        "activeDate=(allDates.filter(function(d){return d<=today;}).pop())||allDates[lastIdx];",
        1,
    )
    have = set(re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', html))
    by_day: dict[str, list[str]] = {}
    for day, matchup in slate:
        by_day.setdefault(day, []).append(matchup)
    blocks = []
    for day in dates:
        if day in have or day < today:
            continue
        games = "".join(f"<p>{name}</p>" for name in by_day.get(day) or [])
        today_tag = (
            ' <span style="background:#067647;color:white;padding:3px 10px;'
            'border-radius:4px;font-size:0.65em;margin-left:8px;">TODAY</span>'
            if day == today
            else ""
        )
        blocks.append(
            f'<div id="date-{day}" class="date-section">'
            f'<div class="date-header">📅 {day}{today_tag}</div>'
            f'<div class="games-grid">{games}</div></div>'
        )
    if blocks and "/* ── Date slider ── */" in html:
        # The marker sits inside a <script>. The sections are HTML, so they go
        # in front of that script tag. Inside it they are a JavaScript syntax error.
        marker_at = html.find("/* ── Date slider ── */")
        script_at = html.rfind("<script", 0, marker_at)
        closed_at = html.rfind("</script>", 0, marker_at)
        if script_at >= 0 and script_at > closed_at:
            html = html[:script_at] + "".join(blocks) + "\n" + html[script_at:]
        else:
            html = html[:marker_at] + "".join(blocks) + "\n" + html[marker_at:]
    return html


def wnba_chart_date_nav(cards_html: str = "") -> str:
    """The chart date picker. Same dates as the cards page plus the scoreboard."""
    today = _wnba_today_iso()
    dates = set(re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', cards_html or ""))
    dates.update(day for day, _name in wnba_scoreboard_matchups())
    dates.add(today)
    listed = json.dumps(sorted(d for d in dates if d <= today))
    return (
        '<div class="date-nav" id="wnba-chart-dates">'
        '<div class="date-bubbles" id="dateBubbles"></div></div>'
        "<script>"
        f"const allDates = {listed};"
        f"const today = {json.dumps(today)};"
        "(function(){"
        "var box=document.getElementById('dateBubbles');"
        "if(!box)return;"
        "allDates.forEach(function(day){"
        "var b=document.createElement('a');"
        "b.className='date-bubble'+(day===today?' today':'');"
        "b.href='/wnba-results#date-'+day;"
        "b.textContent=day;"
        "box.appendChild(b);"
        "});"
        "})();"
        "</script>"
    )


def ensure_wnba_chart_dates(html: str, cards_html: str = "") -> str:
    """Chart view keeps its tables and gets the date picker back."""
    if not html:
        return html
    if 'id="dateBubbles"' in html and "const allDates" in html:
        return extend_wnba_result_dates(html)
    nav = wnba_chart_date_nav(cards_html)
    anchor = html.find('id="ssr-finals"')
    if anchor >= 0:
        anchor = html.rfind("<section", 0, anchor)
    if anchor < 0:
        anchor = html.find("Consensus Based Betting Records")
        if anchor >= 0:
            block = html.rfind('<div class="pl-consensus-records"', 0, anchor)
            anchor = block if block >= 0 else html.rfind("<h2", 0, anchor)
    if anchor > 0:
        return html[:anchor] + nav + html[anchor:]
    if re.search(r"</main>", html, flags=re.I):
        return re.sub(r"</main>", nav + "</main>", html, count=1, flags=re.I)
    return html + nav


def apply_wnba_results_fixups(html: str) -> str:
    """Cards|Chart toggle + last-7/season window from game cards."""
    if not html:
        return html
    try:
        html = strip_wnba_g2_td_tiles(html)
    except Exception:
        pass
    # Do not hide published Edge/XSharp/SC/Efficiency boxes on results —
    # that zeroed chart tiles to "0-0 · no picks" while spread still graded.
    try:
        html = patch_wnba_last7_from_cards(html)
    except Exception:
        pass
    try:
        html = patch_wnba_season_from_cards(html)
    except Exception:
        pass
    try:
        html = patch_wnba_cards_ml_face(html)
    except Exception:
        pass
    try:
        html = _inject_wnba_graded_decisions_note(html)
    except Exception:
        pass
    html = inject_wnba_results_view_toggle(html, active="normal")
    try:
        html = extend_wnba_result_dates(html)
    except Exception:
        pass
    return html


WNBA_ML_MODEL_NAMES = ("Edge", "XSharp", "Sharp Consensus", "Efficiency")


def _wnba_has_pick(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    pick = str(row.get("pick") or "").strip()
    return bool(pick) and pick not in {"—", "-", "–", "n/a", "N/A"}


def _wnba_model_pct_n(row: dict[str, Any] | None) -> tuple[float | None, int]:
    if not row:
        return None, 0
    rec = str(row.get("record") or "")
    parts = [p for p in re.split(r"[-–]", rec) if p.strip().isdigit()]
    n = int(row.get("n") or 0)
    if n <= 0 and len(parts) >= 2:
        n = int(parts[0]) + int(parts[1])
    pct = row.get("pct")
    if pct is None and n > 0 and len(parts) >= 2:
        pct = round(100.0 * int(parts[0]) / n, 1)
    try:
        pct_f = float(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct_f = None
    return pct_f, n


def wnba_best_ml_model(models: dict[str, Any] | None) -> dict[str, Any] | None:
    """Highest season (or same-universe) accuracy among published WNBA ML models.

    Published set: Edge, XSharp, Sharp Consensus, Efficiency. No G2/TD.
    Ties go to the larger sample, then the later published name.
    """
    best: dict[str, Any] | None = None
    for name in WNBA_ML_MODEL_NAMES:
        row = (models or {}).get(name) or {}
        pct, n = _wnba_model_pct_n(row)
        if n <= 0 or pct is None:
            continue
        rec = row.get("record") or f"{row.get('w') or 0}-{row.get('l') or 0}"
        cand = {
            "name": name,
            "pct": pct,
            "n": n,
            "record": rec,
            "w": row.get("w"),
            "l": row.get("l"),
        }
        if best is None:
            best = cand
            continue
        if pct > best["pct"] or (pct == best["pct"] and n >= best["n"]):
            best = cand
    return best


def wnba_published_ml_from_html(html: str) -> dict[str, dict[str, Any]]:
    """Season Moneyline Accuracy by Model tiles (fallback: first model-grid)."""
    chunk = html or ""
    m = re.search(r"Moneyline Accuracy by Model([\s\S]{0,6000})", chunk, flags=re.I)
    if m:
        chunk = m.group(1)
    out: dict[str, dict[str, Any]] = {}
    for name in WNBA_ML_MODEL_NAMES:
        mm = re.search(
            rf'(?:model-label|daily-model)">[^<]*{re.escape(name)}</div>\s*'
            rf'<div class="(?:model-acc|daily-acc)"[^>]*>\s*([^<]*)</div>\s*'
            rf'<div class="(?:model-rec|daily-rec)">\s*([^<]*)',
            chunk,
            flags=re.I,
        )
        if not mm:
            continue
        rec = (mm.group(2) or "").strip()
        parts = [p for p in re.split(r"[-–]", rec) if p.strip().isdigit()]
        if len(parts) < 2:
            continue
        w, l = int(parts[0]), int(parts[1])
        n = w + l
        if n <= 0:
            continue
        pct_s = (mm.group(1) or "").strip().replace("%", "")
        try:
            pct = float(pct_s)
        except ValueError:
            pct = round(100.0 * w / n, 1)
        out[name] = {
            "name": name,
            "w": w,
            "l": l,
            "n": n,
            "pct": pct,
            "record": f"{w}-{l}",
        }
    return out


_WNBA_PROJ_RE = re.compile(
    r"(.+?)\s+(\d+(?:\.\d+)?)\s*[–-]\s*(.+?)\s+(\d+(?:\.\d+)?)"
)


def _wnba_names_match(a: str, b: str) -> bool:
    sa = re.sub(r"[^a-z0-9]+", "", (a or "").lower())
    sb = re.sub(r"[^a-z0-9]+", "", (b or "").lower())
    if not sa or not sb:
        return False
    return sa == sb or sa in sb or sb in sa


def _fill_wnba_final_from_pl_proj(row: dict[str, Any]) -> None:
    """When ML boxes are N/A, grade the published PL projected score."""
    if not isinstance(row, dict):
        return
    models = row.get("models") if isinstance(row.get("models"), dict) else {}
    if any(_wnba_has_pick(m) for m in models.values()):
        return
    proj = str(row.get("pl_proj") or "")
    m = _WNBA_PROJ_RE.search(proj)
    if not m:
        return
    away_n, away_p, home_n, home_p = m.group(1).strip(), float(m.group(2)), m.group(3).strip(), float(m.group(4))
    pick = home_n if home_p >= away_p else away_n
    total = home_p + away_p
    prob = round(100.0 * max(home_p, away_p) / total, 1) if total else None
    hs, aws = row.get("home_score"), row.get("away_score")
    correct = None
    if hs is not None and aws is not None:
        winner = row.get("home_team_id") if float(hs) > float(aws) else row.get("away_team_id")
        if _wnba_names_match(pick, str(winner or "")):
            correct = True
        elif _wnba_names_match(pick, str(row.get("home_team_id") or "")) or _wnba_names_match(
            pick, str(row.get("away_team_id") or "")
        ):
            correct = False
    models = dict(models)
    models["Prediction Lab"] = {"pick": pick, "prob": prob, "correct": correct}
    row["models"] = models


def apply_wnba_best_ml_face(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Point chart moneyline face (pick / % / Correct-Wrong) at the best model."""
    if not payload:
        return payload or {}
    markets = payload.get("markets") or {}
    ml = markets.get("moneyline") or {}
    season = ((ml.get("tallies") or payload.get("tallies") or {}).get("season") or {})
    best = wnba_best_ml_model(season.get("models") or {})
    name = (best or {}).get("name") or "Prediction Lab"
    payload["ml_face_model"] = name
    ml["face_model"] = name
    finals = list(ml.get("finals") or payload.get("finals") or [])
    for row in finals:
        _fill_wnba_final_from_pl_proj(row)
        models = row.get("models") or {}
        face = models.get(name)
        if not _wnba_has_pick(face):
            face = next((models[k] for k in models if _wnba_has_pick(models[k])), None)
        if _wnba_has_pick(face):
            row["face_pick"] = face.get("pick")
            row["face_prob"] = face.get("prob")
            row["correct"] = face.get("correct")
            row["face_model"] = face.get("name") or name
        else:
            row["face_pick"] = "—"
            row["face_prob"] = None
            row["correct"] = None
            row["face_model"] = name
    if "finals" in ml or finals:
        ml["finals"] = finals
    if "finals" in payload:
        payload["finals"] = finals
    ml["ungraded"] = sum(1 for f in finals if f.get("correct") is None)
    markets["moneyline"] = ml
    payload["markets"] = markets
    return payload


def _wnba_banner_color(pct: float) -> str:
    if pct >= 55:
        return "#067647"
    if pct >= 50:
        return "#fbbf24"
    return "#D93025"


def patch_wnba_cards_ml_face(html: str) -> str:
    """Season / last-night / last-7 moneyline headline = best published model."""
    if not html:
        return html
    models = wnba_published_ml_from_html(html)
    best = wnba_best_ml_model(models)
    if not best:
        return html
    name = best["name"]
    pct = best["pct"]
    rec = best["record"]
    color = _wnba_banner_color(float(pct))
    html = re.sub(
        r"(🎯 Moneyline)(?:\s*\([^)]*\))?(</div>\s*)"
        r'(<div style="font-size:2em;font-weight:bold;color:)[^"]+(">)[^<]*(?:</div>)*'
        r"(\s*"
        r'<div style="font-size:0\.85em;opacity:0\.9;color:#334155;">)\s*\d{1,3}-\d{1,3}',
        rf"\g<1> ({name})\g<2>\g<3>{color}\g<4>{pct}%</div>\g<5>{rec}",
        html,
        count=1,
    )
    html = re.sub(
        r'(<div class="(?:daily-tally-card|model-card)\s*)highlight(\s*")',
        r"\1\2",
        html,
    )
    html = re.sub(
        rf'(<div class="(?:daily-tally-card|model-card)\s*)(">\s*'
        rf'<div class="(?:daily-model|model-label)">[^<]*{re.escape(name)})',
        r"\1highlight\2",
        html,
    )
    return html


def relabel_wnba_chart_ml_pick_header(html: str, model_name: str) -> str:
    if not html or not model_name:
        return html
    return html.replace("<th>Edge pick</th>", f"<th>{model_name} pick</th>")


def wnba_ml_table_still_grades_edge(html: str) -> bool:
    """True when the games table still treats Edge as the primary column."""
    return bool(html) and "<th>Edge pick</th>" in html


def wnba_sou_tile_names(
    order: list[str] | None,
    models: dict[str, Any] | None,
    *,
    market: str,
) -> list[str]:
    """Names to render on WNBA Spread/Totals. Never empty ML clones."""
    models = models or {}
    incoming = [n for n in (order or []) if n]
    if not incoming:
        incoming = [n for n, row in models.items() if int((row or {}).get("n") or 0) > 0]
    ml = set(WNBA_ML_MODEL_NAMES)
    names = [
        n
        for n in incoming
        if n not in {"Grinder2", "Takedown"}
        and (n not in ml or int((models.get(n) or {}).get("n") or 0) > 0)
    ]
    if names:
        return names
    if models.get("Prediction Lab"):
        return ["Prediction Lab"]
    xs = models.get("XSharp") or {}
    if int(xs.get("n") or 0) > 0:
        return ["XSharp"]
    return ["Prediction Lab"] if str(market or "").lower() != "moneyline" else list(
        WNBA_ML_MODEL_NAMES
    )


def wnba_results_html_has_empty_ml_sou_tiles(html: str) -> bool:
    """True when Edge/XSharp/SC/Efficiency are 0-0 · no picks under Spread/Totals.

    The owner's broken chart: season headline 68-31 while every named ML tile
    under SPREAD / TOTALS is empty. Moneyline tiles are allowed to use those names.
    """
    if not html:
        return False
    records = [
        (int(w), int(l))
        for w, l in re.findall(
            r"(?:Spread|Season)[^\n<]{0,200}?(\d{1,3})-(\d{1,3})",
            html,
            flags=re.I | re.S,
        )
    ]
    if not records or max(w + l for w, l in records) < 20:
        return False

    chunks: list[str] = []
    for m in re.finditer(
        r'(?:<div class="bet-type-banner"[^>]*>\s*(SPREAD|TOTALS|RUN LINE)[^<]*</div>)'
        r'|(?:<h2[^>]*>\s*(?:Spread|Totals|O/U)\b[^<]*</h2>)',
        html,
        flags=re.I,
    ):
        start = m.end()
        nxt = re.search(
            r'<div class="bet-type-banner"|<h2[^>]*>\s*Moneyline\b',
            html[start:],
            flags=re.I,
        )
        end = start + (nxt.start() if nxt else 8000)
        chunks.append(html[start:end])
    if not chunks:
        # Banner-less paste: treat the whole doc if it names SPREAD + ML 0-0.
        if re.search(r"\bSPREAD\b|\bTOTALS\b", html, flags=re.I):
            chunks = [html]

    empty_re = re.compile(
        rf'(?:daily-model|model-label|mlabel)">\s*[^<]*({"|".join(WNBA_ML_MODEL_NAMES)})\s*</div>\s*'
        rf'<div class="(?:daily-acc|model-acc|acc)[^"]*"[^>]*>\s*(?:—|-|–|0%|0\.0%)?\s*</div>\s*'
        rf'<div class="(?:daily-rec|model-rec|rec)[^"]*"[^>]*>\s*0-0(?:\s*·\s*no picks)?',
        flags=re.I,
    )
    for chunk in chunks:
        hits = empty_re.findall(chunk)
        if len(set(hits)) >= 3:
            return True
    return False


def render_wnba_chart_market_tallies(payload: dict[str, Any], market: str) -> str:
    """Server stand-in for team-results.js Spread/Totals grids (test + contract)."""
    markets = (payload or {}).get("markets") or {}
    block = markets.get(market) or {}
    tallies = block.get("tallies") or {}
    market_order = list(block.get("model_order") or [])
    parts = [f'<div class="bet-type-banner">{market.upper()}</div>']
    face = ((tallies.get("season") or {}).get("models") or {}).get("Prediction Lab") or {}
    if face:
        parts.append(
            f'<p>Spread · Season {face.get("pct")}% ({face.get("record")})</p>'
            if market == "spread"
            else f'<p>Totals · Season {face.get("pct")}% ({face.get("record")})</p>'
        )
    for wk, title in (
        ("last_night", "Last Night"),
        ("last_7", "Last 7"),
        ("season", "Season"),
    ):
        win = tallies.get(wk) or {}
        models = win.get("models") or {}
        names = wnba_sou_tile_names(
            win.get("model_order") or market_order, models, market=market
        )
        parts.append(f'<section class="tally"><h2>{title}</h2><div class="tally-grid">')
        for name in names:
            row = models.get(name) or {}
            n = int(row.get("n") or 0)
            rec = row.get("record") or f"{row.get('w') or 0}-{row.get('l') or 0}"
            pct = row.get("pct")
            acc = f"{pct}%" if pct is not None and n else "—"
            note = ""
            if n <= 0:
                note = " · no O/U data" if market == "totals" else " · no spread data"
            parts.append(
                f'<div class="tally-card"><div class="mlabel">{name}</div>'
                f'<div class="acc">{acc}</div>'
                f'<div class="rec">{rec}{note}</div></div>'
            )
        parts.append("</div></section>")
    return "".join(parts)


def render_wnba_results_chart_page(payload: dict[str, Any] | None = None) -> str:
    """MLB-template Cards|Chart chart view for /wnba-results?view=chart."""
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from mlb_results_ui import inject_ssr_chart_bootstrap

    root = Path(__file__).resolve().parent
    env = Environment(
        loader=FileSystemLoader(str(root / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("team_results.html").render(
        sport="wnba",
        sport_label="WNBA",
        api_base="/wnba/api",
        show_league=False,
        picks_href="/wnba-picks",
        results_href="/wnba-results",
    )
    html = inject_wnba_results_view_toggle(html, active="chart")
    if 'id="league-controls" hidden' not in html:
        html = html.replace('id="league-controls"', 'id="league-controls" hidden')
    if payload:
        try:
            payload = apply_wnba_best_ml_face(payload)
            html = inject_ssr_chart_bootstrap(html, payload, "wnba")
            face = payload.get("ml_face_model") or ""
            if face:
                html = relabel_wnba_chart_ml_pick_header(html, str(face))
        except Exception as e:
            print(f"[wnba_ui_fixup] chart SSR bootstrap: {e}", flush=True)
    return html
