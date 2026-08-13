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
    from sandbox_fixup import (
        fix_share_social_assets,
        inject_sport_subnav,
        strip_sandbox_dev_notes,
    )

    html = strip_sandbox_dev_notes(html)
    # Strip only — do not inject a second Picks/Results bar above pl2-header.
    html = inject_sport_subnav(html, "ufc", which=which)
    html = fix_share_social_assets(html)
    return html


def render_ufc_with_chrome(chrome_html: str, which: str = "picks") -> tuple[str, dict[str, Any]]:
    render = _render_mod(reload=True)
    if which == "results":
        frag, meta = render.build_results_fragment(refresh=False)
        title = "UFC Results | Prediction Lab"
    else:
        frag, meta = render.build_cards_fragment(which="picks", refresh=False)
        title = "UFC Picks | Prediction Lab"

    if not chrome_html or "<body" not in chrome_html.lower():
        page = (
            "<!doctype html><html><head><meta charset='utf-8'/>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
            f"<title>{title}</title>"
            '<link rel="stylesheet" href="/static/css/research-theme.css"/>'
            '<link rel="stylesheet" href="/static/css/picks-nav-overrides.css"/>'
            "</head><body>"
            f'<div class="container">{frag}</div></body></html>'
        )
        page = _ufc_chrome_cleanup(page, which)
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
    html = _ufc_chrome_cleanup(html, which)
    return html, meta


def build_ufc_pick_page(which: str = "picks", *, refresh: bool = False) -> tuple[str, dict[str, Any]]:
    return render_ufc_with_chrome("", which=which)
