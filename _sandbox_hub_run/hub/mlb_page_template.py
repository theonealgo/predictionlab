#!/usr/bin/env python3
"""Force Tennis/UFC picks pages into the signed-off MLB picks template order.

Order (match MLB):
  1. Title + description (writeup)
  2. Predictions | Results tabs
  3. Date picker (date-nav)
  4. Cards | Chart controls
  5. Date sections + cards
  6. Today's <sport> previews
  7. Top-3 share image + social strip
  8. Site footer (from canonical chrome)
"""
from __future__ import annotations

import re
from typing import Any


def _strip_perf_blocks_from_picks(html: str) -> str:
    """Best Performing / LN-L7-Season belong on results/chart — not MLB picks."""
    if not html:
        return html
    html = re.sub(
        r'<section\b[^>]*\bclass="[^"]*\b(?:tennis|ufc)-perf-(?:analytics|window)\b[^"]*"[^>]*>'
        r"[\s\S]*?</section>",
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<style\b[^>]*\bid="(?:tennis|ufc)-perf-tallies"[^>]*>[\s\S]*?</style>',
        "",
        html,
        flags=re.I,
    )
    return html


def _date_keys(html: str) -> list[str]:
    keys = []
    for m in re.finditer(
        r'<div class="date-section[^"]*"\s+id="([^"]+)"',
        html or "",
        flags=re.I,
    ):
        raw = m.group(1).strip()
        if not raw:
            continue
        d = raw[5:] if raw.lower().startswith("date-") else raw
        if d and d not in keys:
            keys.append(d)
    return keys


def _format_date_label(iso: str, *, today: str) -> str:
    """Match live picks: '📅 Today' / 'Tomorrow' / 'Wed, Sep 2'."""
    try:
        from datetime import date as _date, timedelta

        d = _date.fromisoformat(iso[:10])
        t = _date.fromisoformat(today[:10])
    except Exception:
        return iso
    if d == t:
        return "📅 Today"
    if d == t + timedelta(days=1):
        return "Tomorrow"
    return f"{d.strftime('%a')}, {d.strftime('%b')} {d.day}"


def _build_date_nav(dates: list[str], *, sport: str) -> str:
    if not dates:
        return ""
    from datetime import date as _date

    today = _date.today().isoformat()
    bubbles = []
    for i, d in enumerate(dates):
        active = " active" if i == 0 else ""
        today_cls = " today today-bubble" if d == today else ""
        label = _format_date_label(d, today=today)
        bubbles.append(
            f'<button type="button" class="date-bubble{active}{today_cls}" data-date="{d}" '
            f'title="{d}" '
            f'onclick="(function(btn){{var id=btn.getAttribute(\'data-date\');'
            f"document.querySelectorAll('.date-section').forEach(function(s){{"
            f"s.classList.toggle('visible', s.id==='date-'+id);"
            f"s.classList.toggle('seo-hidden', s.id!=='date-'+id);}});"
            f"document.querySelectorAll('.date-bubble').forEach(function(b){{"
            f"b.classList.toggle('active', b===btn);}});}})(this)\">{label}</button>"
        )
    return f"""
<div class="date-nav" aria-label="{sport.upper()} date picker">
  <div class="nav-arrow" aria-hidden="true">‹</div>
  <div class="date-bubbles" id="dateBubbles">
    {"".join(bubbles)}
  </div>
  <div class="nav-arrow" aria-hidden="true">›</div>
</div>
<style id="mlb-tpl-date-nav">
.date-nav{{display:flex;align-items:center;justify-content:center;gap:12px;margin:16px 0;
padding:14px;background:#fff;border:1px solid rgba(15,23,42,0.12);border-radius:12px;
max-width:1200px;margin-left:auto;margin-right:auto;box-sizing:border-box;}}
.date-nav .nav-arrow{{font-size:1.4rem;color:#94a3b8;user-select:none;padding:0 4px;}}
.date-bubbles{{display:flex;gap:8px;overflow-x:auto;padding:4px;max-width:860px;}}
.date-bubble{{background:#fff;border:2px solid rgba(15,23,42,0.18);border-radius:22px;
padding:9px 16px;min-width:105px;text-align:center;cursor:pointer;transition:all .2s;
white-space:nowrap;font-weight:500;font-size:.86em;color:#0f172a;}}
.date-bubble:hover{{border-color:#92400e;}}
.date-bubble.active{{background:#f59e0b;border-color:#d97706;color:#0f172a;font-weight:700;}}
.date-bubble.today,.date-bubble.today-bubble{{border-color:#00C076;color:#059669;font-weight:700;}}
.date-bubble.active.today,.date-bubble.active.today-bubble{{background:#00C076;color:#fff;border-color:#00C076;}}
.date-section.seo-hidden{{display:none!important;}}
.date-section.visible{{display:block;}}
</style>
"""


def _find_div_block(html: str, start: int) -> tuple[int, int] | None:
    """Return [start, end) of the div starting at `start` (must point at '<div')."""
    if start < 0 or start >= len(html) or not html[start : start + 4].lower().startswith("<div"):
        return None
    tag_end = html.find(">", start)
    if tag_end < 0:
        return None
    i = tag_end + 1
    depth = 1
    low = html.lower()
    while i < len(html) and depth:
        nxt_open = low.find("<div", i)
        nxt_close = low.find("</div>", i)
        if nxt_close < 0:
            return None
        if nxt_open >= 0 and nxt_open < nxt_close:
            depth += 1
            i = nxt_open + 4
        else:
            depth -= 1
            i = nxt_close + 6
            if depth == 0:
                return start, i
    return None


def _ensure_date_section_wrap(html: str, *, sport: str) -> str:
    """UFC fragments often have bare games-grid — wrap like MLB date-section."""
    if not html or re.search(r'class="date-section', html, flags=re.I):
        return html
    m = re.search(r'<div class=["\']games-grid["\']', html, flags=re.I)
    if not m:
        return html
    span = _find_div_block(html, m.start())
    if not span:
        return html
    a, b = span
    from datetime import date as _date

    day = _date.today().isoformat()
    wrapped = (
        f'<div class="date-section visible" id="date-{day}">'
        f'<div class="date-header">📅 {day}</div>'
        f"{html[a:b]}"
        f'<div class="chart-table-wrap" hidden></div>'
        f"</div>"
    )
    return html[:a] + wrapped + html[b:]


def _ensure_date_nav(html: str, *, sport: str) -> str:
    if not html:
        return html
    html = _ensure_date_section_wrap(html, sport=sport)
    if re.search(r'class="date-nav"', html, flags=re.I):
        return html
    dates = _date_keys(html)
    nav = _build_date_nav(dates, sport=sport)
    if not nav:
        return html
    # After section-tabs, before picks-view-controls / first date-section
    m = re.search(r'(<div class="section-tabs"[\s\S]*?</div>\s*(?:<style>[\s\S]*?</style>)?)', html, re.I)
    if m:
        return html[: m.end()] + "\n" + nav + html[m.end() :]
    m = re.search(r'(<div class="picks-view-controls")', html, re.I)
    if m:
        return html[: m.start()] + nav + "\n" + html[m.start() :]
    m = re.search(r'(<div class="date-section")', html, re.I)
    if m:
        return html[: m.start()] + nav + "\n" + html[m.start() :]
    return html


def _preview_items_from_cards(html: str, picks_href: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    names = [
        re.sub(r"\s+", " ", n).strip()
        for n in re.findall(r'<div class="team-name">([^<]+)</div>', html or "")
    ]
    for i in range(0, len(names) - 1, 2):
        items.append({"title": f"{names[i]} vs {names[i+1]}", "url": picks_href})
        if len(items) >= 16:
            break
    return items


def _ensure_today_previews(html: str, *, sport: str) -> str:
    label = "Tennis" if sport == "tennis" else "UFC" if sport == "ufc" else sport.upper()
    heading = f"Today's {label} previews"
    if heading in (html or ""):
        return html
    picks = f"/{sport}/"
    items = _preview_items_from_cards(html, picks)
    if not items:
        return html
    lis = "".join(f'<li><a href="{it["url"]}">{it["title"]}</a></li>' for it in items)
    block = (
        '<!-- sport-preview-hub -->'
        f'<nav class="mlb-preview-hub sport-preview-hub" aria-label="{label} previews">'
        f"<h2>{heading}</h2><ul>{lis}</ul></nav>"
        "<style>.mlb-preview-hub,.sport-preview-hub{max-width:1100px;margin:12px auto 18px;"
        "padding:14px 16px;border:1px solid #e2e8f0;border-radius:12px;background:#f8fafc;}"
        ".mlb-preview-hub h2{font-size:1.05rem;margin:0 0 8px;color:#0f172a;}"
        ".mlb-preview-hub ul{margin:0;padding-left:18px;columns:2;gap:12px;}"
        ".mlb-preview-hub li{margin:0 0 6px;break-inside:avoid;}"
        ".mlb-preview-hub a{color:#00529B;font-weight:700;text-decoration:none;}"
        "@media(max-width:700px){.mlb-preview-hub ul{columns:1;}}</style>"
    )
    # After last date-section (MLB order), else before share/footer
    last_ds = None
    for m in re.finditer(r'<div class="date-section\b', html, flags=re.I):
        last_ds = m
    if last_ds:
        span = _find_div_block(html, last_ds.start())
        if span:
            _, end = span
            return html[:end] + "\n" + block + html[end:]
    for pat in (
        r'(?=<div class="social-export-wrap")',
        r'(?=<div class="share-strip")',
        r"(</main>)",
        r'(?=<div\b[^>]*\bsite-directory-footer\b)',
        r"(</body>)",
    ):
        m = re.search(pat, html, flags=re.I)
        if m:
            return html[: m.start()] + block + "\n" + html[m.start() :]
    return html + block


def _force_cards_chart_after_date_nav(html: str) -> str:
    """Guarantee one Cards|Chart control block after date-nav (sidecar often buries it)."""
    if not html:
        return html
    # Drop every existing control strip (may sit after footer from live chrome).
    html = re.sub(
        r'(?:<style id="picks-chart-scaffold">[\s\S]*?</style>\s*)?'
        r'<div class="picks-view-controls">[\s\S]*?</div>\s*</div>\s*',
        "",
        html,
        flags=re.I,
    )
    css = """
<style id="picks-chart-scaffold">
.picks-view-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;max-width:1200px;margin:0 auto 12px;padding:0 4px;}
.pv-toggle{display:inline-flex;border:1px solid #cbd5e1;border-radius:999px;overflow:hidden;background:#fff;}
.pv-btn{border:0;background:transparent;color:#475569;font-size:0.8em;font-weight:700;padding:6px 14px;cursor:pointer;}
.pv-btn.active{background:#0c1e3a;color:#fff;}
.date-section.chart-mode .games-grid,
.date-section.chart-mode .game-card-stack{display:none!important;}
.date-section.chart-mode .chart-table-wrap,
.date-section.chart-mode .chart-table-wrap[hidden]{
  display:block!important;max-height:78vh;overflow:auto;
  border:1px solid rgba(15,23,42,0.12);border-radius:10px;-webkit-overflow-scrolling:touch;
}
</style>
"""
    controls = (
        css
        + '<div class="picks-view-controls">'
        '<div class="pv-toggle" role="group" aria-label="Picks view">'
        '<button type="button" class="pv-btn active" id="pvCardsBtn" '
        "onclick=\"setPicksView('cards')\">Cards</button>"
        '<button type="button" class="pv-btn" id="pvChartBtn" '
        "onclick=\"setPicksView('chart')\">Chart</button>"
        "</div></div>"
    )
    # Balanced close — naive </div> matched the first nav-arrow and stuffed
    # Cards|Chart inside date-nav (broke tennis/UFC date picker).
    nav_m = re.search(r'<div class="date-nav\b', html, flags=re.I)
    if nav_m:
        span = _find_div_block(html, nav_m.start())
        if span:
            _, nav_end = span
            style_m = re.match(
                r'\s*<style id="mlb-tpl-date-nav">[\s\S]*?</style>',
                html[nav_end:],
                flags=re.I,
            )
            insert_at = nav_end + (style_m.end() if style_m else 0)
            html = html[:insert_at] + "\n" + controls + html[insert_at:]
        else:
            nav_m = None
    if not nav_m:
        m = re.search(
            r'(<div class="section-tabs"[\s\S]*?</div>\s*(?:<style>[\s\S]*?</style>)?)',
            html,
            flags=re.I,
        )
        if m:
            html = html[: m.end()] + "\n" + controls + html[m.end() :]
        else:
            m = re.search(r'(<div class="date-section")', html, flags=re.I)
            if m:
                html = html[: m.start()] + controls + "\n" + html[m.start() :]
            else:
                html = controls + html
    # picks-chart.js owns setPicksView; only cover the case where it never loads.
    fallback = (
        "<script>window.addEventListener('load',function(){"
        "if(typeof window.setPicksView==='function')return;"
        "window.setPicksView=function(mode){var chart=mode==='chart';"
        "document.querySelectorAll('.date-section').forEach(function(s){"
        "s.classList.toggle('chart-mode',chart);"
        "var w=s.querySelector('.chart-table-wrap');"
        "if(w){if(chart){w.removeAttribute('hidden');w.hidden=false;}"
        "else{w.setAttribute('hidden','');w.hidden=true;}}"
        "});"
        "var cb=document.getElementById('pvCardsBtn'),hb=document.getElementById('pvChartBtn');"
        "if(cb)cb.classList.toggle('active',!chart);"
        "if(hb)hb.classList.toggle('active',chart);};});</script>"
    )
    if re.search(r"</body\s*>", html, flags=re.I):
        return re.sub(r"</body\s*>", fallback + "</body>", html, count=1, flags=re.I)
    return html + fallback


def apply_mlb_picks_template(html: str, *, sport: str, which: str = "picks") -> str:
    """Normalize Tennis/UFC HTML to MLB picks page skeleton."""
    if not html:
        return html
    sport_l = (sport or "").strip().lower()
    if which == "picks":
        html = _strip_perf_blocks_from_picks(html)
    html = _ensure_date_nav(html, sport=sport_l)
    if which == "picks":
        # Controls first so the scaffold in sandbox_fixup sees setPicksView and does
        # not wrap the real date-sections in a second (pc-slate) section.
        html = _force_cards_chart_after_date_nav(html)
        try:
            from sandbox_fixup import inject_picks_chart_tabs

            html = inject_picks_chart_tabs(html, sport=sport_l, markets=["moneyline"])
        except Exception:
            pass
        html = _ensure_today_previews(html, sport=sport_l)
    try:
        from share_chrome import ensure_share_and_social_chrome

        page_path = f"/{sport_l}/" if which == "picks" else f"/{sport_l}/results"
        html = ensure_share_and_social_chrome(html, sport=sport_l, page_path=page_path)
    except Exception:
        pass
    return html
