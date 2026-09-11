#!/usr/bin/env python3
"""Frozen site-parity header/footer for Sports Sandbox hub pages only.

Canonical chrome lives in hub/static/chrome/ — do not invent per-sport headers.
Call ensure_canonical_chrome() on every HTML response. Do not re-customize.
"""
from __future__ import annotations

import re
from pathlib import Path

_CHROME_DIR = Path(__file__).resolve().parent / "static" / "chrome"
_FROZEN_HEADER: str | None = None
_FROZEN_FOOTER: str | None = None

# Hub routes for sports we serve locally (rewrite live slugs → sandbox paths).
_SPORT_HREFS: dict[str, tuple[str, str, str, str]] = {
    "mlb": ("/mlb-picks", "/mlb/", "/mlb-results", "/mlb/results"),
    "soccer": ("/soccer-picks", "/soccer/", "/soccer-results", "/soccer/results"),
    "wnba": ("/wnba-picks", "/wnba/", "/wnba-results", "/wnba/results"),
    "cfl": ("/nfl-picks", "/cfl/", "/nfl-results", "/cfl/results"),
    "golf": ("/golf-picks", "/golf/", "/golf-results", "/golf/results"),
    "ufc": ("/ufc-picks", "/ufc/", "/ufc-results", "/ufc/results"),
    "tennis": ("/tennis-picks", "/tennis/", "/tennis-results", "/tennis/results"),
}

_CANONICAL_CSS = (
    '<link rel="stylesheet" href="/static/css/research-theme.css" />\n'
    '<link rel="stylesheet" href="/static/css/picks-nav-overrides.css" />\n'
    '<link rel="stylesheet" href="/static/css/sports-chrome.css" />\n'
    '<link rel="stylesheet" href="/static/css/mlb-pick-cards.css?v=pc16" />\n'
)

# Golf keeps its own board layout — still gets frozen header/footer + theme,
# but sports-chrome equal-card rules are optional (individual sport).
_TEAM_SPORTS = frozenset({"mlb", "soccer", "wnba", "cfl", "ufc", "tennis", "fantasy"})

_CFL_TEAM_TOKENS = (
    "Hamilton",
    "Tiger-Cats",
    "Toronto Argonauts",
    "Roughriders",
    "Blue Bombers",
    "BC Lions",
    "Calgary Stampeders",
    "Edmonton Elks",
    "Montreal Alouettes",
    "Ottawa Redblacks",
    "Saskatchewan",
    "Winnipeg",
)

_NFL_LEAK_MARKERS = (
    "teamlogos/nfl",
    "Cincinnati Bengals",
    "Detroit Lions",
    "Atlanta Falcons",
    "Buffalo Bills",
    "New England Patriots",
    "Dallas Cowboys",
    "Green Bay Packers",
)


def strip_hub_links(html: str) -> str:
    """Remove Hub / Sports Hub nav and footer links (product UI only)."""
    if not html:
        return html
    html = re.sub(
        r'<a\b[^>]*href=["\']/?["\'][^>]*>\s*(?:Sports\s+)?Hub\s*</a>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div\b[^>]*class=["\'][^"\']*footer-heading[^"\']*["\'][^>]*>\s*Hub\s*</div>\s*'
        r'(?:<a\b[^>]*>[\s\S]*?</a>\s*)+',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(r">\s*Sports Hub\s*<", "><", html, flags=re.I)
    return html


def rewrite_sandbox_sport_hrefs(html: str, sport: str | None = None) -> str:
    """Point live pick/results slugs at hub routes."""
    if not html:
        return html
    sports = [sport.lower()] if sport else list(_SPORT_HREFS.keys())
    for key in sports:
        row = _SPORT_HREFS.get(key)
        if not row:
            continue
        picks_slug, picks_dest, results_slug, results_dest = row
        for slug, dest in ((picks_slug, picks_dest), (results_slug, results_dest)):
            html = html.replace(f"https://predictionlab.io/{slug.lstrip('/')}", dest)
            html = html.replace(f'href="/{slug.lstrip("/")}"', f'href="{dest}"')
            html = html.replace(f"href='/{slug.lstrip('/')}'", f"href='{dest}'")
        if key == "cfl":
            html = html.replace('href="/cfl-picks"', 'href="/cfl/"')
            html = html.replace('href="/cfl-results"', 'href="/cfl/results"')
    return html


def replace_balanced_container_inner(html: str, inner: str) -> str:
    """Replace inner HTML of the first .container (balanced div walk)."""
    if not html:
        return html
    start = re.search(r'<div class="container\b[^"]*"[^>]*>', html, flags=re.I)
    if not start:
        m = re.search(r"(<main\b[^>]*>)([\s\S]*?)(</main>)", html, flags=re.I)
        if m:
            return (
                html[: m.start(2)]
                + f'\n<div class="container">\n{inner}\n</div>\n'
                + html[m.end(2) :]
            )
        return html + f'<div class="container">{inner}</div>'

    i = start.end()
    depth = 1
    j = i
    close_at = -1
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
                close_at = next_close
                break
            j = next_close + 6
    if close_at < 0:
        return html
    return html[:i] + "\n" + inner + "\n" + html[close_at:]


def sport_section_tabs(sport: str, *, which: str = "picks") -> str:
    """In-page Picks | Results tabs (matches CFL isolation pages)."""
    sport = sport.lower()
    picks_href = f"/{sport}/"
    results_href = f"/{sport}/results"
    pa = "active" if which == "picks" else ""
    ra = "active" if which == "results" else ""
    return (
        '<div class="section-tabs" role="navigation" aria-label="Sport pages">'
        f'<a href="{picks_href}" class="tab {pa}">Picks</a>'
        f'<a href="{results_href}" class="tab {ra}">Results</a>'
        "</div>"
        "<style>.section-tabs{display:flex;gap:8px;margin:12px 0 18px;flex-wrap:wrap}"
        ".section-tabs .tab{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.section-tabs .tab.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def _balanced_div_end(html: str, start: int) -> int:
    """Return index after closing </div> for the div that starts at start, or -1."""
    tag_end = html.find(">", start)
    if tag_end < 0:
        return -1
    j = tag_end + 1
    depth = 1
    while j < len(html) and depth > 0:
        next_open = html.find("<div", j)
        next_close = html.find("</div>", j)
        if next_close < 0:
            return -1
        if next_open >= 0 and next_open < next_close:
            depth += 1
            j = next_open + 4
        else:
            depth -= 1
            if depth == 0:
                return next_close + 6
            j = next_close + 6
    return -1


def strip_cfl_nfl_remnants(html: str) -> str:
    """Remove leftover NFL pick chrome when CFL slate is injected into NFL shell."""
    if not html:
        return html

    def _is_cfl_block(block: str) -> bool:
        # True CFL only — require CFL league marker or CFL team + no NFL logos
        if "teamlogos/nfl" in block.lower():
            return False
        if any(m in block for m in _NFL_LEAK_MARKERS):
            return False
        if re.search(r'data-league=["\']NFL["\']', block, flags=re.I):
            return False
        if 'data-league="CFL"' in block or "data-league='CFL'" in block:
            return True
        return any(tok in block for tok in _CFL_TEAM_TOKENS)

    def _is_nfl_block(block: str) -> bool:
        if "teamlogos/nfl" in block.lower():
            return True
        if any(m in block for m in _NFL_LEAK_MARKERS):
            return True
        if re.search(r'data-league=["\']NFL["\']', block, flags=re.I):
            return True
        return False

    # JSON-LD SportsEvent blobs with NFL teams
    def _ld_repl(m: re.Match) -> str:
        blob = m.group(0)
        if "SportsEvent" not in blob:
            return blob
        if _is_nfl_block(blob) and not _is_cfl_block(blob):
            return ""
        return blob

    html = re.sub(
        r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>[\s\S]*?</script>',
        _ld_repl,
        html,
        flags=re.I,
    )

    # Drop any game-card-stack / pick-card that is NFL (balanced walk).
    for open_pat in (
        r'<div\b[^>]*\bclass=["\'][^"\']*\bgame-card-stack\b[^"\']*["\'][^>]*>',
        r'<div\b[^>]*\bclass=["\'][^"\']*\bpick-card\b[^"\']*["\'][^>]*>',
        r'<div\b[^>]*\bclass=["\'][^"\']*\bgame-card\b[^"\']*["\'][^>]*>',
    ):
        pos = 0
        pieces: list[str] = []
        while True:
            m = re.search(open_pat, html[pos:], flags=re.I)
            if not m:
                pieces.append(html[pos:])
                break
            abs_start = pos + m.start()
            pieces.append(html[pos:abs_start])
            end = _balanced_div_end(html, abs_start)
            if end < 0:
                pieces.append(html[abs_start:])
                break
            block = html[abs_start:end]
            if _is_nfl_block(block) and not _is_cfl_block(block):
                pass  # drop
            else:
                pieces.append(block)
            pos = end
        html = "".join(pieces)

    # Date sections still carrying only NFL face cards.
    pos = 0
    pieces = []
    while True:
        m = re.search(r'<div\b[^>]*\bid=["\']date-\d{4}-\d{2}-\d{2}["\'][^>]*>', html[pos:], flags=re.I)
        if not m:
            pieces.append(html[pos:])
            break
        abs_start = pos + m.start()
        pieces.append(html[pos:abs_start])
        end = _balanced_div_end(html, abs_start)
        if end < 0:
            pieces.append(html[abs_start:])
            break
        block = html[abs_start:end]
        if _is_nfl_block(block) and not _is_cfl_block(block):
            pass
        else:
            pieces.append(block)
        pos = end
    html = "".join(pieces)

    # Hard scrub any remaining NFL logo URLs / team name spans outside cards
    for marker in _NFL_LEAK_MARKERS:
        if marker.startswith("data-league") or marker.startswith("teamlogos"):
            continue
        # Only strip short orphan team-name lines that survived (not whole pages)
        html = re.sub(
            rf'<div class="team-name">\s*{re.escape(marker)}\s*</div>',
            "",
            html,
            flags=re.I,
        )
    html = re.sub(
        r'<img\b[^>]*teamlogos/nfl[^>]*>',
        "",
        html,
        flags=re.I,
    )

    # Orphan analysis toggles
    html = re.sub(
        r'<div\b[^>]*\bclass=["\'][^"\']*\banalysis-toggle\b[^"\']*["\'][^>]*>\s*'
        r'(?:View Details[\s\S]*?)</div>\s*(?=<div class="(?:analysis-|card-details)|<footer|</main)',
        "",
        html,
        flags=re.I,
    )
    return html


def point_sidecar_static_to_hub(html: str) -> str:
    if not html:
        return html
    html = re.sub(
        r'https?://127\.0\.0\.1:\d+/static/',
        "/static/",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'https?://localhost:\d+/static/',
        "/static/",
        html,
        flags=re.I,
    )
    return html


def _ensure_burger_nav(html: str) -> str:
    """Hamburger must open Picks/Results links on every sport, including CFL."""
    if not html:
        return html
    if 'id="tvDrawer"' in html and "TV_MENUS" in html:
        return html
    burger = (_CHROME_DIR / "burger_nav.html").read_text(encoding="utf-8")
    html = re.sub(r'<div class="tv-overlay"[\s\S]*?</div>\s*<div class="tv-drawer"[\s\S]*?</div>', "", html, count=1, flags=re.I)
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", burger + "\n</body>", html, count=1, flags=re.I)
    return html + burger


def _load_frozen_chrome() -> tuple[str, str]:
    global _FROZEN_HEADER, _FROZEN_FOOTER
    if _FROZEN_HEADER is None:
        _FROZEN_HEADER = (_CHROME_DIR / "pl2_header.html").read_text(encoding="utf-8")
    footer = (_CHROME_DIR / "site_directory_footer.html").read_text(encoding="utf-8")
    _FROZEN_FOOTER = footer
    return _FROZEN_HEADER, _FROZEN_FOOTER


def _ensure_chrome_css(html: str) -> str:
    if not html:
        return html
    link_sports = '<link rel="stylesheet" href="/static/css/sports-chrome.css" />'
    link_cards = '<link rel="stylesheet" href="/static/css/mlb-pick-cards.css?v=pc16" />'
    link_nav = '<link rel="stylesheet" href="/static/css/picks-nav-overrides.css" />'
    if "/static/css/research-theme.css" not in html:
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", _CANONICAL_CSS + "</head>", html, count=1, flags=re.I)
        else:
            html = _CANONICAL_CSS + html
        return html

    if "/static/css/picks-nav-overrides.css" not in html:
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", link_nav + "\n</head>", html, count=1, flags=re.I)
        else:
            html = html + link_nav

    # Always inject sports-chrome before </head> when missing (do not rely on
    # fragile href-adjacent regex — large live pages can no-op that path).
    if "/static/css/sports-chrome.css" not in html:
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", link_sports + "\n</head>", html, count=1, flags=re.I)
        else:
            html = html + link_sports

    # MLB pick-card face (matchup-row / team-slot / win-pct / lines-strip / pick-conf).
    # Required for isolation fragment sports (CFL) that do not proxy MLB inline CSS.
    if "/static/css/mlb-pick-cards.css" not in html:
        if re.search(r"</head>", html, re.I):
            html = re.sub(r"</head>", link_cards + "\n</head>", html, count=1, flags=re.I)
        else:
            html = html + link_cards
    return html


def _mark_sports_chrome_body(html: str, sport: str) -> str:
    """Tag body so CSS/checker can assert shared chrome is active."""
    if not html:
        return html
    sport = (sport or "").lower()
    marker = 'data-sandbox-sports-chrome="1"'
    if marker in html:
        return html
    if sport and sport not in _TEAM_SPORTS and sport != "golf":
        # Unknown sport — still mark when we applied frozen chrome
        pass
    m = re.search(r"<body\b([^>]*)>", html, flags=re.I)
    if not m:
        return html
    attrs = m.group(1) or ""
    if "research-site" not in attrs:
        attrs = attrs + ' class="research-site"'
    elif not re.search(r'\bclass="', attrs, flags=re.I):
        attrs = attrs + ' class="research-site"'
    attrs = attrs + f' {marker} data-sandbox-sport="{sport or "home"}"'
    return html[: m.start()] + f"<body{attrs}>" + html[m.end() :]


def normalize_results_layout_classes(html: str) -> str:
    """Map thin/custom results shells onto MLB class names (layout chrome only)."""
    if not html:
        return html
    # Wrap loose tally-grid as daily-tally when missing MLB markers
    if "daily-tally" not in html and re.search(r'class="[^"]*\btally-grid\b', html, re.I):
        html = re.sub(
            r'(<section\b[^>]*class="[^"]*\btally-wrap\b[^"]*"[^>]*>)',
            r'\1<div class="daily-tally">',
            html,
            count=1,
            flags=re.I,
        )
        html = re.sub(
            r'(</section>\s*(?=<section\b|</main>|<div class="pl-view|<footer))',
            r"</div>\1",
            html,
            count=1,
            flags=re.I,
        )
        html = html.replace('class="tally-grid"', 'class="tally-grid daily-tally-grid"', 1)
        html = re.sub(
            r'class="tally-card"',
            'class="tally-card daily-tally-card"',
            html,
            count=6,
        )
    # Results card lists → games-grid
    html = re.sub(
        r'(<div class=")cards(" id="finals")',
        r'\1cards games-grid\2',
        html,
        count=1,
        flags=re.I,
    )
    # Ensure a .container around main content when missing
    if 'class="container' not in html and re.search(r"<main\b", html, re.I):
        html = re.sub(
            r"(<main\b[^>]*>)",
            r'\1<div class="container">',
            html,
            count=1,
            flags=re.I,
        )
        html = re.sub(
            r"</main>",
            "</div></main>",
            html,
            count=1,
            flags=re.I,
        )
    return html


def ensure_canonical_chrome(html: str, sport: str = "", *, which: str = "picks") -> str:
    """Force frozen site header/footer on every page. Single chrome — do not diverge."""
    if not html:
        return html
    header, footer = _load_frozen_chrome()
    # Mark so we can detect double-application safely
    if 'data-sandbox-chrome="frozen"' not in header:
        header = header.replace(
            '<header class="pl2-header">',
            '<header class="pl2-header" data-sandbox-chrome="frozen">',
            1,
        )

    if re.search(r'<header\b[^>]*class="[^"]*pl2-header', html, re.I):
        html = re.sub(
            r'<header\b[^>]*class="[^"]*pl2-header[^"]*"[^>]*>[\s\S]*?</header>',
            header,
            html,
            count=1,
            flags=re.I,
        )
    elif re.search(r"<body\b", html, re.I):
        html = re.sub(r"(<body\b[^>]*>)", r"\1\n" + header + "\n", html, count=1, flags=re.I)
    else:
        html = header + html

    if re.search(r'<footer\b[^>]*class="[^"]*site-directory-footer', html, re.I):
        html = re.sub(
            r'<footer\b[^>]*class="[^"]*site-directory-footer[^"]*"[^>]*>[\s\S]*?</footer>',
            footer,
            html,
            count=1,
            flags=re.I,
        )
    elif re.search(r"</body>", html, re.I):
        html = re.sub(r"</body>", footer + "\n</body>", html, count=1, flags=re.I)
    else:
        html = html + footer

    html = _ensure_chrome_css(html)
    html = _ensure_burger_nav(html)
    html = _mark_sports_chrome_body(html, sport or "")
    if (sport or "").lower() != "golf" and which in ("results", "picks", "performance"):
        html = normalize_results_layout_classes(html)
    # Drop mini hub topbars if present (home base.html leftovers)
    html = re.sub(
        r'<div class="topbar">[\s\S]*?</div>\s*(?=<header class="pl2-header"|<div class="wrap"|<main|<section)',
        "",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="footer">\s*Local hub[^<]*</div>',
        "",
        html,
        flags=re.I,
    )
    return apply_shared_chrome(html, sport or "", which=which)


def apply_shared_chrome(html: str, sport: str, *, which: str = "picks") -> str:
    """Strip Hub links, rewrite sport hrefs, CFL NFL scrub."""
    if not html:
        return html
    sport = (sport or "").lower()
    html = strip_hub_links(html)
    html = rewrite_sandbox_sport_hrefs(html, sport if sport else None)
    # Always rewrite all known sport slugs so frozen menus work everywhere
    html = rewrite_sandbox_sport_hrefs(html, None)
    html = point_sidecar_static_to_hub(html)
    if sport == "cfl":
        html = strip_cfl_nfl_remnants(html)
    return html


def wrap_body_with_live_chrome(
    body_html: str,
    sport: str,
    *,
    which: str = "results",
) -> str:
    """Wrap content in a minimal shell, then force frozen header/footer.

    Preserves head CSS/title and body <script> tags. Older versions kept only
    <main>, which stripped team-results.js and left chart views as empty shells.
    """
    raw = body_html or ""
    title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.I | re.S)
    title = (title_m.group(1).strip() if title_m else f"{sport.upper()} Results") or f"{sport.upper()} Results"
    links = re.findall(r"<link\b[^>]*>", raw, flags=re.I)
    styles = re.findall(r"<style\b[^>]*>[\s\S]*?</style>", raw, flags=re.I)
    scripts = re.findall(r"<script\b[\s\S]*?</script>", raw, flags=re.I)
    top = re.search(
        r'<header\b[^>]*class="[^"]*\btop\b[^"]*"[^>]*>[\s\S]*?</header>',
        raw,
        flags=re.I,
    )
    main = re.search(r"<main\b[\s\S]*?</main>", raw, flags=re.I)
    if main:
        parts = []
        if top:
            parts.append(top.group(0))
        parts.append(main.group(0))
        inner = "\n".join(parts)
    elif raw and "<div" in raw:
        inner = f'<main><div class="container">{raw}</div></main>'
        scripts = []  # already inside raw
    else:
        inner = raw
    if scripts:
        inner = inner + "\n" + "\n".join(scripts)
    head_extra = "\n".join(links + styles)
    if head_extra:
        head_extra = "\n" + head_extra + "\n"
    shell = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"/>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>"
        f"<title>{title}</title>{head_extra}</head>"
        f'<body class="research-site" data-theme="light">{inner}</body></html>'
    )
    return ensure_canonical_chrome(shell, sport, which=which)
