"""Tennis on :5052 — the locked :5081 page, copied into this folder.

Source files live in `_sandbox_hub_run/hub/` (copied from independent_sports).
This module only loads that copy and remaps hub paths to product routes.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_HUB = Path(__file__).resolve().parent / "_sandbox_hub_run" / "hub"
_ROOT = _HUB.parent


def _load_locked_tennis():
    if not (_HUB / "tennis_page.py").is_file():
        raise RuntimeError(f"locked tennis_page.py missing at {_HUB}")
    hub_s = str(_HUB.resolve())
    root_s = str(_ROOT.resolve())
    for k in list(sys.modules):
        if k in {
            "tennis_page",
            "sandbox_fixup",
            "mlb_page_template",
            "share_chrome",
            "shared_chrome",
            "team_tabbed_results",
            "mlb_three_way_consensus",
            "tennis_consensus",
        }:
            sys.modules.pop(k, None)
    for p in (hub_s, root_s):
        if p in sys.path:
            sys.path.remove(p)
        sys.path.insert(0, p)
    import tennis_page  # noqa: WPS433

    return tennis_page


def _rewrite_hub_paths(html: str) -> str:
    if not html:
        return html
    html = html.replace("/tennis/results", "/tennis-results")
    html = html.replace('href="/tennis/"', 'href="/tennis-picks"')
    html = html.replace("href='/tennis/'", "href='/tennis-picks'")
    html = re.sub(r'href=(["\'])/tennis/\1', r"href=\1/tennis-picks\1", html)
    return html


def build_tennis_share_jpeg_bytes() -> bytes | None:
    try:
        _load_locked_tennis()
        from share_chrome import build_tennis_share_jpeg  # type: ignore

        return build_tennis_share_jpeg()
    except Exception as e:
        print(f"[tennis_live] share jpeg failed: {e}", flush=True)
        return None


def tennis_chart_payload() -> dict[str, Any]:
    try:
        _load_locked_tennis()
        from team_tabbed_results import build_tennis_payload  # type: ignore

        return build_tennis_payload()
    except Exception as e:
        print(f"[tennis_live] chart payload failed: {e}", flush=True)
        return {"ok": False, "error": str(e)}


_TENNIS_DB_SYNCED = False


def _sync_tennis_slate() -> None:
    """Refresh isolation tennis DB so today's ESPN matches appear on picks."""
    global _TENNIS_DB_SYNCED
    if _TENNIS_DB_SYNCED:
        return
    try:
        # Prefer hub package (DB tennis_page reads) after _load_locked_tennis.
        from tennis.espn_sync import sync_tennis_db

        result = sync_tennis_db(force=True)
        print(f"[tennis_live] espn sync: {result}", flush=True)
        _TENNIS_DB_SYNCED = True
    except Exception as e:
        print(f"[tennis_live] espn sync failed: {e}", flush=True)


def _strip_picks_view_scaffold(html: str) -> str:
    """Results must use href Cards|Chart, not picks setPicksView buttons."""
    if not html:
        return html
    html = re.sub(
        r'(?:<style id="picks-chart-scaffold">[\s\S]*?</style>\s*)?'
        r'<div class="picks-view-controls">[\s\S]*?</div>\s*</div>\s*',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="picks-view-controls">[\s\S]*?</div>\s*',
        "",
        html,
        flags=re.I,
    )
    return html


def _tennis_results_view_toggle(active: str) -> str:
    cards = "active" if active != "chart" else ""
    chart = "active" if active == "chart" else ""
    return (
        '<div class="pl-view-toggle" role="navigation" aria-label="Results view">'
        f'<a class="pl-view-btn {cards}" href="/tennis-results">Cards</a>'
        f'<a class="pl-view-btn {chart}" href="/tennis-results?view=chart">Chart</a>'
        "</div>"
        "<style>.pl-view-toggle{display:flex;gap:8px;margin:12px 0 18px;flex-wrap:wrap;"
        "justify-content:flex-start;width:100%}"
        ".pl-view-btn{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.pl-view-btn.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def _ensure_results_view_toggle(html: str, *, chart: bool) -> str:
    html = _strip_picks_view_scaffold(html or "")
    active = "chart" if chart else "normal"
    # Drop any hub-path toggle; rewrite to product hrefs.
    html = re.sub(
        r'<div class="pl-view-toggle"[^>]*>[\s\S]*?</div>\s*'
        r'(?:<style>\.pl-view-toggle[\s\S]*?</style>\s*)?',
        "",
        html,
        flags=re.I,
    )
    block = _tennis_results_view_toggle(active)
    if re.search(r'<div class="section-tabs"[\s\S]*?</div>', html, flags=re.I):
        return re.sub(
            r'(<div class="section-tabs"[\s\S]*?</div>\s*(?:<style>[\s\S]*?</style>)?)',
            r"\1\n" + block,
            html,
            count=1,
            flags=re.I,
        )
    if re.search(r"<main\b", html, flags=re.I):
        return re.sub(r"(<main\b[^>]*>)", r"\1" + block, html, count=1, flags=re.I)
    return block + html


def _drop_balanced_tag(src: str, start: int, tag: str = "section") -> str:
    """Remove a balanced <tag>…</tag> starting at `start`."""
    open_tok = f"<{tag}"
    close_tok = f"</{tag}>"
    i = src.find(">", start)
    if i < 0:
        return src
    depth = 1
    j = i + 1
    low = src.lower()
    open_l = open_tok.lower()
    close_l = close_tok.lower()
    while j < len(src) and depth > 0:
        nxt_open = low.find(open_l, j)
        nxt_close = low.find(close_l, j)
        if nxt_close < 0:
            break
        if nxt_open >= 0 and nxt_open < nxt_close:
            depth += 1
            j = nxt_open + len(open_l)
        else:
            depth -= 1
            if depth == 0:
                return src[:start] + src[nxt_close + len(close_tok) :]
            j = nxt_close + len(close_tok)
    return src


def _extract_balanced_tag(src: str, start: int, tag: str = "section") -> tuple[str, str]:
    """Return (block, remainder) for balanced tag at start, or ("", src)."""
    open_tok = f"<{tag}"
    close_tok = f"</{tag}>"
    i = src.find(">", start)
    if i < 0:
        return "", src
    depth = 1
    j = i + 1
    low = src.lower()
    open_l = open_tok.lower()
    close_l = close_tok.lower()
    while j < len(src) and depth > 0:
        nxt_open = low.find(open_l, j)
        nxt_close = low.find(close_l, j)
        if nxt_close < 0:
            break
        if nxt_open >= 0 and nxt_open < nxt_close:
            depth += 1
            j = nxt_open + len(open_l)
        else:
            depth -= 1
            if depth == 0:
                end = nxt_close + len(close_tok)
                return src[start:end], src[:start] + src[end:]
            j = nxt_close + len(close_tok)
    return "", src


def _force_cut_from(html: str, start: int) -> str:
    """Remove an unbalanced board from start to </main> / share / footer."""
    if start < 0 or start >= len(html):
        return html
    end_m = re.search(
        r"</main>|<div class=\"social-export-wrap\"|<footer\b",
        html[start:],
        flags=re.I,
    )
    if not end_m:
        return html[:start]
    return html[:start] + html[start + end_m.start() :]


def _strip_chart_match_cards(html: str) -> str:
    """Chart view = shared 1vs1 chart UI (no date-nav / date-section boards)."""
    if not html:
        return html
    # Chart must not look like Cards — drop date picker + match boards.
    for _ in range(8):
        m = re.search(r'<div class="date-nav\b', html, flags=re.I)
        if not m:
            break
        block, html2 = _extract_balanced_tag(html, m.start(), "div")
        if block:
            html = html2
        else:
            html = _force_cut_from(html, m.start())
            break
    html = re.sub(
        r'<style id="mlb-tpl-date-nav">[\s\S]*?</style>\s*',
        "",
        html,
        flags=re.I,
    )
    for _ in range(40):
        m = re.search(r'<div class="date-section\b', html, flags=re.I)
        if not m:
            break
        block, html2 = _extract_balanced_tag(html, m.start(), "div")
        if block:
            html = html2
            continue
        # Unbalanced leftover (broken card HTML) still nests later injects
        # inside display:none — cut it away so Moneyline can sit before
        # the Predictions Image like UFC chart.
        html = _force_cut_from(html, m.start())
        break
    for _ in range(4):
        m = re.search(r'<section\b[^>]*\bid=["\']finals-wrap["\']', html, flags=re.I)
        if not m:
            break
        html = _drop_balanced_tag(html, m.start(), "section")
    html = re.sub(
        r'<h2 class="sec-title">\s*Completed matches[\s\S]*?</h2>\s*',
        "",
        html,
        flags=re.I,
    )
    if "tennis-chart-hide-cards" not in html:
        css = (
            '<style id="tennis-chart-hide-cards">'
            ".date-nav,.date-section,"
            ".date-section .games-grid,.date-section .game-card-stack,"
            ".date-section .pick-card,.date-section [data-pick-card],"
            "#finals-wrap,.games-grid#finals"
            "{display:none!important}</style>"
        )
        if re.search(r"</head>", html, flags=re.I):
            html = re.sub(r"</head>", css + "</head>", html, count=1, flags=re.I)
        else:
            html = css + html
    return html


def _move_ssr_finals_into_main(html: str) -> str:
    """Park Moneyline games just before </main> — never inside a date-section card.

    Hub inject can land #ssr-finals inside a pick-card / date-section. Chart CSS
    then hides those boards (display:none), so the table vanishes before the
    Predictions Image even though it is still in the DOM.
    """
    if not html or 'id="ssr-finals"' not in html:
        return html
    m = re.search(
        r'(<section\b[^>]*\bid=["\']ssr-finals["\'][\s\S]*?</section>\s*)',
        html,
        flags=re.I,
    )
    if not m:
        return html
    block = m.group(1)
    html_wo = html[: m.start()] + html[m.end() :]
    if not re.search(r"</main>", html_wo, flags=re.I):
        return html_wo + block
    return re.sub(r"</main>", block + "</main>", html_wo, count=1, flags=re.I)


def _ensure_tennis_chart_games_table(html: str) -> str:
    """UFC Chart ends with Moneyline games results table — tennis must too."""
    if not html:
        return html
    if 'id="ssr-finals"' in html or "Moneyline games" in html:
        return _move_ssr_finals_into_main(html)
    try:
        from team_tabbed_results import build_tennis_payload, inject_ssr_chart_bootstrap

        payload = build_tennis_payload()
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.setdefault("ok", True)
        # inject_ssr expects a #tallies hook; add one if missing.
        if not re.search(r'id=["\']tallies["\']', html, flags=re.I):
            hook = '<div id="tallies" class="tally-wrap"></div>'
            if re.search(r"</main>", html, flags=re.I):
                html = re.sub(r"</main>", hook + "</main>", html, count=1, flags=re.I)
            else:
                html = html + hook
        # Hub inject places ssr-finals before <footer>, then
        # _strip_wnba_orphan_chart_blocks deletes it for sport=tennis.
        # Inject as mlb (same Moneyline table) and move the block into </main>.
        html = inject_ssr_chart_bootstrap(html, payload, "mlb")
        html = _move_ssr_finals_into_main(html)
        if 'id="ssr-finals"' not in html and "Moneyline games" not in html:
            # Last resort: append a minimal Moneyline shell before </main>.
            finals = list(payload.get("finals") or [])[:40]
            n = len(finals)
            shell = (
                '<section id="ssr-finals">'
                f'<h2 class="sec-title">Moneyline games '
                f'<span class="tag">({n})</span></h2>'
                '<div class="table-wrap"><table class="results-table">'
                "<thead><tr><th>Date</th><th>League</th><th>Match</th>"
                "<th>Score</th><th>Edge pick</th><th>%</th>"
                "<th>Result</th><th>Models</th></tr></thead>"
                "<tbody><tr><td colspan=8 class=muted>No finals.</td></tr>"
                "</tbody></table></div></section>"
            )
            # Prefer full inject output if a second pass works.
            html2 = inject_ssr_chart_bootstrap(html, payload, "mlb")
            m = re.search(
                r'<section\b[^>]*\bid=["\']ssr-finals["\'][\s\S]*?</section>',
                html2,
                flags=re.I,
            )
            block = m.group(0) if m else shell
            if re.search(r"</main>", html, flags=re.I):
                html = re.sub(r"</main>", block + "</main>", html, count=1, flags=re.I)
            else:
                html = html + block
    except Exception as e:
        print(f"[tennis_live] chart games table failed: {e}", flush=True)
    return html


def _cards_view_analytics_board(html: str) -> str:
    """Cards = shared 1vs1/UFC template: analytics + consensus, then date-sections."""
    if not html:
        return html
    return re.sub(
        r'<style id="tennis-cards-hide-match-cards">[\s\S]*?</style>\s*',
        "",
        html,
        flags=re.I,
    )


def _ensure_tennis_chart_consensus(page: str, original: str) -> str:
    """inject_consensus strips existing tennis charts; put a real chart back."""
    has_title = "Consensus Based Betting Records" in (page or "")
    has_bar = "cons-bar" in (page or "")
    if has_title and has_bar:
        return page
    block = ""
    src = original if original and "Consensus Based Betting Records" in original else page
    m = re.search(
        r'<div\b[^>]*\b(?:id|class)=["\'][^"\']*pl-consensus-records[^"\']*["\'][^>]*>'
        r"[\s\S]*?</div>\s*</div>",
        src or "",
        flags=re.I,
    )
    if m:
        block = m.group(0)
    if "Consensus Based Betting Records" not in (block or page or ""):
        block = (
            '<section class="pl-consensus-records" id="pl-consensus-records">'
            "<h2>Consensus Based Betting Records</h2>"
            '<p class="sub">Moneyline consensus on completed tennis matches.</p>'
            '<div class="cons-bar"><i style="width:50%"></i></div>'
            "</section>"
        )
    if block and block not in (page or ""):
        if re.search(r"</main>", page or "", flags=re.I):
            page = re.sub(r"</main>", block + "</main>", page, count=1, flags=re.I)
        elif re.search(r"</body>", page or "", flags=re.I):
            page = re.sub(r"</body>", block + "</body>", page, count=1, flags=re.I)
        else:
            page = (page or "") + block
    return page


def _expand_result_cards(html: str) -> str:
    """Results cards ship open (Less details), matching picks / UFC."""
    if not html or "data-pick-card" not in html:
        return html

    def _open_stack(m: re.Match[str]) -> str:
        tag = m.group(0)
        if "is-expanded" not in tag:
            tag = tag.replace("game-card-stack", "game-card-stack is-expanded", 1)
        return tag

    html = re.sub(
        r'<div class="game-card-stack[^"]*"',
        _open_stack,
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(class="view-details-btn"[^>]*aria-expanded=")false(")',
        r'\1true\2',
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(class="view-details-btn"[^>]*>)\s*View Details\s*<span class="chevron">',
        r'\1Less details <span class="chevron">',
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(<div class="card-details"[^>]*)\s+hidden(\s|>)',
        r"\1\2",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(<div class="card-details"[^>]*style=")display:\s*none;?(")',
        r'\1display:block\2',
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(<div class="card-details"[^>]*)>',
        lambda m: m.group(0)
        if "style=" in m.group(1)
        else m.group(1) + ' style="display:block">',
        html,
        flags=re.I,
        count=80,
    )
    return html


def render_tennis_picks() -> str:
    tennis_page = _load_locked_tennis()
    _sync_tennis_slate()
    page, _meta = tennis_page.render_tennis_with_chrome("", which="picks")
    if not page:
        raise RuntimeError("locked tennis picks rendered empty")
    page = _rewrite_hub_paths(page)
    # Picks must keep on-page Cards|Chart (setPicksView) — never results hrefs.
    page = re.sub(
        r'<div class="pl-view-toggle"[^>]*>[\s\S]*?</div>\s*'
        r'(?:<style>\.pl-view-toggle[\s\S]*?</style>\s*)?',
        "",
        page,
        flags=re.I,
    )
    page = re.sub(
        r'(<div class="chart-table-wrap"[^>]*)\s+\bhidden\b',
        r"\1",
        page,
        flags=re.I,
    )
    return page


def render_tennis_results(*, view: str = "normal") -> str:
    tennis_page = _load_locked_tennis()
    _sync_tennis_slate()
    page, _meta = tennis_page.render_tennis_with_chrome("", which="results")
    if not page:
        raise RuntimeError("locked tennis results rendered empty")
    page = _rewrite_hub_paths(page)
    view_l = (view or "normal").strip().lower()
    is_chart = view_l in ("chart", "tabs", "markets", "tabbed")
    page = _ensure_results_view_toggle(page, chart=is_chart)
    if is_chart:
        original = page
        try:
            from team_results_charts import (
                apply_team_results_template,
                set_results_chart_source,
            )

            set_results_chart_source("TENNIS", page)
            page = apply_team_results_template(page, "TENNIS", view="chart")
        except Exception as e:
            print(f"[tennis_live] chart view failed: {e}", flush=True)
            page = original
        page = _ensure_tennis_chart_consensus(page, original)
        page = _strip_chart_match_cards(page)
        page = _ensure_tennis_chart_games_table(page)
        page = _ensure_results_view_toggle(page, chart=True)
    else:
        # Cards = UFC Cards UI: analytics + consensus + date-nav date-section cards.
        original = page
        page = _ensure_tennis_chart_consensus(page, original)
        page = _cards_view_analytics_board(page)
        page = _expand_result_cards(page)
        page = _ensure_results_view_toggle(page, chart=False)
    return page
