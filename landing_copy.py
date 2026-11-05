"""Homepage-only footer wording (Google Ads: a prediction site, not a gambling site).

The shared footer is used by every page and the site chrome checks its exact
wording, so the homepage words are swapped on the finished homepage response
only. Every other path passes through untouched.
"""

from __future__ import annotations

import re

_SLUGS = "mlb|nba|nhl|nfl|soccer|ncaab|ncaaf|ncaaw|wnba|cfl|tennis|ufc|golf"
_ALIAS_PAGES = {
    "/ai-sports-projections-today": "/ai-sports-predictions-today",
    "/how-our-models-work": "/what-are-ai-sports-betting-picks",
    "/our-model-vs-market": "/our-model-vs-sportsbooks",
    "/all-sports-projections": "/all-sports-picks",
    "/ladder": "/ladder-picks",
}
_ALIAS_RE = re.compile(rf"^/({_SLUGS})-projections(?=$|/|-)")
_HOME_HREF_RE = re.compile(rf'href="/({_SLUGS})-picks(?=["/?#-])')


def alias_path(path: str) -> str:
    """/nfl-projections… is the same page as /nfl-picks…"""
    if path in _ALIAS_PAGES:
        return _ALIAS_PAGES[path]
    return _ALIAS_RE.sub(r"/\1-picks", path, count=1)


def landing_links(html: str) -> str:
    html = _HOME_HREF_RE.sub(r'href="/\1-projections', html)
    for new, old in _ALIAS_PAGES.items():
        html = html.replace(f'href="{old}"', f'href="{new}"')
    return html


_SPORTS = ("MLB", "NBA", "NHL", "NFL", "Soccer", "NCAAB", "NCAAF", "NCAAW",
           "WNBA", "CFL", "Tennis", "UFC", "Golf")

_FOOTER_WORDS = [
    ("AI-powered sports betting picks. Daily. Transparent. Data-driven.",
     "Algorithmic sports intelligence &amp; predictive modeling. Daily. Transparent. Data-driven."),
    (">AI Picks by Sport<", ">Model Projections by Sport<"),
    (">Model vs Sportsbooks<", ">Model vs Market Consensus<"),
    (">Player Props<", ">Player Performance<"),
    (">Free Picks<", ">Basic Analytics<"),
    (">Premium Edge — $4.99/wk<", ">Pro Intelligence — $4.99/wk<"),
    (">What Are AI Picks<", ">How Our Models Work<"),
    ("21+ (18+ where required). Not an online gambling operator. We do not provide gambling services "
     "or take bets. Outbound book links go only to licensed operators in the relevant location. "
     '<a href="/responsible-gaming">Responsible Gaming</a> · <a href="tel:18005224700">1-800-GAMBLER</a>',
     "Prediction Lab is an independent sports data analytics and predictive modeling platform. "
     "We do not provide gambling services, we do not accept wagers, and we do not host affiliate "
     "links to bookmakers. All simulations, win probabilities, and statistical projections are "
     "published for educational, informational, and research purposes."),
] + [(f">{s} AI Picks<", f">{s} Projections<") for s in _SPORTS]


def landing_footer_words(html: str) -> str:
    start = html.find('<footer class="site-directory-footer"')
    if start < 0:
        return html
    end = html.find("</footer>", start)
    if end < 0:
        return html
    footer = html[start:end]
    for old, new in _FOOTER_WORDS:
        footer = footer.replace(old, new)
    return html[:start] + footer + html[end:]


class ProjectionsAliasMiddleware:
    """Serve the new -projections addresses as the existing -picks pages."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO") or ""
        if "projections" not in path:
            return self.app(environ, start_response)
        new = alias_path(path)
        if new == path:
            return self.app(environ, start_response)
        environ["PATH_INFO"] = new

        def _start(status, headers, exc_info=None):
            if status[:1] == "3":
                headers = [
                    (k, re.sub(rf"/({_SLUGS})-picks(?=$|[/?#-])", r"/\1-projections", v))
                    if k.lower() == "location" else (k, v)
                    for k, v in headers
                ]
            return start_response(status, headers, exc_info)

        return self.app(environ, _start)


_WIDE_CSS = (
    '<style id="pl-wide-preview">'
    ".pl2-shell{width:min(1440px,calc(100% - 48px))!important;max-width:none!important;"
    "margin-left:auto!important;margin-right:auto!important}"
    ".pl2-breadcrumb,.pl2-horizon{display:none!important}"
    ".pl2-hero>.pl2-shell{padding-left:0!important}"
    ".site-directory-footer .directory-shell{width:min(1440px,100%)!important;max-width:none!important}"
    "</style>"
)


def landing_wide(html: str) -> str:
    if "pl-wide-preview" in html or "</head>" not in html:
        return html
    return html.replace("</head>", _WIDE_CSS + "</head>", 1)


class LandingCopyMiddleware:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ.get("PATH_INFO") != "/" or environ.get("REQUEST_METHOD") not in ("GET", "HEAD"):
            return self.app(environ, start_response)
        captured = {}

        def _start(status, headers, exc_info=None):
            captured["status"], captured["headers"], captured["exc"] = status, headers, exc_info
            return lambda data: None

        chunks = self.app(environ, _start)
        try:
            body = b"".join(chunks)
        finally:
            if hasattr(chunks, "close"):
                chunks.close()
        headers = captured.get("headers") or []
        ctype = next((v for k, v in headers if k.lower() == "content-type"), "")
        encoded = any(k.lower() == "content-encoding" for k, _ in headers)
        if captured.get("status", "").startswith("200") and "html" in ctype and not encoded and body:
            try:
                text = body.decode("utf-8")
                text = landing_links(landing_footer_words(text))
                if "wide=1" in (environ.get("QUERY_STRING") or ""):
                    text = landing_wide(text)
                body = text.encode("utf-8")
                headers = [(k, v) for k, v in headers if k.lower() != "content-length"]
                headers.append(("Content-Length", str(len(body))))
            except Exception:
                pass
        start_response(captured.get("status", "200 OK"), headers, captured.get("exc"))
        return [body]
