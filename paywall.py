"""Free-visitor paywall for pick cards (all sports).

Free: primary moneyline predictions and completed results, without prices.
Premium: prices, lines, projections, additional model details and features.
Locked (label kept, value shown as a lock): projected scores,
Prediction Lab / XSharp odds and spread/total lines, spread and total picks,
and the pick confidence boxes. Locked values are removed from the HTML, not
just hidden.
"""
from __future__ import annotations

import re
import gzip
import json
from contextlib import contextmanager
from contextvars import ContextVar
from html import escape, unescape
from urllib.parse import quote, urlsplit

from html_blocks import blocks

LOCK = "\U0001F512"

_RESULTS_PATH_RE = re.compile(r"(?:-|/)results(?:/|$)")

_LOCKED_DATA_ATTRS = (
    "m-grinder2", "m-takedown", "m-edge", "m-xsharp", "m-efficiency",
    "pl-spread", "xs-spread", "pl-proj", "xs-proj", "total-ev",
    "pl-total", "xs-total", "xs-ml", "pl-ml",
)
_DATA_ATTR_RE = re.compile(
    r'\b(data-(?:' + "|".join(re.escape(a) for a in _LOCKED_DATA_ATTRS) + r'))="[^"]*"'
)
_PC_VAL_RE = re.compile(r'(<div class="pc-val\b[^"]*"[^>]*>)(?:(?!</div>).)*(</div>)', re.S)
_PC_SIDE_RE = re.compile(r'<div class="pc-side\b[^"]*"[^>]*>(?:(?!</div>).)*</div>', re.S)
_ODDS_CELL_RE = re.compile(r'(<td class="val-(?:pl|xs)\b[^"]*"[^>]*>)(?:(?!</td>).)*(</td>)', re.S)
_PROJ_VAL_RE = re.compile(r'(<span class="proj-val\b[^"]*"[^>]*>)(?:(?!</span>).)*(</span>)', re.S)
_FACE_PL_ML_RE = re.compile(
    r'(<div class="ml-line face-pl-ml\b[^"]*"[^>]*>\s*<span class="ml-src pl">[^<]*</span>\s*'
    r'<span class="ml-num)[^"]*("[^>]*>)[^<]*(</span>)'
)
_PREMIUM_FLAG_RE = re.compile(r"const IS_PREMIUM = true\b")
_PREMIUM_JSON_RE = re.compile(r'"isPremium"\s*:\s*true\b')

_UPSELL = (
    '<div class="premium-upsell-strip" data-pl-paywall="1">Unlock spreads, totals, '
    'projected scores, and model confidence — <a href="/plans">Join Premium →</a></div>'
)


def is_results_path(path: str) -> bool:
    return bool(_RESULTS_PATH_RE.search(path or ""))


def has_locked_content(html: str) -> bool:
    return (
        'class="pick-conf-grid' in html
        or 'class="proj-score-box' in html
        or "face-pl-ml" in html
        or 'class="odds-pricing-table' in html
    )


def lock_free_picks_html(html: str) -> str:
    """Lock paid card content for a free visitor. Idempotent."""
    if not html or not has_locked_content(html):
        return html
    out = _PC_VAL_RE.sub(lambda m: m.group(1) + LOCK + m.group(2), html)
    out = _PC_SIDE_RE.sub("", out)
    out = _ODDS_CELL_RE.sub(lambda m: m.group(1) + LOCK + m.group(2), out)
    out = _PROJ_VAL_RE.sub(lambda m: m.group(1) + LOCK + m.group(2), out)
    out = _FACE_PL_ML_RE.sub(lambda m: m.group(1) + " locked" + m.group(2) + LOCK + m.group(3), out)
    out = _DATA_ATTR_RE.sub(lambda m: m.group(1) + '=""', out)
    out = _PREMIUM_FLAG_RE.sub("const IS_PREMIUM = false", out)
    out = _PREMIUM_JSON_RE.sub('"isPremium":false', out)
    if 'class="premium-upsell-strip' not in out:
        i = out.find('<div class="games-grid')
        if i < 0:
            i = out.find('class="game-card-stack')
            i = out.rfind("<", 0, i) if i > 0 else -1
        if i > 0:
            out = out[:i] + _UPSELL + out[i:]
    return out


def lock_card_data_attrs(html: str) -> str:
    """Clear the paid values kept in card data attributes. Used on pages with no lock panels."""
    if not html:
        return html
    return _DATA_ATTR_RE.sub(lambda m: m.group(1) + '=""', html)


def _drop_div(html: str, opener: str) -> str:
    """Remove every <div ...opener...> element, nested divs included."""
    while True:
        start = html.find(opener)
        if start < 0:
            return html
        depth, pos = 0, start
        for m in re.compile(r"<div\b|</div>").finditer(html, start):
            depth += 1 if m.group(0) != "</div>" else -1
            if depth == 0:
                pos = m.end()
                break
        else:
            return html
        html = html[:start] + html[pos:]


_ACCOUNT_MENU_OPENER = '<div class="tv-menu-auth">'
_DESKTOP_ACCOUNT_MENU_OPENER = '<div class="pl2-account-menu" id="pl2AccountMenu" hidden>'


def find_div(html: str, opener: str) -> str:
    """The first <div ...opener...> element with its nested divs, or ''."""
    start = html.find(opener) if html else -1
    if start < 0:
        return ""
    depth = 0
    for m in re.compile(r"<div\b|</div>").finditer(html, start):
        depth += 1 if m.group(0) != "</div>" else -1
        if depth == 0:
            return html[start:m.end()]
    return ""


def swap_account_menu(html: str, fresh_block: str, opener: str = _ACCOUNT_MENU_OPENER) -> str:
    """Replace every saved account menu with one rendered for this visitor."""
    if not html or opener not in html or not fresh_block:
        return html
    parts, pos = [], 0
    while True:
        start = html.find(opener, pos)
        if start < 0:
            break
        block = find_div(html[start:], opener)
        if not block:
            break
        parts.append(html[pos:start])
        parts.append(fresh_block)
        pos = start + len(block)
    parts.append(html[pos:])
    return "".join(parts)


# /api/picks fields a free visitor also sees on the card face. Everything else is paid.
_API_PICK_FREE_FIELDS = {"date", "matchup", "homeTeam", "awayTeam", "pick", "winPercent", "sport"}


def lock_api_picks(payload):
    """Return only free primary-forecast fields, including on legacy API paths."""
    if not isinstance(payload, dict) or not isinstance(payload.get("picks"), list):
        return {"picks": [], "locked": True}
    out = []
    for pick in payload["picks"]:
        if isinstance(pick, dict):
            out.append({k: v for k, v in pick.items() if k in _API_PICK_FREE_FIELDS})
    return {**{k: v for k, v in payload.items() if k in ("sport", "date", "count", "total")}, "picks": out}


_JOIN_TAB_RE = re.compile(r'<a href="/plans" class="tab"[^>]*>[^<]*Join Premium[^<]*</a>')


def strip_upsell_for_paid(html: str) -> str:
    """A paying visitor never sees Join Premium prompts."""
    if not html:
        return html
    out = _drop_div(html, '<div class="join-premium-bar"')
    out = _drop_div(out, '<div class="premium-upsell-strip"')
    return _JOIN_TAB_RE.sub("", out)


def leaked_values(html: str) -> list[str]:
    """Paid values still visible in a free visitor's picks page (empty when locked)."""
    found = []
    if _PREMIUM_FLAG_RE.search(html) or _PREMIUM_JSON_RE.search(html):
        found.append("page is flagged premium")
    n = len(re.findall(r'<div class="pc-val\b[^"]*"[^>]*>\s*[0-9]', html))
    if n:
        found.append(f"{n} pick confidence values")
    n = len(re.findall(r'<td class="val-(?:pl|xs)\b[^"]*"[^>]*>\s*[^<\s' + LOCK + r']', html))
    if n:
        found.append(f"{n} Prediction Lab / XSharp lines")
    n = len(re.findall(r'<span class="proj-val\b[^"]*"[^>]*>\s*[^<\s' + LOCK + r']', html))
    if n:
        found.append(f"{n} projected scores")
    n = len(re.findall(
        r'<div class="ml-line face-pl-ml\b[^"]*"[^>]*>\s*<span class="ml-src pl">[^<]*</span>\s*'
        r'<span class="ml-num[^"]*"[^>]*>\s*[+-]?\d', html))
    if n:
        found.append(f"{n} Prediction Lab moneylines")
    n = len(re.findall(
        r'\bdata-(?:m-(?:grinder2|takedown|edge|xsharp|efficiency)|pl-spread|xs-spread|pl-proj|xs-proj)="[^"]+"',
        html))
    if n:
        found.append(f"{n} hidden paid values in the page source")
    return found


def locked_for_paid(html: str) -> list[str]:
    """Paywall pieces a paying visitor must never see."""
    found = []
    if re.search(r"const IS_PREMIUM = false\b", html):
        found.append("page is flagged free")
    if 'id="joinPremiumBar"' in html or 'class="premium-upsell-strip' in html:
        found.append("Join Premium bar shown")
    n = len(re.findall(r'<div class="pc-val\b[^"]*"[^>]*>\s*' + LOCK, html))
    if n:
        found.append(f"{n} locked pick confidence boxes")
    n = len(re.findall(r'<span class="proj-val\b[^"]*"[^>]*>\s*' + LOCK, html))
    if n:
        found.append(f"{n} locked projected scores")
    if "Lines &amp; projections locked" in html or "Lines & projections locked" in html:
        found.append("lines and projections locked")
    return found


_RAW_PAGE_SOURCE = ContextVar("pl_raw_page_source", default=False)


@contextmanager
def page_source():
    """Internal parsing only: never grants membership or trusts a request header."""
    token = _RAW_PAGE_SOURCE.set(True)
    try:
        yield
    finally:
        _RAW_PAGE_SOURCE.reset(token)


_SPORTS = "nhl nba mlb nfl ncaab ncaaf ncaaw wnba cfl soccer tennis ufc golf"
_SPORT_RE = "(?:" + "|".join(_SPORTS.split()) + ")"
_PREDICTION_PATH = re.compile(
    rf"^/({_SPORT_RE})-(?:picks|predictions|prediction|projections)"
    r"(?:-[a-z]+-\d{1,2}-\d{4})?/?$", re.I,
)
_CARD_API_PATH = re.compile(rf"^/api/web/sports/({_SPORT_RE})-(?:picks|projections)/cards/?$", re.I)
_RESULT_PATH = re.compile(rf"^/{_SPORT_RE}-results/?$", re.I)
_SPORT_ALIAS = re.compile(rf"^/(?:sport/)?{_SPORT_RE}(?:/(?:predictions|picks))?/?$", re.I)
_RESULT_ALIAS = re.compile(rf"^/(?:sport/)?{_SPORT_RE}/results(?:/[^/]+)?/?$", re.I)
_PUBLIC_API = frozenset({
    "/api/web/me", "/api/web/chrome", "/api/web/sports", "/api/web/season",
    "/api/web/live", "/api/web/search", "/api/web/soccer-leagues",
    "/api/web/tennis-events", "/api/web/golf", "/api/web/all-sports", "/api/sports",
    "/api/web/results-summary",
    "/api/web/props-navigation",
    "/api/web/blog",
})
_PUBLIC_PAGES = frozenset({
    "/", "/healthz", "/favicon.ico", "/robots.txt", "/sitemap.xml", "/llms.txt", "/ai.txt",
    "/plans", "/login", "/signup", "/logout", "/account", "/tutorial", "/faq",
    "/contact", "/privacy", "/terms", "/refund-policy", "/responsible-gaming",
    "/affiliate", "/affiliate-program", "/creator-partnership", "/donate",
    "/results", "/all-sports-results", "/daily-report", "/historic-results",
    "/last-season-results", "/downloads", "/results/downloads", "/results/export.csv",
    "/what-are-ai-sports-betting-picks", "/what-are-ai-sports-predictions",
})
PUBLIC_CARD_CSS = """
.pl-free-card{border:1px solid #e0e4e8;border-radius:14px;background:#fff;margin:12px 0;padding:16px;color:#0f172a}
.pl-free-card .pick-card-header{display:flex;justify-content:space-between;gap:10px;font-size:12px;color:#64748b;margin-bottom:14px}
.pl-free-card .matchup-row{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);gap:14px;align-items:center}
.pl-free-card .team-slot{text-align:center;min-width:0}.pl-free-card .team-logo{width:44px;height:44px;object-fit:contain}
.pl-free-card .team-name{overflow-wrap:anywhere;font-weight:700;margin:6px 0}.pl-free-card .win-pct{font-size:24px;font-weight:800}
.pl-free-card .model-tag{font-size:11px;color:#64748b}.pl-free-card .odds-pricing-locked{margin-top:16px;padding-top:12px;border-top:1px solid #e5e7eb;font-size:13px;text-align:center}
.pl-free-card .card-confidence,.pl-free-card .soccer-draw-row{text-align:center;margin-top:12px}
"""


def _classes(attrs):
    return set((attrs.get("class") or "").split())


def _text(source):
    source = re.sub(r"<(?:script|style)\b[\s\S]*?</(?:script|style)>", "", source, flags=re.I)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", source)).split())


def _element_text(source, cls):
    found = blocks(source, lambda tag, attrs: cls in _classes(attrs))
    return _text(source[found[0].opening_end:found[0].end]) if found else ""


def _percent(value):
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%?\s*", str(value or ""))
    return match.group(1) if match and 0 <= float(match.group(1)) <= 100 else ""


def _logo(source):
    images = blocks(source, lambda tag, attrs: tag == "img" and "team-logo" in _classes(attrs))
    url = images[0].attrs.get("src", "") if images else ""
    if (url.startswith("/") and not url.startswith("//")) or urlsplit(url).scheme in ("http", "https"):
        return url
    return "/static/pl-logo.svg"


def _card_blocks(source):
    found = blocks(source, lambda tag, attrs: tag == "div" and
                   bool(_classes(attrs) & {"game-card-stack", "game-card", "pick-card"}))
    outer = []
    for block in found:
        if not outer or block.start >= outer[-1].end:
            outer.append(block)
    return outer


def _completed_card(source, attrs):
    if str(attrs.get("data-result") or "").lower() in ("win", "loss", "push", "void", "final"):
        return True
    if re.search(r"\bFINAL\b|\bCOMPLETED\b", _element_text(source, "game-time"), re.I):
        return True
    scores = blocks(source, lambda tag, a: "final-score" in _classes(a))
    return len(scores) >= 2 and all(
        re.fullmatch(r"\d+(?:\.\d+)?", _text(source[score.opening_end:score.end]))
        for score in scores[:2]
    )


def free_card(source, metadata=None, logged_in=False):
    """Whitelist public forecasts; never copy private markup or arbitrary data attributes."""
    metadata = metadata or {}
    cards = _card_blocks(source)
    attrs = cards[0].attrs if cards else {}
    completed = _completed_card(source, attrs)
    slots = blocks(source, lambda tag, a: "team-slot" in _classes(a))[:2]
    names = [metadata.get("away") or attrs.get("data-away") or "",
             metadata.get("home") or attrs.get("data-home") or ""]
    rows = []
    for index, name in enumerate(names):
        slot = source[slots[index].start:slots[index].end] if index < len(slots) else ""
        name = _element_text(slot, "team-name") or unescape(str(name))
        names[index] = name
        match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*%", _element_text(slot, "win-pct"))
        percent = _percent(match.group(1)) if match else ""
        probability = f'<div class="win-pct">{percent}<span class="unit">%</span></div>' if percent else ""
        score = _element_text(slot, "final-score") if completed else ""
        score_html = '<div class="final-score">' + escape(score) + '</div>' if score else ""
        rows.append(
            '<div class="team-slot"><img class="team-logo" width="44" height="44" loading="lazy" alt="" src="'
            + escape(_logo(slot), quote=True) + '"><div class="team-name">' + escape(name)
            + '</div>' + probability + score_html + '</div>'
        )
    date = metadata.get("date") or attrs.get("data-date") or ""
    if not date:
        match = re.search(r'card-details-(\d{4}-\d{2}-\d{2})', source)
        date = match.group(1) if match else ""
    confidence = _percent(attrs.get("data-conf"))
    picked = attrs.get("data-pick") or ""
    public = {
        "data-game-id": attrs.get("data-game-id") or "",
        "data-away": metadata.get("away") or attrs.get("data-away") or names[0],
        "data-home": metadata.get("home") or attrs.get("data-home") or names[1],
        "data-date": date, "data-league": attrs.get("data-league") or "",
        "data-time": attrs.get("data-time") or "", "data-pick": picked, "data-conf": confidence,
    }
    tag_attrs = "".join(f' {key}="{escape(str(value), quote=True)}"' for key, value in public.items())
    confidence_html = (
        '<div class="card-confidence">Forecast confidence · ' + escape(picked)
        + ' <strong>' + confidence + '%</strong></div>'
        if confidence and picked and not any('class="win-pct"' in row for row in rows) else ""
    )
    draw_html = ""
    draws = blocks(source, lambda tag, a: "draw-slot" in _classes(a))
    if draws:
        draw = source[draws[0].start:draws[0].end]
        match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*%", _element_text(draw, "win-pct"))
        percent = _percent(match.group(1)) if match else ""
        if percent:
            draw_html = '<div class="soccer-draw-row"><div class="draw-slot">Draw<div class="win-pct">' + percent + '%</div></div></div>'
    login = '' if logged_in else '<a href="/login">Log in</a> or '
    time_label = _element_text(source, "game-time") or public["data-time"]
    if completed and "FINAL" not in time_label.upper():
        time_label = (time_label + " · FINAL").strip(" ·")
    html = (
        '<div class="game-card-stack game-card pick-card pl-free-card" data-pick-card' + tag_attrs + '>'
        '<header class="pick-card-header"><span class="league-badge">'
        + escape(_element_text(source, "league-badge") or public["data-league"])
        + '</span><span class="game-time">'
        + escape(time_label)
        + '</span></header><div class="matchup-row">' + rows[0] + '<div class="matchup-at">@</div>'
        + rows[1] + '</div>' + draw_html + confidence_html
        + '<div class="odds-pricing-locked">Prices, lines, projections and additional features require premium. '
        + login + '<a href="/plans">See plans</a>.</div></div>'
    )
    return {"html": html, "home": metadata.get("home") or public["data-home"],
            "away": metadata.get("away") or public["data-away"], "date": date}


def free_cards_payload(payload, logged_in=False):
    cards = [free_card(card.get("html") or "", card, logged_in)
             for card in payload.get("cards") or [] if isinstance(card, dict)]
    return {"cards": cards, "total": payload.get("total", len(cards)), "css": PUBLIC_CARD_CSS,
            "shareImage": "", "sport": payload.get("sport", "")}


def _prediction_path(path):
    return _PREDICTION_PATH.match(path) or _SPORT_ALIAS.match(path) or path.startswith("/sport/SOCCER/predictions/")


def _results_path(path):
    return (bool(_RESULT_PATH.match(path) or _RESULT_ALIAS.match(path))
            or path.startswith(("/historic-results/", "/share/results/", "/share/daily-report/"))
            or path in ("/daily-report", "/last-season-results", "/all-sports-results",
                        "/historic-results", "/cfl/results-share.jpg", "/ufc/api/results")
            or bool(re.fullmatch(rf"/sport/{_SPORT_RE}/spreads/results", path, re.I))
            or bool(re.match(r"^/api/web/sports/[^/]+/results/?$", path)))


def _feature_query():
    from flask import request
    return ((request.args.get("market") or "").lower() not in ("", "ml", "moneyline", "win")
            or (request.args.get("view") or "").lower() in {
                "chart", "spread", "spreads", "total", "totals", "ats", "markets", "tabbed", "tabs"})


def _public_request():
    from flask import request
    path = request.path.rstrip("/") or "/"
    if path.startswith(("/player-props-api/", "/player-props/assets/", "/performance/")):
        return False
    if request.blueprint in ("auth", "affiliate"):
        return True
    if path.startswith(("/static/", "/sport-static/", "/soro-img/", "/blog/")):
        return True
    if path in _PUBLIC_PAGES or path == "/blog" or _results_path(path):
        return True
    if _prediction_path(path) or _CARD_API_PATH.match(path) or path.startswith("/api/picks/"):
        return not _feature_query()
    return path in _PUBLIC_API


def _denied():
    from flask import jsonify, make_response, render_template, request
    from flask_login import current_user
    logged_in = bool(current_user.is_authenticated)
    login_url = "/login?next=" + quote(request.full_path.rstrip("?"), safe="")
    if "/api/" in request.path or request.path.startswith("/player-props-api/"):
        response = jsonify({"locked": True, "loggedIn": logged_in, "premium": False,
                            "error": "Premium membership is required.", "loginUrl": login_url})
        response.status_code = 403 if logged_in else 401
    else:
        response = make_response(render_template("premium_required.html", logged_in=logged_in,
                                                login_url=login_url), 403)
    response.headers["Cache-Control"] = "private, no-store"
    response.vary.add("Cookie")
    return response


def install_access_policy(app):
    from flask import request

    @app.before_request
    def _premium_features():
        from auth_system import is_premium_user
        if request.endpoint is None or is_premium_user() or _public_request():
            return None
        if request.endpoint == "seo_picks_page":
            return None
        return _denied()


def enforce_response(response):
    """Final delivery boundary, including cached and compressed response bodies."""
    from flask import current_app, render_template, request
    from flask_login import current_user
    from markupsafe import Markup
    from auth_system import is_premium_user
    path = request.path.rstrip("/") or "/"
    if (path.startswith(("/static/", "/sport-static/", "/soro-img/"))
            or response.status_code != 200 or response.direct_passthrough):
        return response
    private = bool(is_premium_user())
    if private or path.startswith(("/api/", "/player-props-api/")) or not _public_request():
        response.headers["Cache-Control"] = "private, no-store"
        response.vary.add("Cookie")
    try:
        encoding = response.headers.get("Content-Encoding", "")
        raw = response.get_data()
        body = gzip.decompress(raw) if encoding == "gzip" else raw
        if private:
            if response.mimetype == "text/html":
                text = strip_upsell_for_paid(body.decode("utf-8"))
                if 'src="/static/js/pl-access.js"' not in text:
                    script = '<script src="/static/js/pl-access.js" defer></script>'
                    text = re.sub(r"</body\s*>", lambda m: script + m.group(0), text, count=1, flags=re.I) if re.search(r"</body\s*>", text, re.I) else text + script
                out = text.encode()
                if out != body:
                    response.set_data(gzip.compress(out, compresslevel=5) if encoding == "gzip" else out)
            return response
        logged_in = bool(current_user.is_authenticated)
        out = body
        if response.is_json:
            payload = json.loads(body)
            if _CARD_API_PATH.match(path):
                payload = free_cards_payload(payload, logged_in)
            elif path.startswith("/api/picks/"):
                payload = lock_api_picks(payload)
            elif path == "/api/web/golf":
                payload = {
                    "events": [{k: row.get(k) for k in ("id", "name", "date", "status", "group", "selected")}
                               for row in payload.get("events", [])],
                    "selected": payload.get("selected", ""), "picks": [], "locked": True,
                    "field": [{k: row.get(k) for k in ("rank", "photo", "player", "win")}
                              for row in payload.get("field", [])],
                }
            out = json.dumps(payload, separators=(",", ":")).encode()
        elif response.mimetype == "text/html" and not _RAW_PAGE_SOURCE.get():
            text = body.decode("utf-8")
            found = _card_blocks(text)
            if _prediction_path(path) or path == "/":
                cards = [free_card(text[b.start:b.end], logged_in=logged_in) for b in found]
                out = render_template(
                    "free_predictions.html", cards=[Markup(card["html"]) for card in cards],
                    public_css=Markup(PUBLIC_CARD_CSS),
                ).encode()
            elif _results_path(path):
                for block in reversed(found):
                    source = text[block.start:block.end]
                    if not _completed_card(source, block.attrs):
                        text = text[:block.start] + free_card(source, logged_in=logged_in)["html"] + text[block.end:]
                out = text.encode()
        if out != body:
            response.set_data(gzip.compress(out, compresslevel=5) if encoding == "gzip" else out)
            response.headers["Cache-Control"] = "private, no-store"
            response.vary.add("Cookie")
        return response
    except Exception:
        current_app.logger.exception("Premium delivery boundary failed; denying response")
        return _denied()
