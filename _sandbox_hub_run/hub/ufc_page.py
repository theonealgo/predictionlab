#!/usr/bin/env python3
"""UFC sandbox pages — live-parity chrome + isolation engine cards.

Engine/DB: ~/Documents/Personal/ufc/
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

UFC_ISO = Path.home() / "Documents/Personal/ufc"
_RENDER = None
_PIPE = None


def _load(name: str, rel: str):
    if name in sys.modules and name.startswith("ufc_iso_"):
        return sys.modules[name]
    root = str(UFC_ISO.resolve())
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    path = UFC_ISO / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _render_mod(*, reload: bool = False):
    global _RENDER
    if reload or _RENDER is None:
        for k in list(sys.modules):
            if k.startswith("ufc_iso_") or k.startswith("ufc_pipe_for_render") or k.startswith("ufc_fetch") or k.startswith("ufc_predict"):
                sys.modules.pop(k, None)
        _RENDER = _load("ufc_iso_render", "engine/render.py")
    return _RENDER


def _pipe_mod():
    global _PIPE
    if _PIPE is None:
        _PIPE = _load("ufc_iso_pipeline", "engine/pipeline.py")
    return _PIPE


def probe_ufc_api() -> dict[str, Any]:
    pipe = _pipe_mod()
    meta = pipe.ensure_predictions(refresh=False)
    cards = pipe.list_pick_cards()
    return {
        "window_events": cards,
        "window_count": len(cards),
        "schedule_source": "ufc",
        "note": f"db={meta.get('db')} predictions={meta.get('predictions')} books={meta.get('with_books')}",
        "meta": meta,
    }


def _point_static_to_hub(html: str) -> str:
    html = re.sub(
        r'https?://127\.0\.0\.1:\d+/static/css/research-theme\.css',
        "/static/css/research-theme.css",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'href="[^"]*/static/css/research-theme\.css"',
        'href="/static/css/research-theme.css"',
        html,
    )
    html = re.sub(
        r'href="[^"]*/static/css/picks-nav-overrides\.css"',
        'href="/static/css/picks-nav-overrides.css"',
        html,
    )
    return html


def _purge_non_ufc_stacks(html: str) -> str:
    parts: list[str] = []
    pos = 0
    while True:
        m = re.search(r'<div class="game-card-stack\b', html[pos:], flags=re.I)
        if not m:
            parts.append(html[pos:])
            break
        abs_start = pos + m.start()
        tag_end = html.find(">", abs_start)
        if tag_end < 0:
            parts.append(html[pos:])
            break
        open_tag = html[abs_start : tag_end + 1]
        i = tag_end + 1
        depth = 1
        j = i
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
            parts.append(html[pos:])
            break
        is_ufc = 'data-league="UFC"' in open_tag or "data-league='UFC'" in open_tag
        if is_ufc:
            parts.append(html[pos:end])
        else:
            parts.append(html[pos:abs_start])
        pos = end
    return "".join(parts)


def _replace_container(html: str, body: str) -> str:
    start = re.search(r'<div class="container\b[^"]*"[^>]*>', html, flags=re.I)
    if start:
        i = start.end()
        depth = 1
        j = i
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
                    return html[:i] + "\n" + body + "\n" + html[next_close:]
                j = next_close + 6
    m = re.search(r"(<main\b[^>]*>)([\s\S]*?)(</main>)", html, flags=re.I)
    if m:
        return html[: m.start(2)] + f'<div class="container">\n{body}\n</div>' + html[m.end(2) :]
    if re.search(r"<footer\b", html, re.I):
        return re.sub(
            r"(<footer\b)",
            f'<div class="container">\n{body}\n</div>\n' + r"\1",
            html,
            count=1,
            flags=re.I,
        )
    return html + f'<div class="container">{body}</div>'


def _ufc_chrome_cleanup(html: str, which: str = "picks") -> str:
    from sandbox_fixup import apply_sport_fixups

    # Drop chart assets from the chrome shell (may target a different slate),
    # then re-apply UFC fixups + Moneyline picks Chart on the isolation cards.
    html = re.sub(r"<script>\s*window\.PICKS_CHART\s*=[\s\S]*?</script>", "", html, flags=re.I)
    html = re.sub(r'<link[^>]+picks-chart\.css[^>]*>', "", html, flags=re.I)
    html = re.sub(r'<script[^>]+picks-chart\.js[^>]*>\s*</script>', "", html, flags=re.I)
    html = re.sub(
        r'<style id="picks-chart-scaffold">[\s\S]*?</style>', "", html, flags=re.I
    )
    html = re.sub(
        r"<!-- MLB picks Cards/Chart UI signed off[^>]*-->", "", html, flags=re.I
    )
    return apply_sport_fixups(html, "ufc", which=which)


def _ufc_picks_writeup() -> str:
    """MLB-style SEO intro (title + paragraph + Predictions heading). No IP/vendor notes."""
    return (
        '<div class="header">'
        '<h1 id="pageHeading">🥊 UFC AI Picks, Predictions and Fight Probabilities</h1>'
        "</div>\n"
        "<!-- SEO text block -->\n"
        '<div class="sport-picks-writeup" style="margin-bottom:16px;padding:14px 16px;'
        "background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);"
        'border-radius:10px;font-size:0.85em;color:#475569;line-height:1.7;">\n'
        "Our UFC picks today are generated using a specialized AI prediction system that "
        "analyzes fighter performance, striking, grappling, takedown ability, recent form, "
        "and matchup dynamics. By evaluating advanced UFC statistics, fight styles, opponent "
        "quality, and key performance metrics, our model identifies high-value opportunities "
        "across UFC moneyline, method of victory, round, and fight outcome predictions.\n"
        "</div>\n"
        '<h2 class="sport-predictions-heading" style="color:#0f172a;font-size:1.2rem;'
        'margin:0 0 12px;">📊 UFC Predictions</h2>\n'
    )


def _ufc_section_tabs(which: str) -> str:
    """Mirror MLB in-page tabs: 📊 Predictions | 🎯 Results."""
    pa = "active" if which == "picks" else ""
    ra = "active" if which == "results" else ""
    return (
        '<div class="section-tabs" role="navigation" aria-label="Sport pages">'
        f'<a href="/ufc/" class="tab {pa}">📊 Predictions</a>'
        f'<a href="/ufc/results" class="tab {ra}">🎯 Results</a>'
        "</div>"
        "<style>.section-tabs{display:flex;gap:8px;margin:12px 0 18px;flex-wrap:wrap}"
        ".section-tabs .tab{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.section-tabs .tab.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def _with_section_tabs(frag: str, which: str) -> str:
    """In-page Predictions|Results at top of content (footer mega-menu does not count)."""
    tabs = _ufc_section_tabs(which)
    if which == "picks":
        return f"{_ufc_picks_writeup()}{tabs}\n{frag}"
    return f"{tabs}\n{frag}"


def _esc(s: Any) -> str:
    return (
        str(s if s is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_ufc_performance_html() -> str:
    """Best Performing + Efficiency + Last Night / Last 7 / Season for cards views."""
    try:
        from share_chrome import build_ml_sport_performance_html
        from team_tabbed_results import build_ufc_payload

        return build_ml_sport_performance_html(build_ufc_payload(), sport="ufc")
    except Exception as e:
        print(f"[ufc_page] performance payload failed: {e}", flush=True)
        return ""


def render_ufc_with_chrome(chrome_html: str, which: str = "picks") -> tuple[str, dict[str, Any]]:
    from shared_chrome import ensure_canonical_chrome

    render = _render_mod(reload=True)
    if which == "results":
        frag, meta = render.build_results_fragment(refresh=False)
        title = "UFC Results | Prediction Lab"
    else:
        frag, meta = render.build_cards_fragment(which="picks", refresh=False)
        title = "UFC Picks | Prediction Lab"

    frag = _with_section_tabs(frag, which)
    # Results keep Best Performing / LN-L7-Season (chart-parity). Picks match MLB: no tallies.
    if which == "results":
        perf = build_ufc_performance_html()
        if perf:
            frag = re.sub(
                r'<section\b[^>]*\bclass="[^"]*\btally-wrap\b[^"]*"[^>]*>[\s\S]*?</section>',
                "",
                frag,
                count=1,
                flags=re.I,
            )
            if "section-tabs" in frag:
                frag = re.sub(
                    r'(</div>\s*<style>\.section-tabs[\s\S]*?</style>)',
                    r"\1\n" + perf,
                    frag,
                    count=1,
                    flags=re.I,
                )
            else:
                frag = perf + frag

    from mlb_page_template import apply_mlb_picks_template

    if not chrome_html or "<body" not in chrome_html.lower():
        page = (
            "<!doctype html><html><head><meta charset='utf-8'/>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
            f"<title>{title}</title>"
            '<link rel="stylesheet" href="/static/css/research-theme.css"/>'
            '<link rel="stylesheet" href="/static/css/picks-nav-overrides.css"/>'
            '<link rel="stylesheet" href="/static/css/team-results.css"/>'
            "</head><body class='research-site' data-theme='light' "
            "data-sandbox-sport='ufc' data-sandbox-sports-chrome='1'>"
            f'<div class="container">{frag}</div></body></html>'
        )
        page = _ufc_chrome_cleanup(page, which)
        page = ensure_canonical_chrome(page, "ufc", which=which)
        page = apply_mlb_picks_template(page, sport="ufc", which=which)
        return page, meta

    html = chrome_html
    html = re.sub(
        r"(<title>)(.*?)(</title>)",
        rf"\1{title}\3",
        html,
        count=1,
        flags=re.I | re.S,
    )
    html = _replace_container(html, frag)
    html = _purge_non_ufc_stacks(html)
    html = _point_static_to_hub(html)
    html = html.replace("/ufc-picks", "/ufc/")
    html = html.replace("/ufc-results", "/ufc/results")
    # Scope any leftover body-link CSS so frozen pl2-header cannot be restyled
    if "ufc-chrome-isolate" not in html:
        html = html.replace(
            "</head>",
            "<style id=\"ufc-chrome-isolate\">"
            "header.pl2-header, header.pl2-header a, header.pl2-header a:hover{"
            "color:inherit}"
            "</style></head>",
            1,
        )
    html = _ufc_chrome_cleanup(html, which)
    html = ensure_canonical_chrome(html, "ufc", which=which)
    html = apply_mlb_picks_template(html, sport="ufc", which=which)
    return html, meta


def build_ufc_pick_page(which: str = "picks", *, refresh: bool = False) -> tuple[str, dict[str, Any]]:
    _ = refresh
    return render_ufc_with_chrome("", which=which)


def ufc_tally_payload() -> dict[str, Any]:
    """Chart / API tallies from walk-forward graded isolation results."""
    render = _render_mod(reload=True)
    cards = render.list_graded_results(limit=500)
    return render.window_tally_records(cards)
