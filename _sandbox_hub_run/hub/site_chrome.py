"""Graft the locked work2 header/footer onto hub pages.

Do not edit templates/partials/research_header.html or
templates/partials/site_directory_footer.html — those files are locked.
This only renders them and replaces whatever chrome the isolation page shipped.
"""
from __future__ import annotations

import re
from pathlib import Path

_WORK2 = Path(__file__).resolve().parents[2]
_TEMPLATES = _WORK2 / "templates"

_HEADER_RE = re.compile(
    r'(?:<div\b[^>]*\bid="site-chrome-header"[^>]*>[\s\S]*?</div>\s*)?'
    r'<header\b[^>]*\bclass="[^"]*\bpl2-header\b[^"]*"[^>]*>[\s\S]*?</header>'
    r'(?:\s*<script>[\s\S]*?(?:NAV DROPDOWNS|ACCOUNT MENU)[\s\S]*?</script>)*',
    flags=re.I,
)
_OLD_NAV_RE = re.compile(
    r'<nav\b[^>]*\bclass="[^"]*\bnavbar\b[^"]*"[^>]*>[\s\S]*?</nav>',
    flags=re.I,
)
_FOOTER_RE = re.compile(
    r'(?:<div\b[^>]*\bid="site-chrome-footer"[^>]*>[\s\S]*?</div>\s*)?'
    r'<footer\b[^>]*\bclass="[^"]*\b(?:site-directory-footer|pl2-footer)\b[^"]*"[^>]*>'
    r'[\s\S]*?</footer>',
    flags=re.I,
)
_ISO_THEME_RE = re.compile(
    r'https?://127\.0\.0\.1:\d+/static/css/research-theme\.css',
    flags=re.I,
)

# September-safe fallback matching NHL77FINAL in_season_sports_map defaults.
_IN_SEASON = {
    "NBA": False,
    "NFL": True,
    "MLB": True,
    "NHL": False,
    "SOCCER": True,
    "NCAAB": False,
    "NCAAF": True,
    "NCAAW": False,
    "WNBA": False,
    "CFL": True,
    "TENNIS": True,
    "UFC": True,
    "GOLF": True,
}


def _render_partial(name: str) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.get_template(name).render(
        soccer_enabled=True,
        in_season_sports=_IN_SEASON,
        is_logged_in=False,
        stripe_customer_portal_login_url="/login",
    )


def ensure_locked_site_chrome(html: str) -> str:
    """Replace isolation header/footer with locked research chrome."""
    if not html or "<html" not in html.lower():
        return html
    if not _TEMPLATES.is_dir():
        return html
    try:
        header = _render_partial("partials/research_header.html")
        footer = _render_partial("partials/site_directory_footer.html")
    except Exception as e:
        print(f"[site_chrome] render failed: {e}", flush=True)
        return html
    if not header or not footer:
        return html
    header_block = f'<div id="site-chrome-header">{header}</div>'
    footer_block = f'<div id="site-chrome-footer">{footer}</div>'

    nxt, n = _HEADER_RE.subn(header_block, html, count=1)
    if n:
        html = nxt
    else:
        nxt, n = _OLD_NAV_RE.subn(header_block, html, count=1)
        html = nxt if n else re.sub(
            r"(<body\b[^>]*>)",
            r"\1" + header_block,
            html,
            count=1,
            flags=re.I,
        )
    html = _ISO_THEME_RE.sub("/static/css/research-theme.css", html)
    if "/static/css/research-theme.css" not in html:
        link = '<link rel="stylesheet" href="/static/css/research-theme.css" />'
        html = re.sub(r"</head>", link + "</head>", html, count=1, flags=re.I)
    nxt, n = _FOOTER_RE.subn(footer_block, html, count=1)
    if n:
        html = nxt
    else:
        html = re.sub(
            r"</body\s*>",
            footer_block + "</body>",
            html,
            count=1,
            flags=re.I,
        )
    return html
