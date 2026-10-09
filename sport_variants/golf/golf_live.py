"""Live Golf tournament board (backup :5052 board, site chrome).

Golf only — do not import CFL or other isolation sports from here.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
ISO_HUB = ROOT / "iso_hub"
if str(ISO_HUB) not in sys.path:
    sys.path.insert(0, str(ISO_HUB))


def _iso_golf_page():
    """Load iso_hub/golf_page.py by path so hub's golf_page cannot win."""
    cached = sys.modules.get("iso_hub_golf_page")
    if cached is not None:
        return cached
    from importlib.machinery import SourceFileLoader
    import sport_variant as _sv

    path = ISO_HUB / "golf_page.py"
    spec = importlib.util.spec_from_file_location(
        "iso_hub_golf_page",
        path,
        loader=SourceFileLoader("iso_hub_golf_page", _sv.source_for("iso_hub/golf_page.py", "golf")),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["iso_hub_golf_page"] = mod
    return mod


def _nav_ctx() -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "soccer_enabled": True,
        "is_premium": False,
        "is_logged_in": False,
    }
    try:
        from flask_login import current_user

        ctx["is_logged_in"] = bool(getattr(current_user, "is_authenticated", False))
    except Exception:
        pass
    m = sys.modules.get("__main__")
    if m is None or not hasattr(m, "is_premium_user"):
        m = sys.modules.get("NHL77FINAL")
    if m is not None:
        try:
            if hasattr(m, "is_premium_user"):
                ctx["is_premium"] = bool(m.is_premium_user())
        except Exception:
            pass
        if hasattr(m, "SOCCER_ENABLED"):
            ctx["soccer_enabled"] = bool(m.SOCCER_ENABLED)
    return ctx


def _strip_vendor_labels(html: str) -> str:
    if not html:
        return html
    html = re.sub(r"\bTheOddsAPI\b", "", html, flags=re.I)
    html = re.sub(r"\bThe Odds API\b", "", html, flags=re.I)
    html = re.sub(r"Prob source:\s*[^<]+", "", html, flags=re.I)
    html = re.sub(r"Elo \+ market blend", "Model blend", html, flags=re.I)
    html = re.sub(r"\bElo trained on\b[^.<]*", "", html, flags=re.I)
    html = re.sub(r"\bisolation\b", "", html, flags=re.I)
    html = html.replace('data-sandbox-sport="golf"', 'data-sport="golf"')
    html = html.replace('id="sandbox-unlock-details"', 'id="pl-unlock-details"')
    html = re.sub(r"\bSandbox Open\b", "PGA Tour", html, flags=re.I)
    html = re.sub(r"\bATP Tour \(demo\)", "PGA Tour", html, flags=re.I)
    # Tennis name must not leak onto golf title / h1.
    html = re.sub(r"(<title>[^<]*)\bATP Tour\b", r"\1Golf", html, flags=re.I)
    html = re.sub(r"(<h1\b[^>]*>[\s\S]*?)\bATP Tour\b", r"\1PGA Tour", html, count=1, flags=re.I)
    return html


def _inject_chrome_into_page(html: str) -> str:
    from flask import render_template

    chrome = render_template("includes/picks_nav_chrome.html", **_nav_ctx())
    css_html = (
        '<link rel="stylesheet" href="/static/css/research-theme.css">'
        '<link rel="stylesheet" href="/static/css/picks-nav-overrides.css">'
        '<link rel="stylesheet" href="/static/css/sports-chrome.css">'
        '<link rel="stylesheet" href="/static/css/golf-board.css">'
        '<script src="/static/js/pl-header-logo.js" defer></script>'
    )
    if re.search(r"</head\s*>", html, flags=re.I):
        html = re.sub(r"</head\s*>", css_html + "</head>", html, count=1, flags=re.I)
    else:
        html = css_html + html

    def _body_repl(m: re.Match[str]) -> str:
        tag = m.group(0)
        if "research-site" not in tag:
            tag = tag.replace("<body", '<body class="golf-board research-site"', 1)
            if tag == m.group(0):
                tag = tag[:-1] + ' class="golf-board research-site">'
        return tag + chrome

    if re.search(r"<body\b", html, flags=re.I):
        html = re.sub(r"<body\b[^>]*>", _body_repl, html, count=1, flags=re.I)
    else:
        html = chrome + html
    return html


def golf_api_picks() -> list[dict[str, Any]]:
    """Current field as picks. Win percents are the board models. No prices."""
    page = _iso_golf_page()
    data = page.build_ranked_board(None)
    players = data.get("players") or []
    if not players:
        return []
    event = str(data.get("event") or "PGA Tour")
    eid = str(data.get("event_id") or "")
    start = ""
    for tournament in data.get("tournaments") or []:
        if str(tournament.get("id") or "") == eid:
            start = str(tournament.get("start") or "")[:10]
            break

    def _pct(models: dict, key: str):
        raw = (models.get(key) or {}).get("win_pct")
        if raw is None:
            return None
        try:
            return round(float(raw) * 100.0, 1)
        except (TypeError, ValueError):
            return None

    rows: list[dict[str, Any]] = []
    for player in players:
        name = str(player.get("name") or "").strip()
        if not name:
            continue
        models = player.get("models") or {}
        rows.append({
            "date": start,
            "matchup": f"{name} @ {event}",
            "homeTeam": name,
            "awayTeam": event,
            "pick": name,
            "winPercent": _pct(models, "consensus"),
            "edge": _pct(models, "edge"),
            "xsharp": _pct(models, "xsharp"),
            "grinder2": _pct(models, "grinder2"),
            "takedown": _pct(models, "takedown"),
        })
    return rows


def render_golf_picks(event_id: str | None = None) -> str:
    page, _meta = _iso_golf_page().render_golf_board_html(event_id)
    return _strip_vendor_labels(_inject_chrome_into_page(page))


def render_golf_results(event_id: str | None = None) -> str:
    page, _meta = _iso_golf_page().render_golf_results_html(event_id)
    return _strip_vendor_labels(_inject_chrome_into_page(page))
