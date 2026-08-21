#!/usr/bin/env python3
"""Soccer picks publish-layer UI: H2H Last 10 display + hide empty Total EV.

Display only. Does not set Efficiency ``our_total`` / ``our_spread`` or change
soccer pick math. Alias-aware H2H is ported from isolation
``~/Documents/Personal/soccer/engine/h2h_lookup.py`` (min_games=1, all
competitions). True first meetings stay empty.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import sqlite3
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

_LIVE_ROOT = Path(__file__).resolve().parent
_ISO_SOCCER = Path.home() / "Documents/Personal/soccer"
_ESPN_IDS = _LIVE_ROOT / "data" / "soccer_team_espn_ids.json"
_ISO_ESPN_IDS = _ISO_SOCCER / "data" / "soccer_team_espn_ids.json"

_PREFIXES = (
    "cd ",
    "cf ",
    "fc ",
    "ca ",
    "rc ",
    "club ",
    "atletico de ",
    "atletico ",
    "deportivo ",
)
_SUFFIXES = (" cf", " fc", " sc", " ac")

_BAD = ("", "—", "-", "–", "‒", "N/A", "n/a")


def _fold(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.strip().lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _cores(name: str) -> set[str]:
    f = _fold(name)
    out = {f} if f else set()
    for p in _PREFIXES:
        if f.startswith(p):
            rest = f[len(p) :].strip()
            if rest:
                out.add(rest)
    for suf in _SUFFIXES:
        if f.endswith(suf):
            rest = f[: -len(suf)].strip()
            if rest:
                out.add(rest)
    return out


def _alias_key(name: str) -> str:
    """Strip FC/CD/Atletico prefixes so Atlético Madrid == Atletico de Madrid."""
    f = _fold(name)
    changed = True
    while changed and f:
        changed = False
        for p in _PREFIXES:
            if f.startswith(p) and len(f) > len(p) + 1:
                f = f[len(p) :].strip()
                changed = True
        for suf in _SUFFIXES:
            if f.endswith(suf) and len(f) > len(suf) + 1:
                f = f[: -len(suf)].strip()
                changed = True
    return f


H2H_MISSING_NO_HISTORY = (
    "No prior completed meetings between these clubs in our records."
)
H2H_MISSING_NO_TEAMS = (
    "Matchup teams are missing, so head-to-head cannot be shown."
)


@lru_cache(maxsize=1)
def _espn_id_map() -> dict[str, str]:
    for path in (_ESPN_IDS, _ISO_ESPN_IDS):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out: dict[str, str] = {}
        for k, v in (raw or {}).items():
            fk = _fold(str(k))
            sid = str(v).strip()
            if fk and sid:
                out[fk] = sid
        if out:
            return out
    return {}


def _espn_id_for(name: str) -> str | None:
    m = _espn_id_map()
    f = _fold(name)
    if f in m:
        return m[f]
    for c in _cores(name):
        if c == f:
            continue
        # Single-token cores ("madrid") are too loose for ID lookup.
        if len(c.split()) < 2 and len(c) < 10:
            continue
        if c in m:
            return m[c]
    return None


def aliases_for(name: str, db_names: Iterable[str]) -> list[str]:
    """All DB spellings that are the same club as ``name``."""
    if not name:
        return []
    want_id = _espn_id_for(name)
    want_key = _alias_key(name)
    want_cores = _cores(name)
    found: list[str] = []
    seen: set[str] = set()
    for raw in (name, *db_names):
        if not raw or raw in seen:
            continue
        other_id = _espn_id_for(raw)
        if want_id and other_id:
            if other_id == want_id:
                seen.add(raw)
                found.append(raw)
            continue
        other_key = _alias_key(raw)
        if want_key and other_key and want_key == other_key:
            if want_id and other_id and want_id != other_id:
                continue
            seen.add(raw)
            found.append(raw)
            continue
        shared = want_cores & _cores(raw)
        substantial = any(len(c.split()) >= 2 and len(c) >= 8 for c in shared)
        if substantial:
            if want_id and other_id and want_id != other_id:
                continue
            seen.add(raw)
            found.append(raw)
    if name not in seen:
        found.insert(0, name)
    return found


_DB_TEAM_NAMES_CACHE: dict[str, tuple[float, list[str]]] = {}


def _db_team_names(conn: sqlite3.Connection) -> list[str]:
    path = ""
    mtime = 0.0
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        path = str((row[2] if row and len(row) > 2 else "") or "")
        if path:
            mtime = Path(path).stat().st_mtime
    except Exception:
        path = ""
    cache_key = path or f"conn:{id(conn)}"
    cached = _DB_TEAM_NAMES_CACHE.get(cache_key)
    if cached and cached[0] == mtime and cached[1]:
        return cached[1]
    rows = conn.execute(
        """
        SELECT DISTINCT home_team_id FROM games WHERE sport = 'SOCCER' AND home_team_id IS NOT NULL
        UNION
        SELECT DISTINCT away_team_id FROM games WHERE sport = 'SOCCER' AND away_team_id IS NOT NULL
        """
    ).fetchall()
    names = [str(r[0]) for r in rows if r and r[0]]
    _DB_TEAM_NAMES_CACHE[cache_key] = (mtime, names)
    return names


def h2h_projection(
    conn: sqlite3.Connection,
    home_team: str,
    away_team: str,
    n: int = 10,
    min_games: int = 1,
    before_date: str | None = None,
) -> dict[str, Any] | None:
    """Last-N scored H2H across every league in ``games``. Alias-aware."""
    if not home_team or not away_team:
        return None
    names = _db_team_names(conn)
    homes = aliases_for(home_team, names)
    aways = aliases_for(away_team, names)
    if not homes or not aways:
        return None
    placeholders_h = ",".join("?" * len(homes))
    placeholders_a = ",".join("?" * len(aways))
    before = (str(before_date)[:10] if before_date else None)
    as_of = "AND date(game_date) < date(?)" if before else ""
    sql = f"""
        SELECT home_team_id, away_team_id, home_score, away_score, game_date
        FROM games
        WHERE sport = 'SOCCER'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          {as_of}
          AND (
                (home_team_id IN ({placeholders_h}) AND away_team_id IN ({placeholders_a}))
             OR (home_team_id IN ({placeholders_a}) AND away_team_id IN ({placeholders_h}))
          )
        ORDER BY date(game_date) DESC
        LIMIT ?
    """
    params = ([before] if before else []) + [*homes, *aways, *aways, *homes, int(n)]
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return None
    if not rows or len(rows) < min_games:
        return None
    home_set = set(homes)
    home_pts: list[float] = []
    away_pts: list[float] = []
    totals: list[float] = []
    home_wins = away_wins = draws = 0
    for r in rows:
        try:
            ht, at, hs, as_ = r[0], r[1], float(r[2]), float(r[3])
        except (TypeError, ValueError, IndexError):
            continue
        if ht in home_set:
            hp, ap = hs, as_
        else:
            hp, ap = as_, hs
        home_pts.append(hp)
        away_pts.append(ap)
        totals.append(hs + as_)
        if hp > ap:
            home_wins += 1
        elif ap > hp:
            away_wins += 1
        else:
            draws += 1
    if len(home_pts) < min_games:
        return None
    avg_home = sum(home_pts) / len(home_pts)
    avg_away = sum(away_pts) / len(away_pts)
    return {
        "games_used": len(home_pts),
        "avg_home": round(avg_home, 2),
        "avg_away": round(avg_away, 2),
        "our_total": round(avg_home + avg_away, 1),
        "totals": totals,
        "home_wins": home_wins,
        "away_wins": away_wins,
        "draws": draws,
    }


def _fmt_half(n: float) -> str:
    r = round(float(n) * 2.0) / 2.0
    return str(int(r)) if r == int(r) else str(r)


def format_h2h_last10(
    conn: sqlite3.Connection,
    home_team: str,
    away_team: str,
    n: int = 10,
    min_games: int = 1,
) -> str:
    """Face string like ``1.5 (2 games)``, or empty when no history."""
    proj = h2h_projection(conn, home_team, away_team, n=n, min_games=min_games)
    if not proj:
        return ""
    g = int(proj["games_used"])
    games = f"{g} game" if g == 1 else f"{g} games"
    return f"{_fmt_half(float(proj['our_total']))} ({games})"


@lru_cache(maxsize=1)
def _iso_h2h_fns():
    """Clone H2H only — isolation helper has no before-date (look-ahead)."""
    return format_h2h_last10, h2h_projection


def _h2h_db_paths() -> list[Path]:
    paths = [
        _LIVE_ROOT / "sports_predictions_original.db",
        _LIVE_ROOT / "data" / "sports_predictions_original.db",
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _open_h2h_conns() -> list[sqlite3.Connection]:
    out: list[sqlite3.Connection] = []
    for path in _h2h_db_paths():
        if not path.is_file():
            continue
        try:
            out.append(sqlite3.connect(str(path)))
        except Exception:
            continue
    return out


def _best_h2h_proj(
    home: str,
    away: str,
    conns: list[sqlite3.Connection],
    before_date: str | None = None,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_n = -1
    for conn in conns:
        try:
            proj = h2h_projection(
                conn, home, away, n=10, min_games=1, before_date=before_date,
            )
        except Exception:
            proj = None
        if not proj:
            continue
        n = int(proj.get("games_used") or 0)
        if n > best_n:
            best = proj
            best_n = n
    return best


def _best_h2h_text(
    home: str,
    away: str,
    conns: list[sqlite3.Connection],
    before_date: str | None = None,
) -> str:
    proj = _best_h2h_proj(home, away, conns, before_date=before_date)
    if not proj:
        return ""
    g = int(proj["games_used"])
    games = f"{g} game" if g == 1 else f"{g} games"
    return f"{_fmt_half(float(proj['our_total']))} ({games})"


def h2h_missing_reason(*, home: str = "", away: str = "") -> str:
    if not home or not away:
        return H2H_MISSING_NO_TEAMS
    return H2H_MISSING_NO_HISTORY


def fill_soccer_h2h_display_fields(predictions: list | None) -> None:
    """Set ``h2h_last10_*`` only. Never writes ``our_total`` / ``our_spread``."""
    if not predictions:
        return
    conns = _open_h2h_conns()
    if not conns:
        for pred in predictions:
            if not isinstance(pred, dict):
                continue
            if pred.get("h2h_last10_total") is not None:
                continue
            pred.setdefault("h2h_last10_total", None)
            pred.setdefault("h2h_last10_games", 0)
            pred["h2h_missing_reason"] = H2H_MISSING_NO_HISTORY
        return
    try:
        for pred in predictions:
            if not isinstance(pred, dict):
                continue
            ht = pred.get("home_team_id") or pred.get("home")
            at = pred.get("away_team_id") or pred.get("away")
            if not ht or not at:
                pred.setdefault("h2h_last10_total", None)
                pred.setdefault("h2h_last10_games", 0)
                pred["h2h_missing_reason"] = H2H_MISSING_NO_TEAMS
                continue
            before = None
            if pred.get("home_score") is not None:
                before = (pred.get("date") or pred.get("game_date") or "")[:10] or None
            proj = _best_h2h_proj(str(ht), str(at), conns, before_date=before)
            if not proj:
                pred.setdefault("h2h_last10_total", None)
                pred.setdefault("h2h_last10_games", 0)
                pred["h2h_missing_reason"] = h2h_missing_reason(home=str(ht), away=str(at))
                continue
            pred["h2h_missing_reason"] = None
            pred["h2h_last10_total"] = proj["our_total"]
            pred["h2h_last10_games"] = proj["games_used"]
            pred["h2h_last10_home_wins"] = proj.get("home_wins")
            pred["h2h_last10_away_wins"] = proj.get("away_wins")
            pred["h2h_last10_draws"] = proj.get("draws")
    finally:
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass


def _good(val: str) -> bool:
    return bool(val) and val.strip() not in _BAD


def strip_soccer_empty_total_ev(html: str) -> str:
    """Drop soccer Total EV chips that are empty / dash (do not invent EV)."""
    if not html or "Total EV" not in html:
        return html
    chip_re = re.compile(
        r'<div\b[^>]*\bclass="[^"]*\bsf-item\b[^"]*"[^>]*>\s*'
        r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*Total\s*EV\s*</span>\s*'
        r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]*?)\s*</span>\s*'
        r"</div>",
        flags=re.I,
    )

    def _keep(m: re.Match) -> str:
        raw = (m.group(1) or "").strip()
        if not _good(raw) or not re.search(r"\d", raw):
            return ""
        return m.group(0)

    return chip_re.sub(_keep, html)


def enrich_soccer_h2h_from_db(html: str) -> str:
    """Fill data-h2h + View Details H2H chip when scored history exists.

    Display-only. First meetings get ⓘ + reason — never a silent dash.
    """
    if not html or "data-pick-card" not in html:
        return html

    conns = _open_h2h_conns()
    if not conns:
        return html

    cache: dict[tuple[str, str, str], str] = {}

    def _lookup(home: str, away: str, before_date: str | None = None) -> str:
        if not home or not away:
            return ""
        key = (home, away, before_date or "")
        if key in cache:
            return cache[key]
        val = _best_h2h_text(home, away, conns, before_date=before_date)
        cache[key] = val
        return val

    def _set_attr(tag: str, name: str, value: str) -> str:
        esc = (
            (value or "")
            .replace("&", "&amp;")
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

    def _chip_html(h2h: str, *, missing_reason: str = "") -> str:
        extra = ""
        if missing_reason:
            extra = " " + _info_btn(missing_reason)
        return (
            '<div class="sf-item">'
            '<span class="sf-label">H2H Last 10</span> '
            f'<span class="sf-val">{html_lib.escape(h2h)}</span>'
            f"{extra}"
            "</div>"
        )

    def _ensure_chip(rest: str, h2h: str, *, missing_reason: str = "") -> str:
        display = h2h if _good(h2h) else "—"
        chip_re = re.compile(
            r'<div\b[^>]*\bclass="[^"]*\bsf-item\b[^"]*"[^>]*>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*H2H\s*Last\s*10\s*</span>\s*'
            r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*([^<]*?)\s*</span>'
            r'(?:\s*<button\b[^>]*\bpl-info-btn\b[^>]*>\s*ⓘ\s*</button>)?'
            r'\s*</div>',
            flags=re.I,
        )
        m = chip_re.search(rest)
        replacement = _chip_html(display, missing_reason=missing_reason)
        if m:
            return rest[: m.start()] + replacement + rest[m.end() :]
        foot_m = re.search(
            r'(<div\b[^>]*\bclass="[^"]*\bodds-extras-footer\b[^"]*"[^>]*>)',
            rest,
            flags=re.I,
        )
        if foot_m:
            return rest[: foot_m.end()] + "\n        " + replacement + rest[foot_m.end() :]
        return rest

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

    def _resolve_h2h(
        home: str,
        away: str,
        existing: str,
        chip_val: str,
        before_date: str | None = None,
    ) -> str:
        db_h2h = _lookup(home, away, before_date) if home and away else ""
        if _good(db_h2h):
            return db_h2h
        if _good(existing) and existing.strip() not in ("N/A", "n/a"):
            return existing
        if _good(chip_val) and chip_val.strip() not in ("N/A", "n/a"):
            return chip_val
        return ""

    def _names_from_open(open_tag: str) -> tuple[str, str]:
        def _attr(*names: str) -> str:
            for name in names:
                m = re.search(rf'\b{name}="([^"]*)"', open_tag, flags=re.I)
                if m:
                    return html_lib.unescape((m.group(1) or "").strip())
            return ""

        home = _attr("data-home-full", "data-home")
        away = _attr("data-away-full", "data-away")
        return home, away

    def _patch_stack(stack: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            return stack
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        existing = ""
        am = re.search(r'\bdata-h2h="([^"]*)"', open_tag, flags=re.I)
        if am:
            existing = html_lib.unescape((am.group(1) or "").strip())
        chip_val = _chip_value(rest)
        home, away = _names_from_open(open_tag)
        before = ""
        dm = re.search(r'\bdata-date="([^"]*)"', open_tag, flags=re.I)
        if dm:
            before = (html_lib.unescape((dm.group(1) or "").strip()) or "")[:10]
        # Completed cards (FINAL / scored) must not include this kickoff.
        is_final = bool(re.search(r'\bdata-time="FINAL"', open_tag, flags=re.I))
        before_date = before or None
        if is_final and not before_date:
            before_date = None
        h2h = _resolve_h2h(home, away, existing, chip_val, before_date if is_final else None)
        missing_reason = "" if _good(h2h) else h2h_missing_reason(home=home, away=away)
        display = h2h if _good(h2h) else "N/A"
        open2 = _set_attr(open_tag, "data-h2h", display)
        if missing_reason:
            open2 = _set_attr(open2, "data-h2h-reason", missing_reason)
        rest2 = _ensure_chip(rest, display if _good(h2h) else "—", missing_reason=missing_reason)
        if open2 == open_tag and rest2 == rest:
            return stack
        return open2 + rest2

    out = html
    try:
        parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", out, flags=re.I)
        if len(parts) > 1:
            out = parts[0] + "".join(_patch_stack(p) for p in parts[1:])
    finally:
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
    return out


def _replace_balanced_div(html: str, open_re: str, replacement: str) -> tuple[str, bool]:
    m = re.search(open_re, html, flags=re.I)
    if not m:
        return html, False
    start = m.start()
    tag_end = html.find(">", start) + 1
    depth = 1
    j = tag_end
    end = -1
    while j < len(html) and depth > 0:
        next_open = html.find("<div", j)
        next_close = html.find("</div>", j)
        if next_close < 0:
            break
        if next_open >= 0 and next_open < next_close:
            depth += 1
            j = next_open + 4
        else:
            depth -= 1
            if depth == 0:
                end = next_close + 6
                break
            j = next_close + 6
    if end < 0:
        return html, False
    return html[:start] + replacement + html[end:], True


def _html_attr(s: str) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _html_text(s: str) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _soccer_league_pill_label(inner: str, *, is_live: bool) -> tuple[str, str]:
    raw = inner or ""
    count_m = re.search(
        r'<span[^>]*\bleague-pill-count\b[^>]*>(.*?)</span>',
        raw,
        flags=re.I | re.S,
    )
    count_txt = ""
    if count_m:
        count_txt = re.sub(r"<[^>]+>", "", count_m.group(1) or "")
        count_txt = re.sub(r"\s+", " ", count_txt).strip()
    wo_count = re.sub(
        r'<span[^>]*\bleague-pill-count\b[^>]*>.*?</span>',
        " ",
        raw,
        flags=re.I | re.S,
    )
    wo_count = re.sub(
        r'<span[^>]*\blive-dot\b[^>]*>.*?</span>',
        " ",
        wo_count,
        flags=re.I | re.S,
    )
    name = re.sub(r"<[^>]+>", " ", wo_count)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        return "", ""
    if not count_txt:
        glued = re.match(r"^(.*?)(\d{1,4})$", name)
        if glued and glued.group(1).strip() and not glued.group(1).strip()[-1:].isdigit():
            name = glued.group(1).strip()
            count_txt = glued.group(2)
    label = f"{name} ({count_txt})" if count_txt else name
    if is_live and name.lower() not in ("all leagues", "all"):
        label = f"{label} · Live"
    return name, label


def _soccer_href_for_kind(href: str, kind: str) -> str:
    href = href or ""
    if kind == "picks":
        href = re.sub(r"/soccer-results\b", "/soccer-picks", href)
        href = re.sub(r"[?&]view=chart\b", "", href)
        href = href.replace("?&", "?").rstrip("?&")
        return href or "/soccer-picks"
    href = re.sub(r"/soccer-picks\b", "/soccer-results", href)
    if kind == "chart":
        if "view=chart" not in href:
            href = href + ("&" if "?" in href else "?") + "view=chart"
        return href
    href = re.sub(r"[?&]view=chart\b", "", href)
    href = href.replace("?&", "?").rstrip("?&")
    return href or "/soccer-results"


def _options_from_soccer_select(html: str, *, kind: str) -> list[dict[str, str]]:
    if not html or 'id="league"' not in html:
        return []
    m = re.search(r'<select[^>]*\bid="league"[^>]*>([\s\S]*?)</select>', html, flags=re.I)
    if not m:
        return []
    options: list[dict[str, str]] = []
    for om in re.finditer(r"<option\b([^>]*)>([\s\S]*?)</option>", m.group(1), flags=re.I):
        attrs, inner = om.group(1), om.group(2)
        label = re.sub(r"<[^>]+>", "", inner or "")
        label = re.sub(r"\s+", " ", label).strip()
        if not label:
            continue
        href_m = re.search(r'data-href="([^"]*)"', attrs, flags=re.I)
        href = href_m.group(1) if href_m else ""
        val_m = re.search(r'value="([^"]*)"', attrs, flags=re.I)
        slug = val_m.group(1) if val_m else ""
        if label.lower() in ("all leagues", "all"):
            slug = ""
            label = "All"
        href = _soccer_href_for_kind(href, kind) if href else ""
        if not href:
            if kind == "picks":
                href = f"/soccer-picks?league={slug}" if slug else "/soccer-picks"
            elif kind == "chart":
                href = f"/soccer-results?league={slug}&view=chart" if slug else "/soccer-results?view=chart"
            else:
                href = f"/soccer-results?league={slug}" if slug else "/soccer-results"
        options.append(
            {
                "slug": slug,
                "href": href,
                "label": label,
                "selected": "1" if re.search(r"\bselected\b", attrs, flags=re.I) else "",
            }
        )
    return options


def _options_from_soccer_pills(html: str, *, kind: str) -> list[dict[str, str]]:
    if not html or ("league-slider" not in html and "league-pill" not in html):
        return []
    pills = list(
        re.finditer(
            r'<a\b([^>]*\bclass="[^"]*\bleague-pill\b[^"]*"[^>]*)>(.*?)</a>',
            html,
            flags=re.I | re.S,
        )
    )
    options: list[dict[str, str]] = []
    for m in pills:
        attrs, inner = m.group(1), m.group(2)
        href_m = re.search(r'href="([^"]*)"', attrs, flags=re.I)
        href = href_m.group(1) if href_m else ""
        is_live = bool(
            re.search(r"\blive-league\b", attrs, flags=re.I)
            or "live-dot" in (inner or "")
        )
        name, label = _soccer_league_pill_label(inner, is_live=is_live)
        if not name:
            continue
        slug_m = re.search(r"[?&]league=([^&\"'#]+)", href)
        slug = slug_m.group(1) if slug_m else ""
        if name.lower() in ("all leagues", "all"):
            slug = ""
            label = "All"
            if kind == "picks":
                href = "/soccer-picks"
            elif kind == "chart":
                href = "/soccer-results?view=chart"
            else:
                href = "/soccer-results"
        href = _soccer_href_for_kind(href, kind)
        options.append(
            {
                "slug": slug,
                "href": href,
                "label": label,
                "selected": "1" if re.search(r"\bactive\b", attrs, flags=re.I) else "",
            }
        )
    return options


def _soccer_filter_href(*, kind: str, league: str = "", region: str = "") -> str:
    parts = []
    if region:
        parts.append(f"region={region}")
    if league:
        parts.append(f"league={league}")
    if kind == "picks":
        base = "/soccer-picks"
        qs = "&".join(parts)
        return f"{base}?{qs}" if qs else base
    base = "/soccer-results"
    if kind == "chart":
        parts.append("view=chart")
    qs = "&".join(parts)
    return f"{base}?{qs}" if qs else base


def _league_names_from_html_cards(html: str) -> list[str]:
    """Unique catalog league names already on the page's pick/result cards."""
    if not html:
        return []
    try:
        from soccer_league_catalog import SOCCER_LEAGUE_ORDER, _SOCCER_LEAGUE_CANONICAL
    except Exception:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r'\bdata-league="([^"]*)"', html, flags=re.I):
        raw = (m.group(1) or "").strip()
        if not raw or raw.lower() in ("other", "all", "all leagues"):
            continue
        name = _SOCCER_LEAGUE_CANONICAL.get(raw.lower()) or raw
        if name not in SOCCER_LEAGUE_ORDER or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _curated_soccer_league_options(
    *,
    kind: str,
    selected_slug: str = "",
    selected_region: str = "",
    live_names: list[str] | None = None,
    source_html: str | None = None,
) -> list[dict[str, str]]:
    """Catalog leagues, narrowed to the selected continent / Live slate."""
    selected = (selected_slug or "").strip()
    region = (selected_region or "").strip().lower()
    try:
        from soccer_league_catalog import (
            SOCCER_LEAGUE_ORDER,
            SOCCER_REGION_DEFS,
            soccer_leagues_for_region,
            soccer_primary_region,
            soccer_region_from_slug,
        )
        import NHL77FINAL as nhl

        slug_fn = nhl._soccer_league_slug
        region_key = soccer_region_from_slug(region)
        resolved_live = list(live_names) if live_names is not None else None
        if region_key == "live":
            if resolved_live is None:
                try:
                    resolved_live = list(nhl._soccer_live_competition_names(kind=kind) or [])
                except Exception:
                    resolved_live = []
            if source_html:
                have = set(resolved_live)
                for name in _league_names_from_html_cards(source_html):
                    if name not in have:
                        resolved_live.append(name)
                        have.add(name)
        names = soccer_leagues_for_region(region_key, live_names=resolved_live)
        if not region_key and region != "all":
            names = list(SOCCER_LEAGUE_ORDER)
        region_labels = {key: label for key, label in SOCCER_REGION_DEFS}
    except Exception:
        return []
    if region == "all":
        keep_region = "all"
    elif region_key:
        keep_region = region_key
    else:
        keep_region = ""
    selected_in_list = False
    if selected:
        selected_l = selected.lower()
        try:
            resolved = nhl._soccer_league_from_slug(selected)
            if resolved:
                selected_l = slug_fn(resolved).lower()
        except Exception:
            pass
        selected_in_list = any(slug_fn(n).lower() == selected_l for n in names)
    options = [
        {
            "slug": "",
            "href": _soccer_filter_href(kind=kind, region=keep_region),
            "label": "All",
            "group": "",
            "selected": "1" if not selected_in_list else "",
        }
    ]
    for name in names:
        slug = slug_fn(name)
        primary = soccer_primary_region(name) or ""
        options.append(
            {
                "slug": slug,
                "href": _soccer_filter_href(kind=kind, league=slug, region=keep_region),
                "label": name,
                "group": region_labels.get(primary, ""),
                "selected": "1" if selected_in_list and selected_l == slug.lower() else "",
            }
        )
    return options


def soccer_showing_scope_label(
    *,
    selected_region: str = "",
    options: list[dict[str, str]] | None = None,
    league_slug: str = "",
) -> str:
    """Human scope line matching Continent/League dropdown labels."""
    region = (selected_region or "").strip().lower()
    try:
        from soccer_league_catalog import SOCCER_REGION_DEFS
        region_defs = list(SOCCER_REGION_DEFS)
    except Exception:
        region_defs = []
    if (not region) or region == "all":
        continent = "All continents"
    else:
        continent = next((lab for key, lab in region_defs if key == region), region)
        if region == "live":
            continent = "Live"
    opts = options or []
    selected_league = next(
        (
            (opt.get("label") or "").strip()
            for opt in opts
            if opt.get("selected") and (opt.get("slug") or "").strip()
        ),
        "",
    )
    if not selected_league and league_slug:
        want = league_slug.strip().lower()
        for opt in opts:
            if (opt.get("slug") or "").strip().lower() == want:
                selected_league = (opt.get("label") or "").strip()
                break
    league = selected_league or "All leagues"
    return f"Showing: {continent} · {league}"


def soccer_league_dropdown_html(
    options: list[dict[str, str]],
    *,
    kind: str = "results",
    selected_region: str = "",
) -> str:
    if not options:
        return ""
    region = (selected_region or "").strip().lower()
    try:
        from soccer_league_catalog import SOCCER_REGION_DEFS
        region_defs = list(SOCCER_REGION_DEFS)
    except Exception:
        region_defs = []
    all_region = "all" if kind == "picks" else ""
    all_selected = (not region) or region == "all"
    region_tags = [
        f'<option value="" data-href="{_html_attr(_soccer_filter_href(kind=kind, region=all_region))}"'
        f'{" selected" if all_selected else ""}>All</option>'
    ]
    for key, label in region_defs:
        sel = " selected" if region == key else ""
        href = _soccer_filter_href(kind=kind, region=key)
        region_tags.append(
            f'<option value="{_html_attr(key)}" data-href="{_html_attr(href)}"{sel}>'
            f'{_html_text(label)}</option>'
        )
    grouped: dict[str, list[dict[str, str]]] = {}
    ungrouped: list[dict[str, str]] = []
    for opt in options:
        group = (opt.get("group") or "").strip()
        if not opt.get("slug"):
            ungrouped.append(opt)
            continue
        if group and not region:
            grouped.setdefault(group, []).append(opt)
        else:
            ungrouped.append(opt)
    option_tags = []
    for opt in ungrouped:
        sel = " selected" if opt.get("selected") else ""
        option_tags.append(
            f'<option value="{_html_attr(opt.get("slug", ""))}" '
            f'data-href="{_html_attr(opt.get("href", ""))}"{sel}>'
            f'{_html_text(opt.get("label", ""))}</option>'
        )
    if grouped:
        try:
            from soccer_league_catalog import SOCCER_REGION_DEFS
            group_order = [label for _key, label in SOCCER_REGION_DEFS]
        except Exception:
            group_order = list(grouped)
        for gname in group_order:
            rows = grouped.get(gname) or []
            if not rows:
                continue
            option_tags.append(f'<optgroup label="{_html_attr(gname)}">')
            for opt in rows:
                sel = " selected" if opt.get("selected") else ""
                option_tags.append(
                    f'<option value="{_html_attr(opt.get("slug", ""))}" '
                    f'data-href="{_html_attr(opt.get("href", ""))}"{sel}>'
                    f'{_html_text(opt.get("label", ""))}</option>'
                )
            option_tags.append("</optgroup>")
    fallback = _soccer_filter_href(kind=kind, region=region)
    if kind == "chart":
        form_action = "/soccer-results"
        view_hidden = '<input type="hidden" name="view" value="chart" />'
    elif kind == "picks":
        form_action = "/soccer-picks"
        view_hidden = ""
    else:
        form_action = "/soccer-results"
        view_hidden = ""
    showing = soccer_showing_scope_label(
        selected_region=region, options=options,
    )
    return f"""
<section class="controls soccer-league-controls" id="league-controls" aria-label="League filter">
  <form method="GET" action="{_html_attr(form_action)}" id="soccer-league-filter-form"
        class="soccer-league-filter-form" data-fallback-href="{_html_attr(fallback)}">
    {view_hidden}
    <label>
      Continent
      <select id="soccer-region" name="region" aria-label="Select continent">
        {"".join(region_tags)}
      </select>
    </label>
    <label>
      League
      <select id="league" name="league" aria-label="Select league">
        {"".join(option_tags)}
      </select>
    </label>
    <button type="submit" id="soccer-league-load" class="soccer-league-load"
            aria-label="Load selected continent and league">Load</button>
  </form>
  <p class="soccer-showing-scope" id="soccer-showing-scope" aria-live="polite">{_html_text(showing)}</p>
</section>
<style id="soccer-league-dropdown-css">
.soccer-league-controls{{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px 18px;
  max-width:1100px;margin:8px auto 14px;padding:0 16px;}}
.soccer-league-filter-form{{display:flex;flex-wrap:wrap;align-items:flex-end;gap:12px 18px;
  width:100%;margin:0;}}
.soccer-league-controls label{{display:flex;flex-direction:column;gap:6px;
  font-size:0.78rem;font-weight:700;color:#475569;letter-spacing:0.02em;}}
.soccer-league-controls select{{min-width:min(100%,280px);max-width:420px;padding:8px 12px;
  border:1px solid #cbd5e1;border-radius:8px;background:#fff;color:#0f172a;
  font-size:0.95rem;font-weight:600;}}
.soccer-league-load{{padding:9px 18px;border:1px solid #0f172a;border-radius:8px;
  background:#0f172a;color:#fff;font-size:0.9rem;font-weight:700;cursor:pointer;
  letter-spacing:0.02em;}}
.soccer-league-load:disabled{{opacity:0.7;cursor:wait;}}
.soccer-showing-scope{{flex:1 1 100%;margin:2px 0 0;padding:0;font-size:0.92rem;
  font-weight:650;color:#0f172a;letter-spacing:0.01em;}}
.league-slider{{display:none!important;}}
</style>
<script id="soccer-league-dropdown-js">
(function(){{
  var form=document.getElementById('soccer-league-filter-form');
  var regionSel=document.getElementById('soccer-region');
  var leagueSel=document.getElementById('league');
  var loadBtn=document.getElementById('soccer-league-load');
  var base=(form && form.getAttribute('action')) || '{form_action}';
  var fallback=(form && form.getAttribute('data-fallback-href')) || '{fallback}';
  var isPicks=base.indexOf('soccer-picks')>=0;
  function buildHref(clearLeague){{
    var opt=null, dh='';
    if(!clearLeague && leagueSel && leagueSel.selectedIndex>=0){{
      opt=leagueSel.options[leagueSel.selectedIndex];
      dh=(opt && opt.getAttribute('data-href')) || '';
      if(dh) return dh;
    }}
    if(clearLeague && regionSel && regionSel.selectedIndex>=0){{
      opt=regionSel.options[regionSel.selectedIndex];
      dh=(opt && opt.getAttribute('data-href')) || '';
      if(dh) return dh;
    }}
    var parts=[];
    var rv=regionSel ? String(regionSel.value || '') : '';
    var lv=(!clearLeague && leagueSel) ? String(leagueSel.value || '') : '';
    if(rv) parts.push('region='+encodeURIComponent(rv));
    else if(isPicks) parts.push('region=all');
    if(lv) parts.push('league='+encodeURIComponent(lv));
    var view=form && form.querySelector('input[name="view"]');
    if(view && view.value) parts.push('view='+encodeURIComponent(view.value));
    return parts.length ? (base+'?'+parts.join('&')) : (fallback || base);
  }}
  function go(clearLeague){{
    var href=buildHref(!!clearLeague);
    if(loadBtn){{ loadBtn.disabled=true; loadBtn.textContent='Loading…'; }}
    if(href) window.location.assign(href);
  }}
  function bindChange(sel, clearLeague){{
    if(!sel || sel.dataset.soccerDropdownBound==='1') return;
    sel.dataset.soccerDropdownBound='1';
    sel.addEventListener('change', function(){{ go(clearLeague); }});
  }}
  bindChange(regionSel, true);
  bindChange(leagueSel, false);
  if(form && form.dataset.soccerFormBound!=='1'){{
    form.dataset.soccerFormBound='1';
    form.addEventListener('submit', function(ev){{
      ev.preventDefault();
      go(false);
    }});
  }}
}})();
</script>
<script id="soccer-region-dropdown-js"></script>
"""


def _inject_soccer_league_dropdown_block(html: str, block: str) -> str:
    if not html or not block:
        return html
    if re.search(r'<section[^>]*\bid="league-controls"', html, flags=re.I):
        html2, n = re.subn(
            r'<section\b[^>]*\bid="league-controls"[^>]*>[\s\S]*?</section>'
            r'(?:\s*<style\b[^>]*\bid="soccer-league-dropdown-css"[^>]*>[\s\S]*?</style>)?'
            r'(?:\s*<script\b[^>]*\bid="soccer-league-dropdown-js"[^>]*>[\s\S]*?</script>)?'
            r'(?:\s*<script\b[^>]*\bid="soccer-region-dropdown-js"[^>]*>[\s\S]*?</script>)?',
            block,
            html,
            count=1,
            flags=re.I,
        )
        if n:
            return html2
    if "league-slider" in html:
        html2, replaced = _replace_balanced_div(
            html,
            r'<div class="league-slider\b[^"]*"[^>]*>',
            block,
        )
        if replaced:
            return html2
    for pat in (
        r'(<div class="pl-view-toggle"[\s\S]*?</div>\s*(?:<style>[\s\S]*?</style>)?)',
        r'(<div class="section-tabs"[\s\S]*?</div>)',
        r'(<h1 class="page-title"[^>]*>[\s\S]*?</h1>)',
        r'(<header class="top"[^>]*>[\s\S]*?</header>)',
        r'(<main\b[^>]*>)',
    ):
        html2, n = re.subn(pat, r"\1" + block, html, count=1, flags=re.I)
        if n:
            return html2
    return block + html


def ensure_soccer_league_dropdown(
    html: str,
    *,
    kind: str = "results",
    league: str = "",
    region: str = "",
    source_html: str | None = None,
    live_names: list[str] | None = None,
) -> str:
    """League + continent filters. All first; leagues for that continent/Live only."""
    if not html:
        return html
    look = html.lower()
    if not any(tok in look for tok in ('<html', '<main', '<body', 'game-card', 'league-slider', 'league-controls')):
        return html
    src = source_html or html
    options = _curated_soccer_league_options(
        kind=kind,
        selected_slug=league,
        selected_region=region,
        live_names=live_names,
        source_html=src,
    )
    if not options:
        options = _options_from_soccer_pills(src, kind=kind)
        if not options:
            options = _options_from_soccer_select(src, kind=kind)
    if not options:
        return html
    if league:
        hit = False
        want = {league.strip().lower()}
        try:
            import NHL77FINAL as nhl
            resolved = nhl._soccer_league_from_slug(league)
            if resolved:
                want.add(nhl._soccer_league_slug(resolved).lower())
        except Exception:
            pass
        for opt in options:
            if (opt.get("slug") or "").lower() in want:
                opt["selected"] = "1"
                hit = True
            elif opt.get("slug"):
                opt["selected"] = ""
        if hit:
            for opt in options:
                if not opt.get("slug"):
                    opt["selected"] = ""
        else:
            # Stale league (e.g. Championship after switching to Asia) → All.
            for opt in options:
                opt["selected"] = "1" if not opt.get("slug") else ""
    elif not any(opt.get("selected") for opt in options):
        options[0]["selected"] = "1"
    block = soccer_league_dropdown_html(
        options, kind=kind, selected_region=region,
    )
    html = _inject_soccer_league_dropdown_block(html, block)
    if 'id="soccer-league-dropdown-js"' in html and "league-slider" in html:
        html, _ = _replace_balanced_div(
            html,
            r'<div class="league-slider\b[^"]*"[^>]*>',
            "",
        )
    return html


_INFO_ASSET_MARK = 'id="pl-info-tips-css"'
_INFO_JS_MARK = "pl-info-tips.js"

# User-facing copy only — no vendor / blend / training-pipeline wording.
_TIP_ML = (
    "Win-loss record for {model} moneyline picks in this results view. "
    "The percentage is how often the pick was correct (wins ÷ graded games). "
    "It is a hit rate, not units won."
)
_TIP_SPREAD = (
    "Win-loss record for {model} spread picks in this results view. "
    "The percentage is cover rate (covers ÷ graded games). "
    "It is a hit rate, not units won."
)
_TIP_OU = (
    "Win-loss record for {model} over/under picks in this results view. "
    "The percentage is how often the total pick was correct (wins ÷ graded games). "
    "It is a hit rate, not units won."
)
_TIP_PICK_CONF = (
    "Each box is that model's estimated chance its side wins. "
    "The number is moneyline confidence — not a spread or totals pick."
)
_TIP_EFFICIENCY = (
    "Efficiency is our recent-form model. The percentage is how strongly it "
    "favors its moneyline side."
)
_TIP_EDGE = (
    "Difference between our win probability and the sportsbook implied "
    "probability. Positive means our model sees more value than the posted price."
)
_TIP_WIN_PCT = (
    "This is the model's estimated chance that team wins the match. "
    "A draw chance is shown separately when listed."
)
_TIP_PLXG = (
    "Prediction Lab expected goals from each club’s recent form "
    "(home and away), not a head-to-head last-10 average. "
    "When it is missing, the ⓘ explains that a team does not have "
    "enough prior matches."
)
_TIP_H2H = _TIP_PLXG
_TIP_DRAW_ML = (
    "Home or away moneyline picks. A draw counts as a loss for these 1X2 picks."
)
_TIP_SPREAD_LINE = (
    "The posted handicap for this match. Our spread pick is the side we "
    "expect to cover that line."
)
_TIP_ASR = (
    "Wins-losses for this model in the current season sample. "
    "The percentage above is hit rate (wins ÷ graded games), not units won."
)


def _info_btn(tip: str, extra_class: str = "") -> str:
    text = " ".join((tip or "").split())
    esc = html_lib.escape(text, quote=True)
    cls = "pl-info-btn" + ((" " + extra_class) if extra_class else "")
    return (
        f'<button type="button" class="{cls}" data-tip="{esc}" '
        f'data-pl-info-tip="{esc}" title="{esc}" aria-label="{esc}" '
        f'aria-expanded="false" aria-haspopup="true">ⓘ</button>'
    )


def _model_from_heading(heading: str) -> str:
    raw = re.sub(r"<[^>]+>", "", heading or "")
    raw = re.sub(r"[🎯📈🎲🏆]", "", raw).strip()
    m = re.search(r"\(([^)]+)\)", raw)
    if m:
        return m.group(1).strip() or "this model"
    return "this model"


def _season_tip_for_heading(heading: str) -> str:
    low = heading.lower()
    model = _model_from_heading(heading)
    if "moneyline" in low or "ml " in low or low.startswith("ml"):
        return _TIP_ML.format(model=model)
    if "spread" in low or "puck" in low or "run line" in low:
        return _TIP_SPREAD.format(model=model)
    if "o/u" in low or "over" in low or "total" in low:
        return _TIP_OU.format(model=model)
    return _TIP_ASR


_SEASON_INFO_SPAN = re.compile(
    r'(<div style="font-size:0\.8em;[^"]*"[^>]*>)([^<]+)(</div>[\s\S]{0,500}?)'
    r'(<span(?=[^>]*\btitle="Number of Games")[^>]*>\s*ⓘ\s*</span>)',
    flags=re.I,
)
_BARE_NUMBER_OF_GAMES = re.compile(
    r'<span(?=[^>]*\btitle="Number of Games")[^>]*>\s*ⓘ\s*</span>',
    flags=re.I,
)
_ASR_INFO_SPAN = re.compile(
    r'<span class="asr-info"[^>]*>\s*ⓘ\s*</span>',
    flags=re.I,
)


def upgrade_soccer_season_info_icons(html: str) -> str:
    """Turn decorative Season Performance ⓘ spans into real tooltip buttons."""
    if not html or "ⓘ" not in html:
        return html

    def _season_box(m: re.Match[str]) -> str:
        heading = m.group(2)
        tip = _season_tip_for_heading(heading)
        return m.group(1) + heading + m.group(3) + _info_btn(tip, "asr-info")

    html = _SEASON_INFO_SPAN.sub(_season_box, html)
    html = _ASR_INFO_SPAN.sub(_info_btn(_TIP_ASR, "asr-info"), html)
    html = _BARE_NUMBER_OF_GAMES.sub(_info_btn(_TIP_ASR, "asr-info"), html)
    return html


def _insert_info_after_label(html: str, label_html: str, tip: str) -> str:
    if not html or not label_html or label_html not in html:
        return html
    marker = label_html
    # Already upgraded next to this label.
    probe = html.split(marker, 1)
    if len(probe) < 2:
        return html
    if 'class="pl-info-btn"' in probe[1][:80] or "pl-info-btn" in probe[1][:120]:
        return html
    btn = " " + _info_btn(tip)
    return html.replace(marker, marker + btn)


def _insert_info_after_plxg_value(html: str) -> str:
    """Put the PL Expected Goals ⓘ after the number, not between label and value."""
    if not html or "PL Expected Goals" not in html:
        return html

    def _add(m: re.Match[str]) -> str:
        block = m.group(0)
        if "pl-info-btn" in block:
            return block
        return block[:-6] + " " + _info_btn(_TIP_PLXG) + "</div>"

    return re.sub(
        r'<div\b[^>]*\bclass="[^"]*\bsf-item\b[^"]*"[^>]*>\s*'
        r'<span\b[^>]*\bclass="[^"]*\bsf-label\b[^"]*"[^>]*>\s*'
        r'PL\s*Expected\s*Goals\s*</span>\s*'
        r'<span\b[^>]*\bclass="[^"]*\bsf-val\b[^"]*"[^>]*>\s*[^<]*?\s*</span>'
        r'\s*</div>',
        _add,
        html,
        flags=re.I,
    )


def upgrade_soccer_picks_info_icons(html: str) -> str:
    """Add ⓘ explanations on soccer pick-card labels that had none."""
    if not html:
        return html
    html = _insert_info_after_label(
        html, '<div class="pick-conf-title">Pick Confidence</div>', _TIP_PICK_CONF
    )
    html = _insert_info_after_label(
        html, '<div class="line-chip-label">Edge</div>', _TIP_EDGE
    )
    html = _insert_info_after_label(
        html, '<div class="pc-name">Efficiency</div>', _TIP_EFFICIENCY
    )
    html = _insert_info_after_plxg_value(html)
    html = _insert_info_after_label(
        html, '<div class="line-chip-label">Books spread</div>', _TIP_SPREAD_LINE
    )
    # Face win % — ⓘ next to each team win % (not the draw row).
    if 'class="win-pct"' in html and "data-winpct-info" not in html:
        html = re.sub(
            r'(<div class="win-pct">[\s\S]*?</div>)',
            r'\1 <span data-winpct-info="1">' + _info_btn(_TIP_WIN_PCT) + "</span>",
            html,
        )
    return html


def inject_pl_info_tips_assets(html: str) -> str:
    """Load shared tooltip CSS/JS on soccer picks + results."""
    if not html:
        return html
    if _INFO_ASSET_MARK not in html:
        link = (
            '<link rel="stylesheet" href="/static/css/pl-info-tips.css" '
            f'id="pl-info-tips-css">'
        )
        if re.search(r"</head\s*>", html, flags=re.I):
            html = re.sub(r"</head\s*>", link + "\n</head>", html, count=1, flags=re.I)
        else:
            html = link + html
    if _INFO_JS_MARK not in html:
        script = '<script src="/static/js/pl-info-tips.js" defer></script>'
        if re.search(r"</body\s*>", html, flags=re.I):
            html = re.sub(r"</body\s*>", script + "\n</body>", html, count=1, flags=re.I)
        else:
            html = html + script
    return html


def apply_soccer_info_tooltips(html: str, *, kind: str = "results") -> str:
    """Upgrade ⓘ markup and bind the shared tooltip script."""
    if not html:
        return html
    html = upgrade_soccer_season_info_icons(html)
    html = upgrade_soccer_picks_info_icons(html)
    return inject_pl_info_tips_assets(html)


def sanitize_soccer_proj_entities(html: str) -> str:
    """Unescape Be&#39;er so totals cannot ingest 39 from an apostrophe entity."""
    if not html:
        return html

    def _fix(m: re.Match[str]) -> str:
        name, raw = m.group(1), m.group(2)
        val = html_lib.unescape(html_lib.unescape(raw or ""))
        if name.lower() in ("data-pl-proj", "data-xs-proj"):
            nums = re.findall(r"(\d+(?:\.\d+)?)", val)
            if any(_safe_big(n) for n in nums):
                good = [n for n in nums if not _safe_big(n)]
                if len(good) >= 2:
                    val = f"{good[-2]}–{good[-1]}"
        esc = (
            val.replace("&", "&amp;")
            .replace('"', "&quot;")
            .replace("<", "&lt;")
        )
        return f'{name}="{esc}"'

    return re.sub(
        r'\b(data-(?:pl-proj|xs-proj|pl-spread|xs-spread|home-full|away-full|home|away))="([^"]*)"',
        _fix,
        html,
        flags=re.I,
    )


def _safe_big(n: str) -> bool:
    try:
        return float(n) > 8
    except (TypeError, ValueError):
        return False


def enrich_soccer_chart_model_attrs(html: str) -> str:
    """Copy Pick Confidence / face 3-way into data-m-* so the chart equals the card."""
    if not html or "data-pick-card" not in html:
        return html

    name_to_attr = (
        ("grinder2", "data-m-grinder2"),
        ("takedown", "data-m-takedown"),
        ("edge", "data-m-edge"),
        ("xsharp", "data-m-xsharp"),
        ("efficiency", "data-m-efficiency"),
        ("sharp cons", "data-m-consensus"),
        ("consensus", "data-m-consensus"),
    )

    def _attr(tag: str, name: str) -> str:
        m = re.search(rf'\b{re.escape(name)}="([^"]*)"', tag, flags=re.I)
        return html_lib.unescape((m.group(1) or "").strip()) if m else ""

    def _set_attr(tag: str, name: str, value: str) -> str:
        if re.search(rf'\b{re.escape(name)}="', tag, flags=re.I):
            return re.sub(
                rf'\b{re.escape(name)}="[^"]*"',
                f'{name}="{value}"',
                tag,
                count=1,
                flags=re.I,
            )
        return tag[:-1] + f' {name}="{value}">'

    def _pct_from_box(box: str, home: str, away: str) -> str:
        val_m = re.search(
            r'<div\b[^>]*\bclass="[^"]*\bpc-val\b[^"]*"[^>]*>([\s\S]*?)</div>',
            box,
            flags=re.I,
        )
        side_m = re.search(
            r'<div\b[^>]*\bclass="[^"]*\bpc-side\b[^"]*"[^>]*>([\s\S]*?)</div>',
            box,
            flags=re.I,
        )
        raw = re.sub(r"<[^>]+>", "", val_m.group(1) if val_m else "")
        nums = re.findall(r"(\d+(?:\.\d+)?)", raw)
        if not nums:
            return ""
        try:
            pct = float(nums[0])
        except ValueError:
            return ""
        if pct <= 0:
            return ""
        side = re.sub(r"<[^>]+>", "", side_m.group(1) if side_m else "").strip().lower()
        if side in ("n/a", "na", "—", "-", ""):
            return ""
        home_l = home.lower()
        away_l = away.lower()
        if away_l and away_l in side:
            pct = 100.0 - pct
        return f"{pct:.1f}"

    def _face_xsharp_twoway(rest: str) -> str:
        slots = re.findall(
            r'<div\b[^>]*\bclass="[^"]*\bteam-slot\b[^"]*"[^>]*>[\s\S]*?'
            r'<div\b[^>]*\bclass="[^"]*\bwin-pct\b[^"]*"[^>]*>([\s\S]*?)</div>',
            rest,
            flags=re.I,
        )
        if len(slots) < 2:
            return ""
        nums = []
        for block in slots[:2]:
            found = re.findall(r"(\d+(?:\.\d+)?)", re.sub(r"<[^>]+>", "", block))
            if not found:
                return ""
            nums.append(float(found[0]))
        tot = nums[0] + nums[1]
        if tot <= 1:
            return ""
        return f"{(nums[1] / tot) * 100.0:.1f}"

    def _patch(stack: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            return stack
        open_tag = open_m.group(1)
        rest = stack[open_m.end() :]
        home = _attr(open_tag, "data-home-full") or _attr(open_tag, "data-home")
        away = _attr(open_tag, "data-away-full") or _attr(open_tag, "data-away")
        for box in re.finditer(
            r'<div\b[^>]*\bclass="[^"]*\bpc-box\b[^"]*"[^>]*>[\s\S]*?</div>\s*</div>',
            rest,
            flags=re.I,
        ):
            block = box.group(0)
            nm = re.search(
                r'<div\b[^>]*\bclass="[^"]*\bpc-name\b[^"]*"[^>]*>([\s\S]*?)</div>',
                block,
                flags=re.I,
            )
            label = re.sub(r"<[^>]+>", "", nm.group(1) if nm else "").strip().lower()
            attr = ""
            for key, name in name_to_attr:
                if key in label:
                    attr = name
                    break
            if not attr or _attr(open_tag, attr):
                continue
            home_pct = _pct_from_box(block, home, away)
            if home_pct:
                open_tag = _set_attr(open_tag, attr, home_pct)
        if not _attr(open_tag, "data-m-xsharp"):
            tw = _face_xsharp_twoway(rest)
            if tw:
                open_tag = _set_attr(open_tag, "data-m-xsharp", tw)
        return open_tag + rest

    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    return "".join(_patch(p) if "data-pick-card" in p[:80] else p for p in parts)


def apply_soccer_picks_fixups(html: str, *, league: str = "", region: str = "") -> str:
    """Publish-layer soccer picks: chart attrs, PL Expected Goals, hide empty Total EV."""
    if not html:
        return html
    if "data-pick-card" in html:
        try:
            from mlb_ui_fixup import enrich_mlb_chart_data_attrs

            html = enrich_mlb_chart_data_attrs(html)
        except Exception as e:
            print(f"[soccer_ui_fixup] chart attrs: {e}", flush=True)
        try:
            html = enrich_soccer_chart_model_attrs(html)
        except Exception as e:
            print(f"[soccer_ui_fixup] model chart attrs: {e}", flush=True)
        try:
            from soccer_pl_xg import enrich_soccer_plxg_html, strip_soccer_h2h_labels

            html = enrich_soccer_plxg_html(html)
            html = strip_soccer_h2h_labels(html)
        except Exception as e:
            print(f"[soccer_ui_fixup] plxg: {e}", flush=True)
        html = sanitize_soccer_proj_entities(html)
        html = strip_soccer_empty_total_ev(html)
    html = apply_soccer_info_tooltips(html, kind="picks")
    html = ensure_soccer_league_dropdown(html, kind="picks", league=league, region=region)
    try:
        from soccer_pl_xg import strip_soccer_h2h_labels

        html = strip_soccer_h2h_labels(html)
    except Exception:
        pass
    return html


def _soccer_league_qs(league: str = "", region: str = "") -> tuple[str, str]:
    parts = []
    rg = (region or "").strip()
    lg = (league or "").strip()
    if rg:
        parts.append(f"region={rg}")
    if lg:
        parts.append(f"league={lg}")
    cards = ("?" + "&".join(parts)) if parts else ""
    chart_parts = list(parts) + ["view=chart"]
    return cards, "?" + "&".join(chart_parts)


def soccer_results_view_toggle_html(*, active: str = "normal", league: str = "", region: str = "") -> str:
    """MLB/WNBA Cards|Chart toggle pointed at soccer-results."""
    n_cls = "active" if active == "normal" else ""
    c_cls = "active" if active == "chart" else ""
    cards_q, chart_q = _soccer_league_qs(league, region)
    return (
        '<div class="pl-view-toggle" role="navigation" aria-label="Results view">'
        f'<a class="pl-view-btn {n_cls}" href="/soccer-results{cards_q}">Cards</a>'
        f'<a class="pl-view-btn {c_cls}" href="/soccer-results{chart_q}">Chart</a>'
        "</div>"
        "<style>.pl-view-toggle{display:flex;gap:8px;margin:12px 16px 18px;flex-wrap:wrap}"
        ".pl-view-btn{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.pl-view-btn.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def _league_from_soccer_results_html(html: str) -> str:
    if not html:
        return ""
    m = re.search(
        r'<select[^>]*\bid="league"[^>]*>[\s\S]*?'
        r'<option[^>]*\bselected\b[^>]*value="([^"]*)"',
        html,
        flags=re.I,
    )
    if m:
        val = (m.group(1) or "").strip()
        if val and val.upper() != "ALL":
            return val
    m = re.search(
        r'href="/soccer-results\?league=([^"&]+)"[^>]*\bactive\b'
        r'|\bactive\b[^>]*href="/soccer-results\?league=([^"&]+)"',
        html,
        re.I,
    )
    if m:
        return m.group(1) or m.group(2) or ""
    return ""


def inject_soccer_results_page_title(html: str) -> str:
    """Visible Soccer Results heading at the top (Cards-style chrome)."""
    if not html:
        return html
    if 'id="soccer-results-page-title"' in html:
        return html
    if re.search(r'class="page-title"[^>]*>[\s\S]*Soccer Results', html, flags=re.I):
        return html
    block = (
        '<header class="top" id="soccer-results-page-title">'
        '<div class="brand">Soccer Results</div>'
        "</header>"
    )
    if re.search(r"<main\b", html, re.I):
        return re.sub(r"(<main\b[^>]*>)", r"\1" + block, html, count=1, flags=re.I)
    return block + html


def inject_soccer_results_view_toggle(html: str, *, active: str = "normal", league: str = "", region: str = "") -> str:
    if not html:
        return html
    if 'class="pl-view-toggle"' in html or "class='pl-view-toggle'" in html:
        return html
    lg = league or _league_from_soccer_results_html(html)
    bar = soccer_results_view_toggle_html(active=active, league=lg, region=region)
    if re.search(r"<main\b", html, re.I):
        return re.sub(r"(<main\b[^>]*>)", r"\1" + bar, html, count=1, flags=re.I)
    if re.search(r'class="container\b', html, re.I):
        return re.sub(
            r'(<div class="container\b[^"]*"[^>]*>)',
            r"\1" + bar,
            html,
            count=1,
            flags=re.I,
        )
    return bar + html


def _inject_soccer_draw_ml_tips(html: str) -> str:
    """ⓘ on last-night / last-7 MONEYLINE labels: draws are 1X2 losses."""
    if not html or "MONEYLINE" not in html:
        return html
    if "pl-draw-ml-tip" in html:
        return html
    btn = _info_btn(_TIP_DRAW_ML, "pl-draw-ml-tip")
    return html.replace(">MONEYLINE</div>", f">MONEYLINE {btn}</div>")


def apply_soccer_results_fixups(html: str, *, league: str = "", region: str = "") -> str:
    """Cards|Chart toggle + league/continent dropdown on soccer results."""
    if not html:
        return html
    html = inject_soccer_results_view_toggle(html, active="normal", league=league, region=region)
    if "data-pick-card" in html:
        try:
            from soccer_pl_xg import strip_soccer_h2h_labels

            html = strip_soccer_h2h_labels(html)
        except Exception as e:
            print(f"[soccer_ui_fixup] results plxg: {e}", flush=True)
    html = _inject_soccer_draw_ml_tips(html)
    html = apply_soccer_info_tooltips(html, kind="results")
    html = ensure_soccer_league_dropdown(html, kind="results", league=league, region=region)
    try:
        from soccer_pl_xg import strip_soccer_h2h_labels

        html = strip_soccer_h2h_labels(html)
    except Exception:
        pass
    return html


def render_soccer_results_chart_page(
    payload: dict[str, Any] | None = None,
    *,
    league: str = "",
    region: str = "",
    cards_html: str | None = None,
) -> str:
    """MLB-template Cards|Chart chart view for /soccer-results?view=chart."""
    from pathlib import Path

    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from mlb_results_ui import inject_ssr_chart_bootstrap

    root = Path(__file__).resolve().parent
    env = Environment(
        loader=FileSystemLoader(str(root / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    cards_q, _chart_q = _soccer_league_qs(league, region)
    html = env.get_template("team_results.html").render(
        sport="soccer",
        sport_label="Soccer",
        api_base="/soccer/api",
        show_league=False,
        picks_href=f"/soccer-picks{cards_q}",
        results_href=f"/soccer-results{cards_q}",
    )
    html = inject_soccer_results_view_toggle(html, active="chart", league=league, region=region)
    html = inject_soccer_results_page_title(html)
    html = ensure_soccer_league_dropdown(
        html,
        kind="chart",
        league=league,
        region=region,
        source_html=cards_html,
    )
    if payload:
        try:
            html = inject_ssr_chart_bootstrap(html, payload, "soccer")
        except Exception as e:
            print(f"[soccer_ui_fixup] chart SSR bootstrap: {e}", flush=True)
    return apply_soccer_info_tooltips(html, kind="results")
