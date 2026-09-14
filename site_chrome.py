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


# Google Ads “Online gambling” / gambling-promoting landing-page requirements:
# prominent age warning, responsible-gambling + addiction help, Terms, Privacy,
# and a footer that we are not a sportsbook. Do not invent a license number.
_RG_POLICY_MARK = 'id="pl-rg-policy"'
_RG_POLICY_CSS = """<style id="pl-rg-policy-css">
.pl-age-bar{background:#111827;color:#f8fafc;font:600 13px/1.4 system-ui,sans-serif;padding:8px 14px;text-align:center}
.pl-age-bar a{color:#fde68a;font-weight:700}
.pl-rg-policy{background:#0f172a;color:#e2e8f0;font:14px/1.55 system-ui,sans-serif;padding:16px 18px;border-top:1px solid #334155}
.pl-rg-policy p{margin:0 0 8px;max-width:1100px}
.pl-rg-policy p:last-child{margin:0}
.pl-rg-policy a{color:#fde68a;font-weight:700}
</style>"""
_RG_POLICY_TOP = (
    '<div id="pl-age-bar" class="pl-age-bar">'
    "21+ only (18+ where that is the legal age). Not for minors. "
    "If you or someone you know has a gambling problem, call "
    '<a href="tel:18005224700">1-800-GAMBLER</a> (US) · '
    '<a href="/responsible-gaming">Responsible Gaming</a> · '
    '<a href="/terms">Terms</a> · '
    '<a href="/privacy">Privacy</a>'
    "</div>"
)
_RG_POLICY_FOOT = (
    '<div id="pl-rg-policy" class="pl-rg-policy" role="contentinfo">'
    "<p><strong>21+ / 18+ where required. Not intended for minors.</strong> "
    "PredictionLab is a sports research and information site. "
    "We are not an online gambling operator, not a province-run or state-run "
    "gambling operator, and we do not provide online gambling services. "
    "We do not take bets, accept wagers, or pay out winnings.</p>"
    "<p>Any outbound sportsbook or operator links on this domain are exclusively "
    "to gambling entities licensed and authorized in the relevant geographic "
    "location. Gambling involves risk. Please wager only what you can afford "
    "to lose.</p>"
    "<p>Help: <a href=\"https://www.ncpgambling.org/help-treatment/national-helpline-1-800-gambler/\" "
    'target="_blank" rel="noopener">1-800-GAMBLER</a> · '
    '<a href="https://www.connexontario.ca/" target="_blank" rel="noopener">ConnexOntario</a> · '
    '<a href="/responsible-gaming">Responsible Gaming</a> · '
    '<a href="/terms">Terms</a> · '
    '<a href="/privacy">Privacy</a></p>'
    "</div>"
)


def ensure_gambling_policy_chrome(html: str) -> str:
    """Add the age bar + RG footer Google Ads reviewers look for."""
    html = html or ""
    if _RG_POLICY_MARK in html or "<html" not in html.lower():
        return html
    if "id=\"pl-rg-policy-css\"" not in html:
        if re.search(r"</head\s*>", html, flags=re.I):
            html = re.sub(
                r"</head\s*>",
                _RG_POLICY_CSS + "</head>",
                html,
                count=1,
                flags=re.I,
            )
        else:
            html = _RG_POLICY_CSS + html
    if 'id="pl-age-bar"' not in html:
        html = re.sub(
            r"(<body\b[^>]*>)",
            r"\1" + _RG_POLICY_TOP,
            html,
            count=1,
            flags=re.I,
        )
    if re.search(r"</body\s*>", html, flags=re.I):
        html = re.sub(
            r"</body\s*>",
            _RG_POLICY_FOOT + "</body>",
            html,
            count=1,
            flags=re.I,
        )
    else:
        html += _RG_POLICY_FOOT
    return html
