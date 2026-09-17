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
    # Do NOT rewrite /mlb-picks|/mlb-results here — that breaks the global
    # Sports/Results nav (MLB → CFL). Section-tab MLB leftovers are scoped
    # in _strip_mlb_content_from_cfl.
    html = html.replace("/static/img/cfl/montreal.png", "/static/img/cfl/montreal.svg")
    return html


def _dedupe_cfl_results_chrome(html: str) -> str:
    """One Predictions|Results row and one Cards|Chart row under the page title."""
    if not html:
        return html
    # Keep the first section-tabs; drop later duplicates.
    seen_tabs = False

    def _tabs(m: re.Match[str]) -> str:
        nonlocal seen_tabs
        if seen_tabs:
            return ""
        seen_tabs = True
        return m.group(0)

    html = re.sub(
        r'<div class="section-tabs">[\s\S]*?</div>\s*',
        _tabs,
        html,
        flags=re.I,
    )
    # Keep the first pl-view-toggle (+ optional style); drop later duplicates.
    seen_toggle = False

    def _toggle(m: re.Match[str]) -> str:
        nonlocal seen_toggle
        if seen_toggle:
            return ""
        seen_toggle = True
        return m.group(0)

    html = re.sub(
        r'<div class="pl-view-toggle\b[^>]*>[\s\S]*?</div>\s*'
        r'(?:<style>\.pl-view-toggle[\s\S]*?</style>\s*)?',
        _toggle,
        html,
        flags=re.I,
    )
    # Extra page-title H1 under the SEO title (MLB shell leftover).
    titles = list(
        re.finditer(
            r'<h1\b[^>]*class="[^"]*\bpage-title\b[^"]*"[^>]*>[\s\S]*?</h1>\s*',
            html,
            flags=re.I,
        )
    )
    if len(titles) > 1:
        for m in reversed(titles[1:]):
            html = html[: m.start()] + html[m.end() :]
    return html


def _finalize_cfl_html(html: str) -> str:
    """Last-pass CFL HTML: strip MLB leftovers, restore global MLB nav, vendor labels."""
    html = _strip_mlb_content_from_cfl(html)
    html = _rewrite_iso_hrefs(html)
    html = _restore_global_nav_mlb_links(html)
    return _strip_vendor_labels(html)


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


_PL2_HEADER_RE = re.compile(
    r'<header\b[^>]*\bpl2-header\b[^>]*>[\s\S]*?</header>\s*',
    flags=re.I,
)

# research_header.html ships header + ACCOUNT/NAV dropdown <script>. Stripping
# only <header> leaves those scripts; reinjecting chrome doubles them and the
# duplicate document-click handlers cancel the Sports/Models/Results menus.
_RESEARCH_NAV_SCRIPT_RE = re.compile(
    r"<script\b[^>]*>\s*/\*\s*ACCOUNT MENU:[\s\S]*?NAV DROPDOWNS:[\s\S]*?</script>\s*",
    flags=re.I,
)


def _strip_all_pl2_headers(html: str) -> str:
    """Remove every site chrome header (attribute order / extra classes safe)."""
    if not html:
        return html
    return _PL2_HEADER_RE.sub("", html)


def _strip_research_nav_scripts(html: str) -> str:
    """Drop orphaned research_header dropdown scripts (safe before re-inject)."""
    if not html:
        return html
    return _RESEARCH_NAV_SCRIPT_RE.sub("", html)


def _dedupe_pl2_headers(html: str) -> str:
    """Keep the first pl2-header only — chart paths often inject twice."""
    if not html:
        return html
    matches = list(_PL2_HEADER_RE.finditer(html))
    if len(matches) <= 1:
        return html
    # Drop later duplicates (reverse so offsets stay valid).
    for m in reversed(matches[1:]):
        html = html[: m.start()] + html[m.end() :]
    return html


def _dedupe_research_nav_scripts(html: str) -> str:
    """Keep a single ACCOUNT/NAV dropdown script block."""
    if not html:
        return html
    matches = list(_RESEARCH_NAV_SCRIPT_RE.finditer(html))
    if len(matches) <= 1:
        return html
    for m in reversed(matches[1:]):
        html = html[: m.start()] + html[m.end() :]
    return html


def _inject_chrome_into_page(html: str, *, extra_css: list[str] | None = None) -> str:
    from flask import render_template

    # Strip header + its leftover dropdown scripts so we inject one working set.
    html = _strip_all_pl2_headers(html)
    html = _strip_research_nav_scripts(html)

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
    # Avoid stacking duplicate chrome CSS when the shell already linked them.
    for tag in list(css_tags):
        href_m = re.search(r'href="([^"]+)"', tag)
        if href_m and href_m.group(1) in html:
            css_tags.remove(tag)
    css_html = "\n".join(css_tags)
    if css_html:
        css_html += '<script src="/static/js/pl-header-logo.js" defer></script>'
    elif 'pl-header-logo.js' not in html:
        css_html = '<script src="/static/js/pl-header-logo.js" defer></script>'
    if css_html:
        if re.search(r"</head\s*>", html, flags=re.I):
            html = re.sub(r"</head\s*>", css_html + "</head>", html, count=1, flags=re.I)
        else:
            html = css_html + html

    def _body_repl(m: re.Match[str]) -> str:
        tag = m.group(0)
        if "research-site" not in tag:
            if re.search(r'\bclass="', tag, flags=re.I):
                tag = re.sub(
                    r'\bclass="([^"]*)"',
                    r'class="\1 research-site"',
                    tag,
                    count=1,
                    flags=re.I,
                )
            else:
                tag = tag[:-1] + ' class="research-site">'
        if "data-sport=" not in tag and "data-sandbox-sport=" not in tag:
            tag = tag[:-1] + ' data-sport="cfl">'
        return tag + chrome

    if re.search(r"<body\b", html, flags=re.I):
        html = re.sub(r"<body\b[^>]*>", _body_repl, html, count=1, flags=re.I)
    else:
        html = chrome + html
    html = _dedupe_pl2_headers(html)
    return _dedupe_research_nav_scripts(html)


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
    # Stray empty-state from the MLB shell when CFL slate is present.
    html = re.sub(
        r'<div class="no-data">\s*No predictions available for MLB\s*</div>\s*',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"No predictions available for MLB",
        "",
        html,
        flags=re.I,
    )
    # Cards|Chart / Predictions|Results tabs only — never the global Sports nav.
    def _tabs_mlb_to_cfl(m: re.Match[str]) -> str:
        block = m.group(0)
        block = block.replace('href="/mlb-results"', 'href="/cfl-results"')
        block = block.replace("href='/mlb-results'", "href='/cfl-results'")
        block = block.replace('href="/mlb-results?', 'href="/cfl-results?')
        block = block.replace('href="/mlb-picks"', 'href="/cfl-picks"')
        block = block.replace("href='/mlb-picks'", "href='/cfl-picks'")
        return block

    html = re.sub(
        r'<div class="section-tabs\b[\s\S]*?</div>',
        _tabs_mlb_to_cfl,
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="pl-view-toggle\b[\s\S]*?</div>',
        _tabs_mlb_to_cfl,
        html,
        flags=re.I,
    )
    html = html.replace("{l:'MLB',h:'/cfl-results'}", "{l:'MLB',h:'/mlb-results'}")
    html = html.replace('{l:"MLB",h:"/cfl-results"}', '{l:"MLB",h:"/mlb-results"}')
    html = html.replace(
        'content="https://predictionlab.io/mlb-results"',
        'content="https://predictionlab.io/cfl-results"',
    )
    html = html.replace(
        'href="https://predictionlab.io/mlb-results"',
        'href="https://predictionlab.io/cfl-results"',
    )
    html = html.replace("localhost/mlb-results", "localhost/cfl-results")
    html = html.replace("%2Fmlb-results", "%2Fcfl-results")
    # Meta / social leftovers from the MLB template.
    html = re.sub(
        r'(property="og:title" content=")MLB([^"]*)(")',
        r"\1CFL\2\3",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(name="twitter:title" content=")MLB([^"]*)(")',
        r"\1CFL\2\3",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(content=")Daily AI-powered MLB',
        r"\1Daily AI-powered CFL",
        html,
        flags=re.I,
    )
    html = re.sub(
        r">MLB Predictions Today<",
        ">CFL Predictions Today<",
        html,
        flags=re.I,
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


def _open_cfl_cards(html: str) -> str:
    """Match the expanded team-sport template so models are visible."""
    if not html:
        return html
    html = re.sub(
        r'class="game-card pick-card(?! is-expanded)',
        'class="game-card pick-card is-expanded',
        html,
    )
    html = re.sub(
        r'(<div class="card-details"[^>]*?)\s+hidden\b',
        r"\1",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(class="view-details-btn"[^>]*aria-expanded=")false(")',
        r"\1true\2",
        html,
    )
    html = re.sub(
        r'(<button type="button" class="view-details-btn"[^>]*>)\s*View [Dd]etails',
        r"\1Less details",
        html,
    )
    return html


def _strip_cfl_empty_books(html: str) -> str:
    """Drop book spread / book odds chips when we have no number."""
    if not html:
        return html
    html = re.sub(
        r'<div class="line-chip[^"]*">\s*'
        r'<div class="line-chip-label">\s*Books[^<]*</div>\s*'
        r'<div class="line-chip-val">\s*(?:—|&mdash;|&ndash;|N/A|–|-)?\s*</div>\s*'
        r"</div>",
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="ml-line[^"]*">\s*'
        r'<span class="ml-src books">\s*Books\s*</span>\s*'
        r'<span class="ml-num[^"]*">\s*(?:—|&mdash;|&ndash;|N/A)\s*</span>\s*'
        r"</div>",
        "",
        html,
        flags=re.I,
    )
    # Omit chips whose data-* books attrs are blank.
    html = re.sub(
        r'<div class="line-chip[^"]*"[^>]*>\s*'
        r'<div class="line-chip-label">\s*Books Spread\s*</div>\s*'
        r'<div class="line-chip-val">[^<]*</div>\s*</div>'
        r'(?=[\s\S]{0,800}?data-books-spread="\s*")',
        "",
        html,
        flags=re.I,
    )
    try:
        from team_results_charts import _hide_empty_books_spread_total

        html = _hide_empty_books_spread_total(html)
    except Exception:
        pass
    html = re.sub(r'\sdata-books-spread="\s*"', "", html, flags=re.I)
    html = re.sub(r'\sdata-books-total="\s*"', "", html, flags=re.I)
    return html


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
    html = _open_cfl_cards(html)
    try:
        from team_results_charts import apply_team_picks_h2h

        html = apply_team_picks_h2h(html, "CFL")
    except Exception:
        pass
    html = _strip_cfl_empty_books(html)
    if not premium:
        html = _gate_cfl_paid_markets(html)
    html = re.sub(r"const sportName\s*=\s*[^;]+;", 'const sportName = "CFL";', html)
    html = re.sub(r"const sportIcon\s*=\s*[^;]+;", 'const sportIcon = "🏈";', html)
    return _finalize_cfl_html(html)


_CFL_RESULTS_PAGE_CACHE: dict = {}
_CFL_RESULTS_PAGE_TTL = 180


def _cfl_section_tabs(*, results_active: bool = True) -> str:
    picks_cls = "" if results_active else " active"
    results_cls = " active" if results_active else ""
    return (
        '<div class="section-tabs">'
        f'<a href="/cfl-picks" class="tab{picks_cls}">📊 Predictions</a>'
        f'<a href="/cfl-results" class="tab{results_cls}">🎯 Results</a>'
        "</div>"
    )


def _restore_global_nav_mlb_links(html: str) -> str:
    """Shell rewrites every /mlb-* href to CFL — put Sports-nav MLB back."""
    if not html:
        return html
    html = re.sub(
        r'(<a\b[^>]*\bhref=")/cfl-picks("[^>]*>)\s*MLB\s*(</a>)',
        r"\1/mlb-picks\2MLB\3",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(<a\b[^>]*\bhref=")/cfl-results("[^>]*>)\s*MLB\s*(</a>)',
        r"\1/mlb-results\2MLB\3",
        html,
        flags=re.I,
    )
    return html


def _ensure_cfl_results_section_tabs(html: str) -> str:
    """Chart shells often omit Predictions|Results — put them under the title."""
    if not html:
        return html
    if re.search(r'<div class="section-tabs\b', html, flags=re.I):
        return html
    tabs = _cfl_section_tabs(results_active=True)
    h1 = re.search(r'(<h1\b[^>]*>[\s\S]*?</h1>\s*)', html, flags=re.I)
    if h1:
        return html[: h1.end()] + tabs + html[h1.end() :]
    toggle = re.search(
        r'<div class="pl-view-toggle\b[^>]*>[\s\S]*?</div>\s*'
        r'(?:<style>\.pl-view-toggle[\s\S]*?</style>\s*)?',
        html,
        flags=re.I,
    )
    if toggle:
        return html[: toggle.start()] + tabs + html[toggle.start() :]
    return html


def _reorder_cfl_results_headers(html: str) -> str:
    """Title → Predictions|Results → Cards|Chart (single of each)."""
    if not html:
        return html
    html = _ensure_cfl_results_section_tabs(html)

    # If section-tabs landed above the page title, move them under h1.
    h1 = re.search(r'(<h1\b[^>]*>[\s\S]*?</h1>\s*)', html, flags=re.I)
    tabs_m = re.search(
        r'(<div class="section-tabs\b[\s\S]*?</div>\s*(?:<style>[\s\S]*?</style>\s*)?)',
        html,
        flags=re.I,
    )
    if h1 and tabs_m and tabs_m.start() < h1.start():
        tabs = tabs_m.group(1)
        html = html[: tabs_m.start()] + html[tabs_m.end() :]
        h1 = re.search(r'(<h1\b[^>]*>[\s\S]*?</h1>\s*)', html, flags=re.I)
        if h1:
            html = html[: h1.end()] + tabs + html[h1.end() :]

    m = re.search(
        r'(<div class="pl-view-toggle\b[^>]*>[\s\S]*?</div>\s*'
        r'(?:<style>\.pl-view-toggle[\s\S]*?</style>\s*)?)',
        html,
        flags=re.I,
    )
    if not m:
        return html
    toggle = m.group(1)
    html_wo = html[: m.start()] + html[m.end() :]
    tabs = re.search(
        r'(<div class="section-tabs\b[\s\S]*?</div>\s*(?:<style>[\s\S]*?</style>\s*)?)',
        html_wo,
        flags=re.I,
    )
    if tabs:
        return html_wo[: tabs.end()] + toggle + html_wo[tabs.end() :]
    h1 = re.search(r'(<h1\b[^>]*>[\s\S]*?</h1>\s*)', html_wo, flags=re.I)
    if h1:
        return html_wo[: h1.end()] + toggle + html_wo[h1.end() :]
    return toggle + html_wo


def render_cfl_results(*, view: str = "normal") -> str:
    view = (view or "normal").strip().lower()
    if view in ("chart", "tabs", "markets", "tabbed"):
        return _render_cfl_results_chart()
    now = __import__("time").time()
    hit = _CFL_RESULTS_PAGE_CACHE.get("cards")
    if isinstance(hit, dict) and hit.get("html") and (now - hit.get("ts", 0)) < _CFL_RESULTS_PAGE_TTL:
        return hit["html"]

    from mlb_team_shell import render_team_sport
    from sandbox_fixup import apply_sport_fixups

    html, meta = render_team_sport("cfl", which="results")
    if not meta.get("ok") or not html:
        raise RuntimeError(f"cfl mlb shell results failed: {meta}")
    html = _strip_mlb_content_from_cfl(html)
    html = apply_sport_fixups(html, "cfl", which="results")
    html = re.sub(
        r'<style id="sandbox-hide-books">[\s\S]*?</style>',
        "",
        html,
        flags=re.I,
    )
    try:
        from team_results_charts import _inject_cfl_consensus_hist_chips

        html = _inject_cfl_consensus_hist_chips(html)
    except Exception:
        pass
    html = _reorder_cfl_results_headers(html)
    html = _dedupe_cfl_results_chrome(html)
    # Consensus/tabs already applied inside render_team_sport. A second
    # build_cfl_payload() hangs the worker and shadows local ufc_live.
    close = (html or "").lower().find("</html>")
    if close >= 0:
        html = html[: close + len("</html>")]
    html = _finalize_cfl_html(html)
    _CFL_RESULTS_PAGE_CACHE["cards"] = {"ts": now, "html": html}
    return html


def _render_cfl_results_chart() -> str:
    from mlb_team_shell import render_team_sport

    # Chart inject needs cards HTML as source — warm the cache if empty.
    hit = _CFL_RESULTS_PAGE_CACHE.get("cards")
    if not (isinstance(hit, dict) and hit.get("html")):
        try:
            render_cfl_results(view="normal")
        except Exception:
            pass

    try:
        from team_results_charts import set_results_chart_source

        hit = _CFL_RESULTS_PAGE_CACHE.get("cards")
        if isinstance(hit, dict) and hit.get("html"):
            set_results_chart_source("CFL", hit["html"])
    except Exception:
        pass

    html, meta = render_team_sport("cfl", which="chart")
    if meta.get("ok") and html:
        html = _strip_mlb_content_from_cfl(html)
        html = _strip_all_pl2_headers(html)
        html = _strip_research_nav_scripts(html)
        html = html.replace("Spread / Run Line", "Spread")
        try:
            from team_results_charts import (
                _hide_empty_books_spread_total,
                apply_team_results_template,
                set_results_chart_source,
            )

            hit = _CFL_RESULTS_PAGE_CACHE.get("cards")
            if isinstance(hit, dict) and hit.get("html"):
                set_results_chart_source("CFL", hit["html"])
            html = apply_team_results_template(html, "CFL", view="chart")
            html = _hide_empty_books_spread_total(html)
        except Exception:
            pass
        # Template may have re-inserted site chrome — strip before our inject.
        html = _strip_all_pl2_headers(html)
        html = _strip_research_nav_scripts(html)
        html = _inject_chrome_into_page(
            html,
            extra_css=["/static/css/team-results.css", "/static/css/cfl-pick-cards.css"],
        )
        html = _reorder_cfl_results_headers(html)
        html = _dedupe_cfl_results_chrome(html)
        html = _dedupe_pl2_headers(html)
        return _finalize_cfl_html(html)

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
    html = _strip_all_pl2_headers(html)
    try:
        payload = build_cfl_payload()
        if isinstance(payload, dict):
            html = inject_ssr_chart_bootstrap(html, payload, "cfl")
    except Exception:
        pass
    html = _strip_all_pl2_headers(html)
    html = _strip_research_nav_scripts(html)
    html = _inject_chrome_into_page(
        html,
        extra_css=["/static/css/team-results.css", "/static/css/cfl-pick-cards.css"],
    )
    html = _reorder_cfl_results_headers(html)
    html = _dedupe_cfl_results_chrome(html)
    html = _dedupe_pl2_headers(html)
    return _finalize_cfl_html(html)


def cfl_share_jpeg_bytes() -> bytes | None:
    from mlb_team_shell import build_cfl_share_jpeg

    return build_cfl_share_jpeg()


def cfl_results_share_jpeg_bytes() -> bytes | None:
    from mlb_team_shell import build_cfl_results_share_jpeg

    return build_cfl_results_share_jpeg()


def cfl_chart_payload() -> dict[str, Any]:
    from team_tabbed_results import build_cfl_payload

    return build_cfl_payload()
