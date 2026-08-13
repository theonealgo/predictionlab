#!/usr/bin/env python3
"""HTTP proxy helpers for live-parity pages via NHL77FINAL sidecar (:5052).

Hub never imports NHL77FINAL. Sandbox only.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LIVE_ROOT = Path.home() / "Documents/Personal/predictionlabfix_work"
LIVE_VENV = LIVE_ROOT / ".venv" / "bin" / "python"
HUB_DIR = Path(__file__).resolve().parent
SIDECAR_PORT = int(os.environ.get("HUB_LIVE_SIDECAR_PORT", "5152"))
SIDECAR_LOG = Path("/tmp/sports_hub_live_sidecar.log")

_sidecar_lock = threading.Lock()
_sidecar_proc: subprocess.Popen | None = None

# Sport → live routes (CFL has no live pages — uses NFL chrome + sandbox slate)
LIVE_SPORTS = {
    "golf": {"picks": "/golf-picks", "results": "/golf-results", "label": "Golf"},
    "ufc": {"picks": "/ufc-picks", "results": "/ufc-results", "label": "UFC"},
    "tennis": {"picks": "/tennis-picks", "results": "/tennis-results", "label": "Tennis"},
    "cfl": {"picks": "/nfl-picks", "results": "/nfl-results", "label": "CFL", "clone_of": "NFL"},
}


def sidecar_base() -> str:
    return f"http://127.0.0.1:{SIDECAR_PORT}"


def _http_get(url: str, timeout: float = 180.0) -> tuple[str, int] | None:
    try:
        req = Request(url, headers={"User-Agent": "sports-sandbox-hub"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return body, int(getattr(resp, "status", 200) or 200)
    except (URLError, HTTPError, TimeoutError, OSError):
        return None


def sidecar_alive() -> bool:
    got = _http_get(f"{sidecar_base()}/", timeout=2.5)
    if got and got[1] < 500:
        return True
    # Some apps 404 on / but serve sport pages
    got = _http_get(f"{sidecar_base()}/mlb-picks", timeout=3.0)
    return bool(got and got[1] < 500)


def ensure_sidecar(wait_s: float = 120.0) -> bool:
    global _sidecar_proc
    if sidecar_alive():
        return True
    with _sidecar_lock:
        if sidecar_alive():
            return True
        py = str(LIVE_VENV if LIVE_VENV.is_file() else sys.executable)
        script = HUB_DIR / "live_sidecar.py"
        if not script.is_file():
            print(f"[hub] live_sidecar missing: {script}", flush=True)
            return False
        logf = open(SIDECAR_LOG, "ab", buffering=0)
        env = os.environ.copy()
        env["PORT"] = str(SIDECAR_PORT)
        env["HUB_LIVE_SIDECAR_PORT"] = str(SIDECAR_PORT)
        print(f"[hub] starting live sidecar on :{SIDECAR_PORT} …", flush=True)
        _sidecar_proc = subprocess.Popen(
            [py, str(script)],
            cwd=str(HUB_DIR.parent),
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if _sidecar_proc.poll() is not None:
                print(f"[hub] live sidecar exited code={_sidecar_proc.returncode}", flush=True)
                return False
            if sidecar_alive():
                print(f"[hub] live sidecar ready → {sidecar_base()}", flush=True)
                return True
            time.sleep(1.0)
        print(f"[hub] live sidecar timed out (log {SIDECAR_LOG})", flush=True)
        return False


def strip_auth_chrome(html: str) -> str:
    if not html:
        return html
    html = re.sub(
        r'<a\b[^>]*(?:href=["\'][^"\']*/(?:login|register|signup|subscribe|pricing|plans|account|billing|checkout)'
        r'|class=["\'][^"\']*(?:tv-premium-cta|join-premium|nav-cta-premium|premium)[^"\']*["\'])'
        r'[^>]*>.*?</a>',
        "<!-- sandbox: auth link stripped -->",
        html,
        flags=re.I | re.S,
    )
    html = re.sub(
        r'<button\b[^>]*(?:login|sign[\s-]?up|sign[\s-]?in|premium|subscribe)[^>]*>.*?</button>',
        "<!-- sandbox: auth button stripped -->",
        html,
        flags=re.I | re.S,
    )
    html = re.sub(
        r'<div\b[^>]*(?:id=["\']joinPremiumBar["\']|class=["\'][^"\']*join-premium-bar[^"\']*["\'])[^>]*>'
        r'.*?</div>\s*</div>\s*</div>',
        "<!-- sandbox: join-premium-bar stripped -->",
        html,
        flags=re.I | re.S,
    )
    html = re.sub(
        r'<div\b[^>]*class=["\'][^"\']*premium-upsell-strip[^"\']*["\'][^>]*>.*?</div>',
        "<!-- sandbox: premium-upsell stripped -->",
        html,
        flags=re.I | re.S,
    )
    html = re.sub(
        r'href=(["\'])(?:https?://[^"\']*)?/(?:login|register|signup|subscribe|pricing|plans|account|billing|checkout)(?:\?[^"\']*)?\1',
        r'href="/login"',
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div\b[^>]*class=["\'][^"\']*odds-pricing-locked[^"\']*["\'][^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    hide_css = (
        "<style id=\"sandbox-no-login\">"
        ".join-premium-bar,.join-premium-inner,.tv-premium-cta,.nav-cta-premium,"
        ".premium-upsell-strip,.pl2-account-menu,#pl2AccountMenu,"
        ".odds-pricing-locked,.premium-lock,"
        "a[href*=\"/login\"],a[href*=\"/plans\"],a[href*=\"/register\"],a[href*=\"/signup\"],"
        "a[href*=\"/subscribe\"],a[href*=\"/pricing\"],button[aria-label*=\"Account\" i],"
        ".pl2-account-btn,#pl2AccountBtn{display:none!important;visibility:hidden!important;}"
        "body.has-join-premium-bar{padding-bottom:0!important;}"
        "</style>"
    )
    if "sandbox-no-login" not in html:
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", hide_css + "</head>", html, count=1, flags=re.I)
        elif re.search(r"<body[^>]*>", html, re.I):
            html = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + hide_css, html, count=1, flags=re.I)
        else:
            html = hide_css + html
    return html


def soft_sandbox_badge(html: str, sport: str, which: str = "picks") -> str:
    """Strip duplicate sport subnav — live pl2-header already has PICKS/MODELS/RESULTS."""
    from sandbox_fixup import inject_sport_subnav

    return inject_sport_subnav(html, sport, which=which)


def rewrite_live_links(html: str, sport: str, which: str = "picks") -> str:
    base = sidecar_base()
    cfg = LIVE_SPORTS[sport]
    pairs = [
        (cfg["picks"].lstrip("/"), f"/{sport}/"),
        (cfg["results"].lstrip("/"), f"/{sport}/results"),
    ]
    if sport == "cfl":
        pairs.extend([("nfl-picks", "/cfl/"), ("nfl-results", "/cfl/results")])
    for slug, dest in pairs:
        html = html.replace(f"https://predictionlab.io/{slug}", dest)
        html = html.replace(f"{base}/{slug}", dest)
        html = html.replace(f'href="/{slug}"', f'href="{dest}"')
        html = html.replace(f"href='/{slug}'", f"href='{dest}'")
    html = html.replace('href="/static/', f'href="{base}/static/')
    html = html.replace("href='/static/", f"href='{base}/static/")
    html = html.replace('src="/static/', f'src="{base}/static/')
    html = html.replace("src='/static/", f"src='{base}/static/")
    # Social icons: serve from hub (reliable); share stays hub-relative
    html = html.replace(f'src="{base}/static/icons/social/', 'src="/static/icons/social/')
    html = html.replace(f"src='{base}/static/icons/social/", "src='/static/icons/social/")
    html = html.replace(f'src="{base}/share/', 'src="/share/')
    html = html.replace(f'href="{base}/share/', 'href="/share/')
    try:
        from sandbox_fixup import fix_share_social_assets

        html = fix_share_social_assets(html)
    except Exception:
        pass
    if sport == "cfl":
        html = re.sub(r"\bNFL\b", "CFL", html)
        title = "CFL Results | Prediction Lab" if which == "results" else "CFL Picks | Prediction Lab"
        html = re.sub(
            r"(<title>)(.*?)(</title>)",
            rf"\1{title}\3",
            html,
            count=1,
            flags=re.I | re.S,
        )
    return html


def proxy_live(sport: str, which: str = "picks", timeout: float = 180.0) -> tuple[str, dict[str, Any]]:
    """Proxy Golf/UFC/Tennis; CFL uses NFL chrome shell + real CFL schedule (no NFL teams)."""
    if sport not in LIVE_SPORTS:
        return (
            "<!doctype html><html><body><h1>Unknown sport</h1></body></html>",
            {"ok": False, "status": 404},
        )
    cfg = LIVE_SPORTS[sport]
    # Picks chrome for picks / predictions / total-edge; results chrome for results.
    if which in ("results", "performance"):
        path = cfg["results"]
    else:
        path = cfg["picks"]
    if not ensure_sidecar():
        body = (
            "<!doctype html><html><body style='font-family:system-ui;padding:1.5rem;background:#fbfbf8'>"
            f"<h1>{cfg['label']} unavailable</h1>"
            f"<p>Could not reach live sidecar at <code>{sidecar_base()}</code>.</p>"
            f"<p>Log: <code>{SIDECAR_LOG}</code></p>"
            "<p><a href='/'>← Hub</a></p></body></html>"
        )
        return body, {"ok": False, "source": "sidecar_down", "status": 503, "game_cards": 0}
    got = _http_get(f"{sidecar_base()}{path}", timeout=timeout)
    if not got:
        body = (
            "<!doctype html><html><body style='font-family:system-ui;padding:1.5rem;background:#fbfbf8'>"
            f"<h1>{cfg['label']} proxy failed</h1>"
            f"<p>No response from <code>{sidecar_base()}{path}</code>.</p>"
            "<p><a href='/'>← Hub</a></p></body></html>"
        )
        return body, {"ok": False, "source": "proxy_error", "status": 502, "game_cards": 0}
    html, status = got
    html = strip_auth_chrome(html)
    html = rewrite_live_links(html, sport, which=which)

    # CFL: live chrome shell + isolation engine pick cards (never NFL teams).
    if sport == "cfl":
        from cfl_page import probe_cfl_api, render_cfl_with_chrome

        html, cfl_meta = render_cfl_with_chrome(html, which=which)
        from sandbox_fixup import apply_sport_fixups

        html = apply_sport_fixups(html, "cfl", which=which)
        # CFL: no sticky sport subnav — only pl2-header (cleaned in render_cfl_with_chrome).
        probe = probe_cfl_api()
        sched_src = probe.get("schedule_source") or "none"
        return html, {
            "ok": status == 200 and len(html) > 500,
            "source": f"cfl_live_chrome:{sched_src}:{sidecar_base()}{path}",
            "status": status,
            "game_cards": int(cfl_meta.get("cards") or probe.get("window_count") or 0),
            "html_bytes": len(html),
            "cfl_api": {
                "teams": probe.get("teams_count"),
                "window_events": probe.get("window_count"),
                "schedule_source": sched_src,
                "note": probe.get("note"),
                "cards": cfl_meta.get("cards"),
                "which": which,
            },
        }

    # Golf: live chrome shell + ranked tournament win-% / results board.
    if sport == "golf":
        from golf_page import render_golf_with_chrome

        html, golf_meta = render_golf_with_chrome(html, which=which, event_id=None)
        return html, {
            "ok": status == 200 and len(html) > 500,
            "source": f"golf_live_chrome:{sidecar_base()}{path}",
            "status": status,
            "game_cards": int(golf_meta.get("players") or 0),
            "html_bytes": len(html),
            "event_id": golf_meta.get("event_id"),
            "chrome": golf_meta.get("chrome"),
        }

    # UFC: live chrome shell + isolation Elo/odds pick cards (never blank coin-flip).
    if sport == "ufc":
        from ufc_page import probe_ufc_api, render_ufc_with_chrome

        html, ufc_meta = render_ufc_with_chrome(html, which=which)
        from sandbox_fixup import apply_sport_fixups

        html = apply_sport_fixups(html, "ufc", which=which)
        probe = probe_ufc_api()
        sched_src = probe.get("schedule_source") or "none"
        return html, {
            "ok": status == 200 and len(html) > 500,
            "source": f"ufc_live_chrome:{sched_src}:{sidecar_base()}{path}",
            "status": status,
            "game_cards": int(ufc_meta.get("cards") or probe.get("window_count") or 0),
            "html_bytes": len(html),
            "ufc_api": {
                "window_events": probe.get("window_count"),
                "schedule_source": sched_src,
                "note": probe.get("note"),
                "cards": ufc_meta.get("cards"),
                "books": ufc_meta.get("books_on_cards"),
                "which": which,
                "meta": probe.get("meta"),
            },
        }

    from sandbox_fixup import apply_sport_fixups

    html = apply_sport_fixups(html, sport, which=which)
    html = soft_sandbox_badge(html, sport, which=which)
    return html, {
        "ok": status == 200 and len(html) > 500,
        "source": f"sidecar:{sidecar_base()}{path}",
        "status": status,
        "game_cards": html.count("pick-card-header") + html.count('class="game-card"'),
        "html_bytes": len(html),
    }
