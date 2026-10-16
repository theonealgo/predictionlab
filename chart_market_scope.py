"""Results chart views show only the market picked in the top buttons."""
from __future__ import annotations

import re

MARKETS = ("moneyline", "spread", "totals")

_GAME_SECTION = re.compile(r'<section\b([^>]*\bdata-ssr-market="(moneyline|spread|totals)"[^>]*)>')
_MARKET_CHART = re.compile(
    r'<(section|div)\b([^>]*\bid="(?:wnba-)?three-way-(moneyline|spread|totals)"[^>]*)>'
    r'|<(section|div)\b([^>]*\bid="pl-(spread|totals)-(?:three-way|vs-books)"[^>]*)>'
)
_EMPTY_TOTALS_BOX = (
    '<div class="pl-consensus-records"><h2>Prediction Lab · XSharp — Totals</h2></div>'
)

_STYLE = (
    '<style id="pl-chart-market-scope">'
    "section[data-ssr-market][hidden],[data-chart-market][hidden]{display:none!important}"
    "</style>"
)
_SCRIPT = (
    '<script id="pl-chart-market-scope-js">'
    "document.addEventListener('click',function(e){"
    "var t=e.target.closest&&e.target.closest('.market-tab[data-market]');if(!t)return;"
    "var m=t.getAttribute('data-market');"
    "document.querySelectorAll('[data-chart-market]').forEach(function(el){"
    "if(el.getAttribute('data-chart-market')===m)el.removeAttribute('hidden');"
    "else el.setAttribute('hidden','');});});"
    "</script>"
)


def _set_hidden(attrs: str, hide: bool) -> str:
    attrs = re.sub(r'\shidden(?:="[^"]*")?(?=[\s>]|$)', "", attrs)
    return attrs + " hidden" if hide else attrs


def _set_id(attrs: str, new_id: str) -> str:
    if re.search(r'\bid="[^"]*"', attrs):
        return re.sub(r'\bid="[^"]*"', f'id="{new_id}"', attrs, count=1)
    return f' id="{new_id}"' + attrs


def active_market(html: str, requested: str = "") -> str:
    mk = (requested or "").strip().lower()
    if mk in MARKETS:
        return mk
    tab = re.search(r'class="market-tab active"[^>]*\bdata-market="(\w+)"', html or "")
    return tab.group(1) if tab and tab.group(1) in MARKETS else "moneyline"


def scope_chart_markets(html: str, requested: str = "") -> str:
    if not html or "data-ssr-market=" not in html:
        return html
    active = active_market(html, requested)
    end = html.rfind("<footer")
    body, tail = (html, "") if end < 0 else (html[:end], html[end:])

    sections = list(_GAME_SECTION.finditer(body))
    target = next((m for m in sections if m.group(2) == active), None)
    for m in reversed(sections):
        attrs, market = m.group(1), m.group(2)
        if m is target:
            attrs = _set_hidden(_set_id(attrs, "ssr-finals"), False)
        else:
            if target is not None and re.search(r'\bid="ssr-finals"', attrs):
                attrs = _set_id(attrs, f"ssr-finals-{market}")
            attrs = _set_hidden(attrs, True)
        body = body[: m.start()] + f"<section{attrs}>" + body[m.end():]

    for m in reversed(list(_MARKET_CHART.finditer(body))):
        tag = m.group(1) or m.group(4)
        attrs = m.group(2) or m.group(5)
        market = m.group(3) or m.group(6)
        attrs = re.sub(r'\sdata-chart-market="[^"]*"', "", attrs)
        attrs = _set_hidden(attrs + f' data-chart-market="{market}"', market != active)
        body = body[: m.start()] + f"<{tag}{attrs}>" + body[m.end():]

    if active != "totals":
        body = body.replace(_EMPTY_TOTALS_BOX, "")

    if 'id="pl-chart-market-scope"' not in body:
        body = re.sub(r"</head>", lambda _m: _STYLE + "</head>", body, count=1, flags=re.I)
    html = body + tail
    close = html.lower().rfind("</body>")
    if 'id="pl-chart-market-scope-js"' not in html and close >= 0:
        html = html[:close] + _SCRIPT + html[close:]
    return html
