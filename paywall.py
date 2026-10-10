"""Free-visitor paywall for pick cards (all sports).

Free: moneyline picks (team, Sharp Consensus %, Books moneyline) and every
results page. Locked (label kept, value shown as a lock): projected scores,
Prediction Lab / XSharp odds and spread/total lines, spread and total picks,
and the pick confidence boxes. Locked values are removed from the HTML, not
just hidden.
"""
from __future__ import annotations

import re

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
        depth, end = 0, -1
        for m in re.compile(r"<div\b|</div>").finditer(html, start):
            depth += 1 if m.group(0) != "</div>" else -1
            if depth == 0:
                end = m.end()
                break
        if end < 0:
            break
        parts.append(html[pos:start])
        parts.append(fresh_block)
        pos = end
    parts.append(html[pos:])
    return "".join(parts)


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
