#!/usr/bin/env python3
"""Sports Sandbox Hub — CFL | Golf | UFC | Tennis | MLB | Soccer | WNBA.

Run:
  cd "/Users/nimamesghali/Sports Sandbox/independent_sports"
  predictionlabfix_work/.venv/bin/python hub/app.py

Open http://127.0.0.1:5080

Hub NEVER imports NHL77FINAL (avoids macOS OOM). MLB / WNBA / Soccer-picks
HTTP-proxy to isolation Flask apps (:5057 / :5056 / :5055), started on demand.
Soccer results stay on the light league-dropdown UI in-process.

Sandbox only — not live PredictionLab. Do not push / deploy.
"""
from __future__ import annotations

import importlib.util
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

# Prefer live project venv for deps (sklearn, etc.) — re-exec once.
LIVE_VENV_PY = Path.home() / "Documents/Personal/predictionlabfix_work/.venv/bin/python"
if LIVE_VENV_PY.is_file() and Path(sys.executable).resolve() != LIVE_VENV_PY.resolve():
    if os.environ.get("SPORTS_SANDBOX_SKIP_LIVE_VENV") != "1":
        os.execv(str(LIVE_VENV_PY), [str(LIVE_VENV_PY), *sys.argv])

# Hub process must never import NHL77FINAL.
os.environ.setdefault("ISOLATION_NO_LIVE_IMPORT", "1")

from flask import Flask, Response, jsonify, render_template, send_from_directory

ROOT = Path(__file__).resolve().parents[1]
HUB_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HUB_DIR) not in sys.path:
    sys.path.insert(0, str(HUB_DIR))

PORT = int(os.environ.get("PORT", os.environ.get("HUB_PORT", "5081")))
LIVE_ROOT = Path.home() / "Documents/Personal/predictionlabfix_work"
LIVE_VENV = LIVE_ROOT / ".venv" / "bin" / "python"
_ISO_ROOTS = {
    "mlb": Path.home() / "Documents/Personal/mlb",
    "soccer": Path.home() / "Documents/Personal/soccer",
    "wnba": Path.home() / "Documents/Personal/wnba",
}
# Isolation Flask apps (separate processes — may load NHL77FINAL themselves).
_ISO_APPS: dict[str, dict[str, Any]] = {
    "mlb": {
        "port": int(os.environ.get("MLB_LAB_PORT", "5157")),
        "root": _ISO_ROOTS["mlb"],
        "port_env": "MLB_LAB_PORT",
        "health": "/api/health",
        "picks": "/mlb-picks",
        "results": "/mlb-results",
    },
    "soccer": {
        "port": int(os.environ.get("SOCCER_LAB_PORT", "5155")),
        "root": _ISO_ROOTS["soccer"],
        "port_env": "SOCCER_LAB_PORT",
        "health": "/api/health",
        "picks": "/soccer-picks",
        "results": "/soccer-results",
    },
    "wnba": {
        "port": int(os.environ.get("WNBA_LAB_PORT", "5156")),
        "root": _ISO_ROOTS["wnba"],
        "port_env": "WNBA_LAB_PORT",
        "health": "/api/health",
        "picks": "/wnba-picks",
        "results": "/wnba-results",
    },
}
_iso_lock = threading.Lock()
_iso_procs: dict[str, subprocess.Popen] = {}
_iso_logs = {s: Path(f"/tmp/sports_hub_{s}_iso.log") for s in _ISO_APPS}
_soccer_audit_mod = None
_soccer_audit_lock = threading.Lock()

_LOGIN_STUB = (
    '<!doctype html><html><head><meta charset="utf-8"/><title>Sandbox — no login needed</title></head>'
    '<body style="font-family:system-ui;padding:2rem;background:#fbfbf8;color:#0b0b0a">'
    '<div style="max-width:28rem;margin:2rem auto;padding:1.25rem;border:1px solid #ddd;'
    'background:#fff">'
    "<strong>Sandbox — no login needed</strong>"
    "<p>This hub is for local model fixes only. No accounts, no Stripe, no premium wall.</p>"
    '<p><a href="/">← Back to hub</a></p></div></body></html>'
)


def _seed() -> None:
    try:
        from shared.seed_all import main as seed_main

        seed_main()
    except Exception as e:
        print(f"[hub] seed warning: {e}")


def _http_get(url: str, timeout: float = 180.0) -> tuple[str, int] | None:
    try:
        req = Request(url, headers={"User-Agent": "sports-sandbox-hub"})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return body, int(getattr(resp, "status", 200) or 200)
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        # Return body so callers can fall back; non-2xx is not "no response".
        return body, int(getattr(e, "code", 500) or 500)
    except (URLError, TimeoutError, OSError):
        return None


def _iso_base(sport: str) -> str:
    return f"http://127.0.0.1:{_ISO_APPS[sport]['port']}"


def _iso_alive(sport: str) -> bool:
    cfg = _ISO_APPS[sport]
    base = _iso_base(sport)
    for path in (cfg["health"], "/", cfg["picks"]):
        got = _http_get(f"{base}{path}", timeout=2.5)
        if got and got[1] < 500:
            return True
    return False


def ensure_iso_app(sport: str, wait_s: float = 90.0) -> bool:
    """Start isolation app.py in a separate process. Hub never imports NHL77FINAL."""
    if sport not in _ISO_APPS:
        return False
    if _iso_alive(sport):
        return True
    with _iso_lock:
        if _iso_alive(sport):
            return True
        cfg = _ISO_APPS[sport]
        root: Path = cfg["root"]
        app_py = root / "app.py"
        if not app_py.is_file():
            print(f"[hub] isolation app missing: {app_py}", flush=True)
            return False
        py = str(LIVE_VENV if LIVE_VENV.is_file() else sys.executable)
        log_path = _iso_logs[sport]
        logf = open(log_path, "ab", buffering=0)
        env = os.environ.copy()
        env[cfg["port_env"]] = str(cfg["port"])
        env.pop("ISOLATION_NO_LIVE_IMPORT", None)
        if sport == "mlb":
            try:
                from live_proxy import ensure_sidecar, sidecar_base
                ensure_sidecar()
                env["MLB_LIVE_LOCAL"] = sidecar_base()
            except Exception as e:
                print(f"[hub] mlb sidecar wire warning: {e}", flush=True)
                env["MLB_LIVE_LOCAL"] = "http://127.0.0.1:5052"
            env["ISOLATION_NO_LIVE_IMPORT"] = "1"
        print(f"[hub] starting {sport} isolation on :{cfg['port']} …", flush=True)
        proc = subprocess.Popen(
            [py, str(app_py)],
            cwd=str(root),
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _iso_procs[sport] = proc
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if proc.poll() is not None:
                print(f"[hub] {sport} isolation exited code={proc.returncode}", flush=True)
                return False
            if _iso_alive(sport):
                print(f"[hub] {sport} isolation ready → {_iso_base(sport)}", flush=True)
                return True
            time.sleep(1.0)
        print(f"[hub] {sport} isolation start timed out (log {log_path})", flush=True)
        return False


def _strip_auth_chrome(html: str) -> str:
    """Remove login / premium walls from proxied live HTML."""
    if not html:
        return html
    # Whole anchors for auth / premium / plans
    html = re.sub(
        r'<a\b[^>]*(?:href=["\'][^"\']*/(?:login|register|signup|subscribe|pricing|plans|account|billing|checkout)'
        r'|class=["\'][^"\']*(?:tv-premium-cta|join-premium|nav-cta-premium|premium)[^"\']*["\'])'
        r'[^>]*>.*?</a>',
        "<!-- sandbox: auth link stripped -->",
        html,
        flags=re.I | re.S,
    )
    # Buttons that are login / premium CTAs
    html = re.sub(
        r'<button\b[^>]*(?:login|sign[\s-]?up|sign[\s-]?in|premium|subscribe)[^>]*>.*?</button>',
        "<!-- sandbox: auth button stripped -->",
        html,
        flags=re.I | re.S,
    )
    # Sticky join-premium bar (non-greedy across nested divs via known id/class)
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
    # Leftover text from older strip that only removed opening tags
    html = re.sub(
        r'<!-- sandbox: auth cta stripped -->(?:Login|Sign\s*In|Sign\s*Up|Join\s*Premium|Subscribe)</a>',
        "<!-- sandbox: auth leftover stripped -->",
        html,
        flags=re.I,
    )
    # href rewrite for any remaining auth URLs
    html = re.sub(
        r'href=(["\'])(?:https?://[^"\']*)?/(?:login|register|signup|subscribe|pricing|plans|account|billing|checkout)(?:\?[^"\']*)?\1',
        r'href="/login"',
        html,
        flags=re.I,
    )
    html = re.sub(r'\sdata-(?:require-auth|login-required)=["\'][^"\']*["\']', "", html, flags=re.I)
    # Locked card-detail stubs (when premium content was not rendered)
    html = re.sub(
        r'<div\b[^>]*class=["\'][^"\']*odds-pricing-locked[^"\']*["\'][^>]*>[\s\S]*?</div>',
        "",
        html,
        flags=re.I,
    )
    # CSS kill-switch for anything the regex missed
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


def _inject_banner(html: str, sport: str, which: str = "picks") -> str:
    """Strip duplicate Picks/Total Edge/Results bars — live pl2-header is enough."""
    from sandbox_fixup import inject_sport_subnav

    return inject_sport_subnav(html, sport, which=which)


def _rewrite_sport_links(html: str, sport: str) -> str:
    base = _iso_base(sport)
    pairs = {
        "mlb": [
            ("mlb-picks", "/mlb/"),
            ("mlb-results", "/mlb/results"),
        ],
        "soccer": [
            ("soccer-picks", "/soccer/"),
            ("soccer-results", "/soccer/results"),
        ],
        "wnba": [
            ("wnba-picks", "/wnba/"),
            ("wnba-results", "/wnba/results"),
            ("lab", "/wnba/lab"),
        ],
    }
    for slug, dest in pairs.get(sport, []):
        html = html.replace(f"https://predictionlab.io/{slug}", dest)
        html = html.replace(f"{base}/{slug}", dest)
        html = html.replace(f'href="/{slug}"', f'href="{dest}"')
        html = html.replace(f"href='/{slug}'", f"href='{dest}'")
    # Point CSS/JS at isolation, but keep social icons on the hub (iso often lacks them).
    html = html.replace('href="/static/', f'href="{base}/static/')
    html = html.replace("href='/static/", f"href='{base}/static/")
    html = html.replace('src="/static/', f'src="{base}/static/')
    html = html.replace("src='/static/", f"src='{base}/static/")
    # Undo social-icon rewrite → hub /static/icons/social/*
    html = html.replace(f'src="{base}/static/icons/social/', 'src="/static/icons/social/')
    html = html.replace(f"src='{base}/static/icons/social/", "src='/static/icons/social/")
    html = html.replace(f'href="{base}/static/icons/social/', 'href="/static/icons/social/')
    html = html.replace(f"href='{base}/static/icons/social/", "href='/static/icons/social/")
    # Share assets stay hub-relative (proxied below)
    html = html.replace(f'src="{base}/share/', 'src="/share/')
    html = html.replace(f'href="{base}/share/', 'href="/share/')
    try:
        from sandbox_fixup import fix_share_social_assets

        html = fix_share_social_assets(html)
    except Exception:
        pass
    return html


def _proxy_iso(sport: str, path: str, timeout: float = 180.0) -> tuple[str, dict[str, Any]]:
    """HTTP proxy to isolation Flask app. Hub stays light."""
    base = _iso_base(sport)
    log_path = _iso_logs[sport]
    if not ensure_iso_app(sport):
        how = (
            f'cd "{_ISO_APPS[sport]["root"]}" && "{LIVE_VENV}" app.py'
            if LIVE_VENV.is_file()
            else f'cd "{_ISO_APPS[sport]["root"]}" && python3 app.py'
        )
        body = (
            "<!doctype html><html><body style='font-family:system-ui;padding:1.5rem;background:#fbfbf8'>"
            f"<h1>{sport.upper()} isolation app unavailable</h1>"
            f"<p>Could not reach <code>{base}</code>. Hub is still up — "
            "start the isolation app in another terminal:</p>"
            f"<pre style='background:#fff;border:1px solid #ddd;padding:12px;overflow:auto'>{how}</pre>"
            f"<p>Log: <code>{log_path}</code></p>"
            "<p><a href='/'>← Hub</a></p></body></html>"
        )
        return body, {"ok": False, "source": "iso_down", "status": 503, "game_cards": 0}
    got = _http_get(f"{base}{path}", timeout=timeout)
    if not got:
        body = (
            "<!doctype html><html><body style='font-family:system-ui;padding:1.5rem;background:#fbfbf8'>"
            f"<h1>{sport.upper()} proxy failed</h1>"
            f"<p>No response from <code>{base}{path}</code>.</p>"
            f"<p>Isolation may have been OOM-killed — restart it; hub stays up.</p>"
            f"<p>Log: <code>{log_path}</code></p>"
            "<p><a href='/'>← Hub</a></p></body></html>"
        )
        return body, {"ok": False, "source": "proxy_error", "status": 502, "game_cards": 0}
    html, status = got
    html = _strip_auth_chrome(html)
    html = _rewrite_sport_links(html, sport)
    try:
        from sandbox_fixup import apply_sport_fixups

        which = "results" if "results" in path else "picks"
        html = apply_sport_fixups(html, sport, which=which)
    except Exception as e:
        print(f"[hub] sandbox_fixup warning ({sport}): {e}", flush=True)
    which = "results" if "results" in path else "picks"
    html = _inject_banner(html, sport, which=which)
    return html, {
        "ok": status == 200 and len(html) > 500,
        "source": f"proxy:{base}",
        "status": status,
        "game_cards": html.count("pick-card-header") + html.count('class="game-card"'),
        "html_bytes": len(html),
    }


def _wnba_lab_fallback() -> tuple[str, dict[str, Any]]:
    """Light Elo slate when isolation live-parity picks are down (no NHL77FINAL)."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    wnba_root = _ISO_ROOTS["wnba"]
    wr = str(wnba_root.resolve())
    inserted = wr not in sys.path
    if inserted:
        sys.path.insert(0, wr)
    try:
        picks_path = wnba_root / "engine" / "picks.py"
        spec = importlib.util.spec_from_file_location("_hub_wnba_picks_fb", picks_path)
        picks = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(picks)
        data = picks.slate_with_models()
    finally:
        if inserted:
            try:
                sys.path.remove(wr)
            except ValueError:
                pass
    env = Environment(
        loader=FileSystemLoader(str(wnba_root / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("picks.html").render(data=data, port=PORT)
    html = html.replace('href="/static/lab.css"', 'href="/wnba/lab/static/lab.css"')
    html = _strip_auth_chrome(html)
    html = _inject_banner(html, "wnba", which="picks")
    return html, {
        "ok": True,
        "source": "isolation_elo_fallback",
        "status": 200,
        "html_bytes": len(html),
        "game_cards": len((data or {}).get("games") or []),
    }


def _load_soccer_audit():
    """Load isolation audit module (soccer_models — NOT NHL77FINAL).

    Reloads from disk so Last Night / Last 7 fixes apply without a full hub restart.
    """
    global _soccer_audit_mod
    with _soccer_audit_lock:
        soccer_root = str(_ISO_ROOTS["soccer"].resolve())
        if soccer_root not in sys.path:
            sys.path.insert(0, soccer_root)
        path = _ISO_ROOTS["soccer"] / "app_audit_legacy.py"
        sys.modules.pop("_hub_soccer_audit", None)
        _soccer_audit_mod = None
        spec = importlib.util.spec_from_file_location("_hub_soccer_audit", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        _soccer_audit_mod = mod
        return mod


def _fetch_mlb_production_results_html() -> tuple[str, dict[str, Any]]:
    """Fetch predictionlab.io /mlb-results — local sqlite Edge leans diverge from Render."""
    import ssl
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    origin = (os.environ.get("MLB_RESULTS_PROD_ORIGIN") or "https://predictionlab.io").rstrip("/")
    if os.environ.get("MLB_RESULTS_LOCAL", "").strip() in ("1", "true", "yes"):
        return "", {"ok": False, "source": "prod_disabled", "status": 0}
    url = f"{origin}/mlb-results"
    headers = {"User-Agent": "sports-sandbox-hub-mlb-results", "Accept": "text/html"}
    contexts: list = [None]
    if url.startswith("https://"):
        contexts = [ssl.create_default_context(), ssl._create_unverified_context()]
    last_err: Exception | None = None
    for ctx in contexts:
        try:
            req = Request(url, headers=headers)
            kw: dict[str, Any] = {"timeout": 120.0}
            if ctx is not None:
                kw["context"] = ctx
            with urlopen(req, **kw) as resp:
                html = resp.read().decode("utf-8", "replace")
                status = int(getattr(resp, "status", 200) or 200)
            break
        except ssl.SSLError as e:
            last_err = e
            html, status = "", 0
            continue
        except HTTPError as e:
            try:
                html = e.read().decode("utf-8", "replace")
            except Exception:
                html = ""
            status = int(getattr(e, "code", 500) or 500)
            break
        except (URLError, TimeoutError, OSError) as e:
            last_err = e
            html, status = "", 0
            continue
    else:
        print(f"[hub] MLB prod results miss: {last_err}", flush=True)
        return "", {"ok": False, "source": "prod_error", "status": 502, "error": str(last_err)}

    if not html or len(html) < 5000 or "Last Night" not in html or "Edge" not in html:
        return html or "", {
            "ok": False,
            "source": f"prod:{origin}",
            "status": status or 502,
            "error": "production HTML missing tallies",
        }
    from sandbox_fixup import apply_sport_fixups

    html = _strip_auth_chrome(html)
    html = _rewrite_sport_links(html, "mlb")
    html = apply_sport_fixups(html, "mlb", which="results")
    html = _inject_banner(html, "mlb", which="results")
    return html, {
        "ok": True,
        "source": f"prod:{origin}/mlb-results",
        "status": status,
        "game_cards": html.count("pick-card-header") + html.count('class="game-card"'),
        "html_bytes": len(html),
    }


def _fetch_live_style_results_html(sport: str) -> tuple[str, dict[str, Any]]:
    """Fetch MLB/WNBA live-parity results HTML for tally parsing (no NHL77FINAL)."""
    sport = sport.lower()
    if sport == "mlb":
        # Prefer production HTML: local elo_home_prob is near-coin-flip noise vs Render.
        html, meta = _fetch_mlb_production_results_html()
        if meta.get("ok"):
            return html, meta
        html, meta = _proxy_iso("mlb", "/mlb-results")
        if not meta.get("ok") or int(meta.get("status") or 0) >= 500:
            from live_proxy import ensure_sidecar, sidecar_base, strip_auth_chrome
            from sandbox_fixup import apply_sport_fixups

            if not ensure_sidecar():
                return html, meta
            got = _http_get(f"{sidecar_base()}/mlb-results", timeout=180.0)
            if not got or got[1] >= 500:
                return html, meta
            html2, status = got
            html2 = strip_auth_chrome(html2)
            html2 = _rewrite_sport_links(html2, "mlb")
            html2 = apply_sport_fixups(html2, "mlb", which="results")
            html2 = _inject_banner(html2, "mlb", which="results")
            return html2, {
                "ok": status == 200 and len(html2) > 500,
                "source": f"sidecar:{sidecar_base()}/mlb-results",
                "status": status,
            }
        return html, meta
    if sport == "wnba":
        html, meta = _proxy_iso("wnba", "/wnba-results?parity=live")
        if not meta.get("ok"):
            html, meta = _proxy_iso("wnba", "/wnba-results")
        return html, meta
    return "", {"ok": False, "error": f"unsupported sport {sport}"}


def _team_tabbed_results_page(sport: str, sport_label: str) -> str:
    from sandbox_fixup import inject_sport_subnav
    from team_tabbed_results import render_team_results_page

    return render_team_results_page(
        sport=sport,
        sport_label=sport_label,
        api_base=f"/{sport}/api",
        show_league=False,
        inject_subnav=lambda h, s: inject_sport_subnav(h, s, which="results"),
    )


def _team_tabbed_payload(sport: str) -> dict[str, Any]:
    from team_tabbed_results import build_cfl_payload, markets_from_live_html

    sport = sport.lower()
    if sport == "cfl":
        return build_cfl_payload()
    html, meta = _fetch_live_style_results_html(sport)
    if not html or len(html) < 500:
        return {
            "ok": False,
            "error": f"Could not load {sport.upper()} results source",
            "meta": {k: meta.get(k) for k in ("ok", "status", "source") if k in (meta or {})},
        }
    payload = markets_from_live_html(html, sport)
    payload["source"] = str(meta.get("source") or "live_html")
    return payload


def _soccer_results_page() -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from sandbox_fixup import inject_sport_subnav, strip_sandbox_dev_notes

    root = _ISO_ROOTS["soccer"]
    env = Environment(
        loader=FileSystemLoader(str(root / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("results.html").render(
        static_base="/soccer/lab-static",
        api_base="/soccer/api",
        picks_href="/soccer/",
        results_href="/soccer/results",
        show_hub=True,
    )
    html = strip_sandbox_dev_notes(html)
    return inject_sport_subnav(html, "soccer", which="results")


def _wnba_honest_results() -> tuple[str, dict[str, Any]]:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    root = _ISO_ROOTS["wnba"]
    rg_path = root / "engine" / "results_grade.py"
    spec = importlib.util.spec_from_file_location("_hub_wnba_results_grade", rg_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    data = mod.build_results_payload(days=21)
    env = Environment(
        loader=FileSystemLoader(str(root / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("results.html").render(data=data)
    html = html.replace('href="/static/', 'href="/wnba/lab/static/')
    html = _strip_auth_chrome(html)
    html = _inject_banner(html, "wnba", which="results")
    return html, {
        "ok": True,
        "source": "isolation_honest",
        "status": 200,
        "daily_tally_date": data.get("daily_tally_date"),
        "html_bytes": len(html),
        "game_cards": 0,
    }


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(HUB_DIR / "templates"),
        static_folder=str(HUB_DIR / "static"),
        static_url_path="/static",
    )
    _seed()

    for slug in ("cfl", "golf", "ufc", "tennis", "mlb", "soccer", "wnba"):
        try:
            mod = __import__(f"{slug}.api", fromlist=["create_blueprint"])
            app.register_blueprint(mod.create_blueprint())
        except Exception as e:
            print(f"[hub] blueprint skip {slug}: {e}", flush=True)

    @app.get("/")
    def home():
        sports = [
            {"title": "CFL", "href": "/cfl/", "blurb": "CFL scoreboard pick cards.", "chips": ["Pick cards", "No login"]},
            {"title": "Golf", "href": "/golf/", "blurb": "Ranked tournament win-% board (no book lines focus).", "chips": ["Win % board", "No login"]},
            {"title": "Fantasy", "href": "/fantasy/", "blurb": "NFL · MLB · NBA · NHL — rankings, start/sit, waivers, matchups, draft.", "chips": ["MVP", "Hamburger menu", "No login"]},
            {"title": "UFC", "href": "/ufc/", "blurb": "UFC pick-only fight cards.", "chips": ["Pick cards", "No login"]},
            {"title": "Tennis", "href": "/tennis/", "blurb": "Live-parity picks; N/A when books missing.", "chips": ["Live parity", "No login"]},
            {"title": "MLB", "href": "/mlb/", "blurb": "Live-parity picks and results.", "chips": ["Live parity", "No login"]},
            {"title": "Soccer", "href": "/soccer/", "blurb": "Live-like picks cards; league-dropdown results.", "chips": ["Live picks", "No login"]},
            {"title": "WNBA", "href": "/wnba/", "blurb": "Live-parity picks and results.", "chips": ["Live parity", "No login"]},
        ]
        return render_template("home.html", sport="home", sports=sports)

    @app.get("/health")
    def health():
        return jsonify({
            "ok": True,
            "sandbox": True,
            "port": PORT,
            "iso": {
                s: {"url": _iso_base(s), "alive": _iso_alive(s)}
                for s in ("mlb", "soccer", "wnba")
            },
            "no_login": True,
        })

    @app.get("/login")
    @app.get("/register")
    @app.get("/signup")
    @app.get("/subscribe")
    @app.get("/pricing")
    @app.get("/account")
    @app.get("/billing")
    @app.get("/plans")
    def sandbox_no_login():
        return Response(_LOGIN_STUB, mimetype="text/html; charset=utf-8")

    # Social icons: hub serves live project icons (iso apps often lack /static/icons/social).
    _live_icons = LIVE_ROOT / "static" / "icons" / "social"
    _hub_icons = HUB_DIR / "static" / "icons" / "social"
    try:
        _hub_icons.mkdir(parents=True, exist_ok=True)
        if _live_icons.is_dir():
            import shutil

            for p in _live_icons.glob("*.svg"):
                dest = _hub_icons / p.name
                if not dest.is_file() or dest.stat().st_mtime < p.stat().st_mtime:
                    shutil.copy2(p, dest)
    except Exception as e:
        print(f"[hub] social icons copy warning: {e}", flush=True)

    def _proxy_bytes(url: str, timeout: float = 60.0) -> tuple[bytes, str, int] | None:
        try:
            req = Request(url, headers={"User-Agent": "sports-sandbox-hub-share"})
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                ctype = resp.headers.get("Content-Type") or "application/octet-stream"
                return data, ctype, int(getattr(resp, "status", 200) or 200)
        except Exception:
            return None

    @app.route("/share/<path:subpath>")
    def proxy_share(subpath: str):
        """Proxy share JPEG / view pages to live sidecar, then production."""
        from live_proxy import ensure_sidecar, sidecar_base

        bases: list[str] = []
        try:
            if ensure_sidecar():
                bases.append(sidecar_base())
        except Exception:
            pass
        bases.extend(
            [
                "http://127.0.0.1:5052",
                "http://127.0.0.1:5053",
                "https://predictionlab.io",
            ]
        )
        path = f"/share/{subpath}"
        for base in bases:
            got = _proxy_bytes(f"{base.rstrip('/')}{path}")
            if not got:
                continue
            data, ctype, status = got
            if status >= 400 or not data:
                continue
            # Skip tiny error bodies
            if len(data) < 64 and b"<!DOCTYPE" in data[:200].upper():
                continue
            resp = Response(data, status=200, mimetype=ctype)
            resp.headers["X-Share-Proxy"] = base
            return resp
        return Response("Share asset unavailable", status=404)

    # ── CFL — live chrome (sidecar) + isolation pick cards ───────────
    def _cfl_page(which: str = "picks"):
        from live_proxy import proxy_live

        html, meta = proxy_live("cfl", which=which)
        if not meta.get("ok") or int(meta.get("status") or 0) >= 500:
            from cfl_page import build_cfl_pick_page

            html, meta2 = build_cfl_pick_page(which=which)
            meta = {**meta, **meta2, "source": "cfl_fragment_fallback", "ok": True}
        resp = Response(html, status=200, mimetype="text/html; charset=utf-8")
        resp.headers["X-Sandbox-No-Login"] = "1"
        resp.headers["X-CFL-Source"] = str(meta.get("source") or "isolation")
        resp.headers["X-CFL-Cards"] = str(meta.get("game_cards") or meta.get("cards") or 0)
        resp.headers["X-CFL-Which"] = which
        return resp

    @app.get("/cfl/")
    @app.get("/cfl")
    @app.get("/cfl/picks")
    def cfl_picks():
        return _cfl_page("picks")

    @app.get("/cfl/predictions")
    def cfl_predictions():
        # Predictions == Picks — single URL only
        from flask import redirect

        return redirect("/cfl/", code=302)

    @app.get("/cfl/results")
    def cfl_results():
        html = _team_tabbed_results_page("cfl", "CFL")
        resp = Response(html, status=200, mimetype="text/html; charset=utf-8")
        resp.headers["X-Sandbox-No-Login"] = "1"
        resp.headers["X-CFL-Results"] = "tabbed-markets"
        return resp

    @app.get("/cfl-results")
    def cfl_results_alias():
        return cfl_results()

    @app.get("/cfl/api/picks")
    def cfl_api_picks():
        try:
            return jsonify(_team_tabbed_payload("cfl"))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/cfl/api/leagues")
    def cfl_api_leagues():
        return jsonify({"ok": True, "leagues": []})

    @app.get("/cfl/performance")
    def cfl_performance():
        return cfl_results()

    @app.get("/cfl/models")
    def cfl_models():
        return cfl_picks()

    @app.get("/cfl/total-edge")
    def cfl_total_edge_gone():
        # CFL Total Edge deleted — no books feed; edge vs book is N/A for every row.
        return Response("Not Found — CFL Total Edge removed", status=404)

    # ── Golf / UFC / Tennis — live-parity via sidecar (:5052) ──
    def _live_sport_page(sport: str, which: str = "picks"):
        from live_proxy import proxy_live

        html, meta = proxy_live(sport, which=which)
        resp = Response(html, status=int(meta.get("status") or 200), mimetype="text/html; charset=utf-8")
        resp.headers["X-Sandbox-No-Login"] = "1"
        resp.headers["X-Live-Parity"] = str(meta.get("source") or "")
        return resp

    def _golf_page(which: str = "picks"):
        """Live pl2 chrome (sidecar) + ranked win-% / results board."""
        from flask import request
        from golf_page import render_golf_with_chrome
        from live_proxy import ensure_sidecar, rewrite_live_links, sidecar_base, strip_auth_chrome

        event_id = (request.args.get("event") or "").strip() or None
        chrome = ""
        chrome_src = "none"
        try:
            if ensure_sidecar():
                path = "/golf-results" if which == "results" else "/golf-picks"
                got = _http_get(f"{sidecar_base()}{path}", timeout=180.0)
                if got and got[0] and "<body" in got[0].lower():
                    chrome = strip_auth_chrome(got[0])
                    chrome = rewrite_live_links(chrome, "golf", which=which)
                    chrome_src = f"sidecar:{sidecar_base()}{path}"
        except Exception as e:
            print(f"[hub] golf chrome fetch warning: {e}", flush=True)

        try:
            html, meta = render_golf_with_chrome(chrome, which=which, event_id=event_id)
        except Exception as e:
            print(f"[hub] golf board error: {e}", flush=True)
            return _live_sport_page("golf", which)

        resp = Response(html, status=200, mimetype="text/html; charset=utf-8")
        resp.headers["X-Sandbox-No-Login"] = "1"
        resp.headers["X-Golf-Source"] = str(meta.get("source") or which)
        resp.headers["X-Golf-Chrome"] = str(meta.get("chrome") or chrome_src)
        if meta.get("event_id"):
            resp.headers["X-Golf-Event"] = str(meta.get("event_id"))
        return resp

    @app.get("/golf/")
    @app.get("/golf")
    def golf_picks():
        return _golf_page("picks")

    @app.get("/golf/results")
    def golf_results():
        return _golf_page("results")

    @app.get("/golf/performance")
    def golf_performance():
        return golf_results()

    @app.get("/golf/models")
    def golf_models():
        return golf_picks()

    def _ufc_page(which: str = "picks"):
        from live_proxy import proxy_live
        from ufc_page import build_ufc_pick_page, render_ufc_with_chrome

        # Short chrome timeout — isolation cards render even if sidecar is slow.
        html, meta = proxy_live("ufc", which=which, timeout=25.0)
        if not meta.get("ok") or int(meta.get("status") or 0) >= 500:
            # Results: reuse picks chrome shell when /ufc-results is slow/down.
            if which == "results":
                chrome_html, chrome_meta = proxy_live("ufc", which="picks", timeout=20.0)
                if chrome_meta.get("ok") and chrome_html and "<body" in chrome_html.lower():
                    html, meta2 = render_ufc_with_chrome(chrome_html, which="results")
                    meta = {
                        **chrome_meta,
                        **meta2,
                        "source": "ufc_results_via_picks_chrome",
                        "ok": True,
                        "status": 200,
                    }
                else:
                    html, meta2 = build_ufc_pick_page(which="results")
                    meta = {**meta, **meta2, "source": "ufc_fragment_fallback", "ok": True}
            else:
                html, meta2 = build_ufc_pick_page(which=which)
                meta = {**meta, **meta2, "source": "ufc_fragment_fallback", "ok": True}
        resp = Response(html, status=200, mimetype="text/html; charset=utf-8")
        resp.headers["X-Sandbox-No-Login"] = "1"
        resp.headers["X-UFC-Source"] = str(meta.get("source") or "ufc")
        cards_n = meta.get("cards") if which == "results" else None
        resp.headers["X-UFC-Cards"] = str(
            cards_n if cards_n is not None else (meta.get("game_cards") or meta.get("cards") or 0)
        )
        resp.headers["X-UFC-Which"] = which
        return resp

    @app.get("/ufc/")
    @app.get("/ufc")
    def ufc_picks():
        return _ufc_page("picks")

    @app.get("/ufc/results")
    def ufc_results():
        return _ufc_page("results")

    @app.get("/ufc/performance")
    def ufc_performance():
        return ufc_results()

    @app.get("/ufc/models")
    def ufc_models():
        return ufc_picks()

    @app.get("/tennis/")
    @app.get("/tennis")
    def tennis_picks():
        return _live_sport_page("tennis", "picks")

    @app.get("/tennis/results")
    def tennis_results():
        return _live_sport_page("tennis", "results")

    @app.get("/tennis/performance")
    def tennis_performance():
        return tennis_results()

    @app.get("/tennis/models")
    def tennis_models():
        return tennis_picks()


    def _mlb_via_sidecar(path: str) -> tuple[str, dict[str, Any]]:
        """Direct sidecar proxy when isolation MLB app is broken (never import NHL77FINAL)."""
        from live_proxy import ensure_sidecar, sidecar_base, strip_auth_chrome
        from sandbox_fixup import apply_sport_fixups

        # Optional override (e.g. fresh local sidecar while :5052 is stuck on old code).
        override = (os.environ.get("MLB_PICKS_SIDECAR") or "").rstrip("/")
        bases = []
        if override:
            bases.append(override)
        if ensure_sidecar():
            bases.append(sidecar_base())
        bases.append("http://127.0.0.1:5052")
        # Prefer newest fixed sidecar if present.
        bases.append("http://127.0.0.1:5053")

        html, status, used = "", 502, ""
        for base in bases:
            if not base:
                continue
            got = _http_get(f"{base}{path}", timeout=180.0)
            if got and got[1] < 500 and len(got[0] or "") > 500:
                html, status = got
                used = base
                break
        if not used:
            body = (
                "<!doctype html><html><body style='font-family:system-ui;padding:1.5rem'>"
                "<h1>MLB unavailable</h1>"
                f"<p>Sidecar down (tried {', '.join(bases)}).</p>"
                "<p><a href='/'>← Hub</a></p></body></html>"
            )
            return body, {"ok": False, "source": "sidecar_down", "status": 503, "game_cards": 0}
        html = strip_auth_chrome(html)
        html = _rewrite_sport_links(html, "mlb")
        which = "results" if "results" in path else "picks"
        html = apply_sport_fixups(html, "mlb", which=which)
        html = _inject_banner(html, "mlb", which=which)
        return html, {
            "ok": status == 200 and len(html) > 500,
            "source": f"sidecar:{used}{path}",
            "status": status,
            "game_cards": html.count("pick-card-header") + html.count('class="game-card"'),
            "html_bytes": len(html),
        }

    # ── MLB (proxy isolation :5057 — never import NHL77FINAL here) ─────
    @app.get("/mlb/")
    @app.get("/mlb")
    @app.get("/mlb/picks")
    @app.get("/mlb-picks")
    @app.get("/mlb-picks/<filter_date>")
    def mlb_picks(filter_date=None):
        from flask import request

        day = filter_date or request.args.get("date")
        path = "/mlb-picks" + (f"/{day}" if day else "")
        # Prefer fixed local sidecar (MLB_PICKS_SIDECAR / :5053) so pick/projection
        # consistency fixes load even when an old :5052 process cannot be killed.
        html, meta = _mlb_via_sidecar(path)
        if not meta.get("ok") or int(meta.get("status") or 0) >= 500:
            html, meta = _proxy_iso("mlb", path)
        status = int(meta.get("status") or 200)
        resp = Response(html, status=status if status >= 400 and not meta.get("ok") else 200, mimetype="text/html; charset=utf-8")
        resp.headers["X-MLB-Isolation"] = "hub-proxy-iso"
        resp.headers["X-Hub-Source"] = str(meta.get("source") or "")
        resp.headers["X-Sandbox-No-Login"] = "1"
        return resp

    @app.get("/mlb/results")
    @app.get("/mlb-results")
    def mlb_results():
        # Live-parity chrome via sidecar (same path as picks) — not hub-tabbed-results.
        html, meta = _mlb_via_sidecar("/mlb-results")
        if not meta.get("ok") or int(meta.get("status") or 0) >= 500:
            html, meta = _proxy_iso("mlb", "/mlb-results")
        if not meta.get("ok") or int(meta.get("status") or 0) >= 500:
            # Last resort: production HTML (still live chrome, not custom tabbed UI).
            html2, meta2 = _fetch_mlb_production_results_html()
            if meta2.get("ok"):
                html, meta = html2, meta2
        status = int(meta.get("status") or 200)
        resp = Response(
            html,
            status=status if status >= 400 and not meta.get("ok") else 200,
            mimetype="text/html; charset=utf-8",
        )
        resp.headers["X-MLB-Isolation"] = "hub-proxy-iso"
        resp.headers["X-Hub-Source"] = str(meta.get("source") or "")
        resp.headers["X-Sandbox-No-Login"] = "1"
        return resp

    @app.get("/mlb/api/picks")
    def mlb_api_picks():
        try:
            return jsonify(_team_tabbed_payload("mlb"))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/mlb/api/leagues")
    def mlb_api_leagues():
        return jsonify({"ok": True, "leagues": []})

    @app.get("/mlb/performance")
    def mlb_performance():
        return mlb_results()

    @app.get("/mlb/models")
    def mlb_models():
        return mlb_picks()

    @app.get("/mlb/lab")
    def mlb_lab():
        return Response(
            "<!doctype html><html><body style='font-family:system-ui;padding:1.5rem'>"
            "<h1>MLB isolation</h1>"
            f"<p>Hub proxies <code>{_iso_base('mlb')}</code> (separate process).</p>"
            "<ul><li><a href='/mlb/'>Picks</a></li>"
            "<li><a href='/mlb/results'>Results</a></li></ul>"
            "<p>No login in sandbox.</p></body></html>",
            mimetype="text/html",
        )

    # ── Soccer: live-parity picks via isolation :5055; dropdown results in-hub ─
    @app.get("/soccer/")
    @app.get("/soccer")
    @app.get("/soccer/picks")
    @app.get("/soccer-picks")
    @app.get("/soccer-picks/<filter_date>")
    def soccer_picks(filter_date=None):
        from flask import request

        day = filter_date or request.args.get("date")
        path = "/soccer-picks" + (f"/{day}" if day else "")
        q = request.args.get("league")
        if q:
            path += ("&" if "?" in path else "?") + f"league={q}"
        html, meta = _proxy_iso("soccer", path)
        if not meta.get("ok"):
            # Fallback: light hub cards (not live chrome)
            from jinja2 import Environment, FileSystemLoader, select_autoescape

            root = _ISO_ROOTS["soccer"]
            env = Environment(
                loader=FileSystemLoader(str(root / "templates")),
                autoescape=select_autoescape(["html", "xml"]),
            )
            html = env.get_template("picks_hub.html").render(
                static_base="/soccer/lab-static",
                api_base="/soccer/api",
            )
            meta = {**meta, "source": "picks_hub_fallback"}
        status = int(meta.get("status") or 200)
        resp = Response(
            html,
            status=status if status >= 400 and not meta.get("ok") else 200,
            mimetype="text/html; charset=utf-8",
        )
        resp.headers["X-Sandbox-No-Login"] = "1"
        resp.headers["X-Hub-Source"] = str(meta.get("source") or "")
        return resp

    @app.get("/soccer/results")
    @app.get("/soccer-results")
    def soccer_results():
        # Keep league-dropdown + table UI (exception vs live chrome).
        html = _soccer_results_page()
        resp = Response(html, mimetype="text/html; charset=utf-8")
        resp.headers["X-Sandbox-No-Login"] = "1"
        return resp

    @app.get("/soccer/performance")
    def soccer_performance():
        return soccer_results()

    @app.get("/soccer/models")
    def soccer_models():
        return soccer_picks()

    @app.get("/soccer/lab")
    @app.get("/soccer/lab/")
    def soccer_lab():
        from flask import redirect

        return redirect("/soccer/results", code=302)

    @app.get("/soccer/lab-static/<path:filename>")
    def soccer_lab_static(filename: str):
        return send_from_directory(_ISO_ROOTS["soccer"] / "static", filename)

    @app.get("/soccer/api/leagues")
    def soccer_api_leagues():
        try:
            mod = _load_soccer_audit()
            return jsonify({"ok": True, "leagues": mod._leagues()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/soccer/api/picks")
    def soccer_api_picks():
        from flask import request

        try:
            mod = _load_soccer_audit()
            league = (request.args.get("league") or "ALL").strip()
            payload = mod.build_results_payload(league)
            for k in (
                "db",
                "db_path",
                "DB_PATH",
                "comparison",
                "league_orientations",
                "flip_leagues",
                "default_ml_flip",
                "publish_flip",
                "mode",
                "bundle_reason",
            ):
                payload.pop(k, None)
            tallies = payload.get("tallies")
            if isinstance(tallies, dict):
                tallies.pop("season_raw", None)
                tallies.pop("season_flipped", None)
            return jsonify(payload)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/soccer/api/audit")
    def soccer_api_audit():
        from flask import request

        try:
            mod = _load_soccer_audit()
            league = (request.args.get("league") or "ALL").strip()
            games = mod._load_completed(None if league in ("", "ALL") else league)
            raw = mod._grade_league(games, flip=False)
            return jsonify({
                "ok": True,
                "league": league or "ALL",
                "completed_games": len(games),
                "models": (raw or {}).get("models") or {},
            })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500


    # ── WNBA (proxy isolation :5056 — never import NHL77FINAL here) ────
    @app.get("/wnba/")
    @app.get("/wnba")
    @app.get("/wnba/picks")
    @app.get("/wnba-picks")
    @app.get("/wnba-picks/<filter_date>")
    def wnba_picks(filter_date=None):
        from flask import request

        day = filter_date or request.args.get("date")
        path = "/wnba-picks" + (f"/{day}" if day else "")
        html, meta = _proxy_iso("wnba", path)
        if not meta.get("ok"):
            try:
                html, meta = _wnba_lab_fallback()
            except Exception as e:
                meta = {**meta, "elo_fallback_error": str(e)}
        status = int(meta.get("status") or 200)
        resp = Response(html, status=status if status >= 400 and not meta.get("ok") else 200, mimetype="text/html; charset=utf-8")
        resp.headers["X-WNBA-Isolation"] = "hub-proxy-iso"
        resp.headers["X-Hub-Source"] = str(meta.get("source") or "")
        resp.headers["X-Sandbox-No-Login"] = "1"
        return resp

    @app.get("/wnba/results")
    @app.get("/wnba-results")
    def wnba_results():
        from flask import request

        # Escape hatch to prior UIs
        if request.args.get("parity") == "honest":
            html, meta = _wnba_honest_results()
            status = int(meta.get("status") or 200)
            resp = Response(html, status=status, mimetype="text/html; charset=utf-8")
            resp.headers["X-WNBA-Results-Mode"] = "honest"
            resp.headers["X-Sandbox-No-Login"] = "1"
            return resp
        if request.args.get("parity") == "live":
            html, meta = _proxy_iso("wnba", "/wnba-results?parity=live")
            status = int(meta.get("status") or 200)
            resp = Response(html, status=status if meta.get("ok") else 200, mimetype="text/html; charset=utf-8")
            resp.headers["X-WNBA-Results-Mode"] = "live_clone"
            resp.headers["X-Sandbox-No-Login"] = "1"
            return resp

        html = _team_tabbed_results_page("wnba", "WNBA")
        resp = Response(html, status=200, mimetype="text/html; charset=utf-8")
        resp.headers["X-WNBA-Isolation"] = "hub-tabbed-results"
        resp.headers["X-WNBA-Results-Mode"] = "tabbed-markets"
        resp.headers["X-Sandbox-No-Login"] = "1"
        return resp

    @app.get("/wnba/api/picks")
    def wnba_api_picks():
        try:
            return jsonify(_team_tabbed_payload("wnba"))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.get("/wnba/api/leagues")
    def wnba_api_leagues():
        return jsonify({"ok": True, "leagues": []})

    @app.get("/wnba/performance")
    def wnba_performance():
        return wnba_results()

    @app.get("/wnba/models")
    def wnba_models():
        return wnba_picks()

    @app.get("/wnba/lab")
    def wnba_lab():
        from flask import request
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        wnba_root = _ISO_ROOTS["wnba"]
        day = request.args.get("day")
        wr = str(wnba_root.resolve())
        inserted = wr not in sys.path
        if inserted:
            sys.path.insert(0, wr)
        try:
            picks_path = wnba_root / "engine" / "picks.py"
            spec = importlib.util.spec_from_file_location("_hub_wnba_picks", picks_path)
            picks = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(picks)
            data = picks.slate_with_models(day=day)
        finally:
            if inserted:
                try:
                    sys.path.remove(wr)
                except ValueError:
                    pass
        env = Environment(
            loader=FileSystemLoader(str(wnba_root / "templates")),
            autoescape=select_autoescape(["html", "xml"]),
        )
        html = env.get_template("picks.html").render(data=data, port=PORT)
        html = html.replace('href="/static/lab.css"', 'href="/wnba/lab/static/lab.css"')
        html = _strip_auth_chrome(html)
        html = _inject_banner(html, "wnba", which="picks")
        return Response(html, mimetype="text/html; charset=utf-8")

    @app.get("/wnba/lab/static/<path:filename>")
    def wnba_lab_static(filename: str):
        return send_from_directory(_ISO_ROOTS["wnba"] / "static", filename)

    # ── Total Edge (team sports only — Predicted − Book) ───────────────
    def _total_edge_fetch_html(sport: str) -> tuple[str, str]:
        """Return (html, source). Prefer sidecar so sandbox premium unlock applies."""
        from total_edge import TOTAL_EDGE_SPORTS

        cfg = TOTAL_EDGE_SPORTS.get(sport) or {}
        path = cfg.get("picks_path") or ""
        if not path:
            return "", "none"
        try:
            from live_proxy import ensure_sidecar, sidecar_base

            if ensure_sidecar():
                got = _http_get(f"{sidecar_base()}{path}", timeout=180.0)
                if got and got[1] < 500 and got[0] and len(got[0]) > 500:
                    return got[0], f"sidecar:{sidecar_base()}{path}"
        except Exception as e:
            print(f"[hub] total-edge sidecar miss ({sport}): {e}", flush=True)
        # Fallback: same proxy path as picks pages
        if sport in _ISO_APPS:
            html, meta = _proxy_iso(sport, path)
            if meta.get("ok") and html:
                return html, str(meta.get("source") or f"iso:{sport}")
            if sport == "mlb":
                html, meta = _mlb_via_sidecar(path)
                if meta.get("ok") and html:
                    return html, str(meta.get("source") or "mlb-sidecar")
        return "", "unavailable"

    def _render_total_edge(sport: str):
        from total_edge import (
            TOTAL_EDGE_SPORTS,
            build_total_edge_fragment,
            render_total_edge_into_chrome,
            parse_total_edge_rows,
            sort_rows,
        )

        if sport not in TOTAL_EDGE_SPORTS:
            return Response("Not found", status=404)
        label = TOTAL_EDGE_SPORTS[sport]["label"]
        empty_message = None
        note = None
        source = "none"
        rows: list = []
        chrome = ""

        chrome, source = _total_edge_fetch_html(sport)
        rows = sort_rows(parse_total_edge_rows(chrome)) if chrome else []
        if not chrome:
            empty_message = "Picks page unavailable — reload after the live sidecar is up."
        elif not rows:
            empty_message = "No game cards with totals found on the current picks slate."
        pred_na = sum(1 for r in rows if r.get("predicted") is None)
        book_na = sum(1 for r in rows if r.get("book") is None)
        if rows and (pred_na or book_na):
            note = f"{pred_na} predicted N/A · {book_na} book N/A."

        # Prefer live chrome shell (MLB card template parity) over hub sandbox chrome.
        if chrome and "<body" in chrome.lower():
            frag = build_total_edge_fragment(label, rows, empty_message=empty_message, note=note)
            page = render_total_edge_into_chrome(chrome, sport, frag)
            from sandbox_fixup import apply_sport_fixups, inject_sport_subnav

            page = apply_sport_fixups(page, sport, which="total-edge")
            page = inject_sport_subnav(page, sport, which="total-edge")
            page = _strip_auth_chrome(page)
            return Response(page, mimetype="text/html; charset=utf-8")

        return render_template(
            "sport_total_edge.html",
            sport=sport,
            slug=sport,
            title=label,
            rows=rows,
            lean_doc=__import__("total_edge", fromlist=["LEAN_DOC"]).LEAN_DOC,
            source=source,
            empty_message=empty_message,
            note=note,
        )

    @app.get("/mlb/total-edge")
    def mlb_total_edge():
        return _render_total_edge("mlb")

    @app.get("/soccer/total-edge")
    def soccer_total_edge():
        return _render_total_edge("soccer")

    @app.get("/wnba/total-edge")
    def wnba_total_edge():
        return _render_total_edge("wnba")

    # ── Fantasy (isolated — not betting picks) ───────────────────────
    _FANTASY_SPORTS = frozenset({"nfl", "mlb", "nba", "nhl"})
    _FANTASY_SECTIONS = frozenset(
        {"rankings", "start-sit", "waivers", "matchups", "draft", "sleepers"}
    )

    @app.get("/fantasy/")
    @app.get("/fantasy")
    def fantasy_home():
        from fantasy_page import render_dashboard

        html, meta = render_dashboard()
        resp = Response(html, status=200, mimetype="text/html; charset=utf-8")
        resp.headers["X-Sandbox-No-Login"] = "1"
        resp.headers["X-Fantasy-Players"] = str(meta.get("players") or 0)
        return resp

    def _fantasy_response(html: str, meta: dict, *, sport: str | None = None):
        status = 404 if meta.get("status") == 404 or meta.get("ok") is False else 200
        resp = Response(html, status=status, mimetype="text/html; charset=utf-8")
        resp.headers["X-Sandbox-No-Login"] = "1"
        if sport:
            resp.headers["X-Fantasy-Sport"] = sport
        return resp

    # Register player detail before generic <sport>/<section>
    @app.get("/fantasy/player/<player_id>")
    def fantasy_player(player_id: str):
        from flask import request
        from fantasy_page import render_player

        html, meta = render_player(player_id, sport=request.args.get("sport"))
        return _fantasy_response(html, meta)

    @app.get("/fantasy/<sport>/<section>")
    def fantasy_sport_section(sport: str, section: str):
        from flask import request
        from fantasy_page import render_section

        sport_l = sport.lower()
        section_l = section.lower()
        if sport_l not in _FANTASY_SPORTS or section_l not in _FANTASY_SECTIONS:
            return Response("Not found", status=404)
        html, meta = render_section(
            sport_l,
            section_l,
            a=request.args.get("a"),
            b=request.args.get("b"),
        )
        return _fantasy_response(html, meta, sport=sport_l)

    @app.get("/fantasy/<sport>")
    def fantasy_sport(sport: str):
        from flask import request
        from fantasy_page import render_sport

        sport_l = sport.lower()
        if sport_l not in _FANTASY_SPORTS:
            return Response("Not found", status=404)
        html, meta = render_sport(
            sport_l,
            tool=request.args.get("tool"),
            a=request.args.get("a"),
            b=request.args.get("b"),
        )
        return _fantasy_response(html, meta, sport=sport_l)

    return app


def main() -> None:
    app = create_app()
    print(f"Sports Sandbox Hub → http://127.0.0.1:{PORT}")
    print("MLB/WNBA/Soccer-picks → isolation :5057 / :5056 / :5055")
    print("CFL → Personal/cfl isolation engine (pick cards)")
    print("Golf → ranked win-% board")
    print("Fantasy → Personal/fantasy (NFL/MLB/NBA/NHL MVP)")
    print("UFC → Personal/ufc isolation (Elo+odds cards)")
    print("Tennis → live sidecar")
    print("Sandbox · no login")
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
