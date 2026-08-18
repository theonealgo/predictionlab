#!/usr/bin/env python3
"""WNBA picks/results publish-layer UI (MLB template parity).

Display only. Does not change MLB v2 math, invent 50% / −100, or name vendors.
WNBA does not publish Grinder2 or Takedown — drop those tiles, do not fill
them from Elo fallback.
"""
from __future__ import annotations

import re
from typing import Any

from mlb_ui_fixup import dedupe_game_card_stacks, enrich_mlb_chart_data_attrs


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
        # Only upgrade when cards cover more of the 7-day window.
        if card_n <= 0 or card_n <= pub_n:
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
    """Chart attrs + hide empty Grinder2/Takedown boxes. No invented odds."""
    if not html or "data-pick-card" not in html:
        return html
    try:
        html = dedupe_game_card_stacks(html)
    except Exception:
        pass
    html = enrich_mlb_chart_data_attrs(html)
    html = hide_unavailable_model_boxes(html)
    return html


def strip_wnba_g2_td_tiles(html: str) -> str:
    """Remove Grinder2 / Takedown tally and season-grid tiles. WNBA does not publish them."""
    if not html or ("Grinder2" not in html and "Takedown" not in html):
        return html
    card_re = re.compile(
        r'<div class="(?:daily-tally-card|model-card)[^"]*"',
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
    """Cards|Chart toggle + last-7 window from game cards. Season blocks untouched."""
    if not html:
        return html
    try:
        html = strip_wnba_g2_td_tiles(html)
    except Exception:
        pass
    try:
        html = hide_unavailable_model_boxes(html)
    except Exception:
        pass
    try:
        html = patch_wnba_last7_from_cards(html)
    except Exception:
        pass
    html = inject_wnba_results_view_toggle(html, active="normal")
    return html


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
            html = inject_ssr_chart_bootstrap(html, payload, "wnba")
        except Exception as e:
            print(f"[wnba_ui_fixup] chart SSR bootstrap: {e}", flush=True)
    return html
