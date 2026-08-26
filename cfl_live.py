"""Live CFL pages — isolation engine at ~/Documents/Personal/cfl/, site chrome.

CFL only. Do not import other isolation sports from here.
Keep the MLB predictions/results template (no CFL-only tally chrome).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
ISO_HUB = ROOT / "iso_hub"
if str(ISO_HUB) not in sys.path:
    sys.path.insert(0, str(ISO_HUB))


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
        try:
            from flask import request
            host = (request.host or "").split(":")[0].lower()
            if host in ("127.0.0.1", "localhost"):
                ctx["is_premium"] = True
        except Exception:
            pass
        if hasattr(m, "SOCCER_ENABLED"):
            ctx["soccer_enabled"] = bool(m.SOCCER_ENABLED)
    return ctx


def _rewrite_iso_hrefs(html: str) -> str:
    if not html:
        return html
    html = re.sub(r"/cfl/results(?!-share)", "/cfl-results", html)
    html = html.replace("/cfl/predictions", "/cfl-picks")
    html = html.replace('href="/cfl/"', 'href="/cfl-picks"')
    html = html.replace("href='/cfl/'", "href='/cfl-picks'")
    html = html.replace('href="/cfl"', 'href="/cfl-picks"')
    html = html.replace("/static/img/cfl/montreal.png", "/static/img/cfl/montreal.svg")
    return html


def _strip_vendor_labels(html: str) -> str:
    if not html:
        return html
    html = re.sub(r"\bTheOddsAPI\b", "", html, flags=re.I)
    html = re.sub(r"\bThe Odds API\b", "", html, flags=re.I)
    html = re.sub(r"Prob source:\s*[^<]+", "", html, flags=re.I)
    html = re.sub(r"Elo \+ market blend", "Model blend", html, flags=re.I)
    html = re.sub(r"\bElo trained on\b[^.<]*", "", html, flags=re.I)
    html = re.sub(r"\bisolation\b", "", html, flags=re.I)
    html = html.replace('data-sandbox-sport="cfl"', 'data-sport="cfl"')
    html = html.replace("data-sandbox-sport='cfl'", "data-sport='cfl'")
    html = html.replace('id="sandbox-unlock-details"', 'id="pl-unlock-details"')
    return html


def _inject_chrome_into_page(html: str, *, extra_css: list[str] | None = None) -> str:
    from flask import render_template

    chrome = render_template("includes/picks_nav_chrome.html", **_nav_ctx())
    css_tags = [
        '<link rel="stylesheet" href="/static/css/research-theme.css">',
        '<link rel="stylesheet" href="/static/css/picks-nav-overrides.css">',
        '<link rel="stylesheet" href="/static/css/sports-chrome.css">',
    ]
    for href in extra_css or []:
        tag = f'<link rel="stylesheet" href="{href}">'
        if tag not in css_tags:
            css_tags.append(tag)
    css_html = "\n".join(css_tags) + '<script src="/static/js/pl-header-logo.js" defer></script>'
    if re.search(r"</head\s*>", html, flags=re.I):
        html = re.sub(r"</head\s*>", css_html + "</head>", html, count=1, flags=re.I)
    else:
        html = css_html + html

    def _body_repl(m: re.Match[str]) -> str:
        tag = m.group(0)
        if "research-site" not in tag:
            tag = tag[:-1] + ' class="research-site">'
        if "data-sport=" not in tag and "data-sandbox-sport=" not in tag:
            tag = tag[:-1] + ' data-sport="cfl">'
        return tag + chrome

    if re.search(r"<body\b", html, flags=re.I):
        html = re.sub(r"<body\b[^>]*>", _body_repl, html, count=1, flags=re.I)
    else:
        html = chrome + html
    return html


def _cfl_view_toggle(active: str = "normal") -> str:
    n_cls = "active" if active == "normal" else ""
    c_cls = "active" if active == "chart" else ""
    return (
        '<div class="pl-view-toggle" role="navigation" aria-label="Results view">'
        f'<a class="pl-view-btn {n_cls}" href="/cfl-results">Cards</a>'
        f'<a class="pl-view-btn {c_cls}" href="/cfl-results?view=chart">Chart</a>'
        "</div>"
        "<style>.pl-view-toggle{display:flex;gap:8px;margin:12px 0 18px;flex-wrap:wrap}"
        ".pl-view-btn{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.pl-view-btn.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def _gate_cfl_paid_markets(html: str) -> str:
    if not html:
        return html
    locked = (
        '<div class="odds-pricing-locked" style="padding:14px;font-size:0.84em;text-align:center;">'
        "🔒 Lines &amp; projections locked. "
        '<a href="/login">Log in</a> or <a href="/plans">unlock premium</a>.</div>'
    )
    html = re.sub(
        r'<div class="line-chip"><div class="line-chip-label">Model spread</div>'
        r'<div class="line-chip-val">[\s\S]*?</div></div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="line-chip"><div class="line-chip-label">Model total</div>'
        r'<div class="line-chip-val">[\s\S]*?</div></div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="odds-pricing-section">[\s\S]*?<div class="odds-extras-footer">',
        locked + '<div class="odds-extras-footer">',
        html,
        flags=re.I,
    )

    def _strip_paid_attrs(m: re.Match[str]) -> str:
        tag = m.group(0)
        for attr in (
            "data-pl-spread",
            "data-xs-spread",
            "data-pl-proj",
            "data-xs-proj",
            "data-pl-total",
            "data-xs-total",
        ):
            tag = re.sub(rf'\s{attr}="[^"]*"', "", tag, flags=re.I)
        return tag

    html = re.sub(
        r"<div\b[^>]*\bdata-pick-card\b[^>]*>",
        _strip_paid_attrs,
        html,
        flags=re.I,
    )
    return html


def _strip_mlb_content_from_cfl(html: str) -> str:
    """Keep the MLB shell and CFL write-up. Drop leftover MLB preview chrome."""
    if not html:
        return html
    html = re.sub(
        r'<nav class="[^"]*preview-hub[^"]*"[^>]*aria-label="MLB previews"[^>]*>([\s\S]*?)</nav>',
        lambda m: (
            m.group(1)
            if re.search(r"How These AI Picks|What to Expect", m.group(1), re.I)
            else ""
        ),
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<nav class="[^"]*preview-hub[^"]*"[^>]*>[\s\S]*?</nav>',
        lambda m: "" if re.search(r"Today(?:'s|&#x27;s) MLB|aria-label=\"MLB", m.group(0), re.I) else m.group(0),
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<h2[^>]*>\s*Today(?:\'s|&#x27;s) MLB previews\s*</h2>[\s\S]*?(?=<h2\b|<nav\b|<footer\b|$)',
        "",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r'<li><a href="[^"]*">20\d{2}-\d{2}-\d{2}</a></li>\s*',
        "",
        html,
    )
    return html


def _ensure_mlb_copy_all_markets(html: str) -> str:
    """Copy All pastes Moneyline + Spread + Totals, same as the sandbox CFL page."""
    if not html or "function copyVisiblePicks" not in html:
        return html
    if "pl-copy-all-markets" in html:
        return html
    script = """
<script id="pl-copy-all-markets">
(function(){
  function _dash(v){
    v = (v == null ? "" : String(v)).trim();
    return v || "—";
  }
  function _plain(html){
    return String(html || "").replace(/<[^>]+>/g, " ").replace(/\\s+/g, " ").trim();
  }
  function copyVisiblePicks(btn){
    var sec = (typeof _visibleSection === "function")
      ? _visibleSection()
      : document.querySelector(".date-section.visible");
    if(!sec) return;
    var stacks = sec.querySelectorAll("[data-pick-card]");
    var dateLabel = (sec.id || "").replace("date-","");
    var icon = (typeof sportIcon !== "undefined" ? sportIcon : "");
    var name = (typeof sportName !== "undefined" ? sportName : "CFL");
    var spreadFn = (typeof _spreadCell === "function")
      ? _spreadCell
      : function(st, attr){ return st.getAttribute(attr) || ""; };
    var plProjFn = (typeof _plProjDisplay === "function")
      ? _plProjDisplay
      : function(st){ return st.getAttribute("data-pl-proj") || ""; };
    var xsProjFn = (typeof _xsProjDisplay === "function")
      ? _xsProjDisplay
      : function(st){ return st.getAttribute("data-xs-proj") || ""; };
    var lines = [icon + " " + name + " AI Picks — " + dateLabel];

    lines.push("");
    lines.push("MONEYLINE");
    stacks.forEach(function(st){
      var away = st.getAttribute("data-away") || "";
      var home = st.getAttribute("data-home") || "";
      var time = st.getAttribute("data-time") || "";
      var pick = st.getAttribute("data-pick") || "";
      var conf = st.getAttribute("data-conf") || "";
      var result = st.getAttribute("data-result") || "";
      var line = away + " @ " + home;
      if(time) line += " (" + time + ")";
      line += " — Pick: " + pick;
      if(conf) line += " (" + conf + "%)";
      if(result === "WON") line += " ✅";
      else if(result === "LOST") line += " ❌";
      lines.push(line);
    });

    lines.push("");
    lines.push("SPREAD / RUN LINE");
    stacks.forEach(function(st){
      var away = st.getAttribute("data-away") || "";
      var home = st.getAttribute("data-home") || "";
      var books = _dash(spreadFn(st, "data-books-spread", "val-books", ["books run line","books spread"]));
      var pl = _dash(spreadFn(st, "data-pl-spread", "val-pl", ["pl run line","pl spread","model spread","prediction lab run line","prediction lab spread"]));
      var xs = _dash(spreadFn(st, "data-xs-spread", "val-xs", ["xsharp run line","xsharp spread"]));
      lines.push(away + " @ " + home + " — Books: " + books + " | PL: " + pl + " | XSharp: " + xs);
    });

    lines.push("");
    lines.push("TOTALS");
    stacks.forEach(function(st){
      var away = st.getAttribute("data-away") || "";
      var home = st.getAttribute("data-home") || "";
      var tot = (st.getAttribute("data-books-total") || "").trim() || "—";
      if(tot !== "—" && tot.toLowerCase().indexOf("o/u") < 0) tot = "O/U " + tot;
      var pl = _dash(_plain(plProjFn(st)));
      var xs = _dash(_plain(xsProjFn(st)));
      lines.push(away + " @ " + home + " — Books: " + tot + " | PL: " + pl + " | XSharp: " + xs);
    });

    lines.push("");
    lines.push("via predictionlab.io");
    var text = lines.join("\\n");
    var done = function(){
      if(!btn) return;
      var o = btn.textContent;
      btn.textContent = "✓ Copied";
      btn.classList.add("copied");
      setTimeout(function(){ btn.textContent = o; btn.classList.remove("copied"); }, 1500);
    };
    if(navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(text).then(done).catch(function(){
        if(typeof _fallbackCopy === "function") _fallbackCopy(text, done);
      });
    } else if(typeof _fallbackCopy === "function"){
      _fallbackCopy(text, done);
    }
  }
  window.copyVisiblePicks = copyVisiblePicks;
})();
</script>
"""
    if "</body>" in html:
        return html.replace("</body>", script + "\n</body>", 1)
    return html + script


def render_cfl_picks() -> str:
    from mlb_team_shell import render_team_sport
    from sandbox_fixup import unlock_premium_card_details

    nav = _nav_ctx()
    premium = bool(nav.get("is_premium"))
    html, meta = render_team_sport("cfl", which="picks")
    if not meta.get("ok") or not html:
        raise RuntimeError(f"cfl mlb shell failed: {meta}")
    html = _strip_mlb_content_from_cfl(html)
    html = unlock_premium_card_details(html)
    html = _ensure_mlb_copy_all_markets(html)
    html = _strip_mlb_content_from_cfl(html)
    if not premium:
        html = _gate_cfl_paid_markets(html)
    return _strip_vendor_labels(_rewrite_iso_hrefs(html))


def render_cfl_results(*, view: str = "normal") -> str:
    view = (view or "normal").strip().lower()
    if view in ("chart", "tabs", "markets", "tabbed"):
        return _render_cfl_results_chart()

    from mlb_team_shell import render_team_sport
    from sandbox_fixup import apply_sport_fixups

    html, meta = render_team_sport("cfl", which="results")
    if not meta.get("ok") or not html:
        raise RuntimeError(f"cfl mlb shell results failed: {meta}")
    html = _strip_mlb_content_from_cfl(html)
    html = apply_sport_fixups(html, "cfl", which="results")
    try:
        from flask import request
        from team_tabbed_results import (
            apply_cfl_cards_market_tabs,
            build_cfl_payload,
            inject_consensus_records_html,
        )

        payload = build_cfl_payload()
        try:
            html = inject_consensus_records_html(
                html,
                sport="cfl",
                finals=(payload or {}).get("finals"),
                last_night_key=((payload or {}).get("tallies") or {})
                .get("last_night", {})
                .get("date"),
            )
        except TypeError:
            html = inject_consensus_records_html(html, sport="cfl")
        html = apply_cfl_cards_market_tabs(
            html,
            payload,
            market=(request.args.get("market") or "moneyline"),
        )
    except Exception:
        pass
    close = (html or "").lower().find("</html>")
    if close >= 0:
        html = html[: close + len("</html>")]
    return _strip_vendor_labels(_rewrite_iso_hrefs(_strip_mlb_content_from_cfl(html)))


def _render_cfl_results_chart() -> str:
    from mlb_team_shell import render_team_sport

    html, meta = render_team_sport("cfl", which="chart")
    if meta.get("ok") and html:
        html = _strip_mlb_content_from_cfl(html)
        html = re.sub(
            r'<header class="pl2-header">[\s\S]*?</header>',
            "",
            html,
            count=1,
            flags=re.I,
        )
        html = html.replace('href="/mlb-results"', 'href="/cfl-results"')
        html = html.replace("Spread / Run Line", "Spread")
        html = _inject_chrome_into_page(
            html,
            extra_css=["/static/css/team-results.css", "/static/css/cfl-pick-cards.css"],
        )
        return _strip_vendor_labels(_rewrite_iso_hrefs(html))

    from jinja2 import Environment, FileSystemLoader, select_autoescape

    from mlb_results_ui import inject_ssr_chart_bootstrap
    from team_tabbed_results import build_cfl_payload

    env = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("team_results.html").render(
        sport="cfl",
        sport_label="CFL",
        api_base="/cfl/api",
        show_league=False,
        picks_href="/cfl-picks",
        results_href="/cfl-results",
        **_nav_ctx(),
    )
    if 'class="pl-view-toggle"' not in html:
        if re.search(r"<main\b", html, flags=re.I):
            html = re.sub(
                r"(<main\b[^>]*>)",
                r"\1" + _cfl_view_toggle("chart"),
                html,
                count=1,
                flags=re.I,
            )
        else:
            html = _cfl_view_toggle("chart") + html
    if 'id="league-controls" hidden' not in html:
        html = html.replace('id="league-controls"', 'id="league-controls" hidden')
    html = re.sub(
        r"(?is)<label[^>]*>\s*League\s*</label>\s*<select[\s\S]*?</select>",
        "",
        html,
    )
    html = re.sub(
        r'<header class="pl2-header">[\s\S]*?</header>',
        "",
        html,
        count=1,
        flags=re.I,
    )
    try:
        payload = build_cfl_payload()
        if isinstance(payload, dict):
            html = inject_ssr_chart_bootstrap(html, payload, "cfl")
    except Exception:
        pass
    html = _inject_chrome_into_page(
        html,
        extra_css=["/static/css/team-results.css", "/static/css/cfl-pick-cards.css"],
    )
    return _strip_vendor_labels(_rewrite_iso_hrefs(html))


def cfl_share_jpeg_bytes() -> bytes | None:
    from mlb_team_shell import build_cfl_share_jpeg

    return build_cfl_share_jpeg()


def cfl_results_share_jpeg_bytes() -> bytes | None:
    from mlb_team_shell import build_cfl_results_share_jpeg

    return build_cfl_results_share_jpeg()


def cfl_chart_payload() -> dict[str, Any]:
    from cfl_page import _pipe_mod
    from team_tabbed_results import build_cfl_payload

    try:
        _pipe_mod().ensure_predictions(refresh=False)
    except Exception:
        pass
    return build_cfl_payload()
