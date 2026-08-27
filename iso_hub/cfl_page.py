#!/usr/bin/env python3
"""CFL sandbox pages — live-parity chrome + isolation engine cards.

Engine/DB stay in ~/Documents/Personal/cfl/
Presentation reuses NFL/MLB live pick-page chrome (pl2-header, research-theme.css).
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Any


def _resolve_cfl_iso() -> Path:
    """Isolation folder on dev machine; bundled engines/cfl on Render."""
    here = Path(__file__).resolve().parent.parent
    for cand in (
        Path(os.environ.get("CFL_ENGINE_ROOT", "")).expanduser(),
        Path.home() / "Documents/Personal/cfl",
        here / "engines" / "cfl",
    ):
        if cand.is_dir() and (cand / "engine" / "render.py").is_file():
            return cand
    return here / "engines" / "cfl"


CFL_ISO = _resolve_cfl_iso()
_RENDER = None
_PIPE = None
_FETCH = None


def _load(name: str, rel: str):
    if name in sys.modules:
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


def _render_mod(*, reload: bool = False):
    global _RENDER
    if reload or _RENDER is None:
        for k in list(sys.modules):
            if k.startswith("cfl_iso_"):
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


def schedule_rows_for_total_edge() -> list[dict[str, Any]]:
    pipe = _pipe_mod()
    pipe.ensure_predictions(refresh=False)
    rows: list[dict[str, Any]] = []
    for c in pipe.list_pick_cards():
        away = c.get("away_team") or ""
        home = c.get("home_team") or ""
        pred = c.get("model_total")
        rows.append(
            {
                "game": f"{away} @ {home}",
                "away": away,
                "home": home,
                "predicted": pred,
                "book": None,
                "edge": None,
                "predicted_display": f"{pred}" if pred is not None else "N/A",
                "book_display": "N/A",
                "edge_display": "N/A",
                "lean": c.get("pick_ml") or "N/A",
            }
        )
    return rows


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


def _strip_nfl_slate(html: str) -> str:
    """Remove NFL game cards / date grids from live chrome shell."""
    if not html:
        return html
    # Remove full date-header + games-grid blocks
    html = re.sub(
        r'<div class="date-header\b[\s\S]*?</div>\s*<div class="games-grid\b[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="games-grid\b[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="game-card-stack\b[\s\S]*?</div>\s*(?=<div class="(?:game-card-stack|date-header)|<footer|</main|</div>\s*<footer)',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="(?:game-card\s+)?pick-card\b[\s\S]*?</div>\s*(?=<div class="(?:game-card|date-header)|<footer|</main)',
        "",
        html,
        flags=re.I,
    )
    return html


def _inject_into_container(html: str, body: str) -> str:
    """Replace inner HTML of the main .container with CFL slate (balanced div)."""
    from shared_chrome import replace_balanced_container_inner

    return replace_balanced_container_inner(html, body)


def render_cfl_with_chrome(chrome_html: str, which: str = "picks") -> tuple[str, dict[str, Any]]:
    """CFL isolation cards + frozen site chrome.

    Do NOT inject into an NFL picks body — leftover NFL odds panels sit outside
    `.container` and survive container replace. Build a clean page from the CFL
    fragment only; reuse research CSS links from the chrome shell when present.
    """
    from shared_chrome import ensure_canonical_chrome

    render = _render_mod(reload=True)
    frag, meta = render.build_cards_fragment(which=which, refresh=False)
    title = "CFL Picks | Prediction Lab" if which != "results" else "CFL Results | Prediction Lab"

    head_bits = [
        "<meta charset='utf-8'/>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'/>",
        f"<title>{title}</title>",
        '<link rel="stylesheet" href="/static/css/research-theme.css"/>',
        '<link rel="stylesheet" href="/static/css/picks-nav-overrides.css"/>',
        '<link rel="stylesheet" href="/static/css/sports-chrome.css"/>',
        # Isolation fragment has MLB card class names but not MLB inline CSS —
        # without this file, matchup-row stacks as a wireframe.
        '<link rel="stylesheet" href="/static/css/mlb-pick-cards.css"/>',
        # CFL-only Pick Confidence / card width — after MLB so it wins; do not
        # change global .pick-conf-grid (soccer leak).
        '<link rel="stylesheet" href="/static/css/cfl-pick-cards.css"/>',
    ]
    # Pull extra stylesheet links from chrome shell (no body/NFL slate).
    if chrome_html:
        for href in re.findall(r'href="(/static/css/[^"]+)"', chrome_html):
            tag = f'<link rel="stylesheet" href="{href}"/>'
            if tag not in head_bits:
                head_bits.append(tag)

    page = (
        "<!doctype html><html lang='en'><head>"
        + "\n".join(head_bits)
        + "</head>"
        '<body class="research-site" data-theme="light">'
        f'<div class="container">{frag}</div>'
        "</body></html>"
    )
    page = ensure_canonical_chrome(page, "cfl", which=which)
    try:
        from sandbox_fixup import unlock_premium_card_details

        page = unlock_premium_card_details(page)
    except Exception:
        pass
    # Final hard scrub — CFL fragment must never show NFL team strings.
    try:
        from shared_chrome import strip_cfl_nfl_remnants

        page = strip_cfl_nfl_remnants(page)
    except Exception:
        pass
    for marker in (
        "Cincinnati Bengals",
        "Atlanta Falcons",
        "Buffalo Bills",
        "Detroit Lions",
        "Dallas Cowboys",
        "Green Bay Packers",
        "New England Patriots",
        "Arizona Cardinals",
        "teamlogos/nfl",
    ):
        if marker in page:
            page = page.replace(marker, "")
    # Scrub methodology vocabulary from any leftover card blurbs / DB history
    page = re.sub(r"\bElo\b", "Model", page, flags=re.I)
    page = re.sub(r"\bElo\s+edge\b", "Model lean", page, flags=re.I)
    return page, meta


def build_cfl_pick_page(which: str = "picks", *, refresh: bool = False) -> tuple[str, dict[str, Any]]:
    """Deprecated standalone page — prefer render_cfl_with_chrome via live_proxy."""
    render = _render_mod()
    frag, meta = render.build_cards_fragment(which=which, refresh=refresh)
    page = (
        "<!doctype html><html><head><meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
        "<title>CFL Picks | Prediction Lab</title></head>"
        f"<body><div class='container'>{frag}</div></body></html>"
    )
    return page, meta


def _view_toggle_html(active: str = "normal") -> str:
    """Cards (normal) vs chart (tabbed markets) — no league dropdown."""
    n_cls = "active" if active == "normal" else ""
    c_cls = "active" if active == "chart" else ""
    return (
        '<div class="pl-view-toggle" role="navigation" aria-label="Results view">'
        f'<a class="pl-view-btn {n_cls}" href="/cfl/results">Cards</a>'
        f'<a class="pl-view-btn {c_cls}" href="/cfl/results?view=chart">Chart</a>'
        "</div>"
        "<style>.pl-view-toggle{display:flex;gap:8px;margin:12px 0 18px;flex-wrap:wrap}"
        ".pl-view-btn{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.pl-view-btn.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def inject_cfl_view_toggle(html: str, *, active: str = "normal") -> str:
    bar = _view_toggle_html(active=active)
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


def render_cfl_results_normal() -> tuple[str, dict[str, Any]]:
    """Normal site-style CFL results (score cards + LN/L7/Season strips)."""
    from live_proxy import fetch_live_chrome_shell

    render = _render_mod(reload=True)
    # Ensure walk-forward historical locks exist so grades are real.
    try:
        _pipe_mod().ensure_predictions(refresh=False)
    except Exception:
        pass
    frag, meta = render.build_results_fragment(refresh=False)
    chrome_html, chrome_meta = fetch_live_chrome_shell("cfl", which="results", timeout=25.0)
    if not chrome_meta.get("ok") or not chrome_html or "<body" not in chrome_html.lower():
        chrome_html, chrome_meta = fetch_live_chrome_shell("cfl", which="picks", timeout=25.0)
    if chrome_html and "<body" in chrome_html.lower():
        html = chrome_html
        html = _strip_nfl_slate(html)
        try:
            from shared_chrome import strip_cfl_nfl_remnants

            html = strip_cfl_nfl_remnants(html)
        except Exception:
            pass
        html = re.sub(
            r"(<title>)(.*?)(</title>)",
            r"\1CFL Results | Prediction Lab\3",
            html,
            count=1,
            flags=re.I | re.S,
        )
        html = re.sub(r"(<title>[^<]*)\bNFL\b", r"\1CFL", html, flags=re.I)
        html = html.replace("/nfl-picks", "/cfl/")
        html = html.replace("/nfl-results", "/cfl/results")
        html = html.replace("/cfl-picks", "/cfl/")
        html = html.replace("/cfl-results", "/cfl/results")
        html = _strip_nfl_slate(html)
        html = _inject_into_container(html, frag)
        css_tag = '<link rel="stylesheet" href="/static/css/cfl-pick-cards.css"/>'
        if "cfl-pick-cards.css" not in html:
            if re.search(r"</head\s*>", html, flags=re.I):
                html = re.sub(r"</head\s*>", css_tag + "</head>", html, count=1, flags=re.I)
            else:
                html = css_tag + html
        html = inject_cfl_view_toggle(html, active="normal")
        try:
            from sandbox_fixup import inject_sport_subnav, strip_sandbox_dev_notes, unlock_premium_card_details
            from shared_chrome import ensure_canonical_chrome, strip_cfl_nfl_remnants
            from team_tabbed_results import (
                build_cfl_payload,
                inject_mlb_results_analytics_html,
            )

            html = strip_cfl_nfl_remnants(html)
            html = strip_sandbox_dev_notes(html)
            # Best Performing / Efficiency — shared MLB analytics chrome (content only)
            try:
                payload = build_cfl_payload()
                analytics = (payload or {}).get("analytics") or {}
                if analytics:
                    html = inject_mlb_results_analytics_html(
                        html, analytics, sport="cfl"
                    )
            except Exception:
                pass
            html = inject_sport_subnav(html, "cfl", which="results")
            html = ensure_canonical_chrome(html, "cfl", which="results")
            html = re.sub(
                r'data-sandbox-sport="[^"]*"',
                'data-sandbox-sport="cfl"',
                html,
                count=1,
            )
            if "data-sandbox-sport=" not in html:
                html = re.sub(
                    r"(<body\b)([^>]*)(>)",
                    r'\1\2 data-sandbox-sport="cfl"\3',
                    html,
                    count=1,
                    flags=re.I,
                )
            html = unlock_premium_card_details(html)
            try:
                from team_tabbed_results import inject_consensus_records_html

                html = inject_consensus_records_html(html, sport="cfl")
            except Exception:
                pass
        except Exception:
            pass
        meta = {**meta, **chrome_meta, "view": "normal", "ok": True}
        return html, meta

    page = (
        "<!doctype html><html><head><meta charset='utf-8'/>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'/>"
        "<title>CFL Results | Prediction Lab</title>"
        '<link rel="stylesheet" href="/static/css/research-theme.css"/>'
        '<link rel="stylesheet" href="/static/css/mlb-pick-cards.css"/>'
        '<link rel="stylesheet" href="/static/css/cfl-pick-cards.css"/>'
        "</head><body class='research-site' data-sandbox-sport='cfl'>"
        f"{_view_toggle_html('normal')}<div class='container'>{frag}</div></body></html>"
    )
    return page, {**meta, "view": "normal", "chrome": "fallback_shell", "ok": True}
