"""UFC on :5052 — exact HTML copied from the completed :5081 /ufc/ page.

Snapshots: `_sandbox_hub_run/locked_pages/ufc/` (fetched from 5081).
Route remaps + Consensus Historical face chips (same family as WNBA).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
_UFC_ISO = Path.home() / "Documents/Personal/ufc"
_LOCKED = _ROOT / "locked_pages" / "ufc"
_ISO = _ROOT / "iso_hub"
_HUB_FALLBACK = _ROOT / "_sandbox_hub_run"
_PAGES = _LOCKED if (_LOCKED / "picks.html").is_file() else (_HUB_FALLBACK / "locked_pages" / "ufc")
_HUB = _ISO if (_ISO / "team_tabbed_results.py").is_file() else (_HUB_FALLBACK / "hub")


def _public_origin() -> str:
    """Public site root already used for canonical UFC links."""
    for mod in (sys.modules.get("__main__"), sys.modules.get("NHL77FINAL")):
        origin = str(getattr(mod, "_SITE_DOMAIN", "") or "").rstrip("/") if mod else ""
        host = origin.lower()
        if origin.startswith("https://") and "127.0.0.1" not in host and "localhost" not in host:
            return origin
    return "https://predictionlab.io"


_SHARE_HREF_RE = re.compile(r'(class="share-icon"\s+href=")([^"]*)(")', re.I)
_LOCAL_ORIGIN_RE = re.compile(
    r"https?://(?:127\.0\.0\.1|localhost):\d+",
    re.I,
)
_LOCAL_ORIGIN_PART_RE = re.compile(
    r"https?%3A//(?:127\.0\.0\.1|localhost)%3A\d+",
    re.I,
)
_LOCAL_ORIGIN_FULL_RE = re.compile(
    r"https?%3A%2F%2F(?:127\.0\.0\.1|localhost)%3A\d+",
    re.I,
)


def rewrite_ufc_share_hrefs(html: str) -> str:
    """Point share hrefs at the public origin. Leave every other URL alone."""
    if not html or "share-icon" not in html:
        return html
    origin = _public_origin()
    partial = origin.replace(":", "%3A", 1)
    full = origin.replace(":", "%3A", 1).replace("/", "%2F")

    def _one(href: str) -> str:
        href = _LOCAL_ORIGIN_FULL_RE.sub(full, href)
        href = _LOCAL_ORIGIN_PART_RE.sub(partial, href)
        href = _LOCAL_ORIGIN_RE.sub(origin, href)
        return href

    def _repl(match: re.Match[str]) -> str:
        return match.group(1) + _one(match.group(2)) + match.group(3)

    return _SHARE_HREF_RE.sub(_repl, html)


def _name_hit(side: str, fighter: str) -> bool:
    side_l = re.sub(r"\s+", " ", (side or "").strip().lower())
    name_l = re.sub(r"\s+", " ", (fighter or "").strip().lower())
    if not side_l or not name_l:
        return False
    return side_l == name_l or side_l == name_l.split()[-1] or name_l.endswith(" " + side_l)


_UFC_MODEL_ATTRS = (
    ("Grinder2", "data-m-grinder2"),
    ("Takedown", "data-m-takedown"),
    ("Edge", "data-m-edge"),
    ("XSharp", "data-m-xsharp"),
    ("Efficiency", "data-m-efficiency"),
    ("Sharp Consensus", "data-m-consensus"),
)


def keep_ufc_model_percents(html: str) -> str:
    """Undo a stamp that stored one model's pick percent on another.

    data-m-* is that model's home win percent. The visible box prints the
    pick's own percent. When the pick is the away fighter, home win is the
    complement of the percent already printed on that box.
    """
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            out.append(stack)
            continue
        tag = open_m.group(1)
        rest = stack[open_m.end() :]
        home_m = re.search(r'\bdata-home="([^"]*)"', tag)
        away_m = re.search(r'\bdata-away="([^"]*)"', tag)
        home = home_m.group(1) if home_m else ""
        away = away_m.group(1) if away_m else ""
        own_home: dict[str, float] = {}
        for name, pct, side in re.findall(
            r'<div class="pc-name">\s*([^<]+?)\s*</div>\s*'
            r'<div class="pc-val"[^>]*>\s*([\d.]+)\s*%\s*</div>\s*'
            r'<div class="pc-side[^"]*">\s*([^<]*)</div>',
            rest,
            flags=re.I,
        ):
            label = re.sub(r"\s+", " ", name).strip().lower()
            try:
                shown = float(pct)
            except ValueError:
                continue
            attr = next((a for lab, a in _UFC_MODEL_ATTRS if lab.lower() == label), "")
            if not attr:
                continue
            if _name_hit(side, home):
                own_home[attr] = round(shown, 1)
            elif _name_hit(side, away):
                own_home[attr] = round(100.0 - shown, 1)
        stamped: dict[str, float] = {}
        for _label, attr in _UFC_MODEL_ATTRS:
            found = re.search(rf'\b{attr}="([^"]*)"', tag)
            if not found:
                continue
            try:
                stamped[attr] = float(found.group(1))
            except ValueError:
                continue
        groups: dict[str, list[str]] = {}
        for attr, num in stamped.items():
            if abs(num - 50.0) < 0.051:
                continue
            groups.setdefault(f"{num:.1f}", []).append(attr)
        for attrs in groups.values():
            if len(attrs) < 2:
                continue
            for attr in attrs:
                own = own_home.get(attr)
                if own is None or abs(own - stamped[attr]) < 0.051:
                    continue
                tag = re.sub(
                    rf'\b{attr}="[^"]*"',
                    f'{attr}="{own:.1f}"',
                    tag,
                    count=1,
                )
        out.append(tag + rest)
    return "".join(out)


_CHART_STUB_RE = re.compile(
    r'<nav id="dateBubbles" aria-label="Dates"></nav>\s*'
    r'<label for="datePicker"[^>]*>[\s\S]*?</label>\s*'
    r'<select id="datePicker">[\s\S]*?</select>\s*'
    r"<script>const allDates = \[[^\]]*\];[\s\S]*?</script>",
    re.I,
)
_LAYOUT_STYLE_RE = re.compile(
    r'<style id="ufc-fail-layout">[\s\S]*?</style>',
    re.I,
)


def _unique_days(html: str) -> list[str]:
    """Dates already printed on this page. Nothing new is added."""
    found: list[str] = []
    listed = re.search(r"const allDates = (\[[^\]]*\])", html or "")
    if listed:
        found.extend(re.findall(r"20\d\d-\d\d-\d\d", listed.group(1)))
    found.extend(re.findall(r'<option value="(20\d\d-\d\d-\d\d)"', html or ""))
    if not found:
        found.extend(re.findall(r"<td>(20\d\d-\d\d-\d\d)</td>", html or ""))
    days: list[str] = []
    for day in found:
        if day not in days:
            days.append(day)
    return days


def restore_ufc_chart_date_picker(html: str) -> str:
    """Put the chart's existing dates back in a visible picker. Do not add fights."""
    if not html or "results-table" not in html or re.search(r'class="date-bubble[\s"]', html):
        return html
    days = _unique_days(html)
    if not days:
        return html
    html = _CHART_STUB_RE.sub("", html, count=1)
    buttons = "".join(
        f'<button type="button" class="date-bubble" data-date="{day}">{day}</button>'
        for day in days
    )
    options = "".join(f'<option value="{day}">{day}</option>' for day in days)
    block = (
        '<div class="date-nav" aria-label="UFC date picker">'
        '<div class="nav-arrow" aria-hidden="true">‹</div>'
        f'<div class="date-bubbles" id="dateBubbles">{buttons}</div>'
        '<div class="nav-arrow" aria-hidden="true">›</div>'
        "</div>"
        '<label for="datePicker" style="position:absolute;width:1px;height:1px;overflow:hidden">Date</label>'
        f'<select id="datePicker">{options}</select>'
        "<style id=\"ufc-chart-dates\">"
        ".date-nav{display:flex;align-items:center;justify-content:center;gap:12px;margin:16px auto;"
        "padding:14px;background:#fff;border:1px solid rgba(15,23,42,0.12);border-radius:12px;"
        "max-width:1200px;box-sizing:border-box}"
        ".date-nav .nav-arrow{font-size:1.4rem;color:#94a3b8;user-select:none;padding:0 4px}"
        ".date-bubbles{display:flex;gap:8px;overflow-x:auto;padding:4px;max-width:860px}"
        ".date-bubble{background:#fff;border:2px solid rgba(15,23,42,0.18);border-radius:22px;"
        "padding:9px 16px;min-width:105px;text-align:center;cursor:pointer;white-space:nowrap;"
        "font-weight:500;font-size:.86em;color:#0f172a}"
        ".date-bubble.active{background:#f59e0b;border-color:#d97706;color:#0f172a;font-weight:700}"
        "</style>"
        "<script>const allDates = "
        + json.dumps(days)
        + ";(function(){function show(day){var rows=document.querySelectorAll('#ssr-finals tbody tr');"
        "for(var i=0;i<rows.length;i++){var cell=rows[i].cells&&rows[i].cells[0];"
        "var text=cell?(cell.textContent||'').trim():'';if(text===day){rows[i].scrollIntoView({block:'start'});break;}}"
        "document.querySelectorAll('.date-bubble').forEach(function(b){b.classList.toggle('active', b.getAttribute('data-date')===day);});"
        "var sel=document.getElementById('datePicker');if(sel)sel.value=day;}"
        "document.querySelectorAll('.date-bubble').forEach(function(b){b.addEventListener('click', function(){show(b.getAttribute('data-date'));});});"
        "var sel=document.getElementById('datePicker');if(sel)sel.addEventListener('change', function(){show(sel.value);});})();</script>"
    )
    toggle = re.search(
        r'(<div class="pl-view-toggle"[\s\S]*?</div>\s*<style>[\s\S]*?</style>)',
        html,
        flags=re.I,
    )
    if toggle:
        at = toggle.end()
        return html[:at] + block + html[at:]
    main = re.search(r"<main\b[^>]*>", html, flags=re.I)
    if main:
        return html[: main.end()] + block + html[main.end() :]
    return block + html


def _div_span(html: str, marker: str) -> tuple[int, int]:
    at = html.find(marker)
    if at < 0:
        return -1, -1
    start = html.rfind("<div", 0, at)
    if start < 0:
        return -1, -1
    pos = html.find(">", start)
    if pos < 0:
        return -1, -1
    pos += 1
    depth = 1
    while pos < len(html) and depth:
        nxt_open = html.find("<div", pos)
        nxt_close = html.find("</div>", pos)
        if nxt_close < 0:
            return -1, -1
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            pos = nxt_open + 4
        else:
            depth -= 1
            pos = nxt_close + len("</div>")
    return start, pos


def _take_div(html: str, marker: str) -> tuple[str, str]:
    start, end = _div_span(html, marker)
    if start < 0 or end <= start:
        return html, ""
    return html[:start] + html[end:], html[start:end]


def place_ufc_share_above_footer(html: str) -> str:
    """Results image, then the share bar, then the site footer."""
    if not html or 'class="share-strip"' not in html or "site-directory-footer" not in html:
        return html
    footer_at = html.rfind('class="site-directory-footer"')
    if footer_at < 0:
        return html
    footer_tag = html.rfind("<", 0, footer_at)
    if footer_tag < 0:
        return html
    image_start, image_end = _div_span(html, 'class="social-export-wrap"')
    share_start, share_end = _div_span(html, 'class="share-strip"')
    if share_start < 0 or share_end < 0 or share_start > footer_at:
        return html
    image_ok = image_start < 0 or (
        image_end <= share_start and html[image_end:share_start].strip() == ""
    )
    share_ok = html[share_end:footer_tag].strip() == ""
    if image_ok and share_ok and (image_start < 0 or image_start < share_start < footer_at):
        return html
    html, image = _take_div(html, 'class="social-export-wrap"')
    html, share = _take_div(html, 'class="share-strip"')
    if not share:
        return html + image
    footer_at = html.rfind('class="site-directory-footer"')
    footer_tag = html.rfind("<", 0, footer_at) if footer_at >= 0 else -1
    block = ""
    if image:
        block += image + "\n"
    block += share + "\n"
    if footer_tag >= 0:
        return html[:footer_tag] + block + html[footer_tag:]
    return html + block


_UFC_LAYOUT_CSS = (
    '<style id="ufc-fail-layout">'
    ".tv-drawer:not(.open){transform:translate3d(calc(-100% - 64px),0,0)!important}"
    ".pc-box{display:grid!important;grid-template-rows:auto auto auto!important;"
    "height:auto!important;min-height:0!important;overflow:visible!important}"
    ".pc-name,.pc-side,.team-name{white-space:normal!important;overflow-wrap:anywhere!important;"
    "word-break:break-word!important;height:auto!important;max-height:none!important;"
    "max-width:100%!important;min-width:0!important;display:block!important;line-height:1.2!important}"
    "</style>"
)


def ensure_ufc_layout_css(html: str) -> str:
    """Keep the closed menu off the card and let fighter and model names wrap in full."""
    if not html:
        return html
    html = _LAYOUT_STYLE_RE.sub("", html)
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", _UFC_LAYOUT_CSS + "</body>", html, count=1, flags=re.I)
    return html + _UFC_LAYOUT_CSS


def apply_ufc_customer_html(html: str, path: str = "") -> str:
    """Share links use the public origin. Copied model percents stay on their own model."""
    if not html:
        return html
    html = rewrite_ufc_share_hrefs(html)
    if "picks" in (path or "").lower():
        html = merge_stored_upcoming_pick_cards(html)
        html = keep_ufc_model_percents(html)
    html = restore_ufc_chart_date_picker(html)
    html = place_ufc_share_above_footer(html)
    return ensure_ufc_layout_css(html)


def _rewrite_hub_paths(html: str) -> str:
    if not html:
        return html
    html = html.replace("/ufc/results", "/ufc-results")
    html = html.replace('href="/ufc/"', 'href="/ufc-picks"')
    html = html.replace("href='/ufc/'", "href='/ufc-picks'")
    html = re.sub(r'href=(["\'])/ufc/\1', r"href=\1/ufc-picks\1", html)
    return rewrite_ufc_share_hrefs(html)


def _with_consensus_hist(html: str) -> str:
    try:
        from team_results_charts import _inject_ufc_consensus_hist_chips

        return _inject_ufc_consensus_hist_chips(html)
    except Exception as e:
        print(f"[ufc_live] consensus hist inject failed: {e}", flush=True)
        return html


def _relabel_best_performing_today(html: str) -> str:
    """Completed-window Best Performing is Last Night, not Today."""
    if not html or "Best Performing Model" not in html:
        return html
    return re.sub(
        r'(Best Performing Model[\s\S]{0,900}?class="mlabel">)\s*Today\s*<',
        r"\1Last Night<",
        html,
        count=1,
        flags=re.I,
    )


def _today_et() -> datetime.date:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return datetime.now().date()


def _norm_fighter(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _load_stored_upcoming_cards() -> list[dict[str, Any]]:
    """Scheduled/live fights with stored predictions — no ESPN calls."""
    pipe_path = _UFC_ISO / "engine" / "pipeline.py"
    if not pipe_path.is_file():
        return []
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("ufc_iso_pipeline_picks", pipe_path)
        if spec is None or spec.loader is None:
            return []
        pipe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pipe)
        rows = pipe.list_pick_cards()
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        print(f"[ufc_live] stored upcoming load failed: {e}", flush=True)
        return []


def _engine_render_card():
    """Card renderer of the UFC engine, for fights the saved page has no card for."""
    path = _UFC_ISO / "engine" / "render.py"
    if not path.is_file():
        return None
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("ufc_iso_render_cards", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "render_card", None)
    except Exception as e:
        print(f"[ufc_live] card renderer load failed: {e}", flush=True)
        return None


def stored_upcoming_fight_dates() -> list[str]:
    """Calendar days present in stored upcoming slate (for empty-slate reports)."""
    days: list[str] = []
    for row in _load_stored_upcoming_cards():
        raw = str(row.get("fight_date") or "")[:10]
        if len(raw) == 10 and raw not in days:
            days.append(raw)
    return sorted(days)


def _fight_day_key(row: dict[str, Any]) -> str:
    raw = str(row.get("fight_date") or "")
    if not raw:
        return ""
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            from datetime import timezone

            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except ValueError:
        return raw[:10] if len(raw) >= 10 else ""


def _index_pick_stacks(html: str) -> dict[tuple[str, str], str]:
    """Map (home, away) -> full game-card-stack HTML from a picks page."""
    out: dict[tuple[str, str], str] = {}
    if not html or "data-pick-card" not in html:
        return out
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    for chunk in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", chunk, flags=re.I)
        if not open_m:
            continue
        body = chunk[open_m.end() :]
        depth = 1
        pos = 0
        end_at = -1
        while pos < len(body) and depth:
            nxt_o = body.find("<div", pos)
            nxt_c = body.find("</div>", pos)
            if nxt_c < 0:
                break
            if nxt_o != -1 and nxt_o < nxt_c:
                depth += 1
                pos = nxt_o + 4
            else:
                depth -= 1
                pos = nxt_c + len("</div>")
                if depth == 0:
                    end_at = pos
        if end_at < 0:
            continue
        stack = open_m.group(1) + body[:end_at]
        home_m = re.search(r'\bdata-home="([^"]*)"', stack, flags=re.I)
        away_m = re.search(r'\bdata-away="([^"]*)"', stack, flags=re.I)
        if not home_m or not away_m:
            continue
        key = (_norm_fighter(home_m.group(1)), _norm_fighter(away_m.group(1)))
        out[key] = stack
    return out


def _default_visible_day(days: list[str]) -> str:
    if not days:
        return ""
    today = _today_et().isoformat()
    future = [d for d in days if d >= today]
    if future:
        return future[0]
    return days[-1]


def _date_bubbles_html(days: list[str], visible: str, today: str) -> str:
    buttons: list[str] = []
    for day in days:
        active = day == visible
        classes = ["date-bubble"]
        if active:
            classes.append("active")
        if day == today:
            classes.append("today")
            classes.append("today-bubble")
        label = "📅 Today" if day == today else day
        cls = " ".join(classes)
        buttons.append(
            f'<button type="button" class="{cls}" data-date="{day}" title="{day}" '
            f'onclick="(function(btn){{var id=btn.getAttribute(\'data-date\');'
            f"document.querySelectorAll('.date-section').forEach(function(s){{"
            f"s.classList.toggle('visible', s.id==='date-'+id);"
            f"s.classList.toggle('seo-hidden', s.id!=='date-'+id);}});"
            f"document.querySelectorAll('.date-bubble').forEach(function(b){{"
            f"b.classList.toggle('active', b===btn);}});}})(this)\">{label}</button>"
        )
    return (
        '<div class="date-nav" aria-label="UFC date picker">'
        '<div class="nav-arrow" aria-hidden="true">‹</div>'
        f'<div class="date-bubbles" id="dateBubbles">{"".join(buttons)}</div>'
        '<div class="nav-arrow" aria-hidden="true">›</div>'
        "</div>"
    )


def _build_date_sections(stacks_by_day: dict[str, list[str]], visible: str) -> str:
    parts: list[str] = []
    for day in sorted(stacks_by_day.keys()):
        vis_cls = "visible" if day == visible else "seo-hidden"
        stacks = stacks_by_day[day]
        parts.append(
            f'<div class="date-section {vis_cls}" id="date-{day}">'
            f'<div class="date-header">📅 {day}</div>'
            f"<div class='games-grid'>{''.join(stacks)}</div>"
            f'<div class="chart-table-wrap" hidden></div>'
            f"</div>"
        )
    return "".join(parts)


def merge_stored_upcoming_pick_cards(html: str) -> str:
    """Show stored upcoming fights on the in-season picks page (no invented slate)."""
    stored = _load_stored_upcoming_cards()
    if not stored or not html:
        return html

    templates = _index_pick_stacks(html)
    if len(templates) < len(stored):
        try:
            canonical = (_PAGES / "picks.html").read_text(encoding="utf-8", errors="replace")
            templates.update(_index_pick_stacks(canonical))
        except Exception:
            pass

    stacks_by_day: dict[str, list[str]] = {}
    missing = 0
    from datetime import timedelta

    first = (_today_et() - timedelta(days=2)).isoformat()
    last = (_today_et() + timedelta(days=7)).isoformat()
    render_card = _engine_render_card()
    for idx, row in enumerate(stored):
        home = str(row.get("home_fighter") or "")
        away = str(row.get("away_fighter") or "")
        if not home or not away:
            continue
        day = _fight_day_key(row) or str(row.get("fight_date") or "")[:10]
        if not day or not (first <= day <= last):
            continue
        key = (_norm_fighter(home), _norm_fighter(away))
        stack = templates.get(key)
        if not stack and render_card is not None:
            try:
                stack = render_card(row, idx)
            except Exception:
                stack = ""
        if not stack:
            missing += 1
            continue
        stacks_by_day.setdefault(day, []).append(stack)

    if not stacks_by_day:
        print(
            f"[ufc_live] stored upcoming: no matching card HTML ({missing} unmatched)",
            flush=True,
        )
        return html

    days = sorted(stacks_by_day.keys())
    visible = _default_visible_day(days)
    today = _today_et().isoformat()
    sections = _build_date_sections(stacks_by_day, visible)
    nav = _date_bubbles_html(days, visible, today)

    # Replace date picker + slate; keep chart, recent results, tally below/above.
    if re.search(r'class="date-nav"', html, flags=re.I):
        html = re.sub(
            r'<div class="date-nav"[\s\S]*?</div>\s*</div>',
            nav,
            html,
            count=1,
            flags=re.I,
        )
    else:
        html = html.replace(
            '<div class="picks-view-controls">',
            nav + '<div class="picks-view-controls">',
            1,
        )

    block = re.search(
        r'<div class="date-section\b[\s\S]*?(?=<script|window\.PICKS_CHART|id="picks-recent-results"|class="tally-wrap"|</main>)',
        html,
        flags=re.I,
    )
    if block:
        html = html[: block.start()] + sections + html[block.end() :]
    else:
        anchor = re.search(r'<div class="picks-view-controls"[\s\S]*?</div>\s*</div>', html, flags=re.I)
        if anchor:
            at = anchor.end()
            html = html[:at] + sections + html[at:]
        else:
            html = html.replace("</main>", sections + "</main>", 1)

    # Keep SEO date scripts aligned with the visible slate day.
    html = re.sub(
        r"const today = '(\d{4}-\d{2}-\d{2})'",
        f"const today = '{today}'",
        html,
        count=1,
    )
    if "const allDates" in html:
        html = re.sub(
            r"const allDates = \[[^\]]*\]",
            "const allDates = " + json.dumps(days),
            html,
            count=1,
        )
    return html


def _mark_past_fight_times_final(html: str) -> str:
    """A fight before yesterday is a final, not an upcoming clock."""
    if not html or 'class="game-time"' not in html:
        return html
    from datetime import datetime, timedelta

    try:
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        today = datetime.now().date()
    cutoff = today - timedelta(days=1)

    def repl(match: re.Match[str]) -> str:
        text = match.group(1)
        state = text.upper()
        if "FINAL" in state or "LIVE" in state:
            return match.group(0)
        found = re.search(r"(20\d\d-\d\d-\d\d)", text)
        if not found:
            return match.group(0)
        try:
            played = datetime.strptime(found.group(1), "%Y-%m-%d").date()
        except ValueError:
            return match.group(0)
        if played < cutoff:
            return f'class="game-time">{text} FINAL'
        return match.group(0)

    return re.sub(r'class="game-time">([^<]+)', repl, html)


def _read_page(name: str) -> str:
    path = _PAGES / name
    if not path.is_file():
        raise RuntimeError(f"locked UFC snapshot missing: {path}")
    html = _rewrite_hub_paths(path.read_text(encoding="utf-8", errors="replace"))
    html = _relabel_best_performing_today(html)
    if name == "picks.html":
        html = merge_stored_upcoming_pick_cards(html)
        html = _mark_past_fight_times_final(html)
    return _with_consensus_hist(html)


def render_ufc_picks() -> str:
    return _read_page("picks.html")


def render_ufc_results(*, view: str = "normal") -> str:
    view = (view or "normal").strip().lower()
    if view in ("chart", "tabs", "markets", "tabbed"):
        # Chart view keeps tallies / consensus table; no per-fight face chips needed.
        path = _PAGES / "results_chart.html"
        if not path.is_file():
            raise RuntimeError(f"locked UFC snapshot missing: {path}")
        page = _relabel_best_performing_today(
            _rewrite_hub_paths(path.read_text(encoding="utf-8", errors="replace"))
        )
        try:
            from isolate_checker_fixes import (
                ensure_best_performing_width,
                ensure_market_tabs,
                ensure_sou_compare,
                repair_results_html,
            )
            page = ensure_best_performing_width(page)
            page = ensure_market_tabs(page)
            mk = ""
            try:
                from flask import request
                mk = (request.args.get("market") or "").strip().lower()
            except Exception:
                mk = ""
            if mk in ("spread", "totals"):
                page = ensure_sou_compare(page, mk)
            page = repair_results_html(page, "UFC", view=mk or "chart")
            from team_results_charts import _sync_last_night_dates
            page = _sync_last_night_dates(page)
        except Exception:
            pass
        return page
    return _read_page("results.html")


def apply_ufc_isolation_html(html: str, *, which: str = "picks") -> str:
    if which == "results":
        return render_ufc_results()
    return render_ufc_picks()


def build_ufc_share_jpeg_bytes() -> bytes | None:
    jpg = _PAGES / "share.jpg"
    if jpg.is_file() and jpg.stat().st_size > 500:
        data = jpg.read_bytes()
        if data[:2] == b"\xff\xd8":
            return data
    return None


def _load_hub_for_api():
    hub_s = str(_HUB.resolve())
    root_s = str(_ROOT.resolve())
    for k in list(sys.modules):
        if k in {"ufc_page", "team_tabbed_results", "share_chrome"} or k.startswith("ufc_iso_"):
            sys.modules.pop(k, None)
    for p in (hub_s, root_s):
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)


def build_ufc_chart_api_payload() -> dict[str, Any]:
    try:
        _load_hub_for_api()
        from team_tabbed_results import build_ufc_payload  # type: ignore
        from isolate_checker_fixes import ensure_ml_only_markets as _ensure_ufc_mkts
        from isolate_checker_fixes import ensure_ml_only_markets

        payload = build_ufc_payload()
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["ok"] = True
            try:
                payload = ensure_ml_only_markets(payload)
            except Exception:
                pass
            return payload
    except Exception as e:
        print(f"[ufc_live] chart api failed: {e}", flush=True)
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "empty payload"}
