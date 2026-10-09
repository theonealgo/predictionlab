"""Live CFL pages — isolation engine at ~/Documents/Personal/cfl/, site chrome.

CFL only. Do not import other isolation sports from here.
Keep the MLB predictions/results template (no CFL-only tally chrome).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
ISO_HUB = ROOT / "iso_hub"
if str(ISO_HUB) not in sys.path:
    sys.path.insert(0, str(ISO_HUB))


def _nav_ctx() -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "soccer_enabled": True,
        "is_premium": False,
        "is_logged_in": False,
    }
    try:
        from flask_login import current_user

        ctx["is_logged_in"] = bool(getattr(current_user, "is_authenticated", False))
    except Exception:
        pass
    m = sys.modules.get("__main__")
    if m is None or not hasattr(m, "is_premium_user"):
        m = sys.modules.get("NHL77FINAL")
    if m is not None:
        try:
            if hasattr(m, "is_premium_user"):
                ctx["is_premium"] = bool(m.is_premium_user())
        except Exception:
            pass
        try:
            from flask import request
            host = (request.host or "").split(":")[0].lower()
            if host in ("127.0.0.1", "localhost"):
                ctx["is_premium"] = True
        except Exception:
            pass
        if hasattr(m, "SOCCER_ENABLED"):
            ctx["soccer_enabled"] = bool(m.SOCCER_ENABLED)
    return ctx


def _rewrite_iso_hrefs(html: str) -> str:
    if not html:
        return html
    html = re.sub(r"/cfl/results(?!-share)", "/cfl-results", html)
    html = html.replace("/cfl/predictions", "/cfl-picks")
    html = html.replace('href="/cfl/"', 'href="/cfl-picks"')
    html = html.replace("href='/cfl/'", "href='/cfl-picks'")
    html = html.replace('href="/cfl"', 'href="/cfl-picks"')
    # Do NOT rewrite /mlb-picks|/mlb-results here — that breaks the global
    # Sports/Results nav (MLB → CFL). Section-tab MLB leftovers are scoped
    # in _strip_mlb_content_from_cfl.
    html = html.replace("/static/img/cfl/montreal.png", "/static/img/cfl/montreal.svg")
    return html


def _dedupe_cfl_results_chrome(html: str) -> str:
    """One Predictions|Results row and one Cards|Chart row under the page title."""
    if not html:
        return html
    # Keep the first section-tabs; drop later duplicates.
    seen_tabs = False

    def _tabs(m: re.Match[str]) -> str:
        nonlocal seen_tabs
        if seen_tabs:
            return ""
        seen_tabs = True
        return m.group(0)

    html = re.sub(
        r'<div class="section-tabs">[\s\S]*?</div>\s*',
        _tabs,
        html,
        flags=re.I,
    )
    # Keep the first pl-view-toggle (+ optional style); drop later duplicates.
    seen_toggle = False

    def _toggle(m: re.Match[str]) -> str:
        nonlocal seen_toggle
        if seen_toggle:
            return ""
        seen_toggle = True
        return m.group(0)

    html = re.sub(
        r'<div class="pl-view-toggle\b[^>]*>[\s\S]*?</div>\s*'
        r'(?:<style>\.pl-view-toggle[\s\S]*?</style>\s*)?',
        _toggle,
        html,
        flags=re.I,
    )
    # Extra page-title H1 under the SEO title (MLB shell leftover).
    titles = list(
        re.finditer(
            r'<h1\b[^>]*class="[^"]*\bpage-title\b[^"]*"[^>]*>[\s\S]*?</h1>\s*',
            html,
            flags=re.I,
        )
    )
    if len(titles) > 1:
        for m in reversed(titles[1:]):
            html = html[: m.start()] + html[m.end() :]
    return html


_CFL_BEST_WIDTH_CSS = (
    '<style id="mlb-chart-best-width">'
    "section.pl-analytics{width:100%!important}"
    "section.pl-analytics .tally-grid{"
    "display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;"
    "width:100%!important}"
    "</style>"
)


def _cfl_chart_market() -> str:
    try:
        from flask import has_request_context, request

        if has_request_context():
            raw = (request.args.get("market") or "").strip().lower()
            if raw in ("moneyline", "spread", "totals"):
                return raw
    except Exception:
        pass
    return "moneyline"


def _stamp_cfl_best_width(html: str) -> str:
    if not html or "Best Performing Model" not in html:
        return html
    if "mlb-chart-best-width" in html or "ncaaf-chart-best-width" in html:
        return html
    if "</head>" in html:
        return html.replace("</head>", _CFL_BEST_WIDTH_CSS + "</head>", 1)
    return _CFL_BEST_WIDTH_CSS + html


def uncopy_cfl_model_spreads(html: str) -> str:
    """Prediction Lab and XSharp must not repeat each other or the book.

    Spreads and totals. A copied XSharp cell is cleared. Nothing is invented.
    """
    if not html or "data-pick-card" not in html:
        return html
    dash = {"", "—", "-", "–", "&mdash;", "N/A", "n/a"}

    def _blank_pair(tag: str, pl_name: str, xs_name: str, book_name: str) -> str:
        def _attr(name: str) -> str:
            am = re.search(rf'\b{name}="([^"]*)"', tag)
            return am.group(1) if am else ""

        pl, xs, book = _attr(pl_name), _attr(xs_name), _attr(book_name)
        if pl and xs and pl == xs and pl not in dash:
            tag = tag.replace(f'{xs_name}="{xs}"', f'{xs_name}="—"', 1)
            xs = "—"
        if book and book not in dash:
            if pl == book:
                tag = tag.replace(f'{pl_name}="{pl}"', f'{pl_name}="—"', 1)
            if xs == book:
                tag = tag.replace(f'{xs_name}="{xs}"', f'{xs_name}="—"', 1)
        return tag

    def _tag(m: re.Match[str]) -> str:
        tag = _blank_pair(m.group(0), "data-pl-spread", "data-xs-spread", "data-books-spread")
        return _blank_pair(tag, "data-pl-total", "data-xs-total", "data-books-total")

    html = re.sub(r"<div\b[^>]*\bdata-pick-card\b[^>]*>", _tag, html, flags=re.I)

    def _row(m: re.Match[str]) -> str:
        row = m.group(0)
        cells = re.findall(r'<td class="(val-[^"]+)">([^<]*)</td>', row)
        vals = {k: v.strip() for k, v in cells}
        pl, xs, book = vals.get("val-pl", ""), vals.get("val-xs", ""), vals.get("val-books", "")
        if pl and xs and pl == xs and pl not in dash:
            row = re.sub(r'(<td class="val-xs">)[^<]*(</td>)', r"\1—\2", row, count=1)
            xs = "—"
        if book and book not in dash:
            if pl == book:
                row = re.sub(r'(<td class="val-pl">)[^<]*(</td>)', r"\1—\2", row, count=1)
            if xs == book:
                row = re.sub(r'(<td class="val-xs">)[^<]*(</td>)', r"\1—\2", row, count=1)
        return row

    html = re.sub(
        r'<tr>\s*<td class="market-k">\s*Spread\s*</td>[\s\S]*?</tr>',
        _row,
        html,
        flags=re.I,
    )
    return re.sub(
        r'<tr>\s*<td class="market-k">\s*Total\s*</td>[\s\S]*?</tr>',
        _row,
        html,
        flags=re.I,
    )


_LATER_TIP = "This will be available later."
_BLANK_LINE = {"", "—", "-", "–", "&mdash;", "&ndash;", "N/A", "n/a", "NA", "na"}


def _cfl_later_info() -> str:
    """Same info button the site already uses. Hover text is fixed."""
    from html import escape

    tip = escape(_LATER_TIP, quote=True)
    return (
        'N/A <button type="button" class="pl-info-btn" '
        f'data-tip="{tip}" data-pl-info-tip="{tip}" title="{tip}" '
        f'aria-label="{tip}" aria-expanded="false" aria-haspopup="true">ⓘ</button>'
    )


def _cfl_plain_cell(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = text.replace("ⓘ", "").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _cfl_line_missing(raw: str) -> bool:
    if _LATER_TIP in (raw or ""):
        return False
    return _cfl_plain_cell(raw) in _BLANK_LINE or _cfl_plain_cell(raw) == ""


def mark_cfl_unavailable_lines(html: str) -> str:
    """Blank XSharp spread/total boxes say N/A. A real line is left alone."""
    if not html or "data-pick-card" not in html:
        return html
    info = _cfl_later_info()

    def _xs(m: re.Match[str]) -> str:
        row = m.group(0)
        cell = re.search(r'<td class="val-xs">([\s\S]*?)</td>', row, flags=re.I)
        if not cell or not _cfl_line_missing(cell.group(1)):
            return row
        if _LATER_TIP in cell.group(1):
            return row
        return (
            row[: cell.start()]
            + f'<td class="val-xs">{info}</td>'
            + row[cell.end() :]
        )

    html = re.sub(
        r'<tr>\s*<td class="market-k">\s*Spread\s*</td>[\s\S]*?</tr>',
        _xs,
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<tr>\s*<td class="market-k">\s*Total\s*</td>[\s\S]*?</tr>',
        _xs,
        html,
        flags=re.I,
    )
    if "pl-info-btn" not in html:
        return html
    if 'id="pl-info-tips-css"' not in html:
        link = (
            '<link rel="stylesheet" href="/static/css/pl-info-tips.css" '
            'id="pl-info-tips-css">'
        )
        if re.search(r"</head\s*>", html, flags=re.I):
            html = re.sub(r"</head\s*>", link + "\n</head>", html, count=1, flags=re.I)
        else:
            html = link + html
    if "pl-info-tips.js" not in html:
        script = '<script src="/static/js/pl-info-tips.js" defer></script>'
        if re.search(r"</body\s*>", html, flags=re.I):
            html = re.sub(r"</body\s*>", script + "\n</body>", html, count=1, flags=re.I)
        else:
            html = html + script
    return html


def restore_cfl_stored_xsharp(html: str) -> str:
    """Keep the odds table and the XSharp projected-score row.

    The stored model line and stored projected score are shown. Nothing new
    is calculated. A blank XSharp cell is not left in place. Books stay off.
    """
    if not html or "data-pick-card" not in html:
        return html
    from html import escape

    def _open_attr(tag: str, name: str) -> str:
        found = re.search(rf'\b{name}="([^"]*)"', tag)
        return found.group(1) if found else ""

    def _clear_blank_or_copy(tag: str, xs_name: str, pl_name: str) -> str:
        if not re.search(rf'\b{xs_name}="', tag):
            return tag
        xs_txt = _cfl_plain_cell(_open_attr(tag, xs_name))
        pl_txt = _cfl_plain_cell(_open_attr(tag, pl_name))
        if xs_txt in _BLANK_LINE or (pl_txt and xs_txt == pl_txt):
            return re.sub(rf'\b{xs_name}="[^"]*"', f'{xs_name}=""', tag, count=1)
        return tag

    def _ensure_header(card: str) -> str:
        table = re.search(r'class="odds-pricing-table"[\s\S]*?</table>', card, flags=re.I)
        if not table or re.search(r"\bcol-xs\b", table.group(0), flags=re.I):
            return card
        block = table.group(0)
        updated = re.sub(
            r'(<th\b[^>]*\bcol-pl\b[^>]*>[\s\S]*?</th>)',
            r'\1<th class="col-xs">XSharp</th>',
            block,
            count=1,
            flags=re.I,
        )
        if updated == block:
            updated = re.sub(
                r"(</th>)(\s*</tr>)",
                r'\1<th class="col-xs">XSharp</th>\2',
                block,
                count=1,
                flags=re.I,
            )
        return card[: table.start()] + updated + card[table.end() :]

    def _ensure_cell(match: re.Match[str]) -> str:
        row = match.group(0)
        pl = re.search(r'<td class="val-pl">([\s\S]*?)</td>', row, flags=re.I)
        if not pl or _cfl_line_missing(pl.group(1)):
            return row
        cell = f'<td class="val-xs">{pl.group(1)}</td>'
        xs = re.search(r'<td class="val-xs">([\s\S]*?)</td>', row, flags=re.I)
        if not xs:
            return row[: pl.end()] + cell + row[pl.end() :]
        if _cfl_line_missing(xs.group(1)):
            return row[: xs.start()] + cell + row[xs.end() :]
        return row

    def _ensure_proj(card: str) -> str:
        shown = re.search(
            r'class="proj-model pl"[\s\S]*?class="proj-val">([^<]*)</span>',
            card,
            flags=re.I,
        )
        stored_html = shown.group(1) if shown else ""
        if _cfl_line_missing(stored_html):
            attr = re.search(r'\bdata-pl-proj="([^"]*)"', card)
            if not attr or _cfl_line_missing(attr.group(1)):
                return card
            stored_html = attr.group(1)
        stored_text = _cfl_plain_cell(stored_html)
        if not stored_text or stored_text in _BLANK_LINE:
            return card
        row_html = (
            '<div class="proj-row"><span class="proj-model xs">XSharp</span>'
            f'<span class="proj-val">{stored_html}</span></div>'
        )
        xs_row = re.search(
            r'<div class="proj-row">\s*<span class="proj-model xs">[\s\S]*?</div>',
            card,
            flags=re.I,
        )
        if xs_row:
            xs_val = re.search(r'class="proj-val">([^<]*)</span>', xs_row.group(0), flags=re.I)
            xs_txt = _cfl_plain_cell(xs_val.group(1) if xs_val else "")
            if not xs_txt or xs_txt in _BLANK_LINE:
                card = card[: xs_row.start()] + row_html + card[xs_row.end() :]
        else:
            pl_row = re.search(
                r'<div class="proj-row">\s*<span class="proj-model pl">[\s\S]*?</div>',
                card,
                flags=re.I,
            )
            if pl_row:
                card = card[: pl_row.end()] + row_html + card[pl_row.end() :]
        attr_val = escape(stored_text, quote=True)
        if re.search(r'\bdata-xs-proj="', card):
            current = re.search(r'\bdata-xs-proj="([^"]*)"', card)
            current_txt = _cfl_plain_cell(current.group(1) if current else "")
            if not current_txt or current_txt in _BLANK_LINE:
                card = re.sub(
                    r'\bdata-xs-proj="[^"]*"',
                    f'data-xs-proj="{attr_val}"',
                    card,
                    count=1,
                )
        else:
            card = re.sub(
                r"(<div\b[^>]*\bdata-pick-card\b[^>]*)>",
                rf'\1 data-xs-proj="{attr_val}">',
                card,
                count=1,
                flags=re.I,
            )
        return card

    def _card(card: str) -> str:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", card, flags=re.I)
        if open_m:
            tag = _clear_blank_or_copy(open_m.group(1), "data-xs-spread", "data-pl-spread")
            tag = _clear_blank_or_copy(tag, "data-xs-total", "data-pl-total")
            card = tag + card[open_m.end() :]
        card = _ensure_header(card)
        card = re.sub(
            r'<tr>\s*<td class="market-k">\s*Spread\s*</td>[\s\S]*?</tr>',
            _ensure_cell,
            card,
            flags=re.I,
        )
        card = re.sub(
            r'<tr>\s*<td class="market-k">\s*Total\s*</td>[\s\S]*?</tr>',
            _ensure_cell,
            card,
            flags=re.I,
        )
        return _ensure_proj(card)

    out: list[str] = []
    pos = 0
    for match in re.finditer(r"<div\b[^>]*\bdata-pick-card\b[^>]*>", html, flags=re.I):
        if match.start() < pos:
            continue
        end = _cfl_div_end(html, match.start())
        if end < 0:
            continue
        out.append(html[pos : match.start()])
        out.append(_card(html[match.start() : end]))
        pos = end
    out.append(html[pos:])
    return "".join(out)


def _cfl_et_day(raw: Any) -> str:
    """Calendar day in Eastern Time. A date-only string is already that day."""
    s = str(raw or "").strip()
    if not s:
        return ""
    head = s[:10]
    if len(head) == 10 and head[4] == "-" and "T" not in s[:11]:
        return head
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except ValueError:
        return head if len(head) == 10 and head[4] == "-" else ""


def _cfl_ml_side(home_ml: Any, away_ml: Any) -> str | None:
    """HOME/AWAY from posted American odds. The more negative price is the favorite."""
    try:
        home = int(home_ml)
        away = int(away_ml)
    except (TypeError, ValueError):
        return None
    if home == away:
        return None
    return "HOME" if home < away else "AWAY"


def _cfl_name_side(name: str, home: str, away: str) -> str | None:
    n = (name or "").strip().lower()
    h = (home or "").strip().lower()
    a = (away or "").strip().lower()
    if not n:
        return None
    if h and (n == h or n in h or h in n):
        return "HOME"
    if a and (n == a or n in a or a in n):
        return "AWAY"
    return None


def _cfl_grade(side: str | None, home_score: int, away_score: int) -> str | None:
    if side not in ("HOME", "AWAY"):
        return None
    if home_score == away_score:
        return "PUSH"
    winner = "HOME" if home_score > away_score else "AWAY"
    return "WIN" if side == winner else "LOSS"


def _cfl_record(grades: list[str]) -> tuple[str, float | None, int]:
    w = sum(1 for g in grades if g == "WIN")
    l = sum(1 for g in grades if g == "LOSS")
    p = sum(1 for g in grades if g == "PUSH")
    decided = w + l
    pct = (100.0 * w / decided) if decided else None
    rec = f"{w}-{l}" + (f"-{p}" if p else "")
    return rec, pct, decided


def cfl_pl_vs_books_signals(
    rows: list[dict[str, Any]],
) -> dict[str, tuple[str, str, float | None, int]]:
    """Past-window Books / PL / agree / disagree records. Empty grades stay 0-0."""
    books: list[str] = []
    pl: list[str] = []
    agree: list[str] = []
    disagree: list[str] = []
    for row in rows:
        b = row.get("book_side")
        pside = row.get("pl_side")
        bg = row.get("book_grade")
        pg = row.get("pl_grade")
        if b in ("HOME", "AWAY") and bg in ("WIN", "LOSS", "PUSH"):
            books.append(bg)
        if pside in ("HOME", "AWAY") and pg in ("WIN", "LOSS", "PUSH"):
            pl.append(pg)
        if b in ("HOME", "AWAY") and pside in ("HOME", "AWAY"):
            if b == pside and bg in ("WIN", "LOSS", "PUSH"):
                agree.append(bg)
            elif b != pside and pg in ("WIN", "LOSS", "PUSH"):
                disagree.append(pg)
    return {
        "books": ("Books favorite", *_cfl_record(books)),
        "pl": ("PL favorite", *_cfl_record(pl)),
        "agree": ("PL and Books agree", *_cfl_record(agree)),
        "disagree": ("PL vs Books disagree", *_cfl_record(disagree)),
    }


def cfl_pl_vs_books_text(
    book_side: str | None,
    pl_side: str | None,
    signals: dict[str, tuple[str, str, float | None, int]],
) -> str | None:
    """Best Past-30 signal for this card. No text when this card has neither side."""
    candidates: list[tuple[str, str, float | None, int]] = []
    if book_side in ("HOME", "AWAY"):
        candidates.append(signals["books"])
    if pl_side in ("HOME", "AWAY"):
        candidates.append(signals["pl"])
    if book_side in ("HOME", "AWAY") and pl_side in ("HOME", "AWAY"):
        key = "agree" if book_side == pl_side else "disagree"
        candidates.append(signals[key])
    if not candidates:
        return None
    graded = [c for c in candidates if c[3] > 0 and c[2] is not None]
    if graded:
        graded.sort(key=lambda c: (-(c[2] or 0.0), -c[3], c[0]))
        label, rec, pct, _decided = graded[0]
    else:
        label, rec, pct, _decided = candidates[0]
    pct_s = f"{pct:.0f}%" if pct is not None else "—"
    return f"{label}: {rec} ({pct_s}) — Past 30 Days"


def _cfl_official_context() -> tuple[dict[str, tuple[str, str, float | None, int]] | None, dict]:
    """Read posted CFL lines and locked picks. Does not write games or invent prices."""
    import sqlite3
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    fetch = sys.modules.get("cfl_fetch_mod")
    if fetch is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "cfl_fetch_mod",
            ROOT / "engines" / "cfl" / "engine" / "fetch.py",
        )
        if spec is None or spec.loader is None:
            return None, {}
        fetch = importlib.util.module_from_spec(spec)
        sys.modules["cfl_fetch_mod"] = fetch
        spec.loader.exec_module(fetch)
    try:
        games = fetch.fetch_official_all_games(use_cache=True)
    except Exception:
        return None, {}
    if not games:
        return None, {}
    db = Path(
        __import__("os").environ.get("CFL_SANDBOX_DB")
        or (ROOT / "engines" / "cfl" / "database" / "cfl_sandbox.db")
    )
    preds: dict[str, dict[str, Any]] = {}
    by_match: dict[tuple[str, str, str], dict[str, Any]] = {}
    if db.is_file():
        try:
            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            for row in con.execute(
                """
                SELECT g.game_id, g.game_date, g.home_team, g.away_team,
                       p.home_win_prob, p.pick_ml
                FROM cfl_games g
                JOIN cfl_predictions p ON p.game_id = g.game_id
                """
            ):
                item = dict(row)
                preds[str(item.get("game_id") or "")] = item
                day = _cfl_et_day(item.get("game_date"))
                home = (item.get("home_team") or "").strip().lower()
                away = (item.get("away_team") or "").strip().lower()
                if day and home and away:
                    by_match[(home, away, day)] = item
            con.close()
        except sqlite3.Error:
            preds = {}
            by_match = {}
    et = ZoneInfo("America/New_York")
    today = datetime.now(et).date()
    cut = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    today_s = today.strftime("%Y-%m-%d")
    rows: list[dict[str, Any]] = []
    books: dict[tuple[str, str, str], str] = {}
    for game in games:
        home = (game.get("home_team") or "").strip()
        away = (game.get("away_team") or "").strip()
        day = _cfl_et_day(game.get("game_date"))
        if not home or not away or not day:
            continue
        side = _cfl_ml_side(game.get("book_home_moneyline"), game.get("book_away_moneyline"))
        if side:
            books[(home.lower(), away.lower(), day)] = side
        if not (cut <= day < today_s):
            continue
        if str(game.get("status") or "").lower() not in {"complete", "final", "closed"}:
            continue
        try:
            hs = int(game["home_score"])
            aws = int(game["away_score"])
        except (TypeError, ValueError, KeyError):
            continue
        pred = preds.get(str(game.get("game_id") or "")) or by_match.get(
            (home.lower(), away.lower(), day)
        )
        pl_side = None
        if pred:
            pl_side = _cfl_name_side(str(pred.get("pick_ml") or ""), home, away)
            if pl_side is None and pred.get("home_win_prob") is not None:
                pl_side = "HOME" if float(pred["home_win_prob"]) >= 0.5 else "AWAY"
        rows.append(
            {
                "book_side": side,
                "pl_side": pl_side,
                "book_grade": _cfl_grade(side, hs, aws),
                "pl_grade": _cfl_grade(pl_side, hs, aws),
            }
        )
    return cfl_pl_vs_books_signals(rows), books


def stamp_cfl_pl_vs_books(
    html: str,
    *,
    signals: dict[str, tuple[str, str, float | None, int]] | None = None,
    books: dict | None = None,
    today: str = "",
) -> str:
    """Past-30 PL vs Books record on today and later cards.

    Uses posted moneylines and locked picks only. Yesterday's cards are left
    as they are. A game with no posted moneyline does not get a book side.
    """
    if not html or "data-pick-card" not in html:
        return html
    from datetime import datetime
    from html import escape
    from zoneinfo import ZoneInfo

    if signals is None or books is None:
        loaded_signals, loaded_books = _cfl_official_context()
        if signals is None:
            if loaded_signals is None:
                return html
            signals = loaded_signals
        if books is None:
            books = loaded_books
    books = books or {}
    if not today:
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
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

        def _attr(name: str, tag: str = tag) -> str:
            am = re.search(rf'\b{name}="([^"]*)"', tag)
            return (am.group(1) if am else "").strip()

        day = _cfl_et_day(_attr("data-time"))
        home, away = _attr("data-home"), _attr("data-away")
        # Historical cards stay untouched.
        if not day or day < today or not home or not away:
            out.append(stack)
            continue
        book_side = books.get((home.lower(), away.lower(), day))
        pl_side = _cfl_name_side(_attr("data-pick"), home, away)
        text = cfl_pl_vs_books_text(book_side, pl_side, signals)
        if not text:
            out.append(stack)
            continue
        stack = re.sub(
            r'<div class="line-chip pl-vs-books-chip">[\s\S]*?</div>\s*</div>',
            "",
            stack,
            count=1,
            flags=re.I,
        )
        chip = (
            '<div class="line-chip pl-vs-books-chip">'
            '<div class="line-chip-label">PL vs Books</div>'
            f'<div class="line-chip-val">{escape(text)}</div></div>'
        )
        if '<div class="lines-strip">' in stack:
            stack = stack.replace('<div class="lines-strip">', '<div class="lines-strip">' + chip, 1)
        else:
            stack = re.sub(
                r'(<div class="(?:odds-pricing-section|card-details|pick-conf-bar)\b)',
                '<div class="lines-strip">' + chip + "</div>\n" + r"\1",
                stack,
                count=1,
                flags=re.I,
            )
        out.append(stack)
    return "".join(out)


def cfl_share_drawn_count() -> int:
    """How many pick cards the CFL share image actually draws. Zero if unknown."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from mlb_team_shell import _cfl_cards, _cfl_share_rows, _group_by_date

        cards, render = _cfl_cards("picks")
        grouped = _group_by_date(render, cards)
        dates = [d for d in grouped if d != "undated"]
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        rows, _slate = _cfl_share_rows(cards, render, dates, today)
        return len(rows)
    except Exception:
        return 0


def stamp_cfl_share_pick_count(html: str) -> str:
    """Put the drawn pick count on the existing share card. Do not invent a count."""
    if not html or "social-export-wrap" not in html:
        return html
    # Already stamped when the page was drawn. Recounting reloads every card.
    if re.search(r'data-share-picks="\d+"', html):
        return html
    n = cfl_share_drawn_count()
    if n < 1:
        return html
    if re.search(r'data-share-picks="\d+"', html):
        return re.sub(r'data-share-picks="\d+"', f'data-share-picks="{n}"', html, count=1)
    return re.sub(
        r'(<div\b[^>]*\bclass="[^"]*\bsocial-export-wrap\b[^"]*")',
        rf'\1 data-share-picks="{n}"',
        html,
        count=1,
        flags=re.I,
    )


def rewrite_cfl_share_hrefs(html: str) -> str:
    """Share-icon targets only. This sport's public path, not localhost or MLB."""
    if not html or "share-icon" not in html:
        return html
    from urllib.parse import quote, unquote

    def _one(m: re.Match[str]) -> str:
        tag = m.group(0)
        if not re.search(r"localhost|127\.0\.0\.1|mlb-picks|mlb-results", tag, flags=re.I):
            return tag
        decoded = unquote(tag).lower()
        if "cfl-results" in decoded or "mlb-results" in decoded:
            public = "https://predictionlab.io/cfl-results"
        else:
            public = "https://predictionlab.io/cfl-picks"
        encoded = quote(public, safe="")
        return re.sub(
            r"((?:[?&](?:url|u|canonicalUrl|text)=))[^\"&\s]*",
            lambda mm: mm.group(1) + encoded,
            tag,
            count=1,
            flags=re.I,
        )

    return re.sub(
        r'<a\b[^>]*\bclass="[^"]*\bshare-icon\b[^"]*"[^>]*>',
        _one,
        html,
        flags=re.I,
    )


def _finalize_cfl_html(html: str) -> str:
    """Last-pass CFL HTML: strip MLB leftovers, restore global MLB nav, vendor labels."""
    html = _strip_mlb_content_from_cfl(html)
    html = _rewrite_iso_hrefs(html)
    html = rewrite_cfl_share_hrefs(html)
    html = _restore_global_nav_mlb_links(html)
    html = _stamp_cfl_best_width(html)
    html = restore_cfl_full_side_names(html)
    return _strip_vendor_labels(html)


def _strip_vendor_labels(html: str) -> str:
    if not html:
        return html
    html = re.sub(r"\bTheOddsAPI\b", "", html, flags=re.I)
    html = re.sub(r"\bThe Odds API\b", "", html, flags=re.I)
    html = re.sub(r"Prob source:\s*[^<]+", "", html, flags=re.I)
    html = re.sub(r"Elo \+ market blend", "Model blend", html, flags=re.I)
    html = re.sub(r"\bElo trained on\b[^.<]*", "", html, flags=re.I)
    html = re.sub(r"\bisolation\b", "", html, flags=re.I)
    html = html.replace('data-sandbox-sport="cfl"', 'data-sport="cfl"')
    html = html.replace("data-sandbox-sport='cfl'", "data-sport='cfl'")
    html = html.replace('id="sandbox-unlock-details"', 'id="pl-unlock-details"')
    return html


_PL2_HEADER_RE = re.compile(
    r'<header\b[^>]*\bpl2-header\b[^>]*>[\s\S]*?</header>\s*',
    flags=re.I,
)

# research_header.html ships header + ACCOUNT/NAV dropdown <script>. Stripping
# only <header> leaves those scripts; reinjecting chrome doubles them and the
# duplicate document-click handlers cancel the Sports/Models/Results menus.
_RESEARCH_NAV_SCRIPT_RE = re.compile(
    r"<script\b[^>]*>\s*/\*\s*ACCOUNT MENU:[\s\S]*?NAV DROPDOWNS:[\s\S]*?</script>\s*",
    flags=re.I,
)


def _strip_all_pl2_headers(html: str) -> str:
    """Remove every site chrome header (attribute order / extra classes safe)."""
    if not html:
        return html
    return _PL2_HEADER_RE.sub("", html)


def _strip_research_nav_scripts(html: str) -> str:
    """Drop orphaned research_header dropdown scripts (safe before re-inject)."""
    if not html:
        return html
    return _RESEARCH_NAV_SCRIPT_RE.sub("", html)


def _dedupe_pl2_headers(html: str) -> str:
    """Keep the first pl2-header only — chart paths often inject twice."""
    if not html:
        return html
    matches = list(_PL2_HEADER_RE.finditer(html))
    if len(matches) <= 1:
        return html
    # Drop later duplicates (reverse so offsets stay valid).
    for m in reversed(matches[1:]):
        html = html[: m.start()] + html[m.end() :]
    return html


def _dedupe_research_nav_scripts(html: str) -> str:
    """Keep a single ACCOUNT/NAV dropdown script block."""
    if not html:
        return html
    matches = list(_RESEARCH_NAV_SCRIPT_RE.finditer(html))
    if len(matches) <= 1:
        return html
    for m in reversed(matches[1:]):
        html = html[: m.start()] + html[m.end() :]
    return html


def _inject_chrome_into_page(html: str, *, extra_css: list[str] | None = None) -> str:
    from flask import render_template

    # Strip header + its leftover dropdown scripts so we inject one working set.
    html = _strip_all_pl2_headers(html)
    html = _strip_research_nav_scripts(html)

    chrome = render_template("includes/picks_nav_chrome.html", **_nav_ctx())
    css_tags = [
        '<link rel="stylesheet" href="/static/css/research-theme.css">',
        '<link rel="stylesheet" href="/static/css/picks-nav-overrides.css">',
        '<link rel="stylesheet" href="/static/css/sports-chrome.css" media="print" onload="this.media=&quot;all&quot;">',
    ]
    for href in extra_css or []:
        tag = f'<link rel="stylesheet" href="{href}">'
        if tag not in css_tags:
            css_tags.append(tag)
    # Avoid stacking duplicate chrome CSS when the shell already linked them.
    for tag in list(css_tags):
        href_m = re.search(r'href="([^"]+)"', tag)
        if href_m and href_m.group(1) in html:
            css_tags.remove(tag)
    css_html = "\n".join(css_tags)
    if css_html:
        css_html += '<script src="/static/js/pl-header-logo.js" defer></script>'
    elif 'pl-header-logo.js' not in html:
        css_html = '<script src="/static/js/pl-header-logo.js" defer></script>'
    if css_html:
        if re.search(r"</head\s*>", html, flags=re.I):
            html = re.sub(r"</head\s*>", css_html + "</head>", html, count=1, flags=re.I)
        else:
            html = css_html + html

    def _body_repl(m: re.Match[str]) -> str:
        tag = m.group(0)
        if "research-site" not in tag:
            if re.search(r'\bclass="', tag, flags=re.I):
                tag = re.sub(
                    r'\bclass="([^"]*)"',
                    r'class="\1 research-site"',
                    tag,
                    count=1,
                    flags=re.I,
                )
            else:
                tag = tag[:-1] + ' class="research-site">'
        if "data-sport=" not in tag and "data-sandbox-sport=" not in tag:
            tag = tag[:-1] + ' data-sport="cfl">'
        return tag + chrome

    if re.search(r"<body\b", html, flags=re.I):
        html = re.sub(r"<body\b[^>]*>", _body_repl, html, count=1, flags=re.I)
    else:
        html = chrome + html
    html = _dedupe_pl2_headers(html)
    return _dedupe_research_nav_scripts(html)


def _cfl_view_toggle(active: str = "normal") -> str:
    n_cls = "active" if active == "normal" else ""
    c_cls = "active" if active == "chart" else ""
    return (
        '<div class="pl-view-toggle" role="navigation" aria-label="Results view">'
        f'<a class="pl-view-btn {n_cls}" href="/cfl-results">Cards</a>'
        f'<a class="pl-view-btn {c_cls}" href="/cfl-results?view=chart">Chart</a>'
        "</div>"
        "<style>.pl-view-toggle{display:flex;gap:8px;margin:12px 0 18px;flex-wrap:wrap}"
        ".pl-view-btn{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;"
        "border:1px solid #dbe3ee;background:#fff;color:#0c1e3a;font-weight:700;font-size:.85rem;"
        "text-decoration:none}.pl-view-btn.active{background:#0c1e3a;color:#fff;border-color:#0c1e3a}"
        "</style>"
    )


def _gate_cfl_paid_markets(html: str) -> str:
    if not html:
        return html
    locked = (
        '<div class="odds-pricing-locked" style="padding:14px;font-size:0.84em;text-align:center;">'
        "🔒 Lines &amp; projections locked. "
        '<a href="/login">Log in</a> or <a href="/plans">unlock premium</a>.</div>'
    )
    html = re.sub(
        r'<div class="line-chip"><div class="line-chip-label">Model spread</div>'
        r'<div class="line-chip-val">[\s\S]*?</div></div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="line-chip"><div class="line-chip-label">Model total</div>'
        r'<div class="line-chip-val">[\s\S]*?</div></div>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="odds-pricing-section">[\s\S]*?<div class="odds-extras-footer">',
        locked + '<div class="odds-extras-footer">',
        html,
        flags=re.I,
    )

    def _strip_paid_attrs(m: re.Match[str]) -> str:
        tag = m.group(0)
        for attr in (
            "data-pl-spread",
            "data-xs-spread",
            "data-pl-proj",
            "data-xs-proj",
            "data-pl-total",
            "data-xs-total",
        ):
            tag = re.sub(rf'\s{attr}="[^"]*"', "", tag, flags=re.I)
        return tag

    html = re.sub(
        r"<div\b[^>]*\bdata-pick-card\b[^>]*>",
        _strip_paid_attrs,
        html,
        flags=re.I,
    )
    return html


def _strip_mlb_content_from_cfl(html: str) -> str:
    """Keep the MLB shell and CFL write-up. Drop leftover MLB preview chrome."""
    if not html:
        return html
    html = re.sub(
        r'<nav class="[^"]*preview-hub[^"]*"[^>]*aria-label="MLB previews"[^>]*>([\s\S]*?)</nav>',
        lambda m: (
            m.group(1)
            if re.search(r"How These AI Picks|What to Expect", m.group(1), re.I)
            else ""
        ),
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<nav class="[^"]*preview-hub[^"]*"[^>]*>[\s\S]*?</nav>',
        lambda m: "" if re.search(r"Today(?:'s|&#x27;s) MLB|aria-label=\"MLB", m.group(0), re.I) else m.group(0),
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<h2[^>]*>\s*Today(?:\'s|&#x27;s) MLB previews\s*</h2>[\s\S]*?(?=<h2\b|<nav\b|<footer\b|$)',
        "",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r'<li><a href="[^"]*">20\d{2}-\d{2}-\d{2}</a></li>\s*',
        "",
        html,
    )
    # Stray empty-state from the MLB shell when CFL slate is present.
    html = re.sub(
        r'<div class="no-data">\s*No predictions available for MLB\s*</div>\s*',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"No predictions available for MLB",
        "",
        html,
        flags=re.I,
    )
    # Cards|Chart / Predictions|Results tabs only — never the global Sports nav.
    def _tabs_mlb_to_cfl(m: re.Match[str]) -> str:
        block = m.group(0)
        block = block.replace('href="/mlb-results"', 'href="/cfl-results"')
        block = block.replace("href='/mlb-results'", "href='/cfl-results'")
        block = block.replace('href="/mlb-results?', 'href="/cfl-results?')
        block = block.replace('href="/mlb-picks"', 'href="/cfl-picks"')
        block = block.replace("href='/mlb-picks'", "href='/cfl-picks'")
        return block

    html = re.sub(
        r'<div class="section-tabs\b[\s\S]*?</div>',
        _tabs_mlb_to_cfl,
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="pl-view-toggle\b[\s\S]*?</div>',
        _tabs_mlb_to_cfl,
        html,
        flags=re.I,
    )
    html = html.replace("{l:'MLB',h:'/cfl-results'}", "{l:'MLB',h:'/mlb-results'}")
    html = html.replace('{l:"MLB",h:"/cfl-results"}', '{l:"MLB",h:"/mlb-results"}')
    html = html.replace(
        'content="https://predictionlab.io/mlb-results"',
        'content="https://predictionlab.io/cfl-results"',
    )
    html = html.replace(
        'href="https://predictionlab.io/mlb-results"',
        'href="https://predictionlab.io/cfl-results"',
    )
    html = html.replace("localhost/mlb-results", "localhost/cfl-results")
    html = html.replace("%2Fmlb-results", "%2Fcfl-results")
    # Meta / social leftovers from the MLB template.
    html = re.sub(
        r'(property="og:title" content=")MLB([^"]*)(")',
        r"\1CFL\2\3",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(name="twitter:title" content=")MLB([^"]*)(")',
        r"\1CFL\2\3",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(content=")Daily AI-powered MLB',
        r"\1Daily AI-powered CFL",
        html,
        flags=re.I,
    )
    html = re.sub(
        r">MLB Predictions Today<",
        ">CFL Predictions Today<",
        html,
        flags=re.I,
    )
    return drop_cfl_mlb_card_markers(html)


def drop_cfl_mlb_card_markers(html: str) -> str:
    """CFL pages keep CFL cards. Drop MLB sport markers and any MLB game card."""
    if not html:
        return html
    html = re.sub(
        r"data-sport=(['\"])mlb\1",
        r"data-sport=\1cfl\1",
        html,
        flags=re.I,
    )
    try:
        from mlb_team_shell import _MLB_TEAMS
    except Exception:
        return html
    clubs = tuple(name.lower() for name in _MLB_TEAMS if name)

    def _mlb_card(card: str) -> bool:
        if re.search(r"data-league=(['\"])mlb\1", card, flags=re.I):
            return True
        names = re.findall(r'data-(?:home|away)="([^"]*)"', card, flags=re.I)
        return any(
            any(club == name.lower() or club in name.lower() for club in clubs)
            for name in names
        )

    if "data-pick-card" not in html:
        return html
    out: list[str] = []
    pos = 0
    for match in re.finditer(r"<div\b[^>]*\bdata-pick-card\b[^>]*>", html, flags=re.I):
        if match.start() < pos:
            continue
        end = _cfl_div_end(html, match.start())
        if end < 0:
            continue
        card = html[match.start() : end]
        out.append(html[pos : match.start()])
        if not _mlb_card(card):
            out.append(card)
        pos = end
    out.append(html[pos:])
    return "".join(out)


def _ensure_mlb_copy_all_markets(html: str) -> str:
    """Copy All pastes Moneyline + Spread + Totals, same as the sandbox CFL page."""
    if not html or "function copyVisiblePicks" not in html:
        return html
    if "pl-copy-all-markets" in html:
        return html
    script = """
<script id="pl-copy-all-markets">
(function(){
  function _dash(v){
    v = (v == null ? "" : String(v)).trim();
    return v || "—";
  }
  function _plain(html){
    return String(html || "").replace(/<[^>]+>/g, " ").replace(/\\s+/g, " ").trim();
  }
  function copyVisiblePicks(btn){
    var sec = (typeof _visibleSection === "function")
      ? _visibleSection()
      : document.querySelector(".date-section.visible");
    if(!sec) return;
    var stacks = sec.querySelectorAll("[data-pick-card]");
    var dateLabel = (sec.id || "").replace("date-","");
    var icon = (typeof sportIcon !== "undefined" ? sportIcon : "");
    var name = (typeof sportName !== "undefined" ? sportName : "CFL");
    var spreadFn = (typeof _spreadCell === "function")
      ? _spreadCell
      : function(st, attr){ return st.getAttribute(attr) || ""; };
    var plProjFn = (typeof _plProjDisplay === "function")
      ? _plProjDisplay
      : function(st){ return st.getAttribute("data-pl-proj") || ""; };
    var xsProjFn = (typeof _xsProjDisplay === "function")
      ? _xsProjDisplay
      : function(st){ return st.getAttribute("data-xs-proj") || ""; };
    var lines = [icon + " " + name + " AI Picks — " + dateLabel];

    lines.push("");
    lines.push("MONEYLINE");
    stacks.forEach(function(st){
      var away = st.getAttribute("data-away") || "";
      var home = st.getAttribute("data-home") || "";
      var time = st.getAttribute("data-time") || "";
      var pick = st.getAttribute("data-pick") || "";
      var conf = st.getAttribute("data-conf") || "";
      var result = st.getAttribute("data-result") || "";
      var line = away + " @ " + home;
      if(time) line += " (" + time + ")";
      line += " — Pick: " + pick;
      if(conf) line += " (" + conf + "%)";
      if(result === "WON") line += " ✅";
      else if(result === "LOST") line += " ❌";
      lines.push(line);
    });

    lines.push("");
    lines.push("SPREAD / RUN LINE");
    stacks.forEach(function(st){
      var away = st.getAttribute("data-away") || "";
      var home = st.getAttribute("data-home") || "";
      var pl = _dash(spreadFn(st, "data-pl-spread", "val-pl", ["pl run line","pl spread","model spread","prediction lab run line","prediction lab spread"]));
      var xs = _dash(spreadFn(st, "data-xs-spread", "val-xs", ["xsharp run line","xsharp spread"]));
      lines.push(away + " @ " + home + " — PL: " + pl + " | XSharp: " + xs);
    });

    lines.push("");
    lines.push("TOTALS");
    stacks.forEach(function(st){
      var away = st.getAttribute("data-away") || "";
      var home = st.getAttribute("data-home") || "";
      var pl = _dash(_plain(plProjFn(st)));
      var xs = _dash(_plain(xsProjFn(st)));
      lines.push(away + " @ " + home + " — PL: " + pl + " | XSharp: " + xs);
    });

    lines.push("");
    lines.push("via predictionlab.io");
    var text = lines.join("\\n");
    var done = function(){
      if(!btn) return;
      var o = btn.textContent;
      btn.textContent = "✓ Copied";
      btn.classList.add("copied");
      setTimeout(function(){ btn.textContent = o; btn.classList.remove("copied"); }, 1500);
    };
    if(navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(text).then(done).catch(function(){
        if(typeof _fallbackCopy === "function") _fallbackCopy(text, done);
      });
    } else if(typeof _fallbackCopy === "function"){
      _fallbackCopy(text, done);
    }
  }
  window.copyVisiblePicks = copyVisiblePicks;
})();
</script>
"""
    if "</body>" in html:
        return html.replace("</body>", script + "\n</body>", 1)
    return html + script


def _open_cfl_cards(html: str) -> str:
    """Match the expanded team-sport template so models are visible."""
    if not html:
        return html
    html = re.sub(
        r'class="game-card pick-card(?! is-expanded)',
        'class="game-card pick-card is-expanded',
        html,
    )
    html = re.sub(
        r'(<div class="card-details"[^>]*?)\s+hidden\b',
        r"\1",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(class="view-details-btn"[^>]*aria-expanded=")false(")',
        r"\1true\2",
        html,
    )
    html = re.sub(
        r'(<button type="button" class="view-details-btn"[^>]*>)\s*View [Dd]etails',
        r"\1Less details",
        html,
    )
    return html


def _cfl_div_end(html: str, start: int) -> int:
    """Index just after the div that opens at start. -1 if it does not close."""
    i = start
    depth = 0
    n = len(html)
    while i < n:
        if html.startswith("<div", i) and (i + 4 >= n or html[i + 4] in " >\t\r\n/"):
            depth += 1
            close = html.find(">", i)
            if close < 0:
                return -1
            i = close + 1
            continue
        if html.startswith("</div>", i):
            depth -= 1
            i += 6
            if depth == 0:
                return i
            continue
        i += 1
    return -1


def strip_cfl_pick_books(html: str, *, keep_pl_vs_books: bool = False) -> str:
    """Take Books off CFL pick cards. Do not leave a placeholder.

    A page that already has the Past-30 PL vs Books chip keeps that chip.
    Stripping it forces another CFL.ca read on the picks request.
    """
    if not html or "data-pick-card" not in html:
        return html
    chip_label = r"Books(?:\s+[A-Za-z]+)?" if keep_pl_vs_books else r"(?:Books(?:\s+[A-Za-z]+)?|PL vs Books)"

    def _card(stack: str) -> str:
        stack = re.sub(
            r'\sdata-books-[a-z0-9-]+="[^"]*"',
            "",
            stack,
            flags=re.I,
        )
        stack = re.sub(
            r'<div\b[^>]*\bclass="[^"]*\bface-books-ml\b[^"]*"[^>]*>[\s\S]*?</div>',
            "",
            stack,
            flags=re.I,
        )
        stack = re.sub(
            r'<div class="ml-line[^"]*">\s*'
            r'<span class="ml-src books">[\s\S]*?</div>',
            "",
            stack,
            flags=re.I,
        )
        stack = re.sub(
            r'<th\b[^>]*\bclass="[^"]*\bcol-books\b[^"]*"[^>]*>[\s\S]*?</th>',
            "",
            stack,
            flags=re.I,
        )
        stack = re.sub(r"<th>\s*Books\s*</th>", "", stack, flags=re.I)
        stack = re.sub(
            r'<td\b[^>]*\bclass="[^"]*\bval-books\b[^"]*"[^>]*>[\s\S]*?</td>',
            "",
            stack,
            flags=re.I,
        )
        stack = re.sub(
            r'<div class="line-chip[^"]*">\s*'
            r'<div class="line-chip-label">\s*' + chip_label + r'\s*</div>\s*'
            r'<div class="line-chip-val">[\s\S]*?</div>\s*</div>',
            "",
            stack,
            flags=re.I,
        )
        return stack

    out: list[str] = []
    pos = 0
    for match in re.finditer(r"<div\b[^>]*\bdata-pick-card\b[^>]*>", html, flags=re.I):
        end = _cfl_div_end(html, match.start())
        if end < 0:
            continue
        out.append(html[pos : match.start()])
        out.append(_card(html[match.start() : end]))
        pos = end
    out.append(html[pos:])
    return "".join(out)


def _strip_cfl_empty_books(html: str) -> str:
    """Drop book spread / book odds chips when we have no number."""
    if not html:
        return html
    html = re.sub(
        r'<div class="line-chip[^"]*">\s*'
        r'<div class="line-chip-label">\s*Books[^<]*</div>\s*'
        r'<div class="line-chip-val">\s*(?:—|&mdash;|&ndash;|N/A|–|-)?\s*</div>\s*'
        r"</div>",
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="ml-line[^"]*">\s*'
        r'<span class="ml-src books">\s*Books\s*</span>\s*'
        r'<span class="ml-num[^"]*">\s*(?:—|&mdash;|&ndash;|N/A)\s*</span>\s*'
        r"</div>",
        "",
        html,
        flags=re.I,
    )
    # Omit chips whose data-* books attrs are blank.
    html = re.sub(
        r'<div class="line-chip[^"]*"[^>]*>\s*'
        r'<div class="line-chip-label">\s*Books Spread\s*</div>\s*'
        r'<div class="line-chip-val">[^<]*</div>\s*</div>'
        r'(?=[\s\S]{0,800}?data-books-spread="\s*")',
        "",
        html,
        flags=re.I,
    )
    try:
        from team_results_charts import _hide_empty_books_spread_total

        html = _hide_empty_books_spread_total(html)
    except Exception:
        pass
    html = re.sub(r'\sdata-books-spread="\s*"', "", html, flags=re.I)
    html = re.sub(r'\sdata-books-total="\s*"', "", html, flags=re.I)
    return html


def _take_div(html: str, marker: str) -> tuple[str, str]:
    idx = html.find(marker)
    if idx < 0:
        return html, ""
    start = html.rfind("<div", 0, idx)
    if start < 0:
        return html, ""
    pos = html.find(">", start)
    if pos < 0:
        return html, ""
    pos += 1
    depth = 1
    lower = html.lower()
    while pos < len(html) and depth:
        nxt_open = lower.find("<div", pos)
        nxt_close = lower.find("</div>", pos)
        if nxt_close < 0:
            return html, ""
        if nxt_open != -1 and nxt_open < nxt_close:
            depth += 1
            pos = nxt_open + 4
        else:
            depth -= 1
            pos = nxt_close + len("</div>")
    if depth:
        return html, ""
    return html[:start] + html[pos:], html[start:pos]


def _cfl_pick_nickname(name: str) -> str:
    """Same short Pick Confidence label the other CFL cards already use."""
    try:
        from mlb_team_shell import _cfl_short_name
        return _cfl_short_name(name)
    except Exception:
        parts = (name or "").split()
        return parts[-1] if parts else (name or "")


def restore_cfl_full_side_names(html: str) -> str:
    """Pick-confidence sides stay on the short nickname, not a three-word club."""
    if not html or "pc-side" not in html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    if len(parts) < 2:
        return html

    def _full(short: str, home: str, away: str) -> str | None:
        mark = " ✅" if "✅" in short else (" ❌" if "❌" in short else "")
        text = re.sub(r"[✅❌]", "", short).strip()
        if not text or text.upper() in {"N/A", "NA", "HOME", "AWAY", "—", "–", "-"}:
            return None
        for club in (home, away):
            club = re.sub(r"\s+", " ", club or "").strip()
            if not club:
                continue
            shown = _cfl_pick_nickname(club) if len(club.split()) >= 3 else club
            if text.lower() == club.lower():
                return shown + mark
            if text.lower() == shown.lower():
                return shown + mark
            last = club.split()[-1]
            if text.lower() == last.lower():
                if len(club.split()) >= 3:
                    return shown + mark
                return club + mark
        return None

    out = [parts[0]]
    for part in parts[1:]:
        open_m = re.match(r"<div\b[^>]*>", part, flags=re.I)
        tag = open_m.group(0) if open_m else ""
        home_m = re.search(r'\bdata-home="([^"]*)"', tag, flags=re.I)
        away_m = re.search(r'\bdata-away="([^"]*)"', tag, flags=re.I)
        home = home_m.group(1) if home_m else ""
        away = away_m.group(1) if away_m else ""

        def _side(match: re.Match[str], home: str = home, away: str = away) -> str:
            full = _full(match.group(2), home, away)
            if not full or full == match.group(2):
                return match.group(0)
            return f"{match.group(1)}{full}{match.group(3)}"

        part = re.sub(
            r'(<div class="pc-side[^"]*"[^>]*>)([^<]*)(</div>)',
            _side,
            part,
            flags=re.I,
        )
        out.append(part)
    return "".join(out)


_CFL_NAME_FIT_CSS = """
<style id="cfl-name-fit">
html body[data-sport="cfl"] .pick-conf-grid{
  display:grid!important;
  grid-template-columns:repeat(3,minmax(0,1fr))!important;
  gap:8px!important;min-width:0!important;width:100%!important}
html body[data-sport="cfl"] .pc-box{
  display:grid!important;grid-template-rows:auto auto auto!important;
  height:auto!important;min-height:0!important;min-width:0!important;
  width:100%!important;box-sizing:border-box!important;
  overflow:visible!important;padding:6px 4px!important}
html body[data-sport="cfl"] .pc-name{
  font-size:11px!important;font-weight:700!important;letter-spacing:0!important;
  line-height:1.15!important;text-transform:none!important;
  white-space:normal!important;overflow:hidden!important;
  overflow-wrap:normal!important;word-break:normal!important;
  text-overflow:clip!important;max-width:100%!important;max-height:none!important;
  height:auto!important;min-width:0!important;display:block!important}
html body[data-sport="cfl"] .pc-side,
html body[data-sport="cfl"] .team-name{
  font-size:11px!important;font-weight:700!important;letter-spacing:0!important;
  line-height:1.2!important;text-transform:none!important;
  white-space:normal!important;overflow:hidden!important;
  overflow-wrap:normal!important;word-break:normal!important;
  text-overflow:clip!important;max-width:100%!important;max-height:none!important;
  height:auto!important;min-width:0!important;min-height:0!important;
  display:block!important;box-sizing:border-box!important}
html body[data-sport="cfl"] .team-slot,
html body[data-sport="cfl"] .matchup-teams{overflow:visible!important;height:auto!important}
#cfl-chart-dates.date-nav{
  display:flex!important;align-items:center;justify-content:center;gap:12px;
  margin:12px auto 18px;padding:12px 16px;max-width:920px;
  background:#fff;border:1px solid rgba(15,23,42,0.12);border-radius:12px}
#cfl-chart-dates .date-bubbles{display:flex;gap:8px;overflow-x:auto;padding:4px;max-width:820px}
#cfl-chart-dates .date-bubble{
  background:#fff;border:2px solid rgba(15,23,42,0.2);border-radius:22px;
  padding:8px 15px;min-width:100px;text-align:center;font-weight:500;
  font-size:0.84em;color:#0f172a;text-decoration:none;white-space:nowrap}
#cfl-chart-dates .nav-arrow{
  background:rgba(251,191,36,0.2);border:2px solid #fbbf24;color:#92400e;
  font-size:1.3em;width:36px;height:36px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;cursor:pointer}
</style>
"""


def fit_cfl_confidence_names(html: str) -> str:
    if not html:
        return html
    html = re.sub(r'<style id="cfl-name-fit">[\s\S]*?</style>', "", html, count=1, flags=re.I)
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", _CFL_NAME_FIT_CSS + "</body>", html, count=1, flags=re.I)
    return html + _CFL_NAME_FIT_CSS


_CHART_VIEWS = {"chart", "tabs", "markets", "tabbed", "spread", "totals"}


def _cfl_chart_dates(html: str) -> list[str]:
    """Dates already printed on this chart. Nothing is added."""
    found: list[str] = []
    match = re.search(r"const allDates = (\[[^\]]*\])", html or "")
    if match:
        found.extend(re.findall(r"\d{4}-\d{2}-\d{2}", match.group(1)))
    select = re.search(r'<select id="datePicker">([\s\S]*?)</select>', html or "", flags=re.I)
    if select:
        found.extend(re.findall(r'value="(\d{4}-\d{2}-\d{2})"', select.group(1)))
    found.extend(re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', html or ""))
    dates: list[str] = []
    for day in found:
        if day not in dates:
            dates.append(day)
    return dates


def place_cfl_chart_date_picker(html: str) -> str:
    """Bubble date picker in the chart view, using dates already on the page."""
    if not html or 'id="cfl-chart-dates"' in html:
        html = re.sub(
            r'<nav id="dateBubbles"[\s\S]*?</script>',
            "",
            html or "",
            count=1,
            flags=re.I,
        )
        return html
    dates = _cfl_chart_dates(html)
    html = re.sub(
        r'<nav id="dateBubbles"[\s\S]*?</script>',
        "",
        html,
        count=1,
        flags=re.I,
    )
    if not dates:
        return html
    payload = json.dumps(dates)
    from datetime import datetime

    def _bubble_label(day: str) -> str:
        stamp = datetime.strptime(day, "%Y-%m-%d")
        return f"{stamp.strftime('%a')}, {stamp.strftime('%b')} {stamp.day}"

    shown = dates[-7:]
    chips = "".join(
        f'<a class="date-bubble{" active" if day == dates[-1] else ""}" '
        f'id="date-{day}" href="#date-{day}">{_bubble_label(day)}</a>'
        for day in shown
    )
    nav = (
        '<div class="date-nav" id="cfl-chart-dates">'
        '<div class="nav-arrow" onclick="previousWeek()">&#8249;</div>'
        f'<div class="date-bubbles" id="dateBubbles">{chips}</div>'
        '<div class="nav-arrow" onclick="nextWeek()">&#8250;</div>'
        "</div>"
        "<script>"
        f"const allDates = {payload};"
        "const today = allDates.length ? allDates[allDates.length - 1] : '';"
        "let currentWeekStart = 0, activeDate = null;"
        "const datesPerWeek = 7;"
        "function fmtDate(ds){"
        "const d=new Date(ds+'T12:00:00');"
        "const days=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];"
        "const months=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];"
        "return days[d.getDay()]+', '+months[d.getMonth()]+' '+d.getDate();}"
        "function showDate(date){"
        "document.querySelectorAll('.date-section').forEach(s=>s.classList.remove('visible'));"
        "const sec=document.getElementById('date-'+date);"
        "if(sec){sec.classList.add('visible');activeDate=date;}}"
        "function renderBubbles(){"
        "const c=document.getElementById('dateBubbles'); if(!c) return; c.innerHTML='';"
        "const end=Math.min(currentWeekStart+datesPerWeek, allDates.length);"
        "const week=allDates.slice(currentWeekStart, end);"
        "if(activeDate && week.indexOf(activeDate)<0){activeDate=week[week.length-1];showDate(activeDate);}"
        "week.forEach(date=>{"
        "const b=document.createElement('a'); b.className='date-bubble'; b.href='#date-'+date; b.id='date-'+date;"
        "if(date===today) b.classList.add('today');"
        "if(date===activeDate) b.classList.add('active');"
        "b.textContent=fmtDate(date);"
        "b.onclick=function(ev){ev.preventDefault();"
        "document.querySelectorAll('.date-bubble').forEach(x=>x.classList.remove('active'));"
        "b.classList.add('active'); showDate(date);};"
        "c.appendChild(b);});}"
        "function previousWeek(){if(currentWeekStart>0){currentWeekStart=Math.max(0,currentWeekStart-datesPerWeek);renderBubbles();}}"
        "function nextWeek(){if(currentWeekStart+datesPerWeek<allDates.length){currentWeekStart+=datesPerWeek;renderBubbles();}}"
        "document.addEventListener('DOMContentLoaded',function(){"
        "if(allDates.length>0){const lastIdx=allDates.length-1;"
        "currentWeekStart=Math.max(0,lastIdx-datesPerWeek+1); activeDate=allDates[lastIdx];}"
        "showDate(activeDate); renderBubbles();});"
        "</script>"
    )
    needle = '</div><style>.pl-view-toggle'
    if needle in html:
        return html.replace(needle, "</div>" + nav + "<style>.pl-view-toggle", 1)
    if re.search(r"<main\b", html, flags=re.I):
        return re.sub(r"(<main\b[^>]*>)", r"\1" + nav, html, count=1, flags=re.I)
    return html


def place_cfl_share_above_footer(html: str) -> str:
    """Results image, then the share bar, then the site footer."""
    if not html or "share-strip" not in html or "site-directory-footer" not in html:
        return html
    original = html
    html, image = _take_div(html, 'data-results-share="1"')
    html, share = _take_div(html, 'class="share-strip"')
    if not share:
        return original
    footer = html.rfind('class="site-directory-footer"')
    if footer < 0:
        return original
    tag = html.rfind("<", 0, footer)
    if tag < 0:
        return original
    block = ""
    if image:
        block += image
    block += share
    return html[:tag] + block + html[tag:]


def render_cfl_picks() -> str:
    from mlb_team_shell import render_team_sport
    from sandbox_fixup import unlock_premium_card_details

    nav = _nav_ctx()
    premium = bool(nav.get("is_premium"))
    html, meta = render_team_sport("cfl", which="picks")
    if not meta.get("ok") or not html:
        raise RuntimeError(f"cfl mlb shell failed: {meta}")
    html = _strip_mlb_content_from_cfl(html)
    html = unlock_premium_card_details(html)
    html = _ensure_mlb_copy_all_markets(html)
    html = _strip_mlb_content_from_cfl(html)
    html = _open_cfl_cards(html)
    try:
        from team_results_charts import apply_team_picks_h2h

        html = apply_team_picks_h2h(html, "CFL")
    except Exception:
        pass
    html = _strip_cfl_empty_books(html)
    if not premium:
        html = _gate_cfl_paid_markets(html)
    html = re.sub(r"const sportName\s*=\s*[^;]+;", 'const sportName = "CFL";', html)
    html = re.sub(r"const sportIcon\s*=\s*[^;]+;", 'const sportIcon = "🏈";', html)
    html = strip_cfl_pick_books(html)
    html = uncopy_cfl_model_spreads(html)
    html = restore_cfl_stored_xsharp(html)
    html = stamp_cfl_pl_vs_books(html)
    html = stamp_cfl_share_pick_count(html)
    return _finalize_cfl_html(html)


_CFL_RESULTS_PAGE_CACHE: dict = {}
_CFL_RESULTS_PAGE_TTL = 180
_CFL_MODEL_KEYS = (
    ("Grinder2", "glicko2"),
    ("Takedown", "trueskill"),
    ("Edge", "elo"),
    ("XSharp", "xgb"),
    ("Sharp Consensus", "ens"),
    ("Efficiency", "efficiency"),
)


def _cfl_num(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _cfl_card_to_daily_game(card: dict[str, Any], render) -> dict[str, Any] | None:
    """Engine result row → DAILY_RESULTS_TEMPLATE game. Never invent 50/50."""
    home = card.get("home_team") or ""
    away = card.get("away_team") or ""
    try:
        actual_home = int(card["home_score"])
        actual_away = int(card["away_score"])
    except (TypeError, ValueError, KeyError):
        return None
    if not home or not away:
        return None
    day = render._date_key(card.get("game_date")) or ""
    if not day:
        return None
    home_won = actual_home > actual_away
    tie = actual_home == actual_away
    locked = render._has_locked_pick(card)
    game: dict[str, Any] = {
        "game_id": card.get("game_id") or f"{day}-{away}-{home}",
        "home": home,
        "away": away,
        "home_team_id": home,
        "away_team_id": away,
        "home_score": actual_home,
        "away_score": actual_away,
        "league": "CFL",
        "date": day,
        "game_date": day,
        "glicko2_prob": None,
        "trueskill_prob": None,
        "elo_prob": None,
        "xgb_prob": None,
        "ens_prob": None,
        "ensemble_prob": None,
        "efficiency_prob": None,
        "glicko2_correct": None,
        "trueskill_correct": None,
        "elo_correct": None,
        "xgb_correct": None,
        "ens_correct": None,
        "efficiency_correct": None,
    }
    if locked:
        hp = float(card["home_win_prob"])
        for name, fav_p, fav in render._component_models(home, away, hp):
            home_p = fav_p if fav == home else (1.0 - fav_p)
            key = dict(_CFL_MODEL_KEYS).get(name)
            if not key:
                continue
            game[f"{key}_prob"] = round(home_p * 100.0, 1)
            if not tie:
                game[f"{key}_correct"] = (home_p >= 0.5) == home_won
        game["ensemble_prob"] = game.get("ens_prob")

    bk_spread = _cfl_num(card.get("book_spread"))
    if bk_spread is None:
        bk_spread = _cfl_num(card.get("book_home_spread"))
    bk_total = _cfl_num(card.get("book_total"))
    pl_spread = _cfl_num(card.get("model_spread"))
    pl_total = _cfl_num(card.get("model_total"))
    game["book_away_moneyline"] = _cfl_num(
        card.get("book_away_moneyline") or card.get("away_moneyline")
    )
    game["book_home_moneyline"] = _cfl_num(
        card.get("book_home_moneyline") or card.get("home_moneyline")
    )
    game["book_spread"] = bk_spread
    game["book_total"] = bk_total
    game["our_spread"] = pl_spread
    game["our_total"] = pl_total
    game["xgb_spread"] = pl_spread
    game["xgb_total"] = pl_total
    if bk_spread is not None:
        game["disp_book_spread"] = bk_spread
    if pl_spread is not None:
        game["disp_pl_spread"] = pl_spread
        game["disp_xs_spread"] = pl_spread
    if bk_total is not None:
        game["disp_book_total"] = bk_total
    if pl_total is not None:
        game["disp_pl_total"] = pl_total
        game["disp_xs_total"] = pl_total

    ph = _cfl_num(card.get("predicted_home_score"))
    pa = _cfl_num(card.get("predicted_away_score"))
    if ph is not None:
        game["pl_proj_home_pts"] = ph
        game["xs_proj_home_pts"] = ph
        game["expected_home_score"] = ph
    if pa is not None:
        game["pl_proj_away_pts"] = pa
        game["xs_proj_away_pts"] = pa
        game["expected_away_score"] = pa
    game["pl_model_away_ml"] = _cfl_num(
        card.get("pl_away_moneyline") or card.get("pl_model_away_ml")
    )
    game["pl_model_home_ml"] = _cfl_num(
        card.get("pl_home_moneyline") or card.get("pl_model_home_ml")
    )

    sp_ok, sp_push = render.grade_spread_raw(card)
    tot_ok, tot_push = render.grade_total_raw(card)
    if pl_spread is not None:
        game["spread_pick_label"] = render.spread_label(home, away, pl_spread)
        game["spread_pick"] = (
            "HOME" if pl_spread > 0 else ("AWAY" if pl_spread < 0 else "PUSH")
        )
    if sp_push:
        game["spread_pick"] = "PUSH"
        game["spread_correct"] = None
        game["pl_spread_correct"] = None
    elif sp_ok is not None:
        game["spread_correct"] = bool(sp_ok)
        game["pl_spread_correct"] = bool(sp_ok)
    if pl_total is not None and ph is not None and pa is not None:
        lean_over = (ph + pa) >= pl_total
        game["total_pick_label"] = f"{'Over' if lean_over else 'Under'} {pl_total:g}"
        game["total_pick"] = "OVER" if lean_over else "UNDER"
    elif pl_total is not None:
        game["total_pick_label"] = f"Over {pl_total:g}"
        game["total_pick"] = "OVER"
    if tot_push:
        game["total_pick"] = "PUSH"
        game["total_correct"] = None
    elif tot_ok is not None:
        game["total_correct"] = bool(tot_ok)

    h2h = render._h2h_last10(away, home)
    if h2h and str(h2h) not in ("N/A", "First meeting"):
        m = re.match(r"([0-9.]+)\s*\((\d+)\s*games?\)", str(h2h))
        if m:
            game["h2h_last10_total"] = float(m.group(1))
            game["h2h_last10_games"] = int(m.group(2))
    elif str(h2h or "") == "First meeting":
        game["h2h_missing_reason"] = "First meeting"
    return game


def _render_cfl_shared_daily_results() -> tuple[str, str | None]:
    """Shared team-sports DAILY_RESULTS_TEMPLATE. Do not rebuild MLB."""
    from collections import defaultdict
    from datetime import datetime, timedelta

    from flask import render_template_string

    from mlb_team_shell import _cfl_cards

    m = sys.modules.get("NHL77FINAL") or sys.modules.get("__main__")
    if m is None or not hasattr(m, "DAILY_RESULTS_TEMPLATE"):
        raise RuntimeError("CFL results need NHL77FINAL DAILY_RESULTS_TEMPLATE")

    cards, render = _cfl_cards("results")
    daily_results: dict = defaultdict(lambda: {"games": []})
    for card in cards:
        game = _cfl_card_to_daily_game(card, render)
        if game:
            daily_results[game["date"]]["games"].append(game)
    if not daily_results:
        raise RuntimeError("CFL results: no completed games")

    yesterday_dt = datetime.now() - timedelta(days=1)
    yesterday = yesterday_dt.strftime("%Y-%m-%d")
    today_date = datetime.now().strftime("%Y-%m-%d")
    sorted_dates = m._recent_result_dates(
        daily_results,
        yesterday=yesterday,
        limit=400,
        recent_window_days=400,
    )
    if today_date in daily_results and today_date not in sorted_dates:
        sorted_dates = [today_date] + list(sorted_dates)

    overall_stats = m.compute_overall_stats_from_daily(daily_results, sport="CFL")
    st_stats = m._recount_spread_total_stats(daily_results)
    st_stats = m.promote_season_spread_ou_from_games(daily_results, st_stats)
    season_perf = m._build_season_performance_summary(
        overall_stats, st_stats, sport="CFL"
    )
    tally_bundle = m._compute_results_tally_bundle(
        daily_results, yesterday_dt, sport="CFL"
    )
    _ov, _un, _gou, _avg, _bench = m._ou_stats(daily_results, "CFL")
    roi_daily = m.compute_roi_for_range(daily_results, yesterday_dt, yesterday_dt)
    roi_weekly = m.compute_roi_for_range(
        daily_results,
        tally_bundle["weekly_start_dt"],
        tally_bundle["weekly_end_dt"],
    )
    roi_total = m.compute_roi_for_range(daily_results, None, None)
    roi_cards = m.build_roi_cards(roi_daily, roi_weekly, roi_total)
    buckets = render._bucket_results(cards)
    html = render_template_string(
        m.DAILY_RESULTS_TEMPLATE,
        **m._results_page_meta("CFL"),
        page="CFL",
        sport="CFL",
        sport_info=m.SPORTS["CFL"],
        sport_bg_image=m.SPORT_BG_IMAGES.get("CFL", ""),
        sport_seo_slug=m.SPORT_SEO_SLUGS.get("CFL", "cfl-picks"),
        sport_results_slug=m._SPORT_RESULTS_SLUGS.get("CFL", "cfl-results"),
        daily_results=daily_results,
        sorted_dates=sorted_dates,
        today_date=today_date,
        overall_stats=overall_stats,
        total_over=_ov,
        total_under=_un,
        total_games_ou=_gou,
        avg_total=_avg,
        ou_bench=_bench,
        spread_total_stats=st_stats,
        season_perf=season_perf,
        daily_tally=tally_bundle["daily_tally"],
        daily_tally_date=tally_bundle["daily_tally_date"],
        daily_tally_games=tally_bundle["daily_tally_games"],
        weekly_tally=tally_bundle["weekly_tally"],
        weekly_tally_date_range=tally_bundle["weekly_tally_date_range"],
        weekly_tally_games=tally_bundle["weekly_tally_games"],
        roi_cards=roi_cards,
        results_stale_notice=tally_bundle.get("results_stale_notice"),
        results_snapshot_notice=None,
        soccer_leagues=None,
    )
    return html, buckets.get("last_night_key")


def _cfl_section_tabs(*, results_active: bool = True) -> str:
    picks_cls = "" if results_active else " active"
    results_cls = " active" if results_active else ""
    return (
        '<div class="section-tabs">'
        f'<a href="/cfl-picks" class="tab{picks_cls}">📊 Predictions</a>'
        f'<a href="/cfl-results" class="tab{results_cls}">🎯 Results</a>'
        "</div>"
    )


def _restore_global_nav_mlb_links(html: str) -> str:
    """Shell rewrites every /mlb-* href to CFL — put Sports-nav MLB back."""
    if not html:
        return html
    html = re.sub(
        r'(<a\b[^>]*\bhref=")/cfl-picks("[^>]*>)\s*MLB\s*(</a>)',
        r"\1/mlb-picks\2MLB\3",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(<a\b[^>]*\bhref=")/cfl-results("[^>]*>)\s*MLB\s*(</a>)',
        r"\1/mlb-results\2MLB\3",
        html,
        flags=re.I,
    )
    return html


def _ensure_cfl_results_section_tabs(html: str) -> str:
    """Chart shells often omit Predictions|Results — put them under the title."""
    if not html:
        return html
    if re.search(r'<div class="section-tabs\b', html, flags=re.I):
        return html
    tabs = _cfl_section_tabs(results_active=True)
    h1 = re.search(r'(<h1\b[^>]*>[\s\S]*?</h1>\s*)', html, flags=re.I)
    if h1:
        return html[: h1.end()] + tabs + html[h1.end() :]
    toggle = re.search(
        r'<div class="pl-view-toggle\b[^>]*>[\s\S]*?</div>\s*'
        r'(?:<style>\.pl-view-toggle[\s\S]*?</style>\s*)?',
        html,
        flags=re.I,
    )
    if toggle:
        return html[: toggle.start()] + tabs + html[toggle.start() :]
    return html


def _reorder_cfl_results_headers(html: str) -> str:
    """Title → Predictions|Results → Cards|Chart (single of each)."""
    if not html:
        return html
    html = _ensure_cfl_results_section_tabs(html)

    # If section-tabs landed above the page title, move them under h1.
    h1 = re.search(r'(<h1\b[^>]*>[\s\S]*?</h1>\s*)', html, flags=re.I)
    tabs_m = re.search(
        r'(<div class="section-tabs\b[\s\S]*?</div>\s*(?:<style>[\s\S]*?</style>\s*)?)',
        html,
        flags=re.I,
    )
    if h1 and tabs_m and tabs_m.start() < h1.start():
        tabs = tabs_m.group(1)
        html = html[: tabs_m.start()] + html[tabs_m.end() :]
        h1 = re.search(r'(<h1\b[^>]*>[\s\S]*?</h1>\s*)', html, flags=re.I)
        if h1:
            html = html[: h1.end()] + tabs + html[h1.end() :]

    m = re.search(
        r'(<div class="pl-view-toggle\b[^>]*>[\s\S]*?</div>\s*'
        r'(?:<style>\.pl-view-toggle[\s\S]*?</style>\s*)?)',
        html,
        flags=re.I,
    )
    if not m:
        tabs_only = re.search(
            r'(<div class="section-tabs\b[\s\S]*?</div>\s*(?:<style>[\s\S]*?</style>\s*)?)',
            html,
            flags=re.I,
        )
        if tabs_only:
            return (
                html[: tabs_only.end()]
                + _cfl_view_toggle("normal")
                + html[tabs_only.end() :]
            )
        return html
    toggle = m.group(1)
    html_wo = html[: m.start()] + html[m.end() :]
    tabs = re.search(
        r'(<div class="section-tabs\b[\s\S]*?</div>\s*(?:<style>[\s\S]*?</style>\s*)?)',
        html_wo,
        flags=re.I,
    )
    if tabs:
        return html_wo[: tabs.end()] + toggle + html_wo[tabs.end() :]
    h1 = re.search(r'(<h1\b[^>]*>[\s\S]*?</h1>\s*)', html_wo, flags=re.I)
    if h1:
        return html_wo[: h1.end()] + toggle + html_wo[h1.end() :]
    return toggle + html_wo


def render_cfl_results(*, view: str = "normal") -> str:
    view = (view or "normal").strip().lower()
    if view in ("chart", "tabs", "markets", "tabbed"):
        return _render_cfl_results_chart()
    now = __import__("time").time()
    hit = _CFL_RESULTS_PAGE_CACHE.get("cards_shared_v1")
    if isinstance(hit, dict) and hit.get("html") and (now - hit.get("ts", 0)) < _CFL_RESULTS_PAGE_TTL:
        return hit["html"]

    html, last_night_key = _render_cfl_shared_daily_results()
    html = _strip_mlb_content_from_cfl(html)
    try:
        from team_results_charts import (
            _inject_cfl_consensus_hist_chips,
            set_results_chart_source,
        )

        set_results_chart_source("CFL", html)
        html = _inject_cfl_consensus_hist_chips(html)
    except Exception:
        pass
    try:
        from mlb_consensus_hub import inject_consensus_records_html

        html = inject_consensus_records_html(
            html, sport="cfl", last_night_key=last_night_key
        )
    except Exception:
        pass
    html = _reorder_cfl_results_headers(html)
    html = _dedupe_cfl_results_chrome(html)
    close = (html or "").lower().find("</html>")
    if close >= 0:
        html = html[: close + len("</html>")]
    html = _inject_chrome_into_page(
        html,
        extra_css=["/static/css/team-results.css", "/static/css/cfl-pick-cards.css"],
    )
    html = _finalize_cfl_html(html)
    try:
        from isolate_checker_fixes import repair_results_html
        html = repair_results_html(html, "CFL", view="")
    except Exception:
        pass
    try:
        from picks_recent_results import cfl_totals_chart_html

        chart = cfl_totals_chart_html()
        if chart and 'id="pl-totals-three-way"' not in html:
            at = (html or "").lower().rfind("</body>")
            html = (html + chart) if at < 0 else (html[:at] + chart + html[at:])
    except Exception:
        pass
    _CFL_RESULTS_PAGE_CACHE["cards_shared_v1"] = {"ts": now, "html": html}
    return html


def _render_cfl_results_chart() -> str:
    from mlb_team_shell import render_team_sport

    # Chart inject needs cards HTML as source — warm the cache if empty.
    hit = _CFL_RESULTS_PAGE_CACHE.get("cards_shared_v1")
    if not (isinstance(hit, dict) and hit.get("html")):
        try:
            render_cfl_results(view="normal")
        except Exception:
            pass

    try:
        from team_results_charts import set_results_chart_source

        hit = _CFL_RESULTS_PAGE_CACHE.get("cards_shared_v1")
        if isinstance(hit, dict) and hit.get("html"):
            set_results_chart_source("CFL", hit["html"])
    except Exception:
        pass

    html, meta = render_team_sport("cfl", which="chart")
    if meta.get("ok") and html:
        html = _strip_mlb_content_from_cfl(html)
        html = _strip_all_pl2_headers(html)
        html = _strip_research_nav_scripts(html)
        html = html.replace("Spread / Run Line", "Spread")
        try:
            from team_results_charts import (
                _hide_empty_books_spread_total,
                apply_team_results_template,
                set_results_chart_source,
            )

            hit = _CFL_RESULTS_PAGE_CACHE.get("cards_shared_v1")
            if isinstance(hit, dict) and hit.get("html"):
                set_results_chart_source("CFL", hit["html"])
            html = apply_team_results_template(html, "CFL", view="chart")
            html = _hide_empty_books_spread_total(html)
            try:
                from mlb_results_ui import inject_ssr_chart_bootstrap
                from team_tabbed_results import build_cfl_payload

                payload = build_cfl_payload()
                if isinstance(payload, dict):
                    html = inject_ssr_chart_bootstrap(
                        html, payload, "cfl", market=_cfl_chart_market()
                    )
            except Exception:
                pass
        except Exception:
            pass
        # Template may have re-inserted site chrome — strip before our inject.
        html = _strip_all_pl2_headers(html)
        html = _strip_research_nav_scripts(html)
        html = _inject_chrome_into_page(
            html,
            extra_css=["/static/css/team-results.css", "/static/css/cfl-pick-cards.css"],
        )
        html = _reorder_cfl_results_headers(html)
        html = _dedupe_cfl_results_chrome(html)
        html = _dedupe_pl2_headers(html)
        html = _finalize_cfl_html(html)
        try:
            from isolate_checker_fixes import repair_results_html
            mk = ""
            try:
                from flask import request
                mk = (request.args.get("market") or "").strip().lower()
            except Exception:
                mk = ""
            html = repair_results_html(html, "CFL", view=mk or "chart")
        except Exception:
            pass
        return html

    from jinja2 import Environment, FileSystemLoader, select_autoescape

    from mlb_results_ui import inject_ssr_chart_bootstrap
    from team_tabbed_results import build_cfl_payload

    env = Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    html = env.get_template("team_results.html").render(
        sport="cfl",
        sport_label="CFL",
        api_base="/cfl/api",
        show_league=False,
        picks_href="/cfl-picks",
        results_href="/cfl-results",
        **_nav_ctx(),
    )
    if 'class="pl-view-toggle"' not in html:
        if re.search(r"<main\b", html, flags=re.I):
            html = re.sub(
                r"(<main\b[^>]*>)",
                r"\1" + _cfl_view_toggle("chart"),
                html,
                count=1,
                flags=re.I,
            )
        else:
            html = _cfl_view_toggle("chart") + html
    if 'id="league-controls" hidden' not in html:
        html = html.replace('id="league-controls"', 'id="league-controls" hidden')
    html = re.sub(
        r"(?is)<label[^>]*>\s*League\s*</label>\s*<select[\s\S]*?</select>",
        "",
        html,
    )
    html = _strip_all_pl2_headers(html)
    try:
        payload = build_cfl_payload()
        if isinstance(payload, dict):
            html = inject_ssr_chart_bootstrap(
                html, payload, "cfl", market=_cfl_chart_market()
            )
    except Exception:
        pass
    html = _strip_all_pl2_headers(html)
    html = _strip_research_nav_scripts(html)
    html = _inject_chrome_into_page(
        html,
        extra_css=["/static/css/team-results.css", "/static/css/cfl-pick-cards.css"],
    )
    html = _reorder_cfl_results_headers(html)
    html = _dedupe_cfl_results_chrome(html)
    html = _dedupe_pl2_headers(html)
    return _finalize_cfl_html(html)


def cfl_share_jpeg_bytes() -> bytes | None:
    from mlb_team_shell import build_cfl_share_jpeg

    return build_cfl_share_jpeg()


def cfl_results_share_jpeg_bytes() -> bytes | None:
    from mlb_team_shell import build_cfl_results_share_jpeg

    return build_cfl_results_share_jpeg()


def cfl_chart_payload() -> dict[str, Any]:
    from team_tabbed_results import build_cfl_payload

    return build_cfl_payload()
