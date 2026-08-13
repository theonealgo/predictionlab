#!/usr/bin/env python3
"""CFL sandbox pages — live-parity chrome + isolation engine cards.

Engine/DB: ~/Documents/Personal/cfl/
Presentation: NFL picks chrome shell (pl2-header, research-theme) with MLB-style cards.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

CFL_ISO = Path.home() / "Documents/Personal/cfl"
_RENDER = None
_PIPE = None
_FETCH = None


def _load(name: str, rel: str):
    if name in sys.modules:
        # Force reload render/pipeline when files change during hub lifetime
        if name.startswith("cfl_iso_"):
            pass  # keep cached unless hub restarted
        return sys.modules[name]
    root = str(CFL_ISO.resolve())
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    path = CFL_ISO / rel
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _reload(name: str, rel: str):
    sys.modules.pop(name, None)
    return _load(name, rel)


def _render_mod(*, reload: bool = False):
    global _RENDER
    if reload or _RENDER is None:
        # Also drop pipeline cache used by render
        for k in list(sys.modules):
            if k.startswith("cfl_pipe_for_render") or k.startswith("cfl_iso_"):
                sys.modules.pop(k, None)
        _RENDER = _load("cfl_iso_render", "engine/render.py")
    return _RENDER


def _pipe_mod():
    global _PIPE
    if _PIPE is None:
        _PIPE = _load("cfl_iso_pipeline", "engine/pipeline.py")
    return _PIPE


def _fetch_mod():
    global _FETCH
    if _FETCH is None:
        _FETCH = _load("cfl_iso_fetch", "engine/fetch.py")
    return _FETCH


def probe_cfl_api() -> dict[str, Any]:
    pipe = _pipe_mod()
    fetch = _fetch_mod()
    meta = pipe.ensure_predictions(refresh=False)
    cards = pipe.list_pick_cards()
    teams = [t[1] for t in fetch.fetch_espn_teams()]
    return {
        "teams_ok": len(teams) > 0,
        "teams": teams,
        "teams_count": len(teams),
        "window_events": cards,
        "window_count": len(cards),
        "schedule_source": "cfl-isolation",
        "note": f"isolation db={meta.get('db')} predictions={meta.get('predictions')}",
    }


def _point_static_to_hub(html: str) -> str:
    """Serve research CSS from hub /static so assets match MLB and don't die with sidecar path quirks."""
    html = re.sub(
        r'https?://127\.0\.0\.1:\d+/static/css/research-theme\.css',
        "/static/css/research-theme.css",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'https?://127\.0\.0\.1:\d+/static/css/picks-nav-overrides\.css',
        "/static/css/picks-nav-overrides.css",
        html,
        flags=re.I,
    )
    html = html.replace('href="/static/css/research-theme.css"', 'href="/static/css/research-theme.css"')
    # Sidecar-relative after rewrite_live_links
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


def _purge_non_cfl_stacks(html: str) -> str:
    """Drop any leftover chrome stacks that are not CFL isolation cards."""
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
        # Walk to matching </div>
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
        is_cfl = 'data-league="CFL"' in open_tag or "data-league='CFL'" in open_tag
        if is_cfl:
            parts.append(html[pos:end])
        else:
            parts.append(html[pos:abs_start])
        pos = end
    return "".join(parts)


def _replace_container(html: str, body: str) -> str:
    """Replace the entire main .container inner HTML — kills leftover NFL cards."""
    # Prefer the first full-width picks container (balanced div walk).
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
    m = re.search(
        r'(<main\b[^>]*>)([\s\S]*?)(</main>)',
        html,
        flags=re.I,
    )
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


def _cfl_chrome_cleanup(html: str, which: str = "picks") -> str:
    """Keep only pl2-header nav — no sticky Picks/Results bar; fix share assets."""
    from sandbox_fixup import (
        fix_share_social_assets,
        strip_sandbox_dev_notes,
        strip_sport_subnav,
    )

    _ = which  # picks/results share the same chrome cleanup
    html = strip_sandbox_dev_notes(html)
    html = strip_sport_subnav(html)
    html = fix_share_social_assets(html)
    return html


def render_cfl_with_chrome(chrome_html: str, which: str = "picks") -> tuple[str, dict[str, Any]]:
    """Inject CFL isolation content into live chrome; wipe NFL slate completely."""
    render = _render_mod(reload=True)
    if which == "results":
        frag, meta = render.build_results_fragment(refresh=False)
        title = "CFL Results | Prediction Lab"
    else:
        frag, meta = render.build_cards_fragment(which="picks", refresh=False)
        title = "CFL Picks | Prediction Lab"

    if not chrome_html or "<body" not in chrome_html.lower():
        page = (
            "<!doctype html><html><head><meta charset='utf-8'/>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
            f"<title>{title}</title>"
            '<link rel="stylesheet" href="/static/css/research-theme.css"/>'
            '<link rel="stylesheet" href="/static/css/picks-nav-overrides.css"/>'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@700&family=Oswald:wght@400;600;700&display=swap">'
            "</head><body>"
            f'<div class="container">{frag}</div></body></html>'
        )
        page = _cfl_chrome_cleanup(page, which)
        return page, meta

    html = chrome_html
    html = re.sub(r"\bNFL\b", "CFL", html)
    html = re.sub(
        r"(<title>)(.*?)(</title>)",
        rf"\1{title}\3",
        html,
        count=1,
        flags=re.I | re.S,
    )
    html = _replace_container(html, frag)
    html = _purge_non_cfl_stacks(html)
    from sandbox_fixup import hide_books_chrome

    html = hide_books_chrome(html)
    html = _point_static_to_hub(html)
    html = html.replace("/nfl-picks", "/cfl/")
    html = html.replace("/nfl-results", "/cfl/results")
    html = html.replace('href="/cfl-picks"', 'href="/cfl/"')
    if "Space+Grotesk" not in html and "Space Grotesk" not in html:
        html = re.sub(
            r"(</head>)",
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@700&family=Oswald:wght@400;600;700&display=swap">\n</head>',
            html,
            count=1,
            flags=re.I,
        )
    html = _cfl_chrome_cleanup(html, which)
    return html, meta


def build_cfl_pick_page(which: str = "picks", *, refresh: bool = False) -> tuple[str, dict[str, Any]]:
    """Fallback without sidecar chrome."""
    return render_cfl_with_chrome("", which=which)
