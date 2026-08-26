#!/usr/bin/env python3
"""WNBA picks/results publish-layer UI (MLB template parity).

Display only. Does not change MLB v2 math, invent 50% / −100, or name vendors.
WNBA does not publish Grinder2 or Takedown — drop those tiles, do not fill
them from Elo fallback.
"""
from __future__ import annotations

import html as html_lib
import re
import sqlite3
from pathlib import Path
from typing import Any

from mlb_ui_fixup import dedupe_game_card_stacks, enrich_mlb_chart_data_attrs


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


def apply_wnba_picks_fixups(html: str) -> str:
    """Chart attrs + H2H L10 enrich + hide empty Grinder2/Takedown boxes."""
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
    html = hide_unavailable_model_boxes(html)
    html = strip_wnba_g2_td_tiles(html)
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
    html = inject_wnba_results_view_toggle(html, active="normal")
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


def apply_wnba_best_ml_face(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Point chart moneyline face (pick / % / Correct-Wrong) at the best model."""
    if not payload:
        return payload or {}
    markets = payload.get("markets") or {}
    ml = markets.get("moneyline") or {}
    season = ((ml.get("tallies") or payload.get("tallies") or {}).get("season") or {})
    best = wnba_best_ml_model(season.get("models") or {})
    if not best:
        return payload
    name = best["name"]
    payload["ml_face_model"] = name
    ml["face_model"] = name
    finals = list(ml.get("finals") or payload.get("finals") or [])
    for row in finals:
        models = row.get("models") or {}
        face = models.get(name)
        if _wnba_has_pick(face):
            row["face_pick"] = face.get("pick")
            row["face_prob"] = face.get("prob")
            row["correct"] = face.get("correct")
            row["face_model"] = name
        else:
            # Do not keep Edge as the graded face when it is not the winner.
            row["face_pick"] = "—"
            row["face_prob"] = None
            row["correct"] = None
            row["face_model"] = name
    if "finals" in ml or finals:
        ml["finals"] = finals
    if "finals" in payload:
        payload["finals"] = finals
    markets["moneyline"] = ml
    payload["markets"] = markets
    return payload


def _wnba_banner_color(pct: float) -> str:
    if pct >= 55:
        return "#00C076"
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
