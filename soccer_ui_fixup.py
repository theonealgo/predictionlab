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
    "club atletico ",
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
        if c in m:
            return m[c]
    return None


def aliases_for(name: str, db_names: Iterable[str]) -> list[str]:
    """All DB spellings that are the same club as ``name``."""
    if not name:
        return []
    want_id = _espn_id_for(name)
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
        if want_cores & _cores(raw):
            if want_id and other_id and want_id != other_id:
                continue
            if other_id and want_id is None:
                continue
            seen.add(raw)
            found.append(raw)
    if name not in seen:
        found.insert(0, name)
    return found


def _db_team_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT home_team_id FROM games WHERE sport = 'SOCCER' AND home_team_id IS NOT NULL
        UNION
        SELECT DISTINCT away_team_id FROM games WHERE sport = 'SOCCER' AND away_team_id IS NOT NULL
        """
    ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def h2h_projection(
    conn: sqlite3.Connection,
    home_team: str,
    away_team: str,
    n: int = 10,
    min_games: int = 1,
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
    sql = f"""
        SELECT home_team_id, away_team_id, home_score, away_score, game_date
        FROM games
        WHERE sport = 'SOCCER'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND (
                (home_team_id IN ({placeholders_h}) AND away_team_id IN ({placeholders_a}))
             OR (home_team_id IN ({placeholders_a}) AND away_team_id IN ({placeholders_h}))
          )
        ORDER BY date(game_date) DESC
        LIMIT ?
    """
    params = [*homes, *aways, *aways, *homes, int(n)]
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


def _iso_h2h_fns():
    """Prefer isolation module when present (same alias rules)."""
    try:
        soccer_s = str(_ISO_SOCCER.resolve())
        if soccer_s not in sys.path:
            sys.path.insert(0, soccer_s)
        from engine.h2h_lookup import format_h2h_last10 as iso_fmt
        from engine.h2h_lookup import h2h_projection as iso_proj

        return iso_fmt, iso_proj
    except Exception:
        return format_h2h_last10, h2h_projection


def _h2h_db_paths() -> list[Path]:
    return [
        _LIVE_ROOT / "sports_predictions_original.db",
        Path.home() / "Documents/Personal/soccer/data/sandbox_results.db",
    ]


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


def _best_h2h_proj(home: str, away: str, conns: list[sqlite3.Connection]) -> dict[str, Any] | None:
    _, proj_fn = _iso_h2h_fns()
    best: dict[str, Any] | None = None
    best_n = -1
    for conn in conns:
        try:
            proj = proj_fn(conn, home, away, n=10, min_games=1)
        except Exception:
            proj = None
        if not proj:
            continue
        n = int(proj.get("games_used") or 0)
        if n > best_n:
            best = proj
            best_n = n
    return best


def _best_h2h_text(home: str, away: str, conns: list[sqlite3.Connection]) -> str:
    proj = _best_h2h_proj(home, away, conns)
    if not proj:
        return ""
    g = int(proj["games_used"])
    games = f"{g} game" if g == 1 else f"{g} games"
    return f"{_fmt_half(float(proj['our_total']))} ({games})"


def fill_soccer_h2h_display_fields(predictions: list | None) -> None:
    """Set ``h2h_last10_*`` only. Never writes ``our_total`` / ``our_spread``."""
    if not predictions:
        return
    conns = _open_h2h_conns()
    if not conns:
        return
    try:
        for pred in predictions:
            if not isinstance(pred, dict):
                continue
            ht = pred.get("home_team_id")
            at = pred.get("away_team_id")
            if not ht or not at:
                continue
            proj = _best_h2h_proj(str(ht), str(at), conns)
            if not proj:
                continue
            # Improve display when isolation finds more meetings; never touch our_*.
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

    Display-only. First meetings stay empty so the chart shows ``—``.
    """
    if not html or "data-pick-card" not in html:
        return html

    fmt_fn, _proj_fn = _iso_h2h_fns()
    conns = _open_h2h_conns()
    if not conns:
        return html

    cache: dict[tuple[str, str], str] = {}

    def _lookup(home: str, away: str) -> str:
        if not home or not away:
            return ""
        key = (home, away)
        if key in cache:
            return cache[key]
        val = _best_h2h_text(home, away, conns)
        if not val:
            # Isolation formatter on the richest conn (sandbox last if present).
            for conn in reversed(conns):
                try:
                    val = fmt_fn(conn, home, away, n=10, min_games=1) or ""
                except Exception:
                    val = ""
                if _good(val):
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
        if not _good(h2h):
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

    def _resolve_h2h(home: str, away: str, existing: str, chip_val: str) -> str:
        db_h2h = _lookup(home, away) if home and away else ""
        if _good(db_h2h):
            return db_h2h
        if _good(existing):
            return existing
        if _good(chip_val):
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
        h2h = _resolve_h2h(home, away, existing, chip_val)
        if not _good(h2h):
            return stack
        open2 = _set_attr(open_tag, "data-h2h", h2h)
        rest2 = _ensure_chip(rest, h2h)
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


def apply_soccer_picks_fixups(html: str) -> str:
    """Publish-layer soccer picks: H2H L10 display + hide empty Total EV."""
    if not html or "data-pick-card" not in html:
        return html
    html = enrich_soccer_h2h_from_db(html)
    html = strip_soccer_empty_total_ev(html)
    return html
