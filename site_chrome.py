"""Graft locked research header/footer onto pages that shipped a thin or hub bar.

Do not edit templates/partials/research_header.html or
templates/partials/site_directory_footer.html — those files are locked.
This only renders them and replaces a Picks|Results / isolation header.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_TEMPLATES = _ROOT / "templates"

_HEADER_RE = re.compile(
    r'(?:<div\b[^>]*\bid="site-chrome-header"[^>]*>[\s\S]*?</div>\s*)?'
    r'<header\b[^>]*\bclass="[^"]*\bpl2-header\b[^"]*"[^>]*>[\s\S]*?</header>'
    r'(?:\s*<script>[\s\S]*?(?:NAV DROPDOWNS|ACCOUNT MENU)[\s\S]*?</script>)*',
    flags=re.I,
)
_FOOTER_RE = re.compile(
    r'(?:<div\b[^>]*\bid="site-chrome-footer"[^>]*>[\s\S]*?</div>\s*)?'
    r'<footer\b[^>]*\bclass="[^"]*\b(?:site-directory-footer|pl2-footer)\b[^"]*"[^>]*>'
    r"[\s\S]*?</footer>",
    flags=re.I,
)

_SITE_SPORT_HREFS = (
    "/nba-picks",
    "/nfl-picks",
    "/mlb-picks",
    "/nhl-picks",
    "/soccer-picks",
    "/ncaab-picks",
    "/ncaaf-picks",
    "/ncaaw-picks",
    "/wnba-picks",
    "/cfl-picks",
    "/tennis-picks",
    "/ufc-picks",
    "/golf-picks",
)

# Locked surfaces — do not rewrite their HTML chrome.
SKIP_PATHS = frozenset(
    {
        "/mlb-picks",
        "/tennis-picks",
        "/tennis-results",
        "/ufc-picks",
    }
)


def _header_block(html: str) -> str:
    m = re.search(
        r'<header\b[^>]*class="[^"]*pl2-header[\s\S]{0,14000}',
        html or "",
        flags=re.I,
    )
    return m.group(0) if m else ""


def header_matches_site_chrome(html: str) -> bool:
    """True when the page header is Sports / Models / Results + account/search."""
    hdr = _header_block(html)
    if not hdr:
        return False
    if "pl2-nav-trigger" not in hdr:
        return False
    if not re.search(r">\s*Sports\s*<", hdr):
        return False
    if not re.search(r">\s*Models\s*<", hdr):
        return False
    if not re.search(r">\s*Results\s*<", hdr):
        return False
    if 'href="/blog"' not in hdr or "/plans" not in hdr:
        return False
    if "pl2-account" not in hdr or "pl2-search" not in hdr:
        return False
    return all(href in hdr for href in _SITE_SPORT_HREFS)


def footer_matches_site_chrome(html: str) -> bool:
    if "site-directory-footer" not in (html or ""):
        return False
    return "/affiliate" in html and "Affiliate Program" in html


def chrome_gaps(html: str) -> list[str]:
    """Human-readable ways this page's chrome differs from the site header/footer."""
    gaps: list[str] = []
    hdr = _header_block(html)
    if re.search(r"<nav[^>]*>\s*<a[^>]*>\s*Picks\s*</a>\s*<a[^>]*>\s*Results\s*</a>", hdr or "", flags=re.I):
        gaps.append("thin Picks|Results bar instead of Sports/Models/Results")
    if not header_matches_site_chrome(html):
        if "pl2-nav-trigger" not in hdr or not re.search(r">\s*Sports\s*<", hdr or ""):
            gaps.append("header is missing Sports / Models / Results")
        missing = [h for h in _SITE_SPORT_HREFS if h not in hdr]
        if missing:
            gaps.append("Sports menu missing " + ", ".join(missing[:6]))
        if "pl2-account" not in hdr or "pl2-search" not in hdr:
            gaps.append("header is missing search or account")
        if 'href="/blog"' not in hdr or "/plans" not in hdr:
            gaps.append("header is missing Blog or Pricing")
    if not footer_matches_site_chrome(html):
        gaps.append("footer is not the locked site-directory-footer")
    return gaps


def _render_partial(name: str) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    ctx = {
        "soccer_enabled": True,
        "in_season_sports": {},
        "is_logged_in": False,
        "stripe_customer_portal_login_url": "/login",
    }
    try:
        from NHL77FINAL import in_season_sports_map

        ctx["in_season_sports"] = in_season_sports_map()
    except Exception:
        ctx["in_season_sports"] = {
            "NFL": True,
            "MLB": True,
            "SOCCER": True,
            "NCAAF": True,
            "WNBA": True,
            "CFL": True,
            "TENNIS": True,
            "UFC": True,
            "GOLF": True,
        }
    try:
        from flask_login import current_user

        ctx["is_logged_in"] = bool(getattr(current_user, "is_authenticated", False))
    except Exception:
        pass
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.get_template(name).render(**ctx)


def skip_path(path: str) -> bool:
    p = (path or "").split("?")[0].rstrip("/") or "/"
    if p in SKIP_PATHS:
        return True
    if p.startswith("/mlb/") or p.startswith("/tennis/") or p.startswith("/ufc/"):
        if p.startswith("/ufc/") and "picks" not in p:
            return False
        if p.startswith("/ufc/"):
            return True
        return True
    return False


def ensure_locked_site_chrome(html: str, *, path: str = "") -> str:
    """Replace a thin/isolation header/footer with locked research chrome."""
    if not html or "<html" not in html.lower():
        return html
    if skip_path(path):
        return html
    if not _TEMPLATES.is_dir():
        return html
    need_h = not header_matches_site_chrome(html)
    need_f = not footer_matches_site_chrome(html)
    if not need_h and not need_f:
        return html
    try:
        header = _render_partial("partials/research_header.html") if need_h else ""
        footer = _render_partial("partials/site_directory_footer.html") if need_f else ""
    except Exception as e:
        print(f"[site_chrome] render failed: {e}", flush=True)
        return html
    if need_h and header:
        nxt, n = _HEADER_RE.subn(header, html, count=1)
        html = nxt if n else re.sub(
            r"(<body\b[^>]*>)",
            r"\1" + header,
            html,
            count=1,
            flags=re.I,
        )
    if need_f and footer:
        nxt, n = _FOOTER_RE.subn(footer, html, count=1)
        html = nxt if n else re.sub(
            r"</body\s*>",
            footer + "</body>",
            html,
            count=1,
            flags=re.I,
        )
    if "/static/css/research-theme.css" not in html:
        html = re.sub(
            r"</head>",
            '<link rel="stylesheet" href="/static/css/research-theme.css" /></head>',
            html,
            count=1,
            flags=re.I,
        )
    return html


# Google Ads landing-page copy: 21+, helpline, not a sportsbook.
# Fine print goes in the existing footer only — no top bar, no second footer.
_RG_AGE_BAR_RE = re.compile(
    r'<div[^>]*id=["\']pl-age-bar["\'][^>]*>.*?</div>\s*',
    re.I | re.S,
)
_RG_POLICY_RE = re.compile(
    r'<div[^>]*id=["\']pl-rg-policy["\'][^>]*>.*?</div>\s*',
    re.I | re.S,
)
_RG_POLICY_CSS_RE = re.compile(
    r'<style[^>]*id=["\']pl-rg-policy-css["\'][^>]*>.*?</style>\s*',
    re.I | re.S,
)
_RG_FINEPRINT = (
    '<p class="pl-rg-fineprint" id="pl-rg-fineprint">'
    "21+ / 18+ where required. Not intended for minors. "
    "PredictionLab is a sports research and information site. "
    "We are not an online gambling operator and we do not take bets. "
    '<a href="/responsible-gaming">Responsible Gaming</a> · '
    '<a href="tel:18005224700">1-800-GAMBLER</a> · '
    '<a href="/terms">Terms</a> · '
    '<a href="/privacy">Privacy</a>'
    "</p>"
)


def _has_gambling_policy_copy(html: str) -> bool:
    low = (html or "").lower()
    return (
        ("21+" in html or "18+" in html)
        and "1-800-gambler" in low
        and "/responsible-gaming" in low
        and (
            "not an online gambling operator" in low
            or "not a sportsbook" in low
            or "do not take bets" in low
        )
    )


def ensure_gambling_policy_chrome(html: str) -> str:
    """Keep 21+/helpline copy inside the real footer. Never add a second footer."""
    html = html or ""
    if "<html" not in html.lower():
        return html
    html = _RG_AGE_BAR_RE.sub("", html)
    html = _RG_POLICY_RE.sub("", html)
    html = _RG_POLICY_CSS_RE.sub("", html)
    if _has_gambling_policy_copy(html):
        return html
    if 'id="pl-rg-fineprint"' in html:
        return html
    lasts = list(re.finditer(r"</footer\s*>", html, flags=re.I))
    if lasts:
        i = lasts[-1].start()
        return html[:i] + _RG_FINEPRINT + html[i:]
    return html
