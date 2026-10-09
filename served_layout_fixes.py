"""Last-pass layout for every isolated sport page.

Fixes the shared checker misses: pick-confidence names wrapping inside the
box, the share bar sitting with the results image just above the footer,
a closed TV drawer covering cards, smashed consensus columns, clipped names,
and a results totals chart that disagrees with the picks Recent results grades.
Does not invent a line, a percent, or a win-loss record.
"""

from __future__ import annotations

import os
import re

_LAYOUT_CSS = """
<style id="pl-served-layout">
body .pc-name,body[data-sport] .pc-name,.pick-conf-grid .pc-name,.pc-box .pc-name{white-space:normal!important;overflow:hidden!important;text-overflow:clip!important;max-height:none!important;height:auto!important;overflow-wrap:anywhere!important;word-break:break-word!important;width:100%!important;max-width:100%!important;display:block!important;box-sizing:border-box!important}
.pick-conf-grid{display:grid!important;grid-template-columns:repeat(auto-fit,minmax(7.5rem,1fr))!important}
.pc-box{min-width:7.5rem!important;overflow:hidden!important}
.team-name,.pc-side,body[data-sport] .team-name,body[data-sport] .pc-side{white-space:normal!important;overflow:visible!important;text-overflow:clip!important;max-height:none!important;height:auto!important;overflow-wrap:anywhere!important;word-break:break-word!important}
.team-slot,.matchup-teams{overflow:visible!important}
.tv-drawer:not(.open){transform:translateX(-110%)!important;visibility:hidden!important;pointer-events:none!important}
#pl-consensus-records th,#pl-consensus-records td,#pl-books-pl-records th,#pl-books-pl-records td,#pl-totals-three-way th,#pl-totals-three-way td,#pl-xsharp-totals th,#pl-xsharp-totals td{padding:8px 16px!important}
a.market-tab{display:inline-block!important;margin:0 10px 8px 0!important;padding:8px 12px!important}
</style>
"""

_WINDOWS = ("last night", "last 7", "last 30")


def apply_served_layout(html: str, sport: str = "", path: str = "", view: str = "") -> str:
    if not html or "<html" not in html.lower():
        return html
    low = (path or "").lower()
    html = _rewrite_name_rules(html)
    html = _inject_css(html)
    html = _place_share(html, sport, low)
    html = _public_share_urls(html)
    if low.endswith("-picks"):
        html = _place_preview_hub(html)
        html = _ensure_pc_fit(html)
    if (sport or "").upper() == "CFL" and low.endswith("-picks"):
        html = _short_pc_sides(html)
    if low.endswith("-results"):
        html = _ensure_date_picker(html)
        html = _refresh_results_dates(html, sport)
        html = _sync_totals_chart(html, sport)
    if low.rstrip("/").endswith(("-picks", "-results")):
        from page_controls import repair_page_controls

        html = repair_page_controls(html, sport, path, view)
    return _single_all_dates(html)


_HUB_RE = re.compile(r'<nav class="mlb-preview-hub sport-preview-hub"[\s\S]*?</nav>', re.I)
_TABS_RE = re.compile(r'<div class="section-tabs"[^>]*>[\s\S]*?</div>', re.I)


def _place_preview_hub(html: str) -> str:
    """Today's previews links sit directly under the Predictions / Results buttons on every sport."""
    hub = _HUB_RE.search(html or "")
    tabs = _TABS_RE.search(html or "")
    if not hub or not tabs:
        return html
    between = html[tabs.end():hub.start()] if hub.start() >= tabs.end() else None
    if between is not None and not re.sub(r"<style\b[\s\S]*?</style>|\s+", "", between):
        return html  # already directly under the buttons
    block = hub.group(0)
    without = html[:hub.start()] + html[hub.end():]
    tabs = _TABS_RE.search(without)
    if not tabs:
        return html
    return without[:tabs.end()] + "\n" + block + without[tabs.end():]


_ALL_DATES_DECL = re.compile(r"\bconst allDates\s*=\s*\[[^\]]*\]\s*;?")


def _single_all_dates(html: str) -> str:
    """A second `const allDates` is a SyntaxError that stops every script after it.

    Keep the first declaration. Later copies (added by a date-picker repair) lose
    only the declaration, so the picker code beside them still runs.
    """
    found = list(_ALL_DATES_DECL.finditer(html or ""))
    if len(found) < 2:
        return html
    out, last = [], 0
    for match in found[1:]:
        out.append(html[last:match.start()])
        last = match.end()
    out.append(html[last:])
    return "".join(out)


_PC_FIT = (
    '<style id="pl-pc-fit">'
    ".pc-name,.pc-side{font-size:12px!important;line-height:1.2!important;"
    "white-space:normal!important;overflow:hidden!important;"
    "overflow-wrap:anywhere!important;text-overflow:clip!important;"
    "max-width:100%!important;height:auto!important;max-height:none!important}"
    "</style>"
)


def _ensure_pc_fit(html: str) -> str:
    """Model and team names shrink to fit the Pick Confidence box."""
    if not html or "pc-name" not in html or 'id="pl-pc-fit"' in html:
        return html
    if "</head>" in html.lower():
        idx = html.lower().find("</head>")
        return html[:idx] + _PC_FIT + html[idx:]
    return _PC_FIT + html


_SHARE_HREF = re.compile(
    r'(href="https://[^"]*?)'
    r"http%3A(?:%2F%2F|//)(?:127\.0\.0\.1|localhost)(?:%3A\d+)?",
    re.I,
)


def _public_share_urls(html: str) -> str:
    """Share links point at the public site, not the local test host."""
    if not html or "127.0.0.1" not in html and "localhost" not in html:
        return html
    return _SHARE_HREF.sub(r"\1https%3A%2F%2Fpredictionlab.io", html)


def _short_pc_sides(html: str) -> str:
    """Pick Confidence shows a nickname, not a three-word team name."""
    if not html or "pc-side" not in html:
        return html

    def _repl(match: re.Match) -> str:
        body = match.group(2)
        mark = " ✅" if "✅" in body else (" ❌" if "❌" in body else "")
        text = re.sub(r"[✅❌]", "", body).strip()
        words = text.split()
        if len(words) < 3:
            return match.group(0)
        return f"{match.group(1)}{words[-1]}{mark}{match.group(3)}"

    return re.sub(
        r'(<div class="pc-side[^"]*"[^>]*>)([^<]*)(</div>)',
        _repl,
        html,
        flags=re.I,
    )


_DATED_SPORTS = {"NFL", "NCAAF", "NBA", "WNBA", "NCAAB", "NCAAW", "MLB", "NHL", "CFL"}


def _refresh_results_dates(html: str, sport: str) -> str:
    """A saved results page keeps the day it was built on.

    Put today's date on the page and keep every scoreboard day in the picker.
    No game, score, or grade is added.
    """
    if (sport or "").upper() not in _DATED_SPORTS:
        return html
    try:
        import scoreboard_dates as board
    except Exception:
        return html
    try:
        days = sorted(
            set(board._existing_days(html))
            | set(board._scoreboard_days((sport or "").upper()))
        )
        if days:
            html = board._ensure_picker(html, days)
        html = board._fresh_today_stamp(html)
    except Exception:
        return html
    return html


def _inject_css(html: str) -> str:
    html = re.sub(r'<style id="pl-served-layout">[\s\S]*?</style>', "", html, count=1)
    idx = html.lower().rfind("</body>")
    if idx < 0:
        idx = html.lower().rfind("</head>")
    if idx < 0:
        return html + _LAYOUT_CSS
    return html[:idx] + _LAYOUT_CSS + html[idx:]


def _rewrite_name_rules(html: str) -> str:
    def pc(match: re.Match) -> str:
        block = match.group(0)
        block = re.sub(r"white-space\s*:\s*nowrap", "white-space:normal", block, flags=re.I)
        block = re.sub(r"overflow\s*:\s*visible", "overflow:hidden", block, flags=re.I)
        block = re.sub(r"max-height\s*:\s*28px", "max-height:none", block, flags=re.I)
        return block

    def team(match: re.Match) -> str:
        block = match.group(0)
        if ".pc-name" in block.split("{", 1)[0]:
            return block
        block = re.sub(r"white-space\s*:\s*nowrap", "white-space:normal", block, flags=re.I)
        block = re.sub(r"overflow\s*:\s*hidden", "overflow:visible", block, flags=re.I)
        block = re.sub(r"max-height\s*:\s*\d+px", "max-height:none", block, flags=re.I)
        return block

    html = re.sub(r"\.(?:team-name|pc-side)[^{]*\{[^}]*\}", team, html, flags=re.I)
    return re.sub(r"\.pc-name[^{]*\{[^}]*\}", pc, html, flags=re.I)


def _cut_div(html: str, marker: str) -> tuple[str, str]:
    from html_blocks import blocks

    for block in blocks(html, lambda tag, attrs: tag == "div"):
        if marker in html[block.start:block.opening_end]:
            return html[:block.start] + html[block.end:], html[block.start:block.end]
    return html, ""


def _share_shell(sport: str, path: str) -> str:
    """Same 9-icon Share on social media strip as every other page (base.html order)."""
    slug = (path or "/").strip() or "/"
    page = "https://predictionlab.io" + slug
    from urllib.parse import quote

    quoted = quote(page, safe="")
    links = (
        (f"https://x.com/intent/post?url={quoted}", "Share on X", "x.svg", "X"),
        (f"https://www.facebook.com/sharer/sharer.php?u={quoted}", "Share on Facebook", "facebook.svg", "Facebook"),
        ("https://instagram.com/predictionlab.io", "Instagram", "instagram.svg", "Instagram"),
        ("https://predictionlab.io", "TikTok", "tiktok.svg", "TikTok"),
        (f"https://www.linkedin.com/sharing/share-offsite/?url={quoted}", "Share on LinkedIn", "linkedin.svg", "LinkedIn"),
        (f"https://www.reddit.com/submit?url={quoted}", "Share on Reddit", "reddit.svg", "Reddit"),
        (f"https://www.tumblr.com/widgets/share/tool?canonicalUrl={quoted}", "Share on Tumblr", "tumblr.svg", "Tumblr"),
        (f"https://api.whatsapp.com/send?text={quoted}", "Share on WhatsApp", "whatsapp.svg", "WhatsApp"),
        (f"https://telegram.me/share/url?url={quoted}", "Share on Telegram", "telegram.svg", "Telegram"),
    )
    icons = "".join(
        f'<a class="share-icon" href="{href}" target="_blank" rel="noopener" aria-label="{label}">'
        f'<img src="/static/icons/social/{icon}" alt="{alt}"></a>'
        for href, label, icon, alt in links
    )
    return (
        '<div class="share-strip">'
        '<span class="share-strip-label">Share on social media</span>'
        f'<div class="share-icons">{icons}</div></div>'
    )


def _place_share(html: str, sport: str, path: str) -> str:
    if "site-directory-footer" not in html:
        return html
    share = ""
    while True:
        html, piece = _cut_div(html, 'class="share-strip')
        if not piece:
            break
        share = piece
    image = ""
    if 'data-results-share="1"' in html:
        html, image = _cut_div(html, 'data-results-share="1"')
    if not share and path.endswith(("-picks", "-results")):
        share = _share_shell(sport, path)
    if not share and not image:
        return html
    footer = html.rfind('class="site-directory-footer"')
    if footer < 0:
        return html + image + share
    start = html.rfind("<", 0, footer)
    if start < 0:
        start = footer
    return html[:start] + image + share + html[start:]


def _ensure_date_picker(html: str) -> str:
    if 'id="dateBubbles"' in html or re.search(r'id="date-20\d\d-\d\d-\d\d"', html) or "const allDates" in html:
        return html
    dates = []
    for d in re.findall(r'data-date="(20\d\d-\d\d-\d\d)"', html):
        if d not in dates:
            dates.append(d)
    for d in re.findall(r'id="date-(20\d\d-\d\d-\d\d)"', html):
        if d not in dates:
            dates.append(d)
    if not dates:
        return html
    chips = "".join(
        f'<a class="date-bubble" id="date-{d}" href="#date-{d}">{d[5:]}</a>' for d in dates[:14]
    )
    nav = f'<div class="date-nav" id="dateBubbles">{chips}</div>'
    footer = html.rfind('class="site-directory-footer"')
    if footer < 0:
        return html + nav
    start = html.rfind("<", 0, footer)
    return html[:start] + nav + html[start:]


def _record_of(cell: str) -> str:
    found = re.findall(r"(\d+-\d+)", cell or "")
    return found[-1] if found else ""


def _picks_totals(sport: str) -> dict[int, dict[str, str]]:
    sport = (sport or "").upper()
    html = ""
    try:
        from isolate_checker_fixes import lookup_served_picks

        html = lookup_served_picks(sport) or ""
    except Exception:
        html = ""
    if not html:
        cache = os.path.join(
            os.getcwd(),
            ".cache",
            f"served_picks_{sport}.html",
        )
        if os.path.isfile(cache):
            try:
                html = open(cache, encoding="utf-8", errors="replace").read()
            except OSError:
                html = ""
    if "picks-recent-results" not in html:
        return {}
    out: dict[int, dict[str, str]] = {}
    parts = re.split(r'<div class="picks-recent-col">', html)
    for part in parts[1:]:
        head = re.search(r"<h3>(.*?)</h3>", part, re.I | re.S)
        title = re.sub(r"<[^>]+>", "", head.group(1) if head else "").lower()
        slot = next((i for i, name in enumerate(_WINDOWS) if name in title), None)
        if slot is None:
            continue
        tot = re.search(
            r'picks-recent-mkt">Totals</h4>\s*<table[\s\S]*?</table>',
            part,
            re.I,
        )
        if not tot:
            continue
        grades: dict[str, str] = {}
        for row in re.findall(r"<tr>([\s\S]*?)</tr>", tot.group(0), re.I):
            cells = re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", row, re.I)
            if len(cells) < 2:
                continue
            label = re.sub(r"<[^>]+>", "", cells[0]).strip().lower()
            shown = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cells[1])).strip()
            if not _record_of(shown):
                continue
            if label == "prediction lab":
                grades["pl"] = shown
            elif label == "xsharp":
                grades["xs"] = shown
        if grades:
            out[slot] = grades
    return out


def _fill_model_row(row: str, records: list[str]) -> str:
    used = 0

    def td(match: re.Match) -> str:
        nonlocal used
        chunk = match.group(0)
        text = re.sub(r"<[^>]+>", "", chunk)
        if re.search(r"[A-Za-z]", text):
            return chunk
        if used >= len(records) or not records[used]:
            used += 1
            return chunk
        rec = records[used]
        used += 1
        return re.sub(r">[^<]*", ">" + rec, chunk, count=1)

    return re.sub(r"<td\b[^>]*>[\s\S]*?</td>", td, row, flags=re.I)


def _sync_totals_chart(html: str, sport: str) -> str:
    grades = _picks_totals(sport)
    if not grades or 'id="pl-totals-three-way"' not in html:
        return html
    table = re.search(
        r'(<table[^>]*id="pl-totals-three-way"[\s\S]*?</table>)',
        html,
        re.I,
    )
    if not table:
        return html
    block = table.group(1)

    def row(match: re.Match) -> str:
        whole = match.group(0)
        label = re.sub(r"<[^>]+>", "", match.group(1)).strip().lower()
        key = "pl" if label == "prediction lab" else "xs" if label == "xsharp" else ""
        if not key:
            return whole
        records = [grades.get(i, {}).get(key, "") for i in range(3)]
        if not any(records):
            return whole
        return _fill_model_row(whole, records)

    new_block = re.sub(
        r"<tr[^>]*>\s*(<td[^>]*>[\s\S]*?</td>)[\s\S]*?</tr>",
        row,
        block,
        flags=re.I,
    )
    if new_block == block:
        return html
    return html[: table.start(1)] + new_block + html[table.end(1) :]
