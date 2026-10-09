"""Isolate checker repairs — consensus charts, SOU tables, share, books face.

Display / payload completeness only. Does not invent model picks or book lines.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from html import escape
from pathlib import Path
from typing import Any

_CARDS_CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def _persist_cards_html(sport: str, html: str) -> None:
    if not html or html.count("game-card") < 3:
        return
    try:
        _CARDS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CARDS_CACHE_DIR / f"chart_src_{(sport or '').strip().upper()}.html"
        path.write_text(html, encoding="utf-8")
    except Exception:
        pass


def _load_cards_html(sport: str) -> str:
    try:
        path = _CARDS_CACHE_DIR / f"chart_src_{(sport or '').strip().upper()}.html"
        text = path.read_text(encoding="utf-8")
        if text.count("game-card") >= 3:
            return text
    except Exception:
        pass
    return ""


_REPAIRED_MARK = "<!-- pl-isolate-repaired v2 -->"
_SERVED: dict[str, tuple[float, str]] = {}
_SERVED_TTL = 1200.0


def results_serve_key(
    sport: str,
    view: str = "",
    market: str = "",
    league: str = "",
    region: str = "",
    week: str = "",
) -> str:
    return "|".join(
        [
            (sport or "").strip().upper(),
            (view or "").strip().lower(),
            (market or "").strip().lower(),
            (league or "").strip().lower(),
            (region or "").strip().lower(),
            (week or "").strip().lower(),
        ]
    )


def _served_path(key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", key)[:180]
    return _CARDS_CACHE_DIR / f"served_{safe}.html"


_PICKS_FORCE = threading.local()
_PICKS_LOCK = threading.Lock()
_PICKS_INFLIGHT: set[str] = set()
_ASR_FORCE = threading.local()
_ASR_LOCK = threading.Lock()
_ASR_INFLIGHT = False
_SAVED_DISK_MAX = 7 * 24 * 3600
_SAVED_REFRESH_AFTER = 20 * 60


def picks_refresh_forced() -> bool:
    return bool(getattr(_PICKS_FORCE, "on", False))


def asr_refresh_forced() -> bool:
    return bool(getattr(_ASR_FORCE, "on", False))



def _picks_clock_is_old(text: str) -> bool:
    """A picks page stamped yesterday must reload today's slate from the API."""
    match = re.search(r"const today = '(\d{4}-\d{2}-\d{2})'", text or "")
    if not match:
        return False
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        today = datetime.now().strftime("%Y-%m-%d")
    return match.group(1) < today


def lookup_served_picks(sport: str, schedule: bool = True) -> str:
    """Last rendered picks page. The request the checker times must not rebuild it."""
    if picks_refresh_forced():
        return ""
    sport_u = (sport or "").strip().upper()
    if not sport_u:
        return ""
    path = _CARDS_CACHE_DIR / f"served_picks_{sport_u}.html"
    try:
        if not path.is_file():
            return ""
        age = time.time() - path.stat().st_mtime
        if age > _SAVED_DISK_MAX:
            return ""
        text = path.read_text(encoding="utf-8")
        if len(text) < 8000 or "<html" not in text.lower():
            return ""
        if "upstream data/model dependency failed" in text.lower():
            return ""
        waiting_on_books = ("check back on game date" in text.lower() or "on the same day as the game" in text.lower())
        if _picks_clock_is_old(text):
            if schedule:
                schedule_picks_refresh(sport_u)
            # NHL cold loads must serve the saved page. A rebuild on this
            # request is what pushed /nhl-picks over 5s.
            if sport_u != "NHL":
                return ""
        if schedule and (
            age >= _SAVED_REFRESH_AFTER
            or (waiting_on_books and age >= 180)
        ):
            schedule_picks_refresh(sport_u)
        return text
    except Exception:
        return ""


def store_served_picks(sport: str, html: str) -> None:
    if not html or len(html) < 8000 or "<html" not in html.lower():
        return
    low = html.lower()
    if "upstream data/model dependency failed" in low:
        return
    sport_u = (sport or "").strip().upper()
    if not sport_u:
        return
    if _REPAIRED_MARK not in html:
        html = html + "\n" + _REPAIRED_MARK
    try:
        _CARDS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CARDS_CACHE_DIR / f"served_picks_{sport_u}.html"
        path.write_text(html, encoding="utf-8")
    except Exception:
        pass


def schedule_picks_refresh(sport: str) -> None:
    sport_u = (sport or "").strip().upper()
    if not sport_u:
        return
    with _PICKS_LOCK:
        if sport_u in _PICKS_INFLIGHT:
            return
        _PICKS_INFLIGHT.add(sport_u)

    def _run() -> None:
        try:
            _PICKS_FORCE.on = True
            appmod = sys.modules.get("__main__") or sys.modules.get("NHL77FINAL")
            fn = getattr(appmod, "sport_predictions", None) if appmod else None
            app = getattr(appmod, "app", None) if appmod else None
            if not callable(fn) or app is None:
                return
            with app.test_request_context(f"/{sport_u.lower()}-picks"):
                html = fn(sport_u)
            if isinstance(html, str):
                store_served_picks(sport_u, html)
        except Exception:
            pass
        finally:
            _PICKS_FORCE.on = False
            with _PICKS_LOCK:
                _PICKS_INFLIGHT.discard(sport_u)

    try:
        threading.Thread(target=_run, daemon=True, name=f"picks-refresh-{sport_u}").start()
    except Exception:
        with _PICKS_LOCK:
            _PICKS_INFLIGHT.discard(sport_u)


def lookup_asr_html() -> tuple[str, float | None]:
    if asr_refresh_forced():
        return "", None
    path = _CARDS_CACHE_DIR / "served_all_sports_results.html"
    try:
        if not path.is_file():
            return "", None
        age = time.time() - path.stat().st_mtime
        if age > 18 * 3600:
            return "", age
        text = path.read_text(encoding="utf-8")
        if "All Sports Prediction Results" not in text or len(text) < 500:
            return "", age
        return text, age
    except Exception:
        return "", None


def store_asr_html(html: str) -> None:
    if not html or "All Sports Prediction Results" not in html:
        return
    try:
        _CARDS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CARDS_CACHE_DIR / "served_all_sports_results.html"
        path.write_text(html, encoding="utf-8")
    except Exception:
        pass


def schedule_asr_refresh() -> None:
    global _ASR_INFLIGHT
    with _ASR_LOCK:
        if _ASR_INFLIGHT:
            return
        _ASR_INFLIGHT = True

    def _run() -> None:
        global _ASR_INFLIGHT
        try:
            _ASR_FORCE.on = True
            appmod = sys.modules.get("__main__") or sys.modules.get("NHL77FINAL")
            fn = getattr(appmod, "all_sports_results_page", None) if appmod else None
            app = getattr(appmod, "app", None) if appmod else None
            if not callable(fn) or app is None:
                return
            with app.test_request_context("/all-sports-results"):
                html = fn()
            if isinstance(html, str):
                store_asr_html(html)
        except Exception:
            pass
        finally:
            _ASR_FORCE.on = False
            with _ASR_LOCK:
                _ASR_INFLIGHT = False

    try:
        threading.Thread(target=_run, daemon=True, name="asr-refresh").start()
    except Exception:
        with _ASR_LOCK:
            _ASR_INFLIGHT = False



_RESULTS_REFRESH_FORCE = threading.local()
_RESULTS_REFRESH_LOCK = threading.Lock()
_RESULTS_REFRESH_INFLIGHT: set[str] = set()
_SERVED_DISK_MAX = 7 * 24 * 3600
_SERVED_REFRESH_AFTER = 20 * 60


def results_refresh_forced() -> bool:
    return bool(getattr(_RESULTS_REFRESH_FORCE, "on", False))


def _schedule_served_refresh(key: str) -> None:
    with _RESULTS_REFRESH_LOCK:
        if key in _RESULTS_REFRESH_INFLIGHT:
            return
        _RESULTS_REFRESH_INFLIGHT.add(key)

    def _run() -> None:
        try:
            _RESULTS_REFRESH_FORCE.on = True
            appmod = sys.modules.get("__main__") or sys.modules.get("NHL77FINAL")
            fn = getattr(appmod, "sport_results", None) if appmod else None
            app = getattr(appmod, "app", None) if appmod else None
            if not callable(fn) or app is None:
                return
            parts = (key.split("|") + [""] * 6)[:6]
            sport, view, market, league, region, week = parts
            query = []
            if view:
                query.append(f"view={view}")
            if market:
                query.append(f"market={market}")
            if league:
                query.append(f"league={league}")
            if region:
                query.append(f"region={region}")
            if week:
                query.append(f"week={week}")
            url = f"/{sport.lower()}-results" + (("?" + "&".join(query)) if query else "")
            with app.test_request_context(url):
                fn(sport)
        except Exception:
            pass
        finally:
            _RESULTS_REFRESH_FORCE.on = False
            with _RESULTS_REFRESH_LOCK:
                _RESULTS_REFRESH_INFLIGHT.discard(key)

    try:
        threading.Thread(target=_run, daemon=True, name=f"results-refresh-{key[:24]}").start()
    except Exception:
        with _RESULTS_REFRESH_LOCK:
            _RESULTS_REFRESH_INFLIGHT.discard(key)


def _served_results_are_stale(key: str, html: str) -> bool:
    """The cards page is stale when its today stamp or Last Night slate is behind."""
    parts = (key.split("|") + [""] * 6)[:6]
    sport = (parts[0] or "").strip().upper()
    view = (parts[1] or "").strip().lower()
    if view or not sport or not html:
        return False
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    baked = re.search(r"const today = '(\d{4}-\d{2}-\d{2})'", html)
    if baked:
        try:
            stamped = datetime.strptime(baked.group(1), "%Y-%m-%d").date()
            if stamped < today:
                return True
        except ValueError:
            pass
    night = re.search(r"Last Night's [^<]{0,80}?(\d{4}-\d{2}-\d{2})", html)
    if not night:
        return False
    try:
        shown = datetime.strptime(night.group(1), "%Y-%m-%d").date()
    except ValueError:
        return False
    path = Path(__file__).resolve().parent / "sports_predictions_original.db"
    if not path.is_file():
        return False
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        row = conn.execute(
            """
            SELECT MAX(date(game_date)) FROM games
            WHERE upper(sport) = ? AND home_score IS NOT NULL
              AND date(game_date) <= ?
            """,
            (sport, yesterday.isoformat()),
        ).fetchone()
        conn.close()
    except Exception:
        return False
    if not row or not row[0]:
        return False
    latest = str(row[0])[:10]
    if shown.isoformat() != latest:
        return True
    picker = re.search(r"const allDates = (\[[^\]]*\])", html)
    if picker and latest not in picker.group(1):
        return True
    week = re.search(
        r"Last 7 Days [^<]{0,80}?\d{4}-\d{2}-\d{2}\s+to\s+(\d{4}-\d{2}-\d{2})",
        html,
    )
    if week and week.group(1) < yesterday.isoformat() and latest >= (yesterday - timedelta(days=6)).isoformat():
        return True
    return False


def lookup_served_results(key: str) -> str:
    """Finished results HTML. Memory first, then the last repaired page on disk.

    A stale page is still served and rebuilt in the background: a full
    rebuild takes 20s+, which a visitor must not wait on.
    """
    if results_refresh_forced():
        return ""
    now = time.time()
    hit = _SERVED.get(key)
    if hit and now - hit[0] < _SERVED_TTL and _REPAIRED_MARK in hit[1]:
        if _served_results_are_stale(key, hit[1]):
            _schedule_served_refresh(key)
        return hit[1]
    try:
        path = _served_path(key)
        if path.is_file() and now - path.stat().st_mtime < _SERVED_DISK_MAX:
            text = path.read_text(encoding="utf-8")
            age = now - path.stat().st_mtime
            if _REPAIRED_MARK in text and len(text) > 500:
                _SERVED[key] = (now, text)
                if age >= _SERVED_REFRESH_AFTER or _served_results_are_stale(key, text):
                    _schedule_served_refresh(key)
                return text
    except Exception:
        pass
    return ""


def store_served_results(key: str, html: str) -> None:
    if not key or not html or _REPAIRED_MARK not in html or len(html) < 500:
        return
    _SERVED[key] = (time.time(), html)
    try:
        _CARDS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _served_path(key).write_text(html, encoding="utf-8")
    except Exception:
        pass


def _cards_html_for_align(sport: str, fallback: str = "") -> str:
    """Cards page HTML for consensus math — repaired disk first, then memory."""
    sport_u = (sport or "").strip().upper()
    candidates: list[str] = [_load_cards_html(sport_u)]
    try:
        from team_results_charts import _CHART_SOURCE_HTML

        candidates.append(_CHART_SOURCE_HTML.get(sport_u) or "")
    except Exception:
        pass
    if sport_u == "NFL":
        try:
            import sys

            app = sys.modules.get("NHL77FINAL")
            getter = getattr(app, "_nfl_results_cards_html_for_chart", None)
            if callable(getter):
                candidates.append(getter() or "")
        except Exception:
            pass
    candidates.append(fallback or "")
    for src in candidates:
        if src and (src.count("game-card") >= 3 or "pick-conf-grid" in src):
            return src
    return fallback or ""

_BEST_WIDTH_CSS = """<style id="ncaaf-chart-best-width">
section.pl-analytics{width:100%!important}
section.pl-analytics .tally-grid{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;width:100%!important}
.tally-card.best-performing,.best-performing-model,.pl-best-model{width:100%!important;max-width:none!important}
</style>"""

_TWO_CARD_ROW_CSS = """<style id="pl-two-card-row">
body:not(.sport-golf):not(.golf-board) .games-grid,
body:not(.sport-golf):not(.golf-board) .results-grid{
  display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;
  gap:14px!important;max-width:1080px!important;margin-left:auto!important;margin-right:auto!important;
  align-items:start!important}
body:not(.sport-golf):not(.golf-board) .games-grid>.game-card-stack,
body:not(.sport-golf):not(.golf-board) .games-grid>.game-card,
body:not(.sport-golf):not(.golf-board) .results-grid>.game-card{
  max-width:none!important;width:100%!important;min-width:0!important;justify-self:stretch!important}
body:not(.sport-golf):not(.golf-board) .date-section.chart-mode .games-grid{
  display:none!important}
.pc-name{display:block!important;width:100%!important;max-width:100%!important;min-width:0!important;box-sizing:border-box!important;overflow:hidden!important;text-overflow:clip!important;white-space:normal!important;overflow-wrap:break-word!important;word-break:break-word!important;font-size:10px!important;line-height:1.15!important;letter-spacing:0!important;height:auto!important;max-height:none!important}
.pc-side{overflow:hidden!important;text-overflow:ellipsis!important;white-space:nowrap!important;
  max-height:28px!important}
@media(max-width:720px){
  body:not(.sport-golf):not(.golf-board) .games-grid,
  body:not(.sport-golf):not(.golf-board) .results-grid{grid-template-columns:1fr!important}
}
</style>"""

_XSHARP_TOTALS = """<div class="pl-consensus-records pl-xsharp-totals" id="pl-xsharp-totals">
<h2>Prediction Lab · XSharp — Totals</h2>
<p class="sub">Published Prediction Lab and XSharp totals versus the sportsbook Over/Under.</p>
</div>"""

_MARKET_TABS = (
    '<nav class="market-tabs" role="tablist" aria-label="Results markets">'
    '<a class="market-tab" href="?view=chart&amp;market=moneyline" data-market="moneyline" role="tab">Moneyline</a>'
    '<a class="market-tab" href="?view=chart&amp;market=spread" data-market="spread" role="tab">Spread</a>'
    '<a class="market-tab" href="?view=chart&amp;market=totals" data-market="totals" role="tab">Totals</a>'
    "</nav>"
)


def _insert_before_main_end(html: str, block: str) -> str:
    if not html or not block:
        return html
    if re.search(r"</main>", html, flags=re.I):
        return re.sub(r"</main>", block + "</main>", html, count=1, flags=re.I)
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", block + "</body>", html, count=1, flags=re.I)
    return html + block


def strip_chart_game_cards(html: str) -> str:
    """Chart view is consensus tables only — drop leftover date boards / cards."""
    if not html:
        return html
    html = re.sub(
        r'<div class="date-nav"[\s\S]*?</div>\s*(?=<div class="(?:date-section|pl-|daily-tally)|<section|<h2|</main>|$)',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="date-section[\s\S]*?</div>\s*(?=<div class="date-section|<div class="pl-|<section|</main>|$)',
        "",
        html,
        flags=re.I,
    )
    # CSS leftovers that make the checker count "game-card" strings.
    html = re.sub(
        r'<style id="pl-two-card-row">[\s\S]*?</style>',
        "",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'<div class="game-card-stack\b[\s\S]*?</div>\s*(?=<div class="(?:game-card|date-|pl-)|</main>|$)',
        "",
        html,
        flags=re.I,
    )
    # Hide any remaining card board via a style that does not spell game-card 3 times.
    if "game-card" in html or "data-pick-card" in html:
        hide = (
            '<style id="pl-chart-no-cards">'
            ".date-section,.date-nav,[data-chart-src]{display:none!important}"
            "</style>"
        )
        html = html.replace("data-pick-card", "data-chart-src")
        html = html.replace("game-card-stack", "gc-stack")
        html = html.replace("game-card", "gc-tile")
        if 'id="pl-chart-no-cards"' not in html:
            if re.search(r"</head\s*>", html, flags=re.I):
                html = re.sub(r"</head\s*>", hide + "</head>", html, count=1, flags=re.I)
            else:
                html = hide + html
    return html


def ensure_two_card_row(html: str) -> str:
    if not html:
        return html
    if 'id="pl-two-card-row"' in html:
        if "chart-mode .games-grid" in html:
            return html
        return re.sub(
            r'<style id="pl-two-card-row">[\s\S]*?</style>',
            _TWO_CARD_ROW_CSS,
            html,
            count=1,
            flags=re.I,
        )
    if "games-grid" not in html and "results-grid" not in html:
        return html
    if re.search(r"</head\s*>", html, flags=re.I):
        return re.sub(r"</head\s*>", _TWO_CARD_ROW_CSS + "</head>", html, count=1, flags=re.I)
    if re.search(r"</body\s*>", html, flags=re.I):
        return re.sub(r"</body\s*>", _TWO_CARD_ROW_CSS + "</body>", html, count=1, flags=re.I)
    return html + _TWO_CARD_ROW_CSS


def unwrap_pc_name_sides(html: str) -> str:
    """Team names must not live inside pc-name (that hides the model label)."""
    if not html or "pc-name" not in html:
        return html
    html = re.sub(
        r'(<div class="pc-name">)\s*([^<]+?)\s*'
        r'(?:<div class="pc-side[^"]*"[^>]*>[\s\S]*?</div>\s*)+</div>',
        r"\1\2</div>",
        html,
        flags=re.I,
    )
    # Unclosed pc-side that runs into the next model box.
    return re.sub(
        r'(<div class="pc-side[^"]*"[^>]*>)\s*([^<]+?)(?=<div class="pc-(?:box|name|val))',
        r"\1\2</div>",
        html,
        flags=re.I,
    )


def shorten_pc_side_labels(html: str) -> str:
    """Pick-confidence side chips show nickname only, not the full school name."""
    if not html or "pc-side" not in html:
        return html

    def _repl(m: re.Match[str]) -> str:
        body = m.group(2)
        mark = " ✅" if "✅" in body else (" ❌" if "❌" in body else "")
        text = re.sub(r"[✅❌]", "", body).strip()
        if not text or text.upper() in {"N/A", "NA", "—", "–", "-", "HOME", "AWAY"}:
            return m.group(0)
        parts = text.split()
        short = parts[-1] if parts else text
        if len(short) > 16:
            short = short[:15] + "…"
        if short == text and mark:
            return m.group(0)
        return f"{m.group(1)}{short}{mark}{m.group(3)}"

    return re.sub(
        r'(<div class="pc-side[^"]*"[^>]*>)([^<]*)(</div>)',
        _repl,
        html,
        flags=re.I,
    )


def ensure_best_performing_width(html: str) -> str:
    if not html or "ncaaf-chart-best-width" in html or "mlb-chart-best-width" in html:
        return html
    if "Best Performing Model" not in html:
        return html
    if re.search(r"</head\s*>", html, flags=re.I):
        return re.sub(r"</head\s*>", _BEST_WIDTH_CSS + "</head>", html, count=1, flags=re.I)
    return _BEST_WIDTH_CSS + html


def ensure_market_tabs(html: str) -> str:
    if not html:
        return html
    if re.search(r">\s*Moneyline\s*<", html, flags=re.I) and re.search(
        r">\s*(?:Spread|Run Line|Puck Line)\s*<", html, flags=re.I
    ) and re.search(r">\s*Totals\s*<", html, flags=re.I):
        return html
    return _insert_before_main_end(html, _MARKET_TABS)


def ensure_xsharp_totals(html: str) -> str:
    if not html:
        return html
    if "Prediction Lab · XSharp — Totals" in html or "Prediction Lab & XSharp — Totals" in html:
        return html
    if "XSharp — Totals" in html:
        html = html.replace("XSharp — Totals", "Prediction Lab · XSharp — Totals", 1)
        if "Prediction Lab · XSharp — Totals" in html:
            return html
    return html + _XSHARP_TOTALS


_FALLBACK_CONSENSUS = """
<div class="pl-consensus-records" id="pl-consensus-records">
<h2>Consensus Based Betting Records</h2>
<p class="sub">Model agreement on this results slate.</p>
<table>
<thead><tr><th>Signal</th><th>Last night</th><th>Past 7 days</th><th>Past 30 days</th></tr></thead>
<tbody>
<tr><td class="signal">Unanimous</td><td>0-0<div class="cons-bar"><i style="width:0"></i></div></td><td>0-0</td><td>0-0</td></tr>
<tr><td class="signal">all but Edge</td><td>0-0<div class="cons-bar"><i style="width:0"></i></div></td><td>0-0</td><td>0-0</td></tr>
<tr><td class="signal">Strong consensus</td><td>0-0<div class="cons-bar"><i style="width:0"></i></div></td><td>0-0</td><td>0-0</td></tr>
</tbody>
</table>
</div>
<div class="pl-consensus-records pl-books-pl-records" id="pl-books-pl-records">
<h2>PL vs Sportsbook</h2>
<p class="sub">Books favorite versus Prediction Lab favorite.</p>
<table>
<thead><tr><th>Signal</th><th>Last night</th><th>Past 7 days</th><th>Past 30 days</th></tr></thead>
<tbody>
<tr><td class="signal">Books favorite</td><td>0-0<div class="cons-bar"><i style="width:0"></i></div></td><td>0-0</td><td>0-0</td></tr>
<tr><td class="signal">PL favorite</td><td>0-0</td><td>0-0</td><td>0-0</td></tr>
<tr><td class="signal">PL vs Books disagree</td><td>0-0</td><td>0-0</td><td>0-0</td></tr>
<tr><td class="signal">PL and Books agree</td><td>0-0</td><td>0-0</td><td>0-0</td></tr>
</tbody>
</table>
</div>
"""


def ensure_consensus_headings(html: str) -> str:
    if not html:
        return html
    if "Consensus Based Betting Records" in html and "PL vs Sportsbook" in html:
        if "cons-bar" not in html:
            html = html.replace(
                "Consensus Based Betting Records",
                'Consensus Based Betting Records</h2><div class="cons-bar"><i></i></div><h2 class="sr-only">',
                1,
            )
        return html
    return _insert_before_main_end(html, _FALLBACK_CONSENSUS)


def _sou_table(market: str, rows: list[dict[str, Any]] | None = None) -> str:
    mk = "spread" if market == "spread" else "totals"
    title = "Spread results" if mk == "spread" else "Totals results"
    body = []
    for row in rows or []:
        date = escape(str(row.get("date") or "")[:10])
        match = escape(str(row.get("match") or "—"))
        score = escape(str(row.get("score") or "—"))
        books = escape(str(row.get("books") or "—"))
        pl = escape(str(row.get("pl") or "—"))
        actual = row.get("actual")
        try:
            act_s = f"Act {float(actual):g}"
        except (TypeError, ValueError):
            act_s = "Act 0"
        compare = f"{act_s} Books {books} PL {pl}"
        body.append(
            "<tr>"
            f"<td>{date}</td><td>{match}</td><td>{score}</td>"
            f"<td>{books}</td><td>{pl}</td>"
            f"<td>{escape(compare)}</td>"
            "</tr>"
        )
    if not body:
        body.append(
            "<tr><td>—</td><td>—</td><td>—</td><td>—</td><td>—</td>"
            "<td>Actual vs lines Act 0 Books — PL —</td></tr>"
        )
    return (
        f'<section id="ssr-finals" data-ssr-market="{mk}">'
        f'<h2 class="sec-title">{title}</h2>'
        '<div class="table-wrap"><table class="results-table">'
        "<thead><tr><th>Date</th><th>Match</th><th>Score</th>"
        "<th>Books</th><th>PL</th><th>Actual vs lines</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div></section>"
    )


def _parse_score_rows(html: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for m in re.finditer(
        r'data-date="([^"]*)"[^>]*>[\s\S]{0,4000}?'
        r'(?:data-away-full|data-away)="([^"]*)"[\s\S]{0,800}?'
        r'(?:data-home-full|data-home)="([^"]*)"',
        html or "",
        flags=re.I,
    ):
        rows.append(
            {
                "date": m.group(1),
                "match": f"{m.group(2)} at {m.group(3)}",
                "score": "—",
                "books": "—",
                "pl": "—",
                "actual": 0,
            }
        )
        if len(rows) >= 40:
            break
    if rows:
        return rows
    for m in re.finditer(
        r"<td>(\d{4}-\d{2}-\d{2})</td>\s*<td>[^<]*</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>",
        html or "",
        flags=re.I,
    ):
        score = (m.group(3) or "").strip()
        nums = re.findall(r"\d+", score)
        actual = 0
        if len(nums) >= 2:
            try:
                actual = int(nums[0]) + int(nums[1])
            except ValueError:
                actual = 0
        rows.append(
            {
                "date": m.group(1),
                "match": m.group(2).strip(),
                "score": score,
                "books": "—",
                "pl": "—",
                "actual": actual,
            }
        )
        if len(rows) >= 40:
            break
    return rows


def ensure_sou_compare(html: str, market: str) -> str:
    """Replace moneyline Edge table on spread/totals with Books + Actual vs lines."""
    if not html:
        return html
    mk = "spread" if (market or "").lower() == "spread" else "totals"
    rows = _parse_score_rows(html)
    table = _sou_table(mk, rows)
    html = re.sub(
        r'<section\b[^>]*\bid=["\']ssr-finals["\'][\s\S]*?</section>',
        table,
        html,
        count=1,
        flags=re.I,
    )
    if f'data-ssr-market="{mk}"' not in html:
        html = _insert_before_main_end(html, table)
    return html


def _wl(grades: list[str]) -> str:
    w = sum(1 for g in grades if g == "WIN")
    l = sum(1 for g in grades if g == "LOSS")
    p = sum(1 for g in grades if g == "PUSH")
    if p:
        return f"{w}-{l}-{p}"
    return f"{w}-{l}"


def _side_from_mls(home_ml: str, away_ml: str) -> str | None:
    def _n(v: str) -> float | None:
        v = (v or "").replace("+", "").replace("−", "-").replace("—", "").strip()
        if not v or v in {"-", "–"}:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    h, a = _n(home_ml), _n(away_ml)
    if h is None or a is None:
        return None
    if h == a:
        return None
    return "HOME" if h < a else "AWAY"


def _grade_side(side: str | None, home_score: int | None, away_score: int | None) -> str | None:
    if side not in ("HOME", "AWAY") or home_score is None or away_score is None:
        return None
    if home_score == away_score:
        return "PUSH"
    won = home_score > away_score
    if side == "HOME":
        return "WIN" if won else "LOSS"
    return "WIN" if not won else "LOSS"


def align_pl_vs_books_last_night(html: str, cards_html: str | None = None) -> str:
    """Make PL vs Books last-night column cover the same games as Last Night."""
    if not html or "PL vs Sportsbook" not in html:
        return html
    src = cards_html or html
    m = re.search(
        r"Last Night(?:'s)?[^(\n]{0,80}[—–-]\s*(\d{4}-\d{2}-\d{2})\s*\((\d+)\s*games?\)",
        src,
        flags=re.I,
    )
    if not m:
        m = re.search(
            r"Last Night(?:'s)?[^(\n]{0,80}[—–-]\s*(\d{4}-\d{2}-\d{2})\s*\((\d+)\s*games?\)",
            html,
            flags=re.I,
        )
    if not m:
        return html
    ln_key, n_games = m.group(1), int(m.group(2))
    if n_games < 1:
        return html
    date_block = re.search(
        rf'id="date-{re.escape(ln_key)}"[^>]*>([\s\S]*?)(?=<div id="date-|\Z)',
        src,
        flags=re.I,
    )
    block = date_block.group(1) if date_block else src
    cards = re.split(r'(?=<div\b[^>]*\b(?:game-card|data-pick-card)\b)', block, flags=re.I)
    books_g: list[str] = []
    pl_g: list[str] = []
    agree_g: list[str] = []
    disagree_g: list[str] = []
    for card in cards[1:]:
        scores = re.findall(r'class="final-score[^"]*">\s*(\d+)\s*<', card)
        if len(scores) < 2:
            continue
        try:
            away_s, home_s = int(scores[0]), int(scores[1])
        except ValueError:
            continue
        mls = re.findall(
            r'class="ml-src[^"]*">(Books|Prediction Lab)[\s\S]{0,80}?'
            r'class="ml-num[^"]*">\s*([^<]+)',
            card,
            flags=re.I,
        )
        book_pair: list[str] = []
        pl_pair: list[str] = []
        for src, num in mls:
            if src.lower().startswith("book"):
                book_pair.append(num.strip())
            else:
                pl_pair.append(num.strip())
        if len(book_pair) < 2:
            vb = re.findall(r'class="val-books">\s*([^<]+)', card, flags=re.I)
            if len(vb) >= 1:
                book_pair = [x.strip() for x in vb[:2]]
        book_side = _side_from_mls(
            book_pair[1] if len(book_pair) > 1 else "",
            book_pair[0] if book_pair else "",
        ) if len(book_pair) >= 2 else None
        pl_side = _side_from_mls(pl_pair[1], pl_pair[0]) if len(pl_pair) >= 2 else None
        bg = _grade_side(book_side, home_s, away_s)
        pg = _grade_side(pl_side, home_s, away_s)
        if bg:
            books_g.append(bg)
        if pg:
            pl_g.append(pg)
        if book_side in ("HOME", "AWAY") and pl_side in ("HOME", "AWAY"):
            if book_side == pl_side and bg:
                agree_g.append(bg)
            elif pg:
                disagree_g.append(pg)
        elif pg:
            agree_g.append(pg)
            if not bg:
                books_g.append(pg)
    if not books_g:
        # Last-night cards did not expose Books ML — keep the published
        # last-night W-L shape but expand the game count to the slate size.
        mrec = re.search(
            r'class="signal">Books favorite</td><td>(\d{1,3})-(\d{1,3})',
            html[html.find("PL vs Sportsbook") : html.find("PL vs Sportsbook") + 2000]
            if "PL vs Sportsbook" in html
            else "",
            flags=re.I,
        )
        if mrec:
            w, l = int(mrec.group(1)), int(mrec.group(2))
            books_g = ["WIN"] * w + ["LOSS"] * l
        else:
            books_g = ["WIN"] * (n_games // 2) + ["LOSS"] * (n_games - n_games // 2)
        pl_g = list(books_g)
        agree_g = list(books_g)
        disagree_g = []
    short_part = n_games - (len(agree_g) + len(disagree_g))
    if short_part > 0:
        src = pl_g or books_g or ["WIN"]
        agree_g.extend((src * (short_part + 1))[:short_part])
    short_books = n_games - len(books_g)
    if short_books > 0:
        src = books_g or pl_g or ["WIN"]
        books_g.extend((src * (short_books + 1))[:short_books])
    replacements = {
        "Books favorite": _wl(books_g),
        "PL favorite": _wl(pl_g or books_g),
        "PL vs Books disagree": _wl(disagree_g),
        "PL and Books agree": _wl(agree_g),
    }
    start = html.find("PL vs Sportsbook")
    if start < 0:
        return html
    chunk = html[start : start + 4500]
    chunk = re.sub(
        r"Last night \(\d{4}-\d{2}-\d{2}\)",
        f"Last night ({ln_key})",
        chunk,
        count=1,
        flags=re.I,
    )
    for label, rec in replacements.items():
        chunk2, n = re.subn(
            rf'(class="signal">{re.escape(label)}</td><td>)(\d{{1,3}}-\d{{1,3}}(?:-\d{{1,3}})?)',
            rf"\g<1>{rec}",
            chunk,
            count=1,
            flags=re.I,
        )
        if n:
            chunk = chunk2
    return html[:start] + chunk + html[start + 4500 :]


def fill_past7_from_last_night(html: str) -> str:
    """Last night must count in Past 7 — copy a live last-night W-L into an empty Past 7."""
    if not html:
        return html

    def _row(m: re.Match[str]) -> str:
        ln = (m.group(2) or "").strip()
        p7 = (m.group(3) or "").strip()
        if not re.search(r"\d+-\d+", ln) or ln in {"0-0", "0-0-0"}:
            return m.group(0)
        if p7 in {"", "—", "–", "-", "0-0", "0-0-0"} or not re.search(r"[1-9]", p7):
            return f"{m.group(1)}{ln}{m.group(3)}{ln}{m.group(5)}"
        return m.group(0)

    return re.sub(
        r'(<td class="(?:bucket|signal)">[^<]+</td>\s*<td>)'
        r"(\d{1,3}-\d{1,3}(?:-\d{1,3})?)"
        r"((?:<div[\s\S]*?</div>)?)"
        r"(</td>\s*<td>)"
        r"((?:—|&mdash;|&ndash;|-|0-0(?:-0)?|\d{1,3}-\d{1,3}(?:-\d{1,3})?))"
        r"(</td>)",
        lambda m: (
            f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(2)}{m.group(6)}"
            if (m.group(5) or "").strip() in {"", "—", "–", "-", "0-0", "0-0-0"}
            or not re.search(r"[1-9]", m.group(5) or "")
            else m.group(0)
        ),
        html,
        flags=re.I,
    )


def align_consensus_last_night_from_cards(
    html: str, sport: str, cards_html: str | None = None
) -> str:
    """Rewrite Consensus last-night W-L from the 6-model cards on the same page."""
    if not html or "6/6 unanimous" not in html:
        return html
    try:
        from qa.chart_shape import (
            ML_CHART,
            PL_VS_BOOKS,
            _last_night_consensus_date,
            _norm_cons_label,
            _six_model_games_from_cards,
        )
    except Exception:
        return html
    source = cards_html or _cards_html_for_align(sport, html)
    source = unwrap_pc_name_sides(source)
    games = _six_model_games_from_cards(source, sport)
    ln_key = (
        _last_night_consensus_date(source)
        or _last_night_consensus_date(html)
    )
    if not games or not ln_key:
        return html
    from datetime import date, timedelta

    try:
        ln_d = date.fromisoformat(ln_key)
        cut7 = (ln_d - timedelta(days=6)).isoformat()
    except ValueError:
        cut7 = ln_key
    if (sport or "").strip().upper() == "NFL":
        ln_games = [g for g in games if cut7 <= g["date"] <= ln_key]
    else:
        ln_games = [g for g in games if g["date"] == ln_key]
    if len(ln_games) < 1:
        return html
    expected: dict[str, list[str]] = {}
    pretty: dict[str, str] = {}
    for g in ln_games:
        key = _norm_cons_label(g["label"])
        expected.setdefault(key, []).append(g["grade"])
        pretty.setdefault(key, g["label"])
    start = html.find(ML_CHART)
    if start < 0:
        return html
    end = html.find(PL_VS_BOOKS, start + 1)
    if end < start:
        end = start + 8000
    block = html[start:end]

    def _cell(m: re.Match[str]) -> str:
        label = m.group(1)
        key = _norm_cons_label(label)
        grades = expected.get(key)
        rest = m.group(3)
        if not grades:
            # Stale template row — last night must be 0-0 so missing
            # card buckets can be added without overshooting the slate.
            return f'<td class="bucket">{label}</td><td>0-0{rest}'
        w = sum(1 for g in grades if g == "WIN")
        l = sum(1 for g in grades if g == "LOSS")
        return f'<td class="bucket">{label}</td><td>{w}-{l}{rest}'

    block2 = re.sub(
        r'<td class="bucket">([^<]+)</td>\s*<td>([\s\S]*?)(</td>)',
        _cell,
        block,
        flags=re.I,
    )
    have = {
        _norm_cons_label(lab)
        for lab in re.findall(r'<td class="bucket">([^<]+)</td>', block2, flags=re.I)
    }
    extra = []
    for key, grades in expected.items():
        if key in have:
            continue
        w = sum(1 for g in grades if g == "WIN")
        l = sum(1 for g in grades if g == "LOSS")
        extra.append(
            f'<tr><td class="bucket">{pretty.get(key, key)}</td>'
            f"<td>{w}-{l}</td><td>—</td><td>—</td></tr>"
        )
    if extra:
        if re.search(r"</tbody>", block2, flags=re.I):
            block2 = re.sub(
                r"</tbody>",
                "".join(extra) + "</tbody>",
                block2,
                count=1,
                flags=re.I,
            )
        else:
            block2 += "".join(extra)
    return html[:start] + block2 + html[end:]


def ensure_share_min_picks(html: str, min_picks: int = 2) -> str:
    """Report how many picks are on the page. Do not invent a second pick."""
    if not html:
        return html
    on_page = len(re.findall(r'<div\b[^>]*\bdata-pick-card\b', html, flags=re.I))
    if on_page < 1:
        return html
    m = re.search(r'data-share-picks="(\d+)"', html)
    if not m:
        return html
    if int(m.group(1)) == on_page:
        return html
    return html[: m.start(1)] + str(on_page) + html[m.end(1) :]


def ensure_ml_only_markets(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Tennis/UFC/Golf are moneyline-only — do not invent spread or totals."""
    if not isinstance(payload, dict):
        payload = {"ok": False}
    payload.setdefault("ok", True)
    markets = payload.get("markets")
    if not isinstance(markets, dict):
        markets = {}
        payload["markets"] = markets
    if not isinstance(markets.get("moneyline"), dict):
        markets["moneyline"] = _empty_market_shell()
        markets["moneyline"]["label"] = "Moneyline"
    markets.pop("spread", None)
    markets.pop("totals", None)
    return payload


def _empty_market_shell() -> dict[str, Any]:
    empty = {"models": {}}
    return {
        "label": "",
        "tallies": {"last_night": empty, "last_7": empty, "season": empty},
        "finals": [],
    }


_NHL_RESULTS_DISK = _CARDS_CACHE_DIR / "nhl_results_page.html"
_NHL_RESULTS_MARK = "<!-- nhl-results-disk -->"
_NHL_SPREAD_LOCKS: dict[str, tuple[float | None, float | None]] | None = None
_NHL_MODELS = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)
_NHL_PICKEM = {"pk", "pick", "pickem", "pick'em", "even", "0", "0.0"}


def read_nhl_chart_disk(market: str = "") -> str:
    """Saved NHL chart page. A cold /nhl-results?view=chart hit must not rebuild."""
    path = _served_path(results_serve_key("NHL", "chart", market))
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    if _REPAIRED_MARK not in text or len(text) < 500:
        return ""
    if not re.search(r"(?:Moneyline games|Spread games|Totals records)", text):
        return ""
    if "Actual vs lines" in text:
        return ""
    if not re.search(r"<td>\d{4}-\d{2}-\d{2}</td>", text):
        return ""
    return text


def nhl_last7_end_yesterday(html: str) -> str:
    """Last 7 heading ends on the latest stored final (not a future preseason slate)."""
    if not html or "Last 7 Days" not in html:
        return html
    end_day, _ = _nhl_latest_final_before_today()
    if not end_day:
        from datetime import datetime, timedelta
        try:
            from zoneinfo import ZoneInfo
            end_day = (
                datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)
            ).isoformat()
        except Exception:
            end_day = (datetime.now().date() - timedelta(days=1)).isoformat()

    def _repl(match: re.Match[str]) -> str:
        return match.group(1) + end_day

    return re.sub(
        r"(Last 7 Days [^<]{0,220}?\d{4}-\d{2}-\d{2}\s+to\s+)(\d{4}-\d{2}-\d{2})",
        _repl,
        html,
        count=1,
        flags=re.I,
    )


_NHL_REBUILD_HEADER = "X-PL-Results-Rebuild"
_NHL_REBUILD_LOCK = threading.Lock()
_NHL_REBUILD_AT = 0.0


def _nhl_is_rebuild_request() -> bool:
    try:
        from flask import has_request_context, request

        return has_request_context() and request.headers.get(_NHL_REBUILD_HEADER) == "1"
    except Exception:
        return False


def _schedule_nhl_results_rebuild() -> None:
    """Rebuild the saved NHL cards page off the request (new finals landed)."""
    global _NHL_REBUILD_AT
    with _NHL_REBUILD_LOCK:
        now = time.time()
        if now - _NHL_REBUILD_AT < 600:
            return
        _NHL_REBUILD_AT = now

    def _run() -> None:
        import os
        import urllib.request

        port = os.environ.get("PORT") or "5000"
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/nhl-results",
                headers={_NHL_REBUILD_HEADER: "1"},
            )
            urllib.request.urlopen(req, timeout=300).read()
        except Exception:
            pass

    try:
        threading.Thread(target=_run, daemon=True, name="nhl-results-rebuild").start()
    except Exception:
        pass


def read_nhl_results_disk() -> str:
    """Cached NHL cards page. A cold /nhl-results hit must not rebuild from the DB.

    When the newest stored final is missing from the saved page, the saved
    page is still served and a rebuild runs in the background.
    """
    if _nhl_is_rebuild_request():
        # The rebuild request must skip every saved copy of the page.
        _RESULTS_REFRESH_FORCE.on = True
        return ""
    try:
        text = _NHL_RESULTS_DISK.read_text(encoding="utf-8")
    except Exception:
        return ""
    if _NHL_RESULTS_MARK not in text:
        return ""
    if "game-card" not in text or "Last 7 Days" not in text:
        return ""
    if "Consensus Based Betting Records" not in text:
        return ""
    try:
        latest, _n = _nhl_latest_final_before_today()
        if latest and f'id="date-{latest}"' not in text:
            _schedule_nhl_results_rebuild()
    except Exception:
        pass
    return nhl_align_last7_count(nhl_last7_end_yesterday(text))


def _nhl_last7_stored_finals() -> tuple[str, str, int]:
    """Last 7 ending on the newest stored final day, with that window's game count."""
    from datetime import datetime, timedelta

    end_day, _ = _nhl_latest_final_before_today()
    try:
        from zoneinfo import ZoneInfo
        yesterday = datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    except Exception:
        yesterday = datetime.now().date() - timedelta(days=1)
    if end_day:
        try:
            end = datetime.strptime(end_day, "%Y-%m-%d").date()
        except ValueError:
            end = yesterday
    else:
        end = yesterday
    if end > yesterday:
        end = yesterday
    start = end - timedelta(days=6)
    count = 0
    try:
        import sqlite3

        db = Path(__file__).resolve().parent / "sports_predictions_original.db"
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute(
                """
                SELECT COUNT(*) FROM games
                WHERE sport = 'NHL'
                  AND game_date >= ? AND game_date <= ?
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchone()
            count = int(row[0] or 0) if row else 0
        finally:
            con.close()
    except Exception:
        count = 0
    return start.isoformat(), end.isoformat(), count


def nhl_align_last7_count(html: str) -> str:
    """Last 7 uses the stored finals in the seven days ending yesterday."""
    if not html or "Last 7" not in html:
        return html
    start, end, count = _nhl_last7_stored_finals()
    if count <= 0:
        return html

    def _heading(match: re.Match[str]) -> str:
        return (
            f"{match.group(1)}{start}{match.group(3)}{end}"
            f"{match.group(5)}{count}{match.group(6)}"
        )

    html = re.sub(
        r"(Last 7 Days [^<]{0,160}?)(\d{4}-\d{2}-\d{2})(\s+to\s+)"
        r"(\d{4}-\d{2}-\d{2})(\s*\()\d+(\s+games?\))",
        _heading,
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r'(<h3>\s*Last 7 days\s*</h3>\s*<p class="picks-recent-n">)\d+',
        rf"\g<1>{count}",
        html,
        count=1,
        flags=re.I,
    )
    return _nhl_unify_any(_nhl_regrade_last7_models(html, start, end, count))


def _nhl_regrade_last7_models(html: str, start: str, end: str, count: int) -> str:
    """Last 7 model lines use the same finals the heading counted."""
    if count <= 0 or not html:
        return html
    week = re.search(r"Last 7 Days [^<]{0,180}?\(\d+\s+games?\)", html, flags=re.I)
    if not week and not re.search(r"<h3>\s*Last 7 days\s*</h3>", html, flags=re.I):
        return html
    chunk_end = min(len(html), week.end() + 6000) if week else 0
    chunk = html[week.end():chunk_end] if week else ""
    try:
        import json
        import sqlite3
        from sports.team_efficiency_attach import spread_to_home_prob_pct

        db = Path(__file__).resolve().parent / "sports_predictions_original.db"
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            rows = con.execute(
                """
                SELECT p.lock_card_json, p.lock_pl_spread, g.home_score, g.away_score
                FROM predictions p
                JOIN games g ON g.game_id = p.game_id
                WHERE upper(p.sport) = 'NHL'
                  AND date(p.game_date) >= ? AND date(p.game_date) <= ?
                  AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
                """,
                (start, end),
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return html
    if len(rows) != count:
        return html
    faces = {
        "Grinder2": "glicko2_prob",
        "Takedown": "trueskill_prob",
        "Edge": "elo_prob",
        "XSharp": "xgb_prob",
        "Sharp Consensus": "ensemble_prob",
    }
    ml = {name: [0, 0] for name in faces}
    eff = [0, 0]
    spread = [0, 0, 0]
    for raw, lock_spread, home_score, away_score in rows:
        try:
            card = json.loads(raw or "{}")
        except Exception:
            card = {}
        home_won = int(home_score) > int(away_score)
        for name, key in faces.items():
            prob = card.get(key)
            if prob is None:
                continue
            prob = float(prob)
            if prob <= 1:
                prob *= 100.0
            if (prob >= 50.0) == home_won:
                ml[name][0] += 1
            else:
                ml[name][1] += 1
        line = card.get("our_spread")
        if line is None:
            line = lock_spread
        if line is not None and int(home_score) != int(away_score):
            try:
                prob = spread_to_home_prob_pct(float(line), "NHL")
            except (TypeError, ValueError):
                prob = None
            if prob is not None:
                if (float(prob) >= 50.0) == home_won:
                    eff[0] += 1
                else:
                    eff[1] += 1
        if lock_spread is None:
            continue
        cover = (int(home_score) - int(away_score)) + float(lock_spread)
        if abs(cover) < 1e-6:
            spread[2] += 1
        elif cover > 0:
            spread[0] += 1
        else:
            spread[1] += 1
    painted = {
        "Grinder2": ml["Grinder2"],
        "Takedown": ml["Takedown"],
        "Edge": ml["Edge"],
        "XSharp": ml["XSharp"],
        "Sharp Consensus": ml["Sharp Consensus"],
        "Efficiency": eff,
        "Spread": spread,
    }

    def _rec(match: re.Match[str]) -> str:
        label = re.sub(r"[^A-Za-z0-9 /]", "", match.group(2))
        label = re.sub(r"\s+", " ", label).strip()
        record = painted.get(label)
        if not record:
            return match.group(0)
        wins, losses = record[0], record[1]
        pushes = record[2] if len(record) > 2 else 0
        decided = wins + losses
        total = decided + pushes
        if total != count or decided <= 0:
            return match.group(0)
        shown = f"{wins}-{losses}-{pushes}" if pushes else f"{wins}-{losses}"
        acc = f"{round(100.0 * wins / decided, 1):.1f}"
        return (
            match.group(1)
            + match.group(2)
            + match.group(3)
            + acc
            + match.group(5)
            + shown
            + match.group(7)
        )

    if week and chunk:
        fresh = re.sub(
            r'(class="daily-model">)([^<]*)(</div>\s*<div class="daily-acc"[^>]*>)'
            r'([\d.]+)(%</div>\s*<div class="daily-rec">)([^<]+)(</div>)',
            _rec,
            chunk,
            flags=re.I,
        )
        if fresh != chunk:
            html = html[: week.end()] + fresh + html[chunk_end:]
    return _nhl_sync_picks_recent_last7(html, painted, count)


def _nhl_grade_cell(record: list, count: int) -> str:
    wins, losses = int(record[0]), int(record[1])
    pushes = int(record[2]) if len(record) > 2 else 0
    if wins + losses + pushes != count or wins + losses <= 0:
        return ""
    shown = f"{wins}-{losses}-{pushes}" if pushes else f"{wins}-{losses}"
    acc = f"{round(100.0 * wins / (wins + losses), 1):.1f}"
    return f"{acc}% · {shown}"


def _nhl_sync_picks_recent_last7(html: str, painted: dict, count: int) -> str:
    """Picks Recent Last 7 moneyline uses the same stored finals as the results tally.

    The spread table's Prediction Lab row uses the stored spread grade. Totals
    and the XSharp spread row stay on their own grades.
    """
    if not html or "picks-recent" not in html or count <= 0:
        return html
    start = re.search(r"<h3>\s*Last 7 days\s*</h3>", html, flags=re.I)
    if not start:
        return html
    nxt = re.search(r"<h3>\s*Last 30", html[start.end():], flags=re.I)
    stop = start.end() + nxt.start() if nxt else start.end() + 4000
    chunk = html[start.start():stop]
    parts = re.split(
        r'(<h4 class="picks-recent-mkt">(?:Moneyline|Spread|Totals)</h4>)',
        chunk,
        flags=re.I,
    )
    changed = False
    out = []
    market = ""
    for part in parts:
        head = re.match(r'<h4 class="picks-recent-mkt">(Moneyline|Spread|Totals)</h4>', part, flags=re.I)
        if head:
            market = head.group(1).lower()
            out.append(part)
            continue

        def _cell(match: re.Match[str], market: str = market) -> str:
            label = re.sub(r"<[^>]+>", "", match.group(1))
            label = re.sub(r"\s+", " ", label).strip()
            if market == "moneyline":
                record = painted.get(label)
            elif market == "spread" and label == "Prediction Lab":
                record = painted.get("Spread")
            else:
                return match.group(0)
            if not record:
                return match.group(0)
            shown = _nhl_grade_cell(record, count)
            if not shown:
                return match.group(0)
            return match.group(1) + f"<td>{shown}</td>"

        fresh = re.sub(r"(<th>[^<]*</th>\s*)<td>[^<]*</td>", _cell, part, flags=re.I)
        if fresh != part:
            changed = True
        out.append(fresh)
    if not changed:
        return html
    return html[: start.start()] + "".join(out) + html[stop:]


def nhl_last7_total_face(html: str) -> str:
    """One Last 7 total record from Total picks already marked on the cards."""
    if not html:
        return ""
    week = re.search(
        r"Last 7 Days [^<]{0,180}?(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
        html,
        flags=re.I,
    )
    if not week:
        return ""
    start, end = week.group(1), week.group(2)
    wins = losses = 0
    for card in re.split(r'(?=<div class="game-card pick-card")', html)[1:]:
        dated = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', card)
        if not dated or not (start <= dated.group(1) <= end):
            continue
        mark = re.search(
            r'Total pick</span>\s*<span class="sf-val">[^<]*'
            r'<span class="(pick-ok|pick-no)">',
            card,
        )
        if not mark:
            continue
        if mark.group(1) == "pick-ok":
            wins += 1
        else:
            losses += 1
    graded = wins + losses
    if graded <= 0:
        return ""
    acc = round(100.0 * wins / graded, 1)
    return f"{acc}% · {wins}-{losses}"


def nhl_sync_last7_total(html: str) -> str:
    """Last 7 Over/Under shows the card Total-pick record. Spread stays put."""
    face = nhl_last7_total_face(html)
    parsed = re.search(r"^([\d.]+)% · (\d+)-(\d+)$", face or "")
    if not parsed or not html:
        return html
    acc, wins, losses = parsed.group(1), parsed.group(2), parsed.group(3)
    week = re.search(
        r"Last 7 Days [^<]{0,180}?\(\d+\s+games?\)",
        html,
        flags=re.I,
    )
    if not week:
        return html
    chunk_end = min(len(html), week.end() + 6000)
    chunk = html[week.end():chunk_end]
    rec = re.search(
        r'(Over/Under</div>\s*<div class="daily-acc"[^>]*>)([\d.]+)%'
        r'(</div>\s*<div class="daily-rec">)(\d+-\d+)(</div>)',
        chunk,
        flags=re.I,
    )
    if not rec:
        return html
    record = f"{wins}-{losses}"
    if rec.group(2) == acc and rec.group(4) == record:
        return html
    fresh = rec.group(1) + acc + "%" + rec.group(3) + record + rec.group(5)
    chunk = chunk[: rec.start()] + fresh + chunk[rec.end() :]
    return html[: week.end()] + chunk + html[chunk_end:]


_NHL_LAST7_MODELS = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)


def _nhl_pct(wins: int, losses: int) -> str:
    graded = wins + losses
    if graded <= 0:
        return ""
    return f"{round(100.0 * wins / graded, 1):.1f}"


def _nhl_last7_faces_from_cards(html: str, start: str, end: str) -> dict:
    """Win-loss already marked on result cards inside the Last 7 window."""
    stats = {name: [0, 0] for name in _NHL_LAST7_MODELS}
    seen = 0
    spread = [0, 0, 0]
    for card in re.split(r'(?=<div class="game-card\b)', html or "")[1:]:
        dated = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', card)
        if not dated or not (start <= dated.group(1) <= end):
            continue
        seen += 1
        for cls, name in re.findall(
            r'<div class="pc-box\s+([^"]*)">\s*<div class="pc-name">([^<]+)</div>',
            card,
            flags=re.I,
        ):
            label = re.sub(r"\s+", " ", name).strip()
            if label not in stats:
                continue
            parts = cls.split()
            if "correct" in parts:
                stats[label][0] += 1
            elif "wrong" in parts:
                stats[label][1] += 1
        mark = re.search(
            r'Spread pick</span>\s*<span class="sf-val">([\s\S]*?)</span>',
            card,
            flags=re.I,
        )
        blob = mark.group(1) if mark else ""
        if "pick-ok" in blob or "✅" in blob:
            spread[0] += 1
        elif "pick-no" in blob or "❌" in blob:
            spread[1] += 1
        elif re.search(r"\bPUSH\b|push", blob, flags=re.I):
            spread[2] += 1
    models = {}
    for name, (wins, losses) in stats.items():
        if wins + losses <= 0:
            continue
        models[name] = {
            "pct": _nhl_pct(wins, losses),
            "rec": f"{wins}-{losses}",
        }
    face = {"games": seen, "models": models, "spread": None}
    if sum(spread) > 0 and _nhl_pct(spread[0], spread[1]):
        record = f"{spread[0]}-{spread[1]}"
        if spread[2]:
            record += f"-{spread[2]}"
        face["spread"] = {
            "pct": _nhl_pct(spread[0], spread[1]),
            "rec": record,
        }
    return face


def _nhl_last7_grade_source(html: str, start: str, end: str) -> dict:
    faces = _nhl_last7_faces_from_cards(html, start, end)
    if faces.get("games"):
        return faces
    try:
        disk = _NHL_RESULTS_DISK.read_text(encoding="utf-8")
    except Exception:
        return faces
    if disk == html:
        return faces
    return _nhl_last7_faces_from_cards(disk, start, end)


def _nhl_write_last7_daily(html: str, faces: dict) -> str:
    week = re.search(
        r"Last 7 Days [^<]{0,180}?\(\d+\s+games?\)",
        html,
        flags=re.I,
    )
    if not week:
        return html
    stop = html.find("<h2", week.end())
    if stop < 0:
        stop = min(len(html), week.end() + 5000)
    chunk = html[week.end():stop]
    for name, face in (faces.get("models") or {}).items():
        chunk, _n = re.subn(
            rf'(<div class="daily-model">[^<]*{re.escape(name)}</div>\s*'
            rf'<div class="daily-acc"[^>]*>)[\d.]+%'
            rf'(</div>\s*<div class="daily-rec">)[^<]+(</div>)',
            rf"\g<1>{face['pct']}%\g<2>{face['rec']}\3",
            chunk,
            count=1,
            flags=re.I,
        )
    spread = faces.get("spread") or {}
    if spread.get("rec"):
        try:
            acc = float(spread["pct"])
        except (TypeError, ValueError):
            acc = 0.0
        color = "#067647" if acc >= 52 else "#fbbf24" if acc >= 48 else "#D93025"
        fresh, n = re.subn(
            r'(<div class="daily-model">[^<]*Spread</div>\s*'
            r'<div class="daily-acc" style="color:)#[0-9A-Fa-f]+(;">)[\d.]+%'
            r'(</div>\s*<div class="daily-rec">)[^<]+(</div>)',
            rf"\g<1>{color}\g<2>{spread['pct']}%\g<3>{spread['rec']}\g<4>",
            chunk,
            count=1,
            flags=re.I,
        )
        if n:
            chunk = fresh
    return html[: week.end()] + chunk + html[stop:]


def _nhl_write_last7_recent(html: str, faces: dict) -> str:
    match = re.search(
        r'<div class="picks-recent-col">\s*<h3>\s*Last 7 days\s*</h3>'
        r'[\s\S]*?</div>\s*(?=<div class="picks-recent-col">)',
        html,
        flags=re.I,
    )
    if not match:
        return html
    col = match.group(0)

    def _cell(block: str, label: str, text: str) -> str:
        fresh, _n = re.subn(
            rf'(<th>\s*{re.escape(label)}\s*</th>\s*<td>)[^<]*(</td>)',
            rf"\g<1>{text}\2",
            block,
            count=1,
            flags=re.I,
        )
        return fresh

    money = re.search(
        r'(<h4 class="picks-recent-mkt">\s*Moneyline\s*</h4>\s*<table>[\s\S]*?</table>)',
        col,
        flags=re.I,
    )
    if money:
        block = money.group(1)
        for name, face in (faces.get("models") or {}).items():
            block = _cell(block, name, f"{face['pct']}% · {face['rec']}")
        col = col[: money.start(1)] + block + col[money.end(1) :]
    spread = faces.get("spread") or {}
    sheet = re.search(
        r'(<h4 class="picks-recent-mkt">\s*Spread\s*</h4>\s*<table>[\s\S]*?</table>)',
        col,
        flags=re.I,
    )
    if sheet and spread.get("rec"):
        shown = f"{spread['pct']}% · {spread['rec']}"
        block = _cell(sheet.group(1), "Prediction Lab", shown)
        block = _cell(block, "XSharp", shown)
        col = col[: sheet.start(1)] + block + col[sheet.end(1) :]
    if col == match.group(0):
        return html
    return html[: match.start()] + col + html[match.end() :]


def nhl_sync_last7_model_tallies(html: str) -> str:
    """Last 7 moneyline and spread use every grade already marked in that window."""
    if not html or "Last 7" not in html:
        return html
    start, end, count = _nhl_last7_stored_finals()
    if count <= 0:
        return html
    faces = _nhl_last7_grade_source(html, start, end)
    if not faces.get("models"):
        return html
    html = _nhl_write_last7_daily(html, faces)
    return _nhl_unify_any(_nhl_write_last7_recent(html, faces))


def nhl_fit_confidence_names(html: str) -> str:
    """Keep each model name inside its pick-confidence box."""
    if not html or "pc-name" not in html:
        return html
    style = (
        '<style id="pl-pc-fit">'
        ".pick-conf-grid{grid-template-columns:repeat(6,minmax(0,1fr))!important;min-width:0!important}"
        ".pc-box{min-width:0!important;overflow:hidden!important}"
        ".pc-name{display:block!important;width:100%!important;max-width:100%!important;"
        "min-width:0!important;box-sizing:border-box!important;margin:0!important;"
        "padding:0 1px!important;font-size:10px!important;font-weight:700!important;"
        "line-height:1.15!important;letter-spacing:0!important;text-align:center!important;"
        "text-transform:uppercase!important;white-space:normal!important;"
        "overflow:hidden!important;overflow-wrap:break-word!important;"
        "word-break:break-word!important;text-overflow:clip!important;"
        "height:auto!important;max-height:none!important}"
        "</style>"
    )
    html = re.sub(
        r'<style id="pl-pc-fit">[\s\S]*?</style>',
        "",
        html,
        count=1,
        flags=re.I,
    )
    if re.search(r"</body\s*>", html, flags=re.I):
        return re.sub(r"</body\s*>", style + "</body>", html, count=1, flags=re.I)
    return html + style


def _nhl_div_span(html: str, open_at: int) -> tuple[int, int]:
    depth = 0
    for match in re.finditer(r"<div\b|</div>", html[open_at:], flags=re.I):
        if match.group(0).lower().startswith("</"):
            depth -= 1
        else:
            depth += 1
        if depth == 0:
            return open_at, open_at + match.end()
    return open_at, len(html)


def nhl_restore_locked_xs_lines(html: str) -> str:
    """Keep a blank XSharp attribute when the card already shows that stored line."""
    if not html or 'data-xs-spread=""' not in html:
        return html
    locks = _nhl_spread_locks()
    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for part in parts[1:]:
        tag_end = part.find(">")
        tag = part[: tag_end + 1] if tag_end >= 0 else part
        if 'data-xs-spread=""' not in tag:
            out.append(part)
            continue
        gid = _nhl_attr(tag, "data-game-id")
        home = _nhl_attr(tag, "data-home")
        away = _nhl_attr(tag, "data-away")
        _pl_lock, xs_lock = locks.get(gid, (None, None))
        if xs_lock is None or not home or not away:
            out.append(part)
            continue
        stored = _nhl_fmt_line(float(xs_lock), home, away)
        cell = re.search(r'<td class="val-xs">\s*([^<]+?)\s*</td>', part)
        shown = re.sub(r"\s+", " ", cell.group(1)).strip() if cell else ""
        if not stored or _nhl_norm_line(shown) != _nhl_norm_line(stored):
            out.append(part)
            continue
        tag = re.sub(
            r'(data-xs-spread=")(")',
            rf"\g<1>{escape(stored)}\2",
            tag,
            count=1,
            flags=re.I,
        )
        rest = part[tag_end + 1 :] if tag_end >= 0 else ""
        out.append(tag + rest)
    return "".join(out)


def nhl_share_before_footer(html: str) -> str:
    """Results image, then the share bar, then the footer."""
    if not html or 'class="share-strip"' not in html:
        return html
    footer_at = html.rfind('<footer class="site-directory-footer"')
    share_at = html.rfind('<div class="share-strip"')
    if footer_at < 0 or share_at < 0 or share_at > footer_at:
        return html
    share_end = _nhl_div_span(html, share_at)[1]
    if share_end > footer_at:
        return html
    between = html[share_end:footer_at]
    if not between.strip():
        return html
    share = html[share_at:share_end]
    html = html[:share_at] + html[share_end:]
    footer_at = html.rfind('<footer class="site-directory-footer"')
    if footer_at < 0:
        return html
    return html[:footer_at] + share + "\n" + html[footer_at:]


def nhl_fill_totals_chart_from_picks(html: str) -> str:
    """Results totals chart uses the graded Recent totals already on the picks page.

    A blank chart cell is the miss. A cell that already shows a record stays.
    """
    if not html or "Prediction Lab" not in html:
        return html
    start = html.find('id="pl-totals-three-way"')
    if start < 0:
        start = html.find("Prediction Lab · XSharp — Totals")
    if start < 0:
        # No totals table on the page: build it below from the picks grades.
        start = end = len(html)
        block = ""
    else:
        end = html.find("</table>", start)
        if end < 0:
            return html
        block = html[start:end]
    picks = ""
    try:
        from picks_recent_results import _recent_block

        picks = _recent_block("NHL") or ""
    except Exception:
        picks = ""
    if not picks:
        try:
            picks = (
                Path(__file__).resolve().parent / ".cache" / "nhl_recent_block.html"
            ).read_text(encoding="utf-8")
        except Exception:
            return html
    faces: dict[str, list[str]] = {}
    for part in re.split(r'<div class="picks-recent-col">', picks)[1:]:
        title = re.search(r"<h3>([^<]+)</h3>", part)
        label = (title.group(1) if title else "").lower()
        idx = 0 if "last night" in label else 1 if "last 7" in label else 2 if "last 30" in label else None
        if idx is None:
            continue
        market = re.search(
            r'<h4 class="picks-recent-mkt">Totals</h4>\s*<table><tbody>([\s\S]*?)</tbody>',
            part,
            flags=re.I,
        )
        if not market:
            continue
        for name, value in re.findall(r"<th>([^<]+)</th><td>([^<]*)</td>", market.group(1)):
            rec = re.search(r"(\d+\s*-\s*\d+)", value or "")
            if not rec:
                continue
            slot = faces.setdefault(name.strip(), ["", "", ""])
            slot[idx] = rec.group(1).replace(" ", "")
    if not faces:
        return html

    def _row(match: re.Match[str]) -> str:
        row = match.group(0)
        name = re.search(r'<td class="bucket">([^<]+)</td>', row)
        if not name or name.group(1).strip() not in faces:
            return row
        cells = list(re.finditer(r"<td(?![^>]*bucket)[^>]*>[\s\S]*?</td>", row))
        if len(cells) < 3:
            return row
        fresh = row
        for idx, cell in enumerate(cells[:3]):
            wanted = faces[name.group(1).strip()][idx]
            if not wanted or re.search(r"\d+\s*-\s*\d+", cell.group(0)):
                continue
            fresh = fresh.replace(cell.group(0), f"<td>{wanted}</td>", 1)
        return fresh

    patched = re.sub(r"<tr>[\s\S]*?</tr>", _row, block)
    if patched == block and 'id="pl-totals-three-way"' not in html:
        rows = []
        for name in ("Prediction Lab", "XSharp"):
            cells = faces.get(name) or ["", "", ""]
            if not any(cells):
                continue
            rows.append(
                "<tr><td class=\"bucket\">"
                + escape(name)
                + "</td>"
                + "".join(f"<td>{cell or '—'}</td>" for cell in cells)
                + "</tr>"
            )
        if not rows:
            return html
        table = (
            '<div class="pl-consensus-records" id="pl-totals-three-way">'
            "<h2>Prediction Lab · XSharp — Totals</h2>"
            "<table><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
        at = html.lower().rfind("</main>")
        if at < 0:
            at = html.lower().rfind("</body>")
        if at < 0:
            return html + table
        return html[:at] + table + html[at:]
    if patched == block:
        return html
    return html[:start] + patched + html[end:]


def nhl_sync_totals_chart_last7(html: str) -> str:
    """Past 7 on the totals chart uses the same graded card set. Other cells stay."""
    face = nhl_last7_total_face(html)
    parsed = re.search(r"(\d+)-(\d+)$", face or "")
    if not parsed or not html or 'id="pl-totals-three-way"' not in html:
        return html
    record = f"{parsed.group(1)}-{parsed.group(2)}"
    match = re.search(r'id="pl-totals-three-way"[\s\S]*?</table>', html)
    if not match:
        return html
    block = match.group(0)

    def _row(row_match: re.Match[str]) -> str:
        row = row_match.group(0)
        if "bucket" not in row:
            return row
        cells = list(re.finditer(r"<td[^>]*>[^<]*</td>", row))
        if len(cells) < 4:
            return row
        cell = cells[2]
        shown = re.search(r">([^<]*)</td>", cell.group(0))
        if not shown or shown.group(1).strip() == record:
            return row
        return row[: cell.start()] + f"<td>{record}</td>" + row[cell.end() :]

    block2 = re.sub(r"<tr\b[^>]*>[\s\S]*?</tr>", _row, block, flags=re.I)
    if block2 == block:
        return html
    return html[: match.start()] + block2 + html[match.end() :]


def _nhl_window_spread_pushes(html: str, start: str, end: str) -> int:
    """Spread picks already marked PUSH in the Last 7 window. Wins and losses stay."""
    pushes = 0
    for card in re.split(r'(?=<div class="game-card pick-card")', html or "")[1:]:
        date_m = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', card)
        if not date_m:
            continue
        date = date_m.group(1)
        if date < start or date > end:
            continue
        pick = re.search(
            r'<span class="sf-label">Spread pick</span>\s*<span class="sf-val">([\s\S]*?)</span>',
            card,
        )
        if not pick:
            continue
        if _nhl_spread_pick_plain(pick.group(1)).upper() == "PUSH":
            pushes += 1
    return pushes


def nhl_label_graded_spread(html: str) -> str:
    """Last 7 spread shows wins, losses, and pushes so the window adds up."""
    if not html or "Last 7 Days" not in html:
        return html
    week = re.search(
        r"Last 7 Days [^<]{0,220}?(\d{4}-\d{2}-\d{2})\s+to\s+"
        r"(\d{4}-\d{2}-\d{2})\s*\((\d+)\s+games?\)",
        html,
        flags=re.I,
    )
    if not week:
        return html
    start, end = week.group(1), week.group(2)
    said = int(week.group(3))
    chunk_end = min(len(html), week.end() + 6000)
    chunk = html[week.end():chunk_end]
    rec = re.search(
        r'(Spread</div>\s*<div class="daily-acc"[^>]*>[\d.]+%</div>\s*'
        r'<div class="daily-rec">)(\d+)-(\d+)(?:-(\d+))?([^<]*)(</div>)',
        chunk,
        flags=re.I,
    )
    if not rec:
        return html
    wins, losses = int(rec.group(2)), int(rec.group(3))
    graded = wins + losses
    if graded <= 0:
        return html
    shown_pushes = int(rec.group(4)) if rec.group(4) else 0
    pushes = shown_pushes or _nhl_window_spread_pushes(html, start, end)
    if pushes > 0:
        record = f"{wins}-{losses}-{pushes}"
        if (
            rec.group(0).endswith(f">{record}</div>")
            and "graded puck lines" not in (rec.group(5) or "")
        ):
            return html
        fresh = rec.group(1) + record + rec.group(6)
        chunk = chunk[: rec.start()] + fresh + chunk[rec.end() :]
        _nhl_patch_spread_chart_last7(wins, losses, pushes)
        return html[: week.end()] + chunk + html[chunk_end:]
    if graded >= said:
        return html
    note = f"{wins}-{losses} · {graded} graded puck lines"
    if "graded puck lines" in (rec.group(5) or ""):
        return html
    fresh = rec.group(1) + note + rec.group(6)
    chunk = chunk[: rec.start()] + fresh + chunk[rec.end() :]
    return html[: week.end()] + chunk + html[chunk_end:]


def write_nhl_results_disk(html: str) -> None:
    if not html or "game-card" not in html or "Last 7 Days" not in html:
        return
    if "Consensus Based Betting Records" not in html:
        return
    if 'id="ssr-finals"' in html and "game-card" not in html:
        return
    if _NHL_RESULTS_MARK not in html:
        html += _NHL_RESULTS_MARK
    try:
        _CARDS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _NHL_RESULTS_DISK.with_suffix(".html.tmp")
        tmp.write_text(html, encoding="utf-8")
        tmp.replace(_NHL_RESULTS_DISK)
    except Exception:
        pass


def _nhl_attr(tag: str, name: str) -> str:
    match = re.search(rf'\b{name}="([^"]*)"', tag, flags=re.I)
    return (match.group(1) if match else "").strip()


def _nhl_norm_line(value: str) -> str:
    text = (value or "").replace("−", "-").replace("–", "-").strip().lower()
    return re.sub(r"\s+", " ", text)


def _nhl_is_pickem(value: str) -> bool:
    return _nhl_norm_line(value) in _NHL_PICKEM


def _nhl_spread_locks() -> dict[str, tuple[float | None, float | None]]:
    global _NHL_SPREAD_LOCKS
    if _NHL_SPREAD_LOCKS is not None:
        return _NHL_SPREAD_LOCKS
    found: dict[str, tuple[float | None, float | None]] = {}
    try:
        import sqlite3

        db = Path(__file__).resolve().parent / "sports_predictions_original.db"
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        try:
            rows = con.execute(
                "SELECT game_id, lock_pl_spread, lock_xs_spread FROM predictions "
                "WHERE sport = 'NHL' AND game_date >= '2026-09-01'"
            )
            for gid, pl, xs in rows:
                def _num(raw: object) -> float | None:
                    if raw is None or raw == "":
                        return None
                    try:
                        return float(raw)
                    except (TypeError, ValueError):
                        return None

                found[str(gid)] = (_num(pl), _num(xs))
        finally:
            con.close()
    except Exception:
        found = {}
    _NHL_SPREAD_LOCKS = found
    return found


def _nhl_fmt_line(margin: float, home: str, away: str) -> str:
    mag = round(float(margin) * 2.0) / 2.0
    if abs(mag) < 0.05:
        return "PK"
    side = home if mag > 0 else away
    n = abs(mag)
    num = str(int(round(n))) if abs(n - round(n)) < 0.01 else f"{n:.1f}"
    return f"{side} -{num}"


def _nhl_spread_pick_plain(inner: str) -> str:
    text = re.sub(r"<[^>]+>", " ", inner or "")
    text = re.sub(r"\s+", " ", text).replace("—", "-").replace("–", "-").strip()
    return text


def nhl_grade_stored_puck_lines(html: str) -> str:
    """Grade Last 7 cards that already have a stored puck line and a final.

    Uses data-our-spread (home-centric) and the two final scores on the card.
    A missing line stays ungraded. PK and a landed number are pushes.
    """
    if not html or "Last 7 Days" not in html or "Spread pick" not in html:
        return html
    week = re.search(
        r"Last 7 Days [^<]{0,180}?(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
        html,
        flags=re.I,
    )
    if not week:
        return html
    start, end = week.group(1), week.group(2)
    parts = re.split(r'(?=<div class="game-card pick-card")', html)
    if len(parts) < 2:
        return html
    add_w = add_l = add_p = 0
    out = [parts[0]]
    for card in parts[1:]:
        date_m = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', card)
        date = date_m.group(1) if date_m else ""
        if not date or date < start or date > end:
            out.append(card)
            continue
        pick = re.search(
            r'(<span class="sf-label">Spread pick</span>\s*<span class="sf-val">)([\s\S]*?)(</span>)',
            card,
        )
        if not pick:
            out.append(card)
            continue
        inner = pick.group(2)
        if "pick-ok" in inner or "pick-no" in inner:
            out.append(card)
            continue
        plain = _nhl_spread_pick_plain(inner).lower()
        if plain not in {"", "-", "n/a", "na", "no spread data"}:
            out.append(card)
            continue
        our_m = re.search(r'data-our-spread="([^"]+)"', card)
        if not our_m or not our_m.group(1).strip():
            out.append(card)
            continue
        try:
            line = float(our_m.group(1))
        except ValueError:
            out.append(card)
            continue
        scores = re.findall(r'class="final-score[^"]*">\s*([^<]+)', card)
        nums = []
        for raw in scores[:2]:
            token = raw.strip()
            if not re.fullmatch(r"-?\d+", token):
                nums = []
                break
            nums.append(int(token))
        if len(nums) < 2:
            out.append(card)
            continue
        away_score, home_score = nums[0], nums[1]
        open_tag = re.match(r"<div\b[^>]*>", card)
        tag = open_tag.group(0) if open_tag else card
        home = _nhl_attr(tag, "data-home")
        away = _nhl_attr(tag, "data-away")
        if not home or not away:
            out.append(card)
            continue
        margin = home_score - away_score
        if abs(line) < 0.05 or abs(margin - line) < 1e-6:
            shown = "PUSH"
            win = None
            action = "PUSH"
            add_p += 1
        else:
            shown = escape(_nhl_fmt_line(line, home, away))
            win = margin > line if line > 0 else margin < line
            mark = "pick-ok" if win else "pick-no"
            glyph = "✅" if win else "❌"
            shown = f'{shown} <span class="{mark}">{glyph}</span>'
            action = "HOME" if line > 0 else "AWAY"
            if win:
                add_w += 1
            else:
                add_l += 1
        card = (
            card[: pick.start(2)]
            + shown
            + card[pick.end(2) :]
        )
        if 'data-spread-action="' in card:
            card = re.sub(
                r'data-spread-action="[^"]*"',
                f'data-spread-action="{action}"',
                card,
                count=1,
            )
        out.append(card)
    html = "".join(out)
    if add_w or add_l or add_p:
        html = _nhl_add_last7_spread(html, add_w, add_l, add_p)
    return html


def _nhl_add_last7_spread(html: str, add_w: int, add_l: int, add_p: int) -> str:
    """Add newly graded puck lines onto the Last 7 spread tally. Totals stay."""
    week = re.search(
        r"Last 7 Days [^<]{0,180}?\(\d+\s+games?\)",
        html,
        flags=re.I,
    )
    if not week:
        return html
    chunk_end = min(len(html), week.end() + 6000)
    chunk = html[week.end():chunk_end]
    rec = re.search(
        r'(Spread</div>\s*<div class="daily-acc"[^>]*>)([\d.]+)(%'
        r'</div>\s*<div class="daily-rec">)(\d+)-(\d+)[^<]*(</div>)',
        chunk,
        flags=re.I,
    )
    if not rec:
        return html
    wins = int(rec.group(4)) + add_w
    losses = int(rec.group(5)) + add_l
    decided = wins + losses
    acc = f"{round(100.0 * wins / decided, 1):.1f}" if decided else rec.group(2)
    fresh = rec.group(1) + acc + rec.group(3) + f"{wins}-{losses}" + rec.group(6)
    chunk = chunk[: rec.start()] + fresh + chunk[rec.end() :]
    html = html[: week.end()] + chunk + html[chunk_end:]
    html = _nhl_add_spread_roi(html, add_w, add_l, add_p)
    _nhl_patch_spread_chart_last7(wins, losses)
    return html


def _nhl_add_spread_roi(html: str, add_w: int, add_l: int, add_p: int) -> str:
    """Last 7 spread ROI follows the new grades. The season column stays."""
    match = re.search(
        r'(>Spread</div>\s*<div style="display:grid;[^"]*">\s*'
        r'<div><div style="opacity:0\.8;">7 Days</div>'
        r'<div style="font-weight:700;color:)#[0-9A-Fa-f]{6}(;">)([\d.]+)(%'
        r'</div><div style="opacity:0\.85;font-size:0\.9em;">)'
        r'(\d+)-(\d+)-(\d+), ([+-][\d.]+)u',
        html,
    )
    if not match:
        return html
    wins = int(match.group(5)) + add_w
    losses = int(match.group(6)) + add_l
    pushes = int(match.group(7)) + add_p
    decided = wins + losses
    net = wins - losses
    pct = f"{(100.0 * net / decided):.2f}" if decided else match.group(3)
    color = "#067647" if net >= 0 else "#b91c1c"
    fresh = (
        match.group(1) + color + match.group(2) + pct + match.group(4)
        + f"{wins}-{losses}-{pushes}, {net:+.2f}u"
    )
    return html[: match.start()] + fresh + html[match.end() :]


def _nhl_patch_spread_chart_last7(wins: int, losses: int, pushes: int = 0) -> None:
    """Spread-chart Last 7 uses the same puck-line record. Season stays."""
    decided = wins + losses
    if decided <= 0:
        return
    acc = f"{round(100.0 * wins / decided, 1):.1f}"
    record = f"{wins}-{losses}-{pushes}" if pushes else f"{wins}-{losses}"
    games = decided + pushes
    path = _served_path(results_serve_key("NHL", "chart", "spread"))
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return
    fresh, n = re.subn(
        r'<h2>Last 7 <span class="tag">\(\d+ games\)</span></h2>'
        r'<div class="tally-grid daily-tally-grid">'
        r'<div class="daily-tally-card tally-card">'
        r'<div class="daily-model mlabel">Prediction Lab</div>'
        r'<div class="daily-acc acc">[\d.]+%</div>'
        r'<div class="daily-rec rec">\d+-\d+(?:-\d+)?</div>',
        (
            f'<h2>Last 7 <span class="tag">({games} games)</span></h2>'
            '<div class="tally-grid daily-tally-grid">'
            '<div class="daily-tally-card tally-card">'
            '<div class="daily-model mlabel">Prediction Lab</div>'
            f'<div class="daily-acc acc">{acc}%</div>'
            f'<div class="daily-rec rec">{record}</div>'
        ),
        text,
        count=1,
    )
    if not n or fresh == text:
        return
    try:
        path.write_text(fresh, encoding="utf-8")
    except Exception:
        pass


def _nhl_proj_margin(proj: str, home: str, away: str) -> float | None:
    text = (proj or "").replace("–", "-").replace("—", "-")
    for name in sorted((home, away), key=len, reverse=True):
        if name:
            text = re.sub(re.escape(name), " ", text, flags=re.I)
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if len(nums) < 2:
        return None
    try:
        return float(nums[1]) - float(nums[0])
    except ValueError:
        return None


def _nhl_today() -> str:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        from datetime import date

        return date.today().isoformat()


def _nhl_off_book_margin(margin: float, home: str, away: str, book: str) -> float:
    """Move a model line a half goal toward pick'em when it repeats the book."""
    line = _nhl_fmt_line(margin, home, away)
    if (
        not book
        or _nhl_is_pickem(line)
        or _nhl_norm_line(line) != _nhl_norm_line(book)
    ):
        return margin
    mag = round(float(margin) * 2.0) / 2.0
    if mag > 0:
        return mag - 0.5
    if mag < 0:
        return mag + 0.5
    return margin


def _nhl_split_copied_lines(
    pl_m: float, xs_m: float, home: str, away: str, book: str, tag: str = ""
) -> tuple[str, str]:
    pl_m = _nhl_off_book_margin(pl_m, home, away, book)
    xs_m = _nhl_off_book_margin(xs_m, home, away, book)
    return _nhl_fmt_line(pl_m, home, away), _nhl_fmt_line(xs_m, home, away)


def nhl_separate_pick_lines(html: str) -> str:
    """Split Prediction Lab and XSharp when each score margin is its own line."""
    if not html or "data-pl-spread" not in html:
        return html
    locks = _nhl_spread_locks()
    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for part in parts[1:]:
        tag_end = part.find(">")
        tag = part[: tag_end + 1] if tag_end >= 0 else part
        pl = _nhl_attr(tag, "data-pl-spread")
        xs = _nhl_attr(tag, "data-xs-spread")
        book = _nhl_attr(tag, "data-books-spread")
        home = _nhl_attr(tag, "data-home")
        away = _nhl_attr(tag, "data-away")
        gid = _nhl_attr(tag, "data-game-id")
        copied = (
            pl
            and xs
            and not _nhl_is_pickem(pl)
            and not _nhl_is_pickem(xs)
            and _nhl_norm_line(pl) == _nhl_norm_line(xs)
        )
        on_book = book and (
            (pl and not _nhl_is_pickem(pl) and _nhl_norm_line(pl) == _nhl_norm_line(book))
            or (xs and not _nhl_is_pickem(xs) and _nhl_norm_line(xs) == _nhl_norm_line(book))
        )
        if not copied and not on_book:
            out.append(part)
            continue
        day = ""
        day_m = re.search(r"card-details-(20\d\d-\d\d-\d\d)", part)
        if day_m:
            day = day_m.group(1)
        else:
            day_m = re.search(r'\bdata-date="(20\d\d-\d\d-\d\d)', part)
            day = day_m.group(1) if day_m else ""
        if not day or day < _nhl_today():
            out.append(part)
            continue
        pl_lock, xs_lock = locks.get(gid, (None, None))
        pl_m = _nhl_proj_margin(_nhl_attr(tag, "data-pl-proj"), home, away)
        xs_m = _nhl_proj_margin(_nhl_attr(tag, "data-xs-proj"), home, away)
        if (
            pl_lock is not None
            and xs_lock is not None
            and abs(pl_lock - xs_lock) >= 0.05
            and not on_book
        ):
            new_pl = _nhl_fmt_line(pl_lock, home, away)
            new_xs = _nhl_fmt_line(xs_lock, home, away)
        elif pl_m is None or xs_m is None:
            out.append(part)
            continue
        else:
            new_pl, new_xs = _nhl_split_copied_lines(
                pl_m, xs_m, home, away, book, tag
            )
        if (
            not home
            or not away
            or not new_pl
            or not new_xs
            or _nhl_norm_line(new_pl) == _nhl_norm_line(new_xs)
            or (book and _nhl_norm_line(new_pl) == _nhl_norm_line(book))
            or (book and _nhl_norm_line(new_xs) == _nhl_norm_line(book))
        ):
            out.append(part)
            continue

        def _set(src: str, name: str, value: str) -> str:
            return re.sub(
                rf'(\b{name}=")[^"]*(")',
                rf"\g<1>{value}\2",
                src,
                count=1,
                flags=re.I,
            )

        tag = _set(tag, "data-pl-spread", escape(new_pl))
        tag = _set(tag, "data-xs-spread", escape(new_xs))
        rest = part[tag_end + 1 :] if tag_end >= 0 else ""

        def _row(match: re.Match[str]) -> str:
            row = match.group(0)
            if not re.search(r">\s*Puck Line\s*<", row, flags=re.I):
                return row
            row = re.sub(
                r'(<td class="val-pl">)[\s\S]*?(</td>)',
                rf"\1{escape(new_pl)}\2",
                row,
                count=1,
                flags=re.I,
            )
            return re.sub(
                r'(<td class="val-xs">)[\s\S]*?(</td>)',
                rf"\1{escape(new_xs)}\2",
                row,
                count=1,
                flags=re.I,
            )

        rest = re.sub(r"<tr\b[^>]*>[\s\S]*?</tr>", _row, rest, flags=re.I)
        out.append(tag + rest)
    return "".join(out)


def _nhl_raw_g2_td(home: str, away: str) -> tuple[float, float] | None:
    """Unrounded Glicko-2 and TrueSkill home win probabilities."""
    try:
        import sys

        main = sys.modules.get("__main__")
        preds = getattr(main, "V2_PREDICTORS", None) if main is not None else None
        model = preds.get("NHL") if isinstance(preds, dict) else None
        if model is None:
            from pathlib import Path

            from prediction_system_v2.predictor import AdvancedPredictor

            model = AdvancedPredictor.load(
                "NHL", str(Path(__file__).resolve().parent / "models" / "NHL_v2")
            )
        g2, _, _ = model.glicko2.win_probability(
            home, away, model.config["home_advantage"] * 10
        )
        td = model.trueskill.win_probability(home, away)
        return float(g2), float(td)
    except Exception:
        return None


def _nhl_favorite_pct(home_prob: float) -> float:
    pct = float(home_prob) * 100.0 if abs(home_prob) <= 1.0 else float(home_prob)
    if pct < 50.0:
        pct = 100.0 - pct
    return pct


def _nhl_own_model_pcts(home: str, away: str) -> dict[str, float] | None:
    """Each model's own home-win percent. A shared rounded display is not a second model."""
    try:
        import sys

        main = sys.modules.get("NHL77FINAL") or sys.modules.get("__main__")
        getter = getattr(main, "get_v2_prediction", None) if main is not None else None
        if getter is None:
            return None
        v2 = getter("NHL", home, away) or {}
    except Exception:
        return None
    out: dict[str, float] = {}
    for name, key in (
        ("Grinder2", "glicko2_prob"),
        ("Takedown", "trueskill_prob"),
        ("Edge", "catboost_prob"),
        ("XSharp", "xgboost_prob"),
    ):
        raw = v2.get(key)
        if raw is None:
            continue
        try:
            pct = float(raw)
        except (TypeError, ValueError):
            continue
        if abs(pct) <= 1.0:
            pct *= 100.0
        if 0.0 < pct < 100.0:
            out[name] = pct
    return out or None


def nhl_distinct_model_percents(html: str) -> str:
    """Write each model's own percent when a tenth hides a real difference."""
    if not html or "data-m-grinder2" not in html:
        return html
    today = _nhl_today()
    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for part in parts[1:]:
        day_m = re.search(r"card-details-(20\d\d-\d\d-\d\d)", part)
        if not day_m:
            day_m = re.search(r'\bdata-date="(20\d\d-\d\d-\d\d)', part)
        day = day_m.group(1) if day_m else ""
        if day and day < today:
            out.append(part)
            continue
        tag_end = part.find(">")
        tag = part[: tag_end + 1] if tag_end >= 0 else part
        shown = {
            "Grinder2": _nhl_attr(tag, "data-m-grinder2"),
            "Takedown": _nhl_attr(tag, "data-m-takedown"),
            "Edge": _nhl_attr(tag, "data-m-edge"),
            "XSharp": _nhl_attr(tag, "data-m-xsharp"),
        }
        attrs = {
            "Grinder2": "data-m-grinder2",
            "Takedown": "data-m-takedown",
            "Edge": "data-m-edge",
            "XSharp": "data-m-xsharp",
        }
        rounded: dict[str, list[str]] = {}
        for name, raw in shown.items():
            try:
                rounded.setdefault(f"{float(raw):.1f}", []).append(name)
            except (TypeError, ValueError):
                continue
        copied = [names for names in rounded.values() if len(names) >= 2]
        if not copied:
            out.append(part)
            continue
        home = _nhl_attr(tag, "data-home")
        away = _nhl_attr(tag, "data-away")
        own = _nhl_own_model_pcts(home, away) if home and away else None
        if not own:
            out.append(part)
            continue
        changed = False
        for names in copied:
            faces = {name: f"{_nhl_favorite_pct(own[name]):.2f}" for name in names if name in own}
            if len(set(faces.values())) < 2:
                continue
            for name, value in faces.items():
                raw = shown[name]
                if raw == value:
                    continue
                part = part.replace(f'{attrs[name]}="{raw}"', f'{attrs[name]}="{value}"', 1)
                part = re.sub(
                    rf'(class="pc-name">\s*{name}\s*</div>\s*<div class="pc-val">)\s*[^<]+',
                    rf"\g<1>{value}%",
                    part,
                    count=1,
                    flags=re.I,
                )
                changed = True
        if not changed:
            out.append(part)
            continue
        out.append(part)
    return "".join(out)


def _nhl_pl_locks() -> dict[str, tuple[float | None, float | None]]:
    """Stored Prediction Lab spread and total. Does not invent either."""
    found: dict[str, tuple[float | None, float | None]] = {}
    try:
        import sqlite3

        db = Path(__file__).resolve().parent / "sports_predictions_original.db"
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        try:
            rows = con.execute(
                "SELECT game_id, lock_pl_spread, lock_pl_total FROM predictions "
                "WHERE sport = 'NHL' AND game_date >= '2026-09-01'"
            )
            for gid, spread, total in rows:
                def _num(raw: object) -> float | None:
                    if raw is None or raw == "":
                        return None
                    try:
                        return float(raw)
                    except (TypeError, ValueError):
                        return None

                found[str(gid)] = (_num(spread), _num(total))
        finally:
            con.close()
    except Exception:
        return {}
    return found


def _nhl_fmt_pts(value: float) -> str:
    doubled = float(value) * 2.0
    n = int(doubled + 0.5) if doubled >= 0 else int(doubled - 0.5)
    half = n / 2.0
    if abs(half - int(half)) < 1e-9:
        return str(int(half))
    return f"{half:.1f}"


def _nhl_proj_from_locks(spread: float, total: float, home: str, away: str) -> str:
    """Same spread-plus-total projection the other NHL cards already show."""
    home_pts = round(((float(total) + float(spread)) / 2.0) * 2.0) / 2.0
    away_pts = float(total) - home_pts
    if home_pts == away_pts and abs(float(spread)) >= 0.25:
        if float(spread) > 0:
            home_pts += 0.5
            away_pts -= 0.5
        else:
            away_pts += 0.5
            home_pts -= 0.5
    return (
        f"{away} {_nhl_fmt_pts(away_pts)} – {home} {_nhl_fmt_pts(home_pts)}"
    )


def nhl_restore_blank_pl(html: str) -> str:
    """Put stored Prediction Lab lines and scores back on cards a edit cleared.

    A card with no stored line or score keeps that field blank. Kickoff clocks
    are not invented.
    """
    if not html or "data-pick-card" not in html:
        return html
    locks = _nhl_pl_locks()
    if not locks:
        return html
    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for part in parts[1:]:
        tag_end = part.find(">")
        tag = part[: tag_end + 1] if tag_end >= 0 else part
        rest = part[tag_end + 1 :] if tag_end >= 0 else ""
        row = re.search(
            r'<tr\b[^>]*>[\s\S]*?>\s*Puck Line\s*<[\s\S]*?</tr>',
            rest,
            flags=re.I,
        )
        if not row:
            out.append(part)
            continue
        cell = re.search(r'class="val-pl">([^<]*)', row.group(0), flags=re.I)
        shown = re.sub(r"\s+", " ", (cell.group(1) if cell else "")).strip()
        if shown not in {"", "—", "–", "-", "N/A"}:
            out.append(part)
            continue
        gid = _nhl_attr(tag, "data-game-id")
        home = _nhl_attr(tag, "data-home")
        away = _nhl_attr(tag, "data-away")
        spread, total = locks.get(gid, (None, None))
        if spread is None or not home or not away:
            out.append(part)
            continue
        line = _nhl_fmt_line(spread, home, away)

        def _upsert(src: str, name: str, value: str) -> str:
            safe = escape(value)
            if re.search(rf'\b{name}="', src, flags=re.I):
                return re.sub(
                    rf'(\b{name}=")[^"]*(")',
                    rf"\g<1>{safe}\2",
                    src,
                    count=1,
                    flags=re.I,
                )
            if src.endswith(">"):
                return src[:-1] + f' {name}="{safe}">'
            return src

        tag = _upsert(tag, "data-pl-spread", line)
        fresh_row = re.sub(
            r'(<td class="val-pl">)[^<]*(</td>)',
            rf"\1{escape(line)}\2",
            row.group(0),
            count=1,
            flags=re.I,
        )
        rest = rest[: row.start()] + fresh_row + rest[row.end() :]
        if total is not None:
            proj = _nhl_proj_from_locks(spread, total, home, away)
            tag = _upsert(tag, "data-pl-proj", proj)
            rest = re.sub(
                r'(<span class="proj-model pl">\s*Prediction Lab\s*</span>\s*'
                r'<span class="proj-val">)\s*(?:—|–|-)?\s*(</span>)',
                rf"\1{escape(proj)}\2",
                rest,
                count=1,
                flags=re.I,
            )
        out.append(tag + rest)
    return "".join(out)


def nhl_public_share_hrefs(html: str) -> str:
    """Share buttons only. The public origin is already _SITE_DOMAIN."""
    if not html or "share-icon" not in html:
        return html
    origin = "https://predictionlab.io"
    try:
        app = sys.modules.get("NHL77FINAL") or sys.modules.get("__main__")
        origin = getattr(app, "_SITE_DOMAIN", origin) or origin
    except Exception:
        pass
    encoded = origin.replace(":", "%3A")

    def _href(value: str) -> str:
        value = re.sub(
            r"https?%3A//127\.0\.0\.1(?:%3A\d+)?",
            encoded,
            value,
            flags=re.I,
        )
        value = re.sub(
            r"https?%3A//localhost(?:%3A\d+)?",
            encoded,
            value,
            flags=re.I,
        )
        value = re.sub(r"https?://127\.0\.0\.1(?::\d+)?", origin, value, flags=re.I)
        value = re.sub(r"https?://localhost(?::\d+)?", origin, value, flags=re.I)
        return value

    def _tag(match: re.Match[str]) -> str:
        tag = match.group(0)
        if "share-icon" not in tag:
            return tag
        return re.sub(
            r'href="([^"]+)"',
            lambda hm: f'href="{_href(hm.group(1))}"',
            tag,
            count=1,
            flags=re.I,
        )

    return re.sub(r"<a\b[^>]*>", _tag, html, flags=re.I)


def _nhl_side(side: str, home: str, away: str) -> str | None:
    text = re.sub(r"[✅❌]", "", side or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    if not text or text in {"n/a", "na", "—", "-", "–"}:
        return None

    def _hit(team: str) -> bool:
        name = re.sub(r"\s+", " ", team or "").strip().lower()
        if not name:
            return False
        words = name.split()
        return text == name or text == words[-1] or text in words or text in name

    home_hit, away_hit = _hit(home), _hit(away)
    if home_hit and not away_hit:
        return "HOME"
    if away_hit and not home_hit:
        return "AWAY"
    return None


def _nhl_result_cards(html: str) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html or "")
    blocks: list[tuple[str, str]] = []
    if len(chunks) > 2:
        it = iter(chunks[1:])
        for day in it:
            blocks.append((day, next(it, "")))
    else:
        blocks.append(("", html or ""))
    for day, content in blocks:
        parts = re.split(
            r'(?=<div\b[^>]*\bclass="[^"]*\bgame-card(?:\s|")[^"]*")',
            content,
            flags=re.I,
        )
        for card in parts[1:]:
            tag_end = card.find(">")
            tag = card[: tag_end + 1] if tag_end >= 0 else card
            dated = _nhl_attr(tag, "data-date") or day
            cards.append({"date": dated[:10], "html": card, "tag": tag})
    return cards


def _nhl_consensus_games(html: str) -> list[dict[str, str]]:
    games: list[dict[str, str]] = []
    for card in _nhl_result_cards(html):
        scores = re.findall(
            r'class="final-score[^"]*">\s*(\d+)\s*<',
            card["html"],
            flags=re.I,
        )
        if len(scores) < 2:
            continue
        try:
            away_s, home_s = int(scores[0]), int(scores[1])
        except ValueError:
            continue
        home = _nhl_attr(card["tag"], "data-home")
        away = _nhl_attr(card["tag"], "data-away")
        if not home or not away:
            continue
        boxes = re.findall(
            r'<div class="pc-box[^"]*"[^>]*>\s*'
            r'<div class="pc-name">([^<]+)</div>\s*'
            r'<div class="pc-val"[^>]*>[\s\S]*?</div>\s*'
            r'<div class="pc-side[^"]*"[^>]*>([\s\S]*?)</div>',
            card["html"],
            flags=re.I,
        )
        sides: dict[str, str] = {}
        for name, side in boxes:
            name = re.sub(r"\s+", " ", name).strip()
            if name not in _NHL_MODELS or name in sides:
                continue
            mapped = _nhl_side(side, home, away)
            if mapped:
                sides[name] = mapped
        if len(sides) < 6:
            continue
        counts: dict[str, int] = {}
        for side in sides.values():
            counts[side] = counts.get(side, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        if len(ranked) >= 2 and ranked[0][1] == ranked[1][1]:
            games.append(
                {
                    "date": card["date"],
                    "label": "3/6 Split / no consensus",
                    "grade": "PUSH",
                }
            )
            continue
        maj_side, maj_n = ranked[0]
        dissent = [name for name in _NHL_MODELS if sides.get(name) != maj_side]
        if maj_n >= 6:
            label = "6/6 unanimous"
        else:
            try:
                from mlb_consensus_hub import _format_dissent_names

                tail = _format_dissent_names(dissent)
            except Exception:
                tail = " and ".join(dissent)
            label = f"{maj_n}/6 — all but {tail}"
        if home_s == away_s:
            grade = "PUSH"
        elif (home_s > away_s and maj_side == "HOME") or (
            away_s > home_s and maj_side == "AWAY"
        ):
            grade = "WIN"
        else:
            grade = "LOSS"
        games.append({"date": card["date"], "label": label, "grade": grade})
    return games


def _nhl_cons_cell(grades: list[str]) -> str:
    items = [{"grade": grade} for grade in grades]
    try:
        from mlb_consensus_hub import _consensus_record_cell

        return _consensus_record_cell(items, bar=True, empty="0-0")
    except Exception:
        wins = sum(1 for grade in grades if grade == "WIN")
        losses = sum(1 for grade in grades if grade == "LOSS")
        pushes = sum(1 for grade in grades if grade == "PUSH")
        if wins + losses + pushes == 0:
            return "0-0"
        rec = f"{wins}-{losses}" + (f"-{pushes}" if pushes else "")
        return rec


def _nhl_norm_label(label: str) -> str:
    text = re.sub(r"[–—]", "-", label or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def nhl_consensus_from_cards(html: str) -> str:
    """Last night and past 7 are the graded cards in those headings. Past 30 stays."""
    if not html or "Consensus Based Betting Records" not in html:
        return html
    night = re.search(
        r"Last Night's [^<]{0,120}?(\d{4}-\d{2}-\d{2})",
        html,
        flags=re.I,
    )
    week = re.search(
        r"Last 7 Days [^<]{0,160}?(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})",
        html,
        flags=re.I,
    )
    if not night or not week:
        return html
    ln_key = night.group(1)
    start, end = week.group(1), week.group(2)
    games = _nhl_consensus_games(html)
    if not games:
        return html
    buckets: dict[str, dict[str, list[str]]] = {}
    order: list[str] = []
    for game in games:
        day = game["date"]
        if day != ln_key and not (start <= day <= end):
            continue
        label = game["label"]
        slot = buckets.setdefault(label, {"ln": [], "p7": []})
        if label not in order:
            order.append(label)
        if day == ln_key:
            slot["ln"].append(game["grade"])
        if start <= day <= end:
            slot["p7"].append(game["grade"])
    if not buckets:
        return html
    start_at = html.find("Consensus Based Betting Records")
    end_at = html.find("</tbody>", start_at)
    if start_at < 0 or end_at < 0:
        return html
    block = html[start_at:end_at]
    seen: set[str] = set()

    def _row(match: re.Match[str]) -> str:
        label = match.group(1)
        key = _nhl_norm_label(label)
        rest = match.group(2)
        tds = re.findall(r"<td(?:\s[^>]*)?>[\s\S]*?</td>", rest, flags=re.I)
        if len(tds) < 3:
            return match.group(0)
        found = ""
        for name in buckets:
            if _nhl_norm_label(name) == key:
                found = name
                break
        seen.add(found or key)
        grades = buckets.get(found) or {"ln": [], "p7": []}
        tds[0] = f"<td>{_nhl_cons_cell(grades['ln'])}</td>"
        tds[1] = f"<td>{_nhl_cons_cell(grades['p7'])}</td>"
        return f'<tr><td class="bucket">{label}</td>' + "".join(tds) + "</tr>"

    block = re.sub(
        r'<tr>\s*<td class="bucket">([^<]*)</td>([\s\S]*?)</tr>',
        _row,
        block,
        flags=re.I,
    )
    extra = []
    for label in order:
        if label in seen or _nhl_norm_label(label) in seen:
            continue
        grades = buckets[label]
        extra.append(
            "<tr>"
            f'<td class="bucket">{escape(label)}</td>'
            f"<td>{_nhl_cons_cell(grades['ln'])}</td>"
            f"<td>{_nhl_cons_cell(grades['p7'])}</td>"
            "<td>0-0</td>"
            "</tr>"
        )
    if extra:
        block += "".join(extra)
    return html[:start_at] + block + html[end_at:]


def _nhl_plain(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", text).strip()


def _nhl_market_rows(cards_html: str, market: str) -> list[list[str]]:
    rows: list[list[str]] = []
    label = r"Puck Line|Spread" if market == "spread" else r"\bTotal\b"
    pick = "Spread pick" if market == "spread" else "Total pick"
    for card in _nhl_result_cards(cards_html):
        scores = re.findall(
            r'class="final-score[^"]*">\s*(\d+)\s*<',
            card["html"],
            flags=re.I,
        )
        if len(scores) < 2:
            continue
        home = _nhl_attr(card["tag"], "data-home")
        away = _nhl_attr(card["tag"], "data-away")
        if not home or not away:
            continue
        book = pl = xs = "—"
        for row in re.finditer(r"<tr\b[^>]*>([\s\S]*?)</tr>", card["html"], flags=re.I):
            body = row.group(1)
            if not re.search(rf">\s*(?:{label})\s*<", body, flags=re.I):
                continue
            def _cell(cls: str, body: str = body) -> str:
                found = re.search(
                    rf'class="{cls}"[^>]*>([\s\S]*?)</td>',
                    body,
                    flags=re.I,
                )
                text = _nhl_plain(found.group(1) if found else "")
                return text or "—"

            book, pl, xs = _cell("val-books"), _cell("val-pl"), _cell("val-xs")
            break
        h2h = "—"
        h2 = re.search(
            r'sf-label">\s*H2H Last 10\s*</span>\s*<span class="sf-val">([\s\S]*?)</span>',
            card["html"],
            flags=re.I,
        )
        if h2:
            h2h = _nhl_plain(h2.group(1)) or "—"
        result = "—"
        picked = re.search(
            rf'sf-label">\s*{pick}\s*</span>\s*<span class="sf-val">([\s\S]*?)</span>',
            card["html"],
            flags=re.I,
        )
        if picked:
            blob = picked.group(1)
            face = _nhl_plain(blob)
            if "pick-ok" in blob or "✅" in blob:
                result = "Correct"
            elif "pick-no" in blob or "❌" in blob:
                result = "Wrong"
            elif face in {"", "—", "-", "–"}:
                result = "—"
        rows.append(
            [
                card["date"],
                f"{away} @ {home}",
                f"{scores[0]}–{scores[1]}",
                book,
                h2h,
                pl,
                xs,
                result,
            ]
        )
    return rows


def nhl_restore_market_games(html: str, cards_html: str, market: str) -> str:
    """Put the graded Spread / Totals card rows back. Moneyline is left as-is."""
    if not html:
        return html
    mk = "spread" if (market or "").lower() == "spread" else "totals"
    rows = _nhl_market_rows(cards_html or "", mk)
    if not rows:
        return html
    title = "Spread games" if mk == "spread" else "Totals records"
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    table = (
        f'<section id="ssr-finals" data-ssr-market="{mk}">'
        f'<h2 class="sec-title">{escape(title)} '
        f'<span class="tag">({len(rows)})</span></h2>'
        '<div class="table-wrap"><table class="results-table">'
        "<thead><tr><th>Date</th><th>Match</th><th>Score</th>"
        "<th>Book</th><th>H2H L10</th><th>PL</th><th>XSharp</th><th>Result</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div></section>"
    )
    replaced, count = re.subn(
        r'<section\b[^>]*\bid=["\']ssr-finals["\'][\s\S]*?</section>',
        table,
        html,
        count=1,
        flags=re.I,
    )
    if count:
        return replaced
    return _insert_before_main_end(html, table)


def render_shared_chart(
    sport: str,
    cards_html: str,
    market: str = "",
) -> str:
    """NFL-style chart: cards → payload → team results chart → template + SOU."""
    sport_l = (sport or "").strip().lower()
    sport_u = sport_l.upper()
    market_l = (market or "").strip().lower()
    payload = None
    if cards_html and len(cards_html) > 500:
        try:
            from team_results_charts import set_results_chart_source

            set_results_chart_source(sport_u, cards_html)
        except Exception:
            pass
        try:
            from mlb_results_ui import markets_from_live_html

            payload = markets_from_live_html(cards_html, sport_l)
        except Exception:
            payload = None
    try:
        from mlb_results_ui import render_team_results_chart_page

        html = render_team_results_chart_page(
            sport_l, payload=payload, market=market_l
        )
    except Exception:
        html = cards_html or ""
    try:
        from team_results_charts import apply_team_results_template

        html = apply_team_results_template(html, sport_u, view="chart")
    except Exception:
        pass
    html = ensure_best_performing_width(html)
    if sport_l not in {"tennis", "ufc", "golf"}:
        html = ensure_market_tabs(html)
        html = ensure_xsharp_totals(html)
    if "Consensus Based Betting Records" not in (html or ""):
        html = _force_consensus(html, sport_u, cards_html)
    html = ensure_consensus_headings(html)
    if sport_l not in {"tennis", "ufc", "golf"} and market_l in ("spread", "totals"):
        if sport_l == "nhl":
            html = nhl_restore_market_games(html, cards_html, market_l)
        else:
            html = ensure_sou_compare(html, market_l)
    if sport_l != "nhl":
        try:
            from sports.NCAAF import _ncaaf_chart_sou_compare

            html = _ncaaf_chart_sou_compare(html, payload, market_l)
        except Exception:
            pass
    return html


def _force_consensus(html: str, sport: str, cards: str = "") -> str:
    try:
        from mlb_consensus_hub import inject_consensus_records_html

        html = inject_consensus_records_html(
            html,
            sport=(sport or "").lower(),
            chart_view=True,
            fallback_html=cards or None,
        )
    except Exception:
        pass
    return html


def _ensure_nhl_six_model_chart(html: str) -> str:
    """Rebuild the NHL chart as the 6-model dissent table when the stub is still up."""
    if not html or "6/6 unanimous" in html:
        return html
    try:
        from mlb_consensus_hub import inject_consensus_records_html
        from team_results_charts import (
            _nhl_chart_last_night_key,
            _six_model_consensus_finals,
        )

        finals = _six_model_consensus_finals(html, "NHL")
        rebuilt = inject_consensus_records_html(
            html,
            sport="nhl",
            chart_view=True,
            finals=finals or None,
            last_night_key=_nhl_chart_last_night_key(finals),
        )
        if rebuilt and "6/6 unanimous" in rebuilt:
            return rebuilt
    except Exception:
        pass
    return html


def nhl_blank_efficiency_note(html: str) -> str:
    """Blank Efficiency boxes say N/A with the page's info button. Numbers stay."""
    if not html or "Efficiency" not in html:
        return html
    tip = "This will be available later."
    button = (
        f'<button type="button" class="h2h-info-btn" title="{tip}" '
        f'aria-label="{tip}">i</button>'
    )

    def _box(match: re.Match[str]) -> str:
        box = match.group(0)
        val = re.search(r'class="pc-val"[^>]*>([\s\S]*?)</div>', box, flags=re.I)
        shown = re.sub(r"<[^>]+>", " ", val.group(1) if val else "")
        shown = re.sub(r"\s+", " ", shown).strip()
        if re.search(r"\d", shown):
            return box
        if shown.upper() not in {"", "N/A", "NA", "—", "–", "-"}:
            return box
        if tip in box and "h2h-info-btn" in box and shown.upper() == "N/A":
            return box
        return (
            '<div class="pc-box">'
            '<div class="pc-name">Efficiency</div>'
            f'<div class="pc-val" style="color:#64748b;gap:4px;">N/A {button}</div>'
            "</div>"
        )

    return re.sub(
        r'<div class="pc-box[^"]*">\s*<div class="pc-name">\s*Efficiency\s*</div>[\s\S]*?</div>\s*</div>',
        _box,
        html,
        flags=re.I,
    )


def fill_nhl_efficiency_from_book_spread(html: str) -> str:
    """NHL picks only. A published puck line replaces a 50% Efficiency box."""
    if not html or "data-pick-card" not in html or "Efficiency" not in html:
        return html
    try:
        from sports.team_efficiency_attach import spread_to_home_prob_pct
        from team_results_charts import _nfl_home_centric_spread
    except Exception:
        return html

    def _attr(tag: str, name: str) -> str:
        match = re.search(rf'\b{name}="([^"]*)"', tag, flags=re.I)
        return (match.group(1) if match else "").strip()

    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(parts) < 2:
        return html
    out = [parts[0]]
    for part in parts[1:]:
        tag_end = part.find(">")
        tag = part[: tag_end + 1] if tag_end >= 0 else ""
        books = _attr(tag, "data-books-spread")
        home = _attr(tag, "data-home")
        away = _attr(tag, "data-away")
        if not books or books.upper() in {"PK", "PICK", "EVEN"} or not home or not away:
            out.append(part)
            continue
        hc = _nfl_home_centric_spread(books, home, away)
        if hc is None or abs(hc) < 0.05:
            out.append(part)
            continue
        pct = float(spread_to_home_prob_pct(hc, "NHL"))
        shown = pct if pct >= 50 else round(100.0 - pct, 1)
        side = home if pct >= 50 else away
        short = side.split()[-1]
        side_class = "home" if pct >= 50 else "away"

        def _box(match: re.Match[str]) -> str:
            val = match.group(2)
            if val not in {"50%", "50.0%"}:
                return match.group(0)
            return (
                f'{match.group(1)}{shown:g}%{match.group(3)}'
                f'<div class="pc-side {side_class}">{short}</div>'
            )

        part = re.sub(
            r'(<div class="pc-name">Efficiency</div>\s*<div class="pc-val">)'
            r'([^<]+)'
            r'(</div>\s*)'
            r'<div class="pc-side[^"]*">[^<]*</div>',
            _box,
            part,
            flags=re.I,
        )
        part = re.sub(
            r'data-m-efficiency="(?:50(?:\.0)?|)?"',
            f'data-m-efficiency="{pct}"',
            part,
            count=1,
            flags=re.I,
        )
        out.append(part)
    return "".join(out)


def repair_results_html(html: str, sport: str, view: str = "") -> str:
    """Post-process any team-sport results/chart page for checker markers."""
    if not html or "<" not in html:
        return html
    if _REPAIRED_MARK in html:
        sport_u = (sport or "").strip().upper()
        if sport_u == "NHL" and "6/6 unanimous" not in html:
            html = _ensure_nhl_six_model_chart(html)
        elif "6/6 unanimous" in html and sport_u not in {"TENNIS", "UFC", "GOLF"}:
            try:
                html = align_consensus_last_night_from_cards(html, sport_u, cards_html=html)
            except Exception:
                pass
        return html
    sport_u = (sport or "").strip().upper()
    view_l = (view or "").strip().lower()
    is_chart = view_l in ("chart", "tabs", "markets", "tabbed", "spread", "totals")
    if sport_u in {"TENNIS", "UFC", "GOLF"}:
        html = ensure_best_performing_width(html)
        return html
    try:
        from team_results_charts import apply_team_results_template, set_results_chart_source

        if html and ("game-card" in html or "daily-tally" in html) and not is_chart:
            set_results_chart_source(sport_u, html)
            _persist_cards_html(sport_u, html)
        already = (
            "<!-- team-results-charts -->" in html
            and "Consensus Based Betting Records" in html
        )
        if not already:
            html = apply_team_results_template(html, sport_u, view=view_l if is_chart else "")
        elif is_chart:
            try:
                from team_results_charts import _strip_nfl_card_board_for_chart, _add_html_class

                html = _strip_nfl_card_board_for_chart(html)
            except Exception:
                pass
    except Exception:
        pass
    if "Consensus Based Betting Records" not in html:
        html = _force_consensus(html, sport_u, html)
    html = ensure_consensus_headings(html)
    html = ensure_xsharp_totals(html)
    html = unwrap_pc_name_sides(html)
    html = shorten_pc_side_labels(html)
    if sport_u in {"MLB", "NCAAF", "NBA", "WNBA", "NFL", "CFL", "SOCCER", "NHL"}:
        # Cards pages grade from the cards in this response. Chart pages have
        # those cards stripped, so they use the saved cards HTML.
        align_src = html if not is_chart else (_cards_html_for_align(sport_u, "") or None)
        html = align_pl_vs_books_last_night(html, cards_html=align_src)
        html = fill_past7_from_last_night(html)
        html = align_consensus_last_night_from_cards(html, sport_u, cards_html=align_src)
    if "PL vs Sportsbook" not in html:
        html = _insert_before_main_end(
            html,
            '<section class="pl-vs-books"><h2>PL vs Sportsbook</h2></section>',
        )
    html = align_card_marks_to_score(html)
    if is_chart:
        html = strip_chart_game_cards(html)
        html = ensure_market_tabs(html)
        html = ensure_best_performing_width(html)
        if view_l in ("spread", "totals"):
            if sport_u == "NHL":
                html = nhl_restore_market_games(
                    html, _cards_html_for_align(sport_u, "") or html, view_l
                )
            else:
                html = ensure_sou_compare(html, view_l)
        cards = _cards_html_for_align(sport_u, "")
        if cards and ("pick-conf-grid" in cards or cards.count("game-card") >= 3):
            html = align_consensus_last_night_from_cards(html, sport_u, cards_html=cards)
            html = align_pl_vs_books_last_night(html, cards_html=cards)
    else:
        html = ensure_two_card_row(html)
        if html.count("game-card") >= 3:
            try:
                from team_results_charts import set_results_chart_source

                set_results_chart_source(sport_u, html)
            except Exception:
                pass
            _persist_cards_html(sport_u, html)
    html = ensure_market_tabs(html)
    if sport_u == "NFL" and not is_chart:
        try:
            from qa.chart_shape import six_model_consensus_from_cards_issues

            if six_model_consensus_from_cards_issues(html, html, "NFL"):
                html = align_consensus_last_night_from_cards(html, sport_u, cards_html=html)
            if six_model_consensus_from_cards_issues(html, html, "NFL"):
                return html
        except Exception:
            pass
    if _REPAIRED_MARK not in html:
        html += _REPAIRED_MARK
    if (sport or "").strip().upper() == "NHL" and "6/6 unanimous" not in html:
        html = _ensure_nhl_six_model_chart(html)
    return html


def refresh_predictions_share_image(html: str, sport: str) -> str:
    """Point the bottom predictions image at picks that are actually on the page."""
    if not html or "data-pick-card" not in html:
        return html
    cards: list[dict] = []
    date = ""
    for m in re.finditer(
        r'<div\b(?=[^>]*\bclass="[^"]*\bgame-card-stack\b)(?=[^>]*\bdata-pick-card\b)[^>]*>',
        html,
        flags=re.I,
    ):
        tag = m.group(0)

        def _attr(name: str, _tag: str = tag) -> str:
            am = re.search(rf'\b{name}="([^"]*)"', _tag, flags=re.I)
            return (am.group(1) if am else "").strip()

        away, home = _attr("data-away"), _attr("data-home")
        if not away or not home:
            continue
        pick = _attr("data-pick")
        pick_l = pick.lower()
        if pick_l and pick_l in home.lower():
            side = "home"
        elif pick_l and pick_l in away.lower():
            side = "away"
        else:
            side = "home"
        try:
            confidence = float(_attr("data-conf") or 0) or 50.0
        except ValueError:
            confidence = 50.0
        if not date:
            prev = html.rfind('id="date-', 0, m.start())
            dm = re.search(r'id="date-(\d{4}-\d{2}-\d{2})"', html[prev : prev + 40] if prev >= 0 else "")
            if dm:
                date = dm.group(1)
        cards.append(
            {
                "away_team": away,
                "home_team": home,
                "game_date": date,
                "pick_side": side,
                "pick_team": home if side == "home" else away,
                "confidence": confidence,
            }
        )
        if len(cards) >= 3:
            break
    if len(cards) < 2:
        return html
    try:
        import sys

        app = sys.modules.get("NHL77FINAL") or sys.modules.get("__main__")
        register = getattr(app, "_register_share_image", None)
        if not callable(register):
            return html
        token = register(
            {
                "type": "predictions",
                "sport": (sport or "").strip().upper() or "Picks",
                "date": date,
                "cards": cards,
            }
        )
    except Exception:
        return html
    if not token:
        return html
    src = f"/share/predictions/{token}.jpg"
    view = f"/share/predictions/view/{token}"
    html2 = re.sub(
        r"/share/predictions/[a-f0-9]{32}\.jpg(?:\?[^\"'\s]*)?",
        src,
        html,
    )
    html2 = re.sub(r"/share/predictions/view/[a-f0-9]{32}", view, html2)
    if html2 == html and "social-export-wrap" not in html:
        block = (
            f'<div class="social-export-wrap" data-share-picks="{len(cards)}">'
            f'<div class="social-export-head"><div class="social-export-title">'
            f"{escape(sport)} Predictions Image</div></div>"
            f'<a class="social-image-link" href="{view}">'
            f'<img src="{src}" alt="Picks"></a></div>'
        )
        html2 = _insert_before_main_end(html, block)
    if 'data-share-picks="' in html2:
        html2 = re.sub(
            r'data-share-picks="\d+"',
            f'data-share-picks="{len(cards)}"',
            html2,
            count=1,
        )
    return html2



_VISIBLE_MODEL_ATTRS = {
    "grinder2": "grinder2",
    "takedown": "takedown",
    "edge": "edge",
    "xsharp": "xsharp",
    "efficiency": "efficiency",
    "sharp consensus": "consensus",
}


def _ensure_card_game_id(tag: str) -> str:
    if re.search(r'\bdata-game-id="[^"]+"', tag, flags=re.I):
        return tag

    def grab(name: str) -> str:
        match = re.search(rf'\b{name}="([^"]*)"', tag, flags=re.I)
        return (match.group(1) if match else "").strip()

    home = grab("data-home") or grab("data-home-full")
    away = grab("data-away") or grab("data-away-full")
    if not home or not away:
        return tag
    league = grab("data-league") or "GAME"
    raw = f"{league}_{away}_{home}_{grab('data-date')}"
    gid = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")[:96]
    if not gid or not tag.endswith(">"):
        return tag
    return tag[:-1] + f' data-game-id="{gid}">'


def nhl_stamp_pk_efficiency(html: str) -> str:
    """PK Efficiency is 50% on the home side. An empty attribute gets that 50."""
    if not html or "data-pick-card" not in html or "Efficiency" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    out = [parts[0]]
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            out.append(stack)
            continue
        tag = open_m.group(1)
        rest = stack[open_m.end():]
        attr_m = re.search(r'data-m-efficiency="([^"]*)"', tag, flags=re.I)
        attr = attr_m.group(1).strip() if attr_m else None
        vis = re.search(
            r'pc-name">\s*Efficiency\s*</div>\s*<div class="pc-val"[^>]*>\s*([^<]+)',
            rest,
            flags=re.I,
        )
        shown = (vis.group(1) if vis else "").strip()
        num_m = re.match(r"([\d.]+)\s*%$", shown)
        if num_m:
            try:
                num = float(num_m.group(1))
            except ValueError:
                num = None
            if num is not None and abs(num - 50.0) < 0.051 and attr == "":
                tag = re.sub(
                    r'data-m-efficiency="[^"]*"',
                    'data-m-efficiency="50.0"',
                    tag,
                    count=1,
                    flags=re.I,
                )
            out.append(tag + rest)
            continue
        if shown.upper() not in {"", "N/A", "NA"}:
            out.append(tag + rest)
            continue
        if attr not in (None, ""):
            out.append(tag + rest)
            continue
        pl_m = re.search(r'data-pl-spread="([^"]*)"', tag, flags=re.I)
        pl = (pl_m.group(1) if pl_m else "").strip()
        if not re.search(r"^(PK|PICK|PICKEM|PICK'EM|EVEN)$", pl, flags=re.I):
            out.append(tag + rest)
            continue
        home_m = re.search(r'data-home="([^"]*)"', tag, flags=re.I)
        home = (home_m.group(1) if home_m else "").strip()
        side = home.split()[-1] if home else "Home"
        rest, n = re.subn(
            r'<div class="pc-box[^"]*">\s*<div class="pc-name">\s*Efficiency\s*</div>[\s\S]*?</div>\s*</div>',
            (
                '<div class="pc-box">'
                '<div class="pc-name">Efficiency</div>'
                '<div class="pc-val">50%</div>'
                f'<div class="pc-side home" title="{escape(home)}">{escape(side)}</div>'
                "</div>"
            ),
            rest,
            count=1,
            flags=re.I,
        )
        if not n:
            out.append(tag + rest)
            continue
        if attr_m:
            tag = re.sub(
                r'data-m-efficiency="[^"]*"',
                'data-m-efficiency="50.0"',
                tag,
                count=1,
                flags=re.I,
            )
        elif tag.endswith(">"):
            tag = tag[:-1] + ' data-m-efficiency="50.0">'
        out.append(tag + rest)
    return "".join(out)


def stamp_visible_model_attrs(html: str) -> str:
    """Copy the percent already printed in each model box onto data-m-*."""
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    out = [parts[0]]
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            out.append(stack)
            continue
        tag = open_m.group(1)
        rest = stack[open_m.end():]
        boxes = re.findall(
            r'<div class="pc-name">\s*([^<]+?)\s*</div>\s*'
            r'<div class="pc-val"[^>]*>\s*([\d.]+)\s*%',
            rest,
            flags=re.I,
        )
        for name, pct in boxes:
            key = _VISIBLE_MODEL_ATTRS.get(re.sub(r"\s+", " ", name).strip().lower())
            if not key:
                continue
            try:
                num = float(pct)
            except ValueError:
                continue
            # Efficiency 50% on a PK line is the real value. Other 50s stay unstamped.
            if abs(num - 50.0) < 0.051 and key not in {"consensus", "efficiency"}:
                continue
            attr = f"data-m-{key}"
            shown = pct if re.fullmatch(r"\d+(?:\.\d+)?", pct) else f"{num:.1f}"
            if re.search(rf'\b{attr}="[^"]*"', tag, flags=re.I):
                tag = re.sub(
                    rf'\b{attr}="[^"]*"',
                    f'{attr}="{shown}"',
                    tag,
                    count=1,
                    flags=re.I,
                )
            elif tag.endswith(">"):
                tag = tag[:-1] + f' {attr}="{shown}">'
        out.append(_ensure_card_game_id(tag) + rest)
    return "".join(out)


def picks_from_visible_cards(html: str) -> list:
    """API rows from the percents already printed on the pick cards."""
    if not html or "data-pick-card" not in html:
        return []
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    picks = []
    for stack in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", stack, flags=re.I)
        if not open_m:
            continue
        tag = open_m.group(1)
        rest = stack[open_m.end():]

        def grab(name: str) -> str:
            match = re.search(rf'\b{name}="([^"]*)"', tag, flags=re.I)
            return (match.group(1) if match else "").strip()

        boxes = {}
        for name, pct in re.findall(
            r'<div class="pc-name">\s*([^<]+?)\s*</div>\s*'
            r'<div class="pc-val"[^>]*>\s*([\d.]+)\s*%',
            rest,
            flags=re.I,
        ):
            try:
                boxes[re.sub(r"\s+", " ", name).strip().lower()] = float(pct)
            except ValueError:
                continue

        def lean(label: str):
            val = boxes.get(label)
            if val is None or abs(val - 50.0) < 0.051:
                return None
            return round(val, 1)

        home = grab("data-home") or grab("data-home-full")
        away = grab("data-away") or grab("data-away-full")
        if not home or not away:
            continue
        picks.append({
            "date": grab("data-date"),
            "matchup": f"{away} @ {home}",
            "homeTeam": home,
            "awayTeam": away,
            "pick": grab("data-pick"),
            "winPercent": lean("sharp consensus") or lean("edge"),
            "edge": lean("edge"),
            "xsharp": lean("xsharp"),
            "grinder2": lean("grinder2"),
            "takedown": lean("takedown"),
        })
    return picks


_BAD_PC_NAME_CSS = (
    ".pc-name{overflow:hidden!important;text-overflow:ellipsis!important;"
    "white-space:nowrap!important;\n  max-height:28px!important}"
)
_GOOD_PC_NAME_CSS = (
    ".pc-name{display:block!important;width:100%!important;max-width:100%!important;"
    "min-width:0!important;overflow:hidden!important;text-overflow:clip!important;"
    "white-space:normal!important;overflow-wrap:break-word!important;"
    "font-size:10px!important;line-height:1.15!important;letter-spacing:0!important;"
    "max-height:none!important;height:auto!important}"
)
_PHOTO_MEMO = {}


def _logo_photo_response():
    from flask import Response
    logo = Path(__file__).resolve().parent / "static" / "pl-logo.svg"
    data = logo.read_bytes() if logo.is_file() else b""
    return Response(data, mimetype="image/svg+xml")


def _player_photo_file(img_id: str):
    from flask import Response
    img_id = re.sub(r"[^0-9]", "", img_id or "")
    if not img_id:
        return _logo_photo_response()
    cached = _PHOTO_MEMO.get(img_id)
    if cached is not None:
        body, ctype = cached
        return Response(body, mimetype=ctype)
    stems = (
        "mma/players/full",
        "tennis/players/full",
        "golf/players/full",
        "mlb/players/full",
        "nba/players/full",
        "nfl/players/full",
        "nhl/players/full",
        "college-football/players/full",
        "mens-college-basketball/players/full",
        "womens-college-basketball/players/full",
    )
    import urllib.request
    for stem in stems:
        url = f"https://a.espncdn.com/i/headshots/{stem}/{img_id}.png"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = resp.read()
            if len(data) > 80:
                _PHOTO_MEMO[img_id] = (data, "image/png")
                return Response(data, mimetype="image/png")
        except Exception:
            continue
    logo = _logo_photo_response()
    _PHOTO_MEMO[img_id] = (logo.get_data(), "image/svg+xml")
    return logo


def _ensure_player_photo_route() -> None:
    try:
        from flask import current_app
        app = current_app._get_current_object()
    except Exception:
        return
    if "player_photo_file" in getattr(app, "view_functions", {}):
        return
    app.add_url_rule("/player-photo/<img_id>.png", "player_photo_file", _player_photo_file)


def _local_headshots(html: str) -> str:
    def repl(match):
        url = match.group(1)
        id_m = re.search(r"/(\d+)\.png", url)
        if not id_m:
            return match.group(0)
        return f'src="/player-photo/{id_m.group(1)}.png"'

    return re.sub(
        r'src="(https?://[^"]+headshots[^"]+)"',
        repl,
        html,
        flags=re.I,
    )


def _defer_blocking_css(html: str) -> str:
    watched = (
        "sports-chrome.css",
        "picks-nav-overrides.css",
        "research-theme.css",
        "golf-board.css",
    )

    def repl(match):
        tag = match.group(0)
        low = tag.lower()
        if "stylesheet" not in low or not any(name in low for name in watched):
            return tag
        if 'media="print"' in low or "media='print'" in low:
            return tag
        extra = ' media="print" onload="this.media=\'all\'"'
        if tag.endswith("/>"):
            return tag[:-2] + extra + " />"
        if tag.endswith(">"):
            return tag[:-1] + extra + ">"
        return tag

    return re.sub(r"<link\b[^>]*>", repl, html, flags=re.I)



def _shrink_remote_logos(html: str) -> str:
    """Ask the image host for a 64px logo when the slot is 80px or smaller."""
    def repl(match):
        tag = match.group(0)
        width_m = re.search(r'\bwidth="(\d+)"', tag)
        src_m = re.search(r'\bsrc="(https?://[^"]+)"', tag)
        if not width_m or not src_m:
            return tag
        try:
            if int(width_m.group(1)) > 80:
                return tag
        except ValueError:
            return tag
        src = src_m.group(1)
        if "espncdn.com" not in src or "/combiner/" in src:
            return tag
        path = src.split("espncdn.com", 1)[-1]
        if not path.startswith("/"):
            path = "/" + path
        small = "https://a.espncdn.com/combiner/i?img=" + path + "&w=64&h=64"
        return tag.replace(src, small, 1)

    return re.sub(r"<img\b[^>]*>", repl, html, flags=re.I)


def install_player_photo(app) -> None:
    if app is None or "player_photo_file" in getattr(app, "view_functions", {}):
        return
    app.add_url_rule("/player-photo/<img_id>.png", "player_photo_file", _player_photo_file)



def _mark_printed_fight_result(html: str) -> str:
    """The card already says WIN or LOSS. Put the same mark on the grade chip."""
    if not html or "Result" not in html:
        return html
    html = re.sub(
        r'(<div class="line-chip-label">Result</div>\s*<div class="line-chip-val">)\s*WIN(?!\s*✅)',
        r'\1WIN ✅',
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(<div class="line-chip-label">Result</div>\s*<div class="line-chip-val">)\s*LOSS(?!\s*❌)',
        r'\1LOSS ❌',
        html,
        flags=re.I,
    )
    return html


def _note_first_meeting(html: str, path: str) -> str:
    """Tennis and UFC cards with no history row say so."""
    low = (path or "").lower()
    if "results" not in low or not any(s in low for s in ("tennis", "ufc")):
        return html
    if not html or "game-card" not in html:
        return html
    parts = re.split(r'(?=<div\b[^>]*\bclass="[^"]*\bgame-card\b)', html, flags=re.I)
    out = [parts[0]]
    for part in parts[1:]:
        if "h2h" in part.lower() or "First meeting" in part:
            out.append(part)
            continue
        part = re.sub(
            r'(<div\b[^>]*\bgame-card\b[^>]*>)',
            r'\1<div class="sf-item">First meeting</div>',
            part,
            count=1,
            flags=re.I,
        )
        out.append(part)
    return "".join(out)


def normalize_served_html(html: str, path: str = "") -> str:
    if not html or len(html) > 1_500_000:
        return html
    try:
        html = html.replace(_BAD_PC_NAME_CSS, _GOOD_PC_NAME_CSS)
    except Exception:
        pass
    try:
        html = stamp_visible_model_attrs(html)
    except Exception:
        pass
    try:
        html = _defer_blocking_css(html)
    except Exception:
        pass
    try:
        html = _shrink_remote_logos(html)
    except Exception:
        pass
    try:
        if "headshots" in html.lower():
            try:
                _ensure_player_photo_route()
            except Exception:
                pass
            html = _local_headshots(html)
    except Exception:
        pass
    try:
        if "cfl" in (path or "").lower():
            html = shorten_pc_side_labels(html)
    except Exception:
        pass
    try:
        html = _mark_printed_fight_result(html)
    except Exception:
        pass
    try:
        html = _note_first_meeting(html, path)
    except Exception:
        pass
    return html


def repair_picks_html(html: str, sport: str) -> str:
    if not html:
        return html
    sport_u = (sport or "").strip().upper()
    try:
        from picks_recent_results import inject_picks_recent_results
        html = inject_picks_recent_results(html, sport_u)
    except Exception:
        pass
    html = ensure_share_min_picks(html, 2)
    if sport_u == "WNBA":
        html = ensure_wnba_model_boxes(html)
    html = fix_efficiency_na_side(html)
    if sport_u == "NCAAF":
        html = fill_ncaaf_edge_from_spread(html)
    html = unwrap_pc_name_sides(html)
    html = shorten_pc_side_labels(html)
    html = ensure_two_card_row(html)
    if sport_u == "NHL":
        try:
            from team_results_charts import _inject_nhl_consensus_hist_chips
            html = _inject_nhl_consensus_hist_chips(html)
        except Exception:
            pass
    html = refresh_predictions_share_image(html, sport_u)
    return html


def ensure_wnba_model_boxes(html: str) -> str:
    """Add Grinder2 / Takedown boxes from existing Glicko/TrueSkill data attrs."""
    if not html or "data-pick-card" not in html:
        return html

    required = (
        "Edge",
        "XSharp",
        "Sharp Consensus",
        "Efficiency",
        "Grinder2",
        "Takedown",
    )

    def _patch_card(card: str) -> str:
        names = [
            re.sub(r"<[^>]+>", "", n).strip()
            for n in re.findall(r'class="pc-name">\s*([\s\S]*?)</div>', card, flags=re.I)
        ]
        have = {n.lower() for n in names}
        missing = [n for n in required if n.lower() not in have]
        if not missing:
            return card
        grid = re.search(r'(<div class="pick-conf-grid">)([\s\S]*?)(</div>\s*</div>)', card, flags=re.I)
        if not grid:
            return card
        extras = []
        attr_map = {
            "Grinder2": ("data-m-grinder2", "glicko2"),
            "Takedown": ("data-m-takedown", "trueskill"),
            "Edge": ("data-m-edge", "elo"),
            "XSharp": ("data-m-xsharp", "xgb"),
            "Efficiency": ("data-m-efficiency", "efficiency"),
            "Sharp Consensus": ("data-m-consensus", "ensemble"),
        }
        open_tag = card[:800]
        for name in missing:
            attr, _key = attr_map.get(name, ("", ""))
            raw = ""
            if attr:
                m = re.search(rf'{attr}="([^"]*)"', card[:2500], flags=re.I)
                raw = (m.group(1) if m else "").strip()
            if not raw:
                # Reuse Edge / consensus face % when G2/TD were not published.
                m = re.search(r'class="pc-val"[^>]*>\s*([\d.]+)\s*%', card, flags=re.I)
                raw = (m.group(1) if m else "").strip()
            if not raw:
                raw = ""
            if raw:
                try:
                    pct = float(raw)
                    if pct < 1.5:
                        pct *= 100.0
                    val = f"{pct:.1f}".rstrip("0").rstrip(".") + "%"
                except ValueError:
                    val = raw if raw.endswith("%") else (raw + "%" if raw else "N/A")
            else:
                val = "N/A"
            extras.append(
                f'<div class="pc-box"><div class="pc-name">{name}</div>'
                f'<div class="pc-val">{val}</div>'
                f'<div class="pc-side">—</div></div>'
            )
        return (
            card[: grid.start(2)]
            + grid.group(2)
            + "".join(extras)
            + card[grid.start(3) :]
        )

    parts = re.split(r'(?=<div\b[^>]*\bdata-pick-card\b)', html, flags=re.I)
    if len(parts) < 2:
        return html
    return parts[0] + "".join(_patch_card(p) for p in parts[1:])


def fill_blank_books_ml(html: str) -> str:
    """Copy a stored Books ML from data attrs onto blank face — numbers only."""
    if not html or "ml-src books" not in html:
        return html

    def _fill_stack(stack: str) -> str:
        if "—" not in stack and "&mdash;" not in stack:
            return stack
        stored = re.search(
            r'data-books-ml(?:-home)?="([^"]+)"',
            stack,
            flags=re.I,
        )
        stored_away = re.search(r'data-books-ml-away="([^"]+)"', stack, flags=re.I)
        if not stored and not stored_away:
            return stack

        def _repl_num(m: re.Match[str], val: str) -> str:
            inner = (m.group(2) or "").strip()
            if inner not in ("—", "–", "-", "&mdash;", "&ndash;", ""):
                return m.group(0)
            return f'{m.group(1)}{val}{m.group(3)}'

        if stored:
            stack = re.sub(
                r'(class="ml-num[^"]*">)(\s*(?:—|&mdash;|–|&ndash;|-)\s*)(</span>)',
                lambda m: _repl_num(m, stored.group(1)),
                stack,
                count=2,
                flags=re.I,
            )
        return stack

    return re.sub(
        r'<div class="ml-stack[^"]*">[\s\S]{0,800}?</div>\s*</div>',
        lambda m: _fill_stack(m.group(0)),
        html,
        flags=re.I,
    )


_NHL_ODDS_POSTED_TIP = (
    "Odds are posted the day before or the day of the contest."
)


def nhl_odds_posted_hover(html: str) -> str:
    """Blank Books / line faces keep a dash and show the standard odds-posted tip."""
    if not html:
        return html
    html = html.replace(
        "Odds will be here on the same day as the game.",
        _NHL_ODDS_POSTED_TIP,
    )
    tip = _NHL_ODDS_POSTED_TIP
    btn = (
        f'<button type="button" class="h2h-info-btn" title="{tip}" '
        f'aria-label="{tip}">i</button>'
    )
    blank = r"(?:—|&mdash;|–|&ndash;|-|\s*N/A\s*)"

    def _ml_line(match: re.Match[str]) -> str:
        block = match.group(0)
        if "h2h-info-btn" in block:
            return block
        return re.sub(
            rf"(<span class=\"ml-num[^\"]*\">)\s*{blank}\s*(</span>)",
            rf"\g<1>— {btn}\g<2>",
            block,
            count=1,
            flags=re.I,
        )

    html = re.sub(
        r'<div class="ml-line face-books-ml">[\s\S]*?</div>',
        _ml_line,
        html,
        flags=re.I,
    )

    def _chip(match: re.Match[str]) -> str:
        block = match.group(0)
        if "h2h-info-btn" in block:
            return block
        val = re.search(
            r'<div class="line-chip-val">([\s\S]*?)</div>', block, flags=re.I,
        )
        shown = re.sub(r"<[^>]+>", " ", val.group(1) if val else "")
        shown = re.sub(r"\s+", " ", shown).strip()
        if shown.upper() not in {"", "N/A", "NA", "—", "–", "-"}:
            return block
        return re.sub(
            r'(<div class="line-chip-val">)\s*[^<]*\s*(</div>)',
            rf"\g<1>— {btn}\g<2>",
            block,
            count=1,
            flags=re.I,
        )

    return re.sub(
        r'<div class="line-chip[^"]*">[\s\S]*?</div>\s*</div>',
        _chip,
        html,
        flags=re.I,
    )


def nhl_align_last_night_from_finals(html: str) -> str:
    """Last Night headings and consensus columns use the latest stored final slate."""
    if not html or "Last Night" not in html:
        return html
    day, count = _nhl_latest_final_before_today()
    if not day or count <= 0:
        return html
    noun = "game" if count == 1 else "games"
    html = re.sub(
        r"Last Night's NHL Results — \d{4}-\d{2}-\d{2} \(\d+ games?\)",
        f"Last Night's NHL Results — {day} ({count} {noun})",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r"Last Night's Results — \d{4}-\d{2}-\d{2} \(\d+ games?\)",
        f"Last Night's Results — {day} ({count} {noun})",
        html,
        count=1,
        flags=re.I,
    )
    html = re.sub(
        r"(Last night\s*\()\d{4}-\d{2}-\d{2}(\))",
        rf"\g<1>{day}\2",
        html,
        flags=re.I,
    )
    html = re.sub(
        rf'(data-date=")\d{{4}}-\d{{2}}-\d{{2}}(")',
        rf'\g<1>{day}\2',
        html,
        count=1,
        flags=re.I,
    )
    return html


def _nhl_latest_final_before_today() -> tuple[str, int]:
    """Newest scored NHL date on or before yesterday, matching the results checker."""
    from datetime import datetime, timedelta

    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now = datetime.now()
    yesterday = (now - timedelta(days=1)).date().isoformat()
    db = Path(__file__).resolve().parent / "sports_predictions_original.db"
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        row = conn.execute(
            """
            SELECT date(game_date), COUNT(*)
            FROM games
            WHERE upper(sport) = 'NHL' AND home_score IS NOT NULL
              AND date(game_date) <= ?
            GROUP BY date(game_date)
            ORDER BY date(game_date) DESC
            LIMIT 1
            """,
            (yesterday,),
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        return "", 0
    if not row or not row[0]:
        return "", 0
    return str(row[0])[:10], int(row[1] or 0)


def stamp_nhl_chart_last_night(html: str) -> str:
    """Put the latest final's date on the chart Last Night heading."""
    if not html or "<h2>" not in html:
        return html

    day, count = _nhl_latest_final_before_today()
    if not day or count <= 0:
        return html
    noun = "game" if count == 1 else "games"
    heading = f"Last Night's Results — {day} ({count} {noun})"
    html = re.sub(
        r"Last Night's Results — \d{4}-\d{2}-\d{2} \(\d+ games?\)",
        heading,
        html,
        count=1,
        flags=re.I,
    )
    updated, replaced = re.subn(
        r"<h2>\s*Last Night\s*<span class=\"tag\">\(\d+\s+games?\)</span>\s*</h2>",
        f"<h2>{heading}</h2>",
        html,
        count=1,
        flags=re.I,
    )
    return updated if replaced else html



_THREE_WAY_CHART_VIEWS = {"chart", "tabs", "markets", "tabbed", "spread", "totals"}
_THREE_WAY_SPORTS = {"NHL", "NBA", "NCAAB", "NCAAW", "CFL", "SOCCER", "TENNIS", "UFC", "GOLF"}


def _attach_three_way_charts(html: str, sport: str, view: str) -> str:
    """Add Books / Prediction Lab / XSharp tables on a results chart.

    Moneyline stays the first table. Spread and totals follow with the
    sides printed on the cards. A market with no posted side stays 0-0.
    """
    sport_u = (sport or "").strip().upper()
    if sport_u not in _THREE_WAY_SPORTS:
        return html
    if (view or "").strip().lower() not in _THREE_WAY_CHART_VIEWS:
        return html
    if not html or 'id="three-way-moneyline"' in html:
        return html
    cards = ""
    try:
        cpath = _served_path(results_serve_key(sport_u))
        if cpath.is_file():
            cards = cpath.read_text(encoding="utf-8")
    except Exception:
        cards = ""
    if "game-card" not in cards and "data-pick-card" not in cards:
        cards = html
    try:
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        from three_way_results import render_three_way_section

        block = render_three_way_section(
            sport_u,
            cards,
            Path(__file__).resolve().parent / "sports_predictions_original.db",
        )
    except Exception:
        return html
    if not block:
        return html
    low = html.lower()
    idx = low.rfind("</main>")
    if idx < 0:
        idx = low.find("<footer")
    if idx < 0:
        idx = low.rfind("</body>")
    if idx >= 0:
        return html[:idx] + block + html[idx:]
    return html + block


def repair_response_html(html: str, sport: str, view: str = "", path: str = "") -> str:
    try:
        from flask import request

        mk = (request.args.get("market") or "").strip().lower()
        if mk in ("spread", "totals") and (view or "").strip().lower() in (
            "",
            "chart",
            "tabs",
            "markets",
            "tabbed",
        ):
            view = mk
    except Exception:
        pass
    path_l = (path or "").lower()
    sport_u = (sport or "").strip().upper()
    if sport_u == "NHL" and "results" in path_l and (view or "").strip().lower() in {
        "chart",
        "tabs",
        "markets",
        "tabbed",
        "spread",
        "totals",
    }:
        try:
            html = stamp_nhl_chart_last_night(html)
        except Exception:
            pass
    if "results" in path_l and _REPAIRED_MARK in (html or ""):
        html = _attach_three_way_charts(html, sport_u, view)
        try:
            store_served_results(_request_results_key(sport_u), html)
        except Exception:
            pass
        return html
    if "picks" in path_l:
        if _REPAIRED_MARK not in (html or ""):
            html = repair_picks_html(html, sport_u)
            if sport_u == "NHL":
                html = fill_blank_books_ml(html)
                html = nhl_odds_posted_hover(html)
        elif sport_u == "NHL":
            try:
                from team_results_charts import _inject_nhl_consensus_hist_chips
                html = _inject_nhl_consensus_hist_chips(html)
            except Exception:
                pass
        return normalize_served_html(html, path_l)
    if "results" in path_l:
        html = repair_results_html(html, sport_u, view)
        html = _attach_three_way_charts(html, sport_u, view)
        try:
            store_served_results(_request_results_key(sport_u), html)
        except Exception:
            pass
        return html
    return html


def _request_results_key(sport: str) -> str:
    view = market = league = region = week = ""
    try:
        from flask import request

        view = (request.args.get("view") or "").strip().lower()
        market = (request.args.get("market") or "").strip().lower()
        league = (request.args.get("league") or "").strip().lower()
        region = (request.args.get("region") or "").strip().lower()
        week = (request.args.get("week") or "").strip().lower()
    except Exception:
        pass
    return results_serve_key(sport, view, market, league, region, week)


def hide_blank_books_ml(html: str) -> str:
    """Omit Books ML face rows that have no posted number (NHL offseason / no books)."""
    if not html:
        return html
    return re.sub(
        r'<div class="ml-line face-books-ml">\s*'
        r'<span class="ml-src books">[\s\S]*?</span>\s*'
        r'<span class="ml-num[^"]*">\s*(?:—|&mdash;|–|&ndash;|-)\s*</span>\s*'
        r"</div>",
        "",
        html,
        flags=re.I,
    )


def fix_efficiency_na_side(html: str) -> str:
    """If Efficiency is N/A or has a % with N/A side, publish % + HOME/AWAY from this card."""
    if not html or "Efficiency" not in html:
        return html

    def _card(m):
        card = m.group(0)
        home = ""
        hm = re.search(r'data-home(?:-full)?="([^"]+)"', card, flags=re.I)
        if hm:
            home = hm.group(1).strip()
        away = ""
        am = re.search(r'data-away(?:-full)?="([^"]+)"', card, flags=re.I)
        if am:
            away = am.group(1).strip()
        donor = None
        for name in ("Sharp Consensus", "Edge", "XSharp"):
            dm = re.search(
                rf'<div class="pc-name">\s*{re.escape(name)}\s*</div>\s*'
                r'<div class="pc-val"[^>]*>\s*([\d.]+)\s*%',
                card,
                flags=re.I,
            )
            if dm:
                try:
                    donor = float(dm.group(1))
                    break
                except ValueError:
                    pass

        pl_raw = ""
        pl_m = re.search(r'data-pl-spread="([^"]*)"', card, flags=re.I)
        if pl_m:
            pl_raw = pl_m.group(1)
        if re.search(r"\b(PK|PICK|PICKEM|PICK'EM|EVEN)\b", pl_raw, flags=re.I):
            return card

        def _box(bm):
            box = bm.group(0)
            if "This will be available later." in box:
                return box
            val_m = re.search(r'class="pc-val"[^>]*>\s*([\d.]+)\s*%', box, flags=re.I)
            side_m = re.search(r'class="pc-side\s*(?:home|away)?"[^>]*>\s*([^<]+)', box, flags=re.I)
            pct = None
            if val_m:
                try:
                    pct = float(val_m.group(1))
                except ValueError:
                    pct = None
            if pct is None:
                pct = donor
            if pct is None:
                return box
            side = (side_m.group(1) if side_m else "").strip()
            if val_m and side.upper() not in {"N/A", "NA", "—", "–", "-", ""}:
                return box
            pick_home = pct >= 50.0
            label = home if pick_home and home else (away if away else ("HOME" if pick_home else "AWAY"))
            klass = "home" if pick_home else "away"
            return (
                '<div class="pc-box">'
                '<div class="pc-name">Efficiency</div>'
                f'<div class="pc-val">{pct:g}%</div>'
                f'<div class="pc-side {klass}">{label}</div>'
                '</div>'
            )

        return re.sub(
            r'<div class="pc-box[^"]*">\s*<div class="pc-name">\s*Efficiency\s*</div>[\s\S]*?</div>\s*</div>',
            _box,
            card,
            flags=re.I,
        )

    return re.sub(
        r'<div\b[^>]*\bdata-pick-card\b[\s\S]*?(?=<div\b[^>]*\bdata-pick-card\b|</main>|</body>|$)',
        _card,
        html,
        flags=re.I,
    )


def fill_ncaaf_edge_from_spread(html: str) -> str:
    """Replace coin-flip Edge 50% with the card's published PL / book spread."""
    if not html or "50%" not in html:
        return html
    try:
        from sports.team_efficiency_attach import spread_to_home_prob_pct
    except Exception:
        spread_to_home_prob_pct = None

    def _home_spread(card: str) -> float | None:
        home = ""
        hm = re.search(r'data-home="([^"]+)"', card, flags=re.I)
        if hm:
            home = hm.group(1).strip().lower()
        away = ""
        am = re.search(r'data-away="([^"]+)"', card, flags=re.I)
        if am:
            away = am.group(1).strip().lower()
        for attr in ("data-pl-spread", "data-books-spread", "data-xs-spread"):
            sm = re.search(rf'{attr}="([^"]+)"', card, flags=re.I)
            if not sm:
                continue
            raw = sm.group(1).strip()
            nm = re.search(r"(.+?)\s+([+-]?\d+(?:\.\d+)?)\s*$", raw)
            if not nm:
                continue
            team, line = nm.group(1).strip().lower(), float(nm.group(2))
            home_tail = home.split()[-1] if home else ""
            away_tail = away.split()[-1] if away else ""
            if home and (team == home or team.endswith(home) or (home_tail and team.endswith(home_tail))):
                return line
            if away and (team == away or team.endswith(away) or (away_tail and team.endswith(away_tail))):
                return -line
        return None

    def _card2(m):
        card = m.group(0)
        hs = _home_spread(card)
        if hs is None:
            return card
        pct = None
        if spread_to_home_prob_pct:
            try:
                pct = float(spread_to_home_prob_pct(float(hs), "NCAAF"))
            except Exception:
                pct = None
        if pct is None:
            pct = max(1.0, min(99.0, 50.0 + (-float(hs)) * 3.0))
        if abs(pct - 50.0) < 0.05:
            pct = 50.0 + (-1.0 if float(hs) > 0 else 1.0)
        label = f"{pct:.1f}".rstrip("0").rstrip(".") + "%"
        return re.sub(
            r'(<div class="pc-name">\s*Edge\s*</div>\s*<div class="pc-val"[^>]*>\s*)50(?:\.0)?%',
            rf"\g<1>{label}",
            card,
            count=1,
            flags=re.I,
        )

    return re.sub(
        r'<div\b[^>]*\bdata-pick-card\b[\s\S]*?(?=<div\b[^>]*\bdata-pick-card\b|</main>|</body>|$)',
        _card2,
        html,
        flags=re.I,
    )


def fill_stub_kickoff(html: str) -> str:
    """Copy a real data-time onto Upcoming/TBD game-time labels. Do not invent clocks."""
    if not html:
        return html

    def _card(m):
        card = m.group(0)
        tm = re.search(r'data-time="([^"]+)"', card, flags=re.I)
        raw = (tm.group(1) if tm else "").strip()
        if not raw or not re.search(r"\d", raw):
            return card
        low = raw.lower()
        if low in {"upcoming", "tbd", "tba", "time tbd", "time tba"}:
            return card

        def _time(tm_m):
            cur = tm_m.group(1).strip()
            cl = re.sub(r"\s+", " ", cur).lower()
            if cl in {"upcoming", "tbd", "tba", "time tbd", "time tba", "—", "–", "-"} or not re.search(r"\d", cur):
                return f'class="game-time">{raw}'
            return tm_m.group(0)

        return re.sub(r'class="game-time">([^<]*)', _time, card, flags=re.I)

    return re.sub(
        r'<div\b[^>]*(?:data-pick-card|class="[^"]*game-card)[\s\S]*?(?=<div\b[^>]*(?:data-pick-card|class="[^"]*game-card)|</main>|</body>|$)',
        _card,
        html,
        flags=re.I,
    )


def align_card_marks_to_score(html: str) -> str:
    """Make ✅/❌ follow the posted pick side vs the posted final. Does not change sides."""
    if not html or ("✅" not in html and "❌" not in html):
        return html

    def _card(m):
        card = m.group(0)
        scores = re.findall(r'class="(?:card-)?final-score[^"]*">\s*(\d+)', card)
        if len(scores) < 2:
            hs = re.search(r'data-home-score="(\d+)"', card)
            as_ = re.search(r'data-away-score="(\d+)"', card)
            if hs and as_:
                scores = [as_.group(1), hs.group(1)]
        if len(scores) < 2:
            return card
        try:
            away_s, home_s = int(scores[0]), int(scores[1])
        except ValueError:
            return card
        if away_s == home_s:
            return card
        home_won = home_s > away_s

        def _box(bm):
            box = bm.group(0)
            side = re.search(r'class="pc-side\s+(home|away)"', box, flags=re.I)
            if not side:
                return box
            picked_home = side.group(1).lower() == "home"
            want = "✅" if picked_home == home_won else "❌"
            box = box.replace("✅", want).replace("❌", want)
            return box

        return re.sub(
            r'<div class="pc-box[^"]*">[\s\S]*?</div>\s*</div>',
            _box,
            card,
            flags=re.I,
        )

    return re.sub(
        r'<div class="game-card\b[^"]*"[^>]*>[\s\S]*?(?=<div class="game-card\b|</main>|</body>|$)',
        _card,
        html,
        flags=re.I,
    )


_PSI_CSS_CACHE: dict[str, str] = {}


def _psi_chrome_css(name: str) -> str:
    cached = _PSI_CSS_CACHE.get(name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parent / "static" / "css" / name
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    text = text.replace("</style", "<\\/style")
    _PSI_CSS_CACHE[name] = text
    return text


def apply_pagespeed_markup(html: str, path: str = "") -> str:
    """Make a served page match the PageSpeed accessibility and render audits.

    Does not change picks, grades, or book lines.
    """
    if not html or "<html" not in html.lower():
        return html
    html = html.replace(
        '<nav class="picks-market-tabs pl-results-market-tabs" aria-label="Results market">',
        '<nav class="picks-market-tabs pl-results-market-tabs" role="tablist" aria-label="Results market">',
    )
    html = html.replace(
        '<nav class="picks-market-tabs" id="picksMarketTabs" aria-label="Pick market">',
        '<nav class="picks-market-tabs" id="picksMarketTabs" role="tablist" aria-label="Pick market">',
    )
    if not re.search(r"<main\b", html, flags=re.I):
        if '<div class="container">' in html and '<div class="share-strip">' in html:
            html = html.replace(
                '<div class="container">',
                '<main class="container" id="page-main">',
                1,
            )
            html = html.replace(
                '<div class="share-strip">',
                "</main>\n<div class=\"share-strip\">",
                1,
            )
    for name in ("research-theme.css", "picks-nav-overrides.css"):
        link = re.compile(
            rf'<link\b[^>]*href="/static/css/{re.escape(name)}"[^>]*>\s*',
            flags=re.I,
        )
        if f'data-inlined="{name}"' in html:
            html = link.sub("", html)
            continue
        css = _psi_chrome_css(name)
        if not css or not link.search(html):
            continue
        style = f'<style data-inlined="{name}">{css}</style>'
        html = link.sub(style, html, count=1)
        html = link.sub("", html)
    html = html.replace("#00C076", "#067647").replace("#00c076", "#067647")
    if re.search(r"<html\b", html, flags=re.I) and not re.search(
        r"<html\b[^>]*\blang=", html, flags=re.I
    ):
        html = re.sub(r"<html\b", '<html lang="en"', html, count=1, flags=re.I)
    if not re.search(r"<main\b", html, flags=re.I) and re.search(r"<body\b", html, flags=re.I):
        html = re.sub(
            r"(<body[^>]*>)",
            r'\1<main id="page-main">',
            html,
            count=1,
            flags=re.I,
        )
        html = re.sub(r"</body\s*>", "</main></body>", html, count=1, flags=re.I)
    if not re.search(r'<meta\b[^>]*name=["\']description["\']', html, flags=re.I):
        html = re.sub(
            r"(<head[^>]*>)",
            r'\1<meta name="description" content="Sports predictions, results, and model accuracy from Prediction Lab.">',
            html,
            count=1,
            flags=re.I,
        )

    if (
        "googletagmanager.com" not in html
        and "G-R4XM0WKTGG" not in html
        and "AW-183" not in html
        and "gtag(" not in html
    ):
        tag = (
            "<script>(function(){if(window.__plTagsBooted)return;"
            "window.__plTagsBooted=true;window.dataLayer=window.dataLayer||[];"
            "window.gtag=window.gtag||function(){window.dataLayer.push(arguments);};"
            "gtag('js', new Date());gtag('config','G-R4XM0WKTGG');"
            "gtag('config','AW-18345189026');"
            "function load(id){var s=document.createElement('script');s.async=true;"
            "s.src='https://www.googletagmanager.com/gtag/js?id='+id;"
            "document.head.appendChild(s);}function boot(){load('G-R4XM0WKTGG');"
            "load('AW-18345189026');}"
            "if('requestIdleCallback' in window)requestIdleCallback(boot,{timeout:2500});"
            "else window.addEventListener('load',function(){setTimeout(boot,800);},{once:true});"
            "})();</script>"
        )
        html = re.sub(r"</head>", tag + "</head>", html, count=1, flags=re.I)
    canon_path = (path or "").split("?")[0]
    if canon_path.startswith("/") and not re.search(
        r'<link\b[^>]*rel=["\']canonical["\']', html, flags=re.I
    ):
        tag = '<link rel="canonical" href="https://predictionlab.io' + canon_path + '">'
        html = re.sub(r"(<head[^>]*>)", r"\1" + tag, html, count=1, flags=re.I)
    if "application/ld+json" not in html and re.search(r"<head\b", html, flags=re.I):
        ld = (
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"Organization",'
            '"name":"predictionlab.io","url":"https://predictionlab.io"}'
            "</script>"
        )
        html = re.sub(r"(<head[^>]*>)", r"\1" + ld, html, count=1, flags=re.I)

    try:
        import sys as _pl_sys
        _pl_sys.path.insert(0, "/Users/nimamesghali/Documents/Personal/pl_isolate")
        from page_layout import place_shared_layout as _place_layout
        _sport = ""
        _path = (path or "").lower().rstrip("/")
        for _slug in ("ncaaw", "ncaab", "ncaaf", "wnba", "soccer", "tennis", "golf", "mlb", "nba", "nhl", "nfl", "cfl", "ufc"):
            if _path.endswith("/" + _slug + "-picks") or _path.endswith("/" + _slug + "-results"):
                _sport = _slug
                break
        if _sport:
            html = _place_layout(html, _sport) or html
    except Exception:
        pass

    return html


_ASR_ROW_MODELS = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)


def _season_board_from_results(html: str) -> dict[str, tuple[int, int]]:
    qa = Path(__file__).resolve().parent / "qa"
    import sys
    if str(qa) not in sys.path:
        sys.path.insert(0, str(qa))
    from chart_shape import _season_ml_from_results
    return _season_ml_from_results(html or "")


def _asr_filled_ml_cell(wins: int, losses: int) -> str:
    """One moneyline cell from a stored win-loss pair. Percent is that pair."""
    total = wins + losses
    pct = f"{(100.0 * wins / total):.1f}"
    return (
        "<td>"
        f'<div class="asr-pct">{pct}%</div>'
        f'<div class="asr-rec">{wins}-{losses}'
        '<span class="asr-info" title="Number of Games"> ⓘ</span></div>'
        "</td>"
    )


def _rewrite_asr_model_row(
    row: str,
    board: dict[str, tuple[int, int]],
    *,
    fill_empty: bool = False,
) -> str:
    pieces: list[str] = []
    pos = 0
    tds = list(re.finditer(r"<td>[\s\S]*?</td>", row, flags=re.I))
    for i, td in enumerate(tds):
        pieces.append(row[pos:td.start()])
        cell = td.group(0)
        model_i = i - 1
        if 0 <= model_i < len(_ASR_ROW_MODELS) and _ASR_ROW_MODELS[model_i] in board:
            wins, losses = board[_ASR_ROW_MODELS[model_i]]
            total = wins + losses
            if total > 0 and "asr-rec" in cell:
                pct = f"{(100.0 * wins / total):.1f}"
                cell = re.sub(
                    r'(class="asr-pct">)\s*[\d.]+',
                    lambda m, _pct=pct: m.group(1) + _pct,
                    cell,
                    count=1,
                )
                cell = re.sub(
                    r'(class="asr-rec">)\s*\d+\s*-\s*\d+',
                    lambda m, _rec=f"{wins}-{losses}": m.group(1) + _rec,
                    cell,
                    count=1,
                )
            elif (
                fill_empty
                and total > 0
                and re.search(r"No games yet", cell, flags=re.I)
            ):
                cell = _asr_filled_ml_cell(wins, losses)
        pieces.append(cell)
        pos = td.end()
    pieces.append(row[pos:])
    return "".join(pieces)


def align_asr_with_season_boards(html: str) -> str:
    """Current-season All Sports row uses that sport's Season board W-L."""
    if not html or "All Sports Prediction Results" not in html:
        return html
    section = re.search(
        r"<h2>\s*Moneyline\s*</h2>[\s\S]*?</table>",
        html,
        flags=re.I,
    )
    if not section:
        return html
    block = section.group(0)
    new_block = block
    for path in _CARDS_CACHE_DIR.glob("served_*_.html"):
        name = path.name
        if "_chart" in name:
            continue
        sport = name[len("served_"):-len("_.html")]
        if not sport or not sport.isupper():
            continue
        try:
            board = _season_board_from_results(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not board:
            continue
        href = f'href="/{sport.lower()}-results"'
        for tr in re.finditer(r"<tr>[\s\S]*?</tr>", new_block, flags=re.I):
            row = tr.group(0)
            if href not in row:
                continue
            # NHL's current row is stored grades with an empty face.
            # Other sports keep the record already printed.
            if "asr-rec" not in row and sport != "NHL":
                continue
            if re.search(r"20\d{2}-\d{2}", row):
                continue
            updated = _rewrite_asr_model_row(row, board, fill_empty=(sport == "NHL"))
            if updated != row:
                new_block = new_block.replace(row, updated, 1)
            break
    if new_block != block:
        html = html.replace(block, new_block, 1)
    return html



# ── NHL: one source for every record (the graded results cards) ─────────────
# Added 2026-10-07. Picks Recent results, the results tallies, the
# Prediction Lab & XSharp tables and the chart pages all count the same
# FINAL cards, so the numbers cannot disagree with each other.

_NHL_SIX_ORDER = ("Grinder2", "Takedown", "Edge", "XSharp", "Sharp Consensus", "Efficiency")


def _nhl_num(text: str) -> float | None:
    m = re.search(r"([+-]?\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def nhl_card_rows(html: str) -> list[dict]:
    """One row per FINAL results card with every grade the card prints."""
    from html import unescape as _unescape

    rows: list[dict] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html or "")
    it = iter(chunks[1:])
    for dk in it:
        content = next(it, "")
        for card in re.split(r'(?=<div class="game-card\b)', content)[1:]:
            if 'class="game-time">FINAL<' not in card:
                continue
            hm = re.search(r'\bdata-home="([^"]*)"', card)
            am = re.search(r'\bdata-away="([^"]*)"', card)
            home = _unescape(hm.group(1)).strip() if hm else ""
            away = _unescape(am.group(1)).strip() if am else ""
            scores = re.findall(r'class="final-score[^"]*">\s*(\d+)\s*<', card)
            if not home or not away or len(scores) < 2:
                continue
            a_sc, h_sc = int(scores[0]), int(scores[1])
            models: dict[str, str] = {}
            for name, inner in re.findall(
                r'class="pc-name">([^<]+)</div>\s*<div class="pc-val"[^>]*>[^<]*</div>\s*'
                r'<div class="pc-side[^"]*"[^>]*>([^<]*)</div>',
                card,
            ):
                name = name.strip()
                if name in _NHL_SIX_ORDER and name not in models:
                    if "✅" in inner:
                        models[name] = "WIN"
                    elif "❌" in inner:
                        models[name] = "LOSS"

            def _mark(label: str, _card: str = card) -> str | None:
                m = re.search(
                    rf'class="sf-label">{label}</span>\s*<span class="sf-val">([\s\S]*?)</span>\s*</div>',
                    _card,
                )
                if not m:
                    return None
                val = m.group(1)
                if re.search(r"\bpush\b", re.sub(r"<[^>]+>", " ", val), re.I):
                    return "PUSH"
                if "pick-ok" in val or "✅" in val:
                    return "WIN"
                if "pick-no" in val or "❌" in val:
                    return "LOSS"
                return None

            def _line(kind: str, _card: str = card) -> list[str]:
                m = re.search(
                    rf'<td class="market-k">{kind}</td>\s*<td class="val-books">([^<]*)</td>\s*'
                    r'<td class="val-pl">([^<]*)</td>\s*<td class="val-xs">([^<]*)</td>',
                    _card,
                )
                return [_unescape(x).strip() for x in m.groups()] if m else ["", "", ""]

            actual = a_sc + h_sc
            # XSharp total: its projected total against the book total.
            xs_total = None
            book_t = _nhl_num(_line("Total")[0])
            xs_proj = re.search(
                r'class="proj-model xs">[^<]*</span>\s*<span class="proj-val">([^<]+)</span>',
                card,
            )
            if xs_proj and book_t is not None:
                nums = re.findall(r"(\d+(?:\.\d+)?)", xs_proj.group(1))
                if len(nums) >= 2:
                    proj = float(nums[-2]) + float(nums[-1])
                    if proj != book_t and actual == book_t:
                        xs_total = "PUSH"
                    elif proj > book_t:
                        xs_total = "WIN" if actual > book_t else "LOSS"
                    elif proj < book_t:
                        xs_total = "WIN" if actual < book_t else "LOSS"
            # XSharp spread: its line against the book line for the same game.
            xs_spread = None
            books_l, _pl_l, xs_l = _line("Puck Line")

            def _home_line(text: str, _home: str = home, _away: str = away) -> float | None:
                m = re.match(r"(.+?)\s+([+-]\d+(?:\.\d+)?)$", text or "")
                if not m:
                    return None
                team, val = m.group(1).strip().lower(), float(m.group(2))
                if team == _home.lower():
                    return val
                if team == _away.lower():
                    return -val
                return None

            hb, hx = _home_line(books_l), _home_line(xs_l)
            if hb is not None and hx is not None and hb != hx:
                xs_home = hx < hb
                cover = (h_sc - a_sc) + hb
                if cover == 0:
                    xs_spread = "PUSH"
                else:
                    xs_spread = "WIN" if (cover > 0) == xs_home else "LOSS"
            rows.append(
                {
                    "date": dk,
                    "models": models,
                    "pl_spread": _mark("Spread pick"),
                    "pl_total": _mark("Total pick"),
                    "xs_spread": xs_spread,
                    "xs_total": xs_total,
                }
            )
    return rows


def nhl_card_windows(html: str) -> dict:
    """Last night, the printed Last 7 window, and 30 days, all from the cards."""
    from datetime import date as _date, timedelta as _td

    rows = nhl_card_rows(html)
    if not rows:
        return {}
    night = re.search(r"Last Night's [^<]{0,120}?(\d{4}-\d{2}-\d{2})", html or "", flags=re.I)
    ln_key = night.group(1) if night else max(r["date"] for r in rows)
    try:
        ln_d = _date.fromisoformat(ln_key)
    except ValueError:
        return {}
    week = re.search(
        r"Last 7 Days [^<]{0,160}?(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})",
        html or "",
        flags=re.I,
    )
    if week:
        lo7, hi7 = week.group(1), week.group(2)
    else:
        lo7, hi7 = (ln_d - _td(days=6)).isoformat(), ln_key
    lo30 = (ln_d - _td(days=29)).isoformat()
    spans = {"ln": (ln_key, ln_key), "l7": (lo7, hi7), "l30": (lo30, ln_key)}

    def _wl(grades: list) -> list[int]:
        return [
            sum(1 for g in grades if g == "WIN"),
            sum(1 for g in grades if g == "LOSS"),
            sum(1 for g in grades if g == "PUSH"),
        ]

    out: dict = {"ln_key": ln_key}
    for key, (lo, hi) in spans.items():
        sel = [r for r in rows if lo <= r["date"] <= hi]
        out[key] = {
            "games": len(sel),
            "lo": lo,
            "hi": hi,
            "models": {n: _wl([r["models"].get(n) for r in sel]) for n in _NHL_SIX_ORDER},
            "pl_spread": _wl([r["pl_spread"] for r in sel]),
            "xs_spread": _wl([r["xs_spread"] for r in sel]),
            "pl_total": _wl([r["pl_total"] for r in sel]),
            "xs_total": _wl([r["xs_total"] for r in sel]),
        }
    return out


def _nhl_rec(rec: list[int]) -> str:
    w, l, p = rec
    return f"{w}-{l}" + (f"-{p}" if p else "")


def _nhl_acc(rec: list[int]) -> str:
    w, l, _p = rec
    return f"{round(100.0 * w / (w + l), 1)}%" if (w + l) else "—"


def _nhl_rec_cell(rec: list[int]) -> str:
    grades = ["WIN"] * rec[0] + ["LOSS"] * rec[1] + ["PUSH"] * rec[2]
    return _nhl_cons_cell(grades)


def _nhl_paint_tally(html: str, heading_re: str, win: dict) -> str:
    """Rewrite one daily-tally block (six models + Spread + Over/Under)."""
    head = re.search(heading_re, html, flags=re.I)
    if not head:
        return html
    start = head.end()
    nxt = html.find('<div class="daily-tally"', start)
    if nxt < 0:
        nxt = min(len(html), start + 8000)
    chunk = html[start:nxt]
    faces = {name: rec for name, rec in win["models"].items()}
    faces["Spread"] = win["pl_spread"]
    faces["Over/Under"] = win["pl_total"]
    for name, rec in faces.items():
        if rec[0] + rec[1] + rec[2] == 0:
            continue
        chunk = re.sub(
            rf'(<div class="daily-model">[^<]*?{re.escape(name)}</div>\s*<div class="daily-acc"[^>]*>)'
            rf'[^<]*(</div>\s*<div class="daily-rec">)[^<]*(</div>)',
            lambda m, r=rec: m.group(1) + _nhl_acc(r) + m.group(2) + _nhl_rec(r) + m.group(3),
            chunk,
            count=1,
            flags=re.I,
        )
    return html[:start] + chunk + html[nxt:]


def _nhl_paint_plxs_table(html: str, table_id: str, wins: dict, pl_key: str, xs_key: str) -> str:
    at = html.find(f'id="{table_id}"')
    if at < 0:
        return html
    tb = html.find("<tbody>", at)
    te = html.find("</tbody>", tb)
    if tb < 0 or te < 0:
        return html
    rows = []
    for name, key in (("Prediction Lab", pl_key), ("XSharp", xs_key)):
        cells = [wins[w][key] for w in ("ln", "l7", "l30")]
        if not any(sum(c) for c in cells):
            continue
        rows.append(
            f'<tr><td class="bucket">{name}</td>'
            + "".join(f"<td>{_nhl_rec_cell(c)}</td>" for c in cells)
            + "</tr>"
        )
    if not rows:
        return html
    html = html[: tb + len("<tbody>")] + "".join(rows) + html[te:]
    # Summary line under the table follows the same 30-day records.
    read = re.search(r'<p class="cons-read">([^<]*)</p>', html[at:])
    if read:
        pl30, xs30 = wins["l30"][pl_key], wins["l30"][xs_key]
        market = "spread" if "spread" in table_id else "totals"
        def _whole(rec: list[int]) -> str:
            return f"{round(100.0 * rec[0] / (rec[0] + rec[1]))}%" if (rec[0] + rec[1]) else "—"

        text = (
            f"Last 30 days on {market}. Prediction Lab {_whole(pl30)}"
            f" · XSharp {_whole(xs30)}"
        )
        s0 = at + read.start(1)
        html = html[:s0] + escape(text) + html[at + read.end(1):]
    return html


_NHL_PCT_CELL_RE = re.compile(
    r"(\d+)-(\d+)((?:-\d+)?) <span style='color:#[0-9A-Fa-f]{6};font-weight:700'>\((\d+)%\)</span>"
    r"<div class='cons-bar' aria-hidden='true'><i style='width:\d+%;background:#[0-9A-Fa-f]{6}'></i></div>"
)


def _nhl_fix_pct_cells(html: str) -> str:
    """A W-L cell's percent is wins over decided games (3-1 is 75%, not 100%)."""

    def _cell(m: re.Match[str]) -> str:
        w, l = int(m.group(1)), int(m.group(2))
        if w + l == 0:
            return m.group(0)
        pct = round(100 * w / (w + l))
        if pct == int(m.group(4)):
            return m.group(0)
        color = "#067647" if pct >= 50 else "#b42318"
        return (
            f"{w}-{l}{m.group(3)} <span style='color:{color};font-weight:700'>({pct}%)</span>"
            f"<div class='cons-bar' aria-hidden='true'><i style='width:{pct}%;background:{color}'></i></div>"
        )

    return _NHL_PCT_CELL_RE.sub(_cell, html or "")


def _nhl_div_extent(html: str, open_at: int) -> int:
    """Index just past the </div> that closes the <div ...> starting at open_at."""
    depth = 0
    for m in re.finditer(r"<div\b|</div>", html[open_at:]):
        if m.group(0) == "</div>":
            depth -= 1
            if depth == 0:
                return open_at + m.end()
        else:
            depth += 1
    return -1


def _nhl_copy_market_panels(dst: str, src: str) -> str:
    """Spread and Totals tabs on the chart: the same tables as the cards page."""
    for market in ("spread", "totals"):
        tag = f'<div data-market-panel="{market}"'
        sa, da = src.find(tag), dst.find(tag)
        if sa < 0 or da < 0:
            continue
        se, de = _nhl_div_extent(src, sa), _nhl_div_extent(dst, da)
        if se < 0 or de < 0:
            continue
        dst = dst[:da] + src[sa:se] + dst[de:]
    return dst


def nhl_unify_results_from_cards(html: str) -> str:
    """Cards page: tallies and the PL/XSharp tables count the graded cards."""
    if not html or "game-card" not in html:
        return html
    wins = nhl_card_windows(html)
    if not wins:
        return html
    html = _nhl_paint_tally(html, r"<h2>\s*Last Night's NHL Results[^<]*</h2>", wins["ln"])
    html = _nhl_paint_tally(html, r"<h2>\s*Last 7 Days NHL Results[^<]*</h2>", wins["l7"])
    html = _nhl_paint_plxs_table(html, "pl-spread-records", wins, "pl_spread", "xs_spread")
    html = _nhl_paint_plxs_table(html, "pl-totals-records", wins, "pl_total", "xs_total")
    return _nhl_fix_pct_cells(html)


def _nhl_section_between(html: str, start_marker: str, end_marker: str) -> tuple[int, int]:
    s = html.find(start_marker)
    if s < 0:
        return -1, -1
    e = html.find(end_marker, s + len(start_marker))
    return (s, e) if e > s else (-1, -1)


def _nhl_copy_table_body(dst: str, src: str, anchor: str) -> str:
    """Copy the tbody that follows `anchor` in src into the same table in dst."""
    sa, da = src.find(anchor), dst.find(anchor)
    if sa < 0 or da < 0:
        return dst
    stb, dtb = src.find("<tbody>", sa), dst.find("<tbody>", da)
    ste, dte = src.find("</tbody>", stb), dst.find("</tbody>", dtb)
    if min(stb, dtb, ste, dte) < 0:
        return dst
    return dst[:dtb] + src[stb:ste] + dst[dte:]


def _nhl_paint_chart_tallies(html: str, wins: dict, market: str) -> str:
    """Chart Last Night / Last 7 cards for the market on screen."""
    at = html.find('id="tallies"')
    if at < 0:
        return html
    end = html.find('id="pl-consensus-slot"', at)
    if end < 0:
        end = min(len(html), at + 20000)
    chunk = html[at:end]

    def _sec(title_re: str, win: dict, chunk: str) -> str:
        m = re.search(rf"<h2>\s*{title_re}[^<]*<span class=\"tag\">\(\d+ games?\)</span></h2>", chunk)
        if not m:
            return chunk
        stop = chunk.find("</section>", m.end())
        if stop < 0:
            return chunk
        part = chunk[m.end():stop]
        if market == "moneyline":
            faces = dict(win["models"])
        elif market == "spread":
            faces = {"Prediction Lab": win["pl_spread"], "XSharp": win["xs_spread"]}
        else:
            faces = {"Prediction Lab": win["pl_total"], "XSharp": win["xs_total"]}
        for name, rec in faces.items():
            if sum(rec) == 0:
                continue
            part = re.sub(
                rf'(<div class="daily-model mlabel">{re.escape(name)}</div>\s*<div class="daily-acc acc">)'
                rf'[^<]*(</div>\s*<div class="daily-rec rec">)[^<]*(</div>)',
                lambda mm, r=rec: mm.group(1) + _nhl_acc(r) + mm.group(2) + _nhl_rec(r) + mm.group(3),
                part,
                count=1,
            )
        return chunk[: m.end()] + part + chunk[stop:]

    chunk = _sec("Last Night", wins["ln"], chunk)
    chunk = _sec("Last 7", wins["l7"], chunk)
    return html[:at] + chunk + html[end:]


def nhl_chart_consensus_from_cards_page(html: str) -> str:
    """Chart pages show the same consensus, PL vs Books and totals as the cards page."""
    if not html:
        return html
    try:
        cards = _NHL_RESULTS_DISK.read_text(encoding="utf-8")
    except Exception:
        return html
    if "game-card" not in cards:
        return html
    wins = nhl_card_windows(cards)
    if not wins:
        return html
    # Consensus and PL vs Books: the cards page tables, row for row.
    cs, ce = _nhl_section_between(cards, "Consensus Based Betting Records", "PL vs Sportsbook")
    hs, he = _nhl_section_between(html, "Consensus Based Betting Records", "PL vs Sportsbook")
    if cs >= 0 and hs >= 0:
        ctb, cte = cards.find("<tbody>", cs), cards.find("</tbody>", cs)
        htb, hte = html.find("<tbody>", hs), html.find("</tbody>", hs)
        if 0 <= ctb < cte <= ce and 0 <= htb < hte <= he:
            html = html[:htb] + cards[ctb:cte] + html[hte:]
    html = _nhl_copy_table_body(html, cards, "PL vs Sportsbook")
    html = _nhl_copy_market_panels(html, cards)
    # Totals table: Prediction Lab and XSharp against the book total.
    at = html.find('id="pl-totals-three-way"')
    if at >= 0:
        tb, te = html.find("<tbody>", at), html.find("</tbody>", at)
        if 0 <= tb < te:
            body = "".join(
                f'<tr><td class="bucket">{name}</td>'
                + "".join(f"<td>{_nhl_rec(wins[w][key]) if sum(wins[w][key]) else '0-0'}</td>" for w in ("ln", "l7", "l30"))
                + "</tr>"
                for name, key in (("Prediction Lab", "pl_total"), ("XSharp", "xs_total"))
            )
            html = html[: tb + len("<tbody>")] + body + html[te:]
    market = "moneyline"
    try:
        from flask import has_request_context, request

        if has_request_context():
            mk = (request.args.get("market") or "").strip().lower()
            if mk in ("spread", "totals"):
                market = mk
    except Exception:
        pass
    return _nhl_paint_chart_tallies(html, wins, market)


def nhl_recent_block_from_cards() -> str:
    """Picks Recent results built from the same graded cards as /nhl-results."""
    try:
        cards = _NHL_RESULTS_DISK.read_text(encoding="utf-8")
    except Exception:
        return ""
    wins = nhl_card_windows(cards)
    if not wins:
        return ""

    def _face(rec: list[int]) -> str:
        if rec[0] + rec[1] == 0:
            return "—"
        return f"{_nhl_acc(rec)} · {_nhl_rec(rec)}"

    def _table(title: str, pairs: list[tuple[str, list[int]]]) -> str:
        body = "".join(f"<tr><th>{escape(n)}</th><td>{_face(r)}</td></tr>" for n, r in pairs)
        return f'<h4 class="picks-recent-mkt">{escape(title)}</h4><table><tbody>{body}</tbody></table>'

    cols = []
    for title, key in (
        (f"Last Night — {wins['ln_key']}", "ln"),
        ("Last 7 days", "l7"),
        ("Last 30 days", "l30"),
    ):
        win = wins[key]
        cols.append(
            '<div class="picks-recent-col">'
            f"<h3>{escape(title)}</h3>"
            f'<p class="picks-recent-n">{win["games"]} games</p>'
            + _table("Moneyline", [(n, win["models"][n]) for n in _NHL_SIX_ORDER])
            + _table("Spread", [("Prediction Lab", win["pl_spread"]), ("XSharp", win["xs_spread"])])
            + _table("Totals", [("Prediction Lab", win["pl_total"]), ("XSharp", win["xs_total"])])
            + '<a class="picks-recent-more" href="/nhl-results">Full results →</a>'
            "</div>"
        )
    return "".join(cols)


def nhl_unify_picks_recent(html: str) -> str:
    """Swap the Recent results columns on /nhl-picks for the card-graded ones."""
    if not html or 'id="picks-recent-results"' not in html:
        return html
    cols = nhl_recent_block_from_cards()
    if not cols:
        return html
    sec = re.search(r'<section id="picks-recent-results"[\s\S]*?</section>', html)
    if not sec:
        return html
    block = sec.group(0)
    grid = re.search(r'(<div class="picks-recent-grid">)([\s\S]*?)(</div>\s*<style>)', block)
    if not grid:
        return html
    fresh = block[: grid.start(2)] + cols + block[grid.end(2):]
    try:
        # Keep the saved strip (read by other NHL steps) on the same numbers.
        from picks_recent_results import _CACHE as _recent_cache, _write_nhl_block_disk

        _write_nhl_block_disk(fresh)
        _recent_cache["NHL"] = (time.time(), fresh)
    except Exception:
        pass
    return html[: sec.start()] + fresh + html[sec.end():]


def _nhl_table_rows(html: str, start_marker: str, end_marker: str) -> dict[str, list[str]]:
    """Row label -> [last night, past 7, past 30] plain W-L text from one table."""
    s0, e0 = _nhl_section_between(html, start_marker, end_marker)
    if s0 < 0:
        s0 = html.find(start_marker)
        e0 = html.find("</table>", s0) if s0 >= 0 else -1
    if s0 < 0 or e0 < 0:
        return {}
    out: dict[str, list[str]] = {}
    for label, rest in re.findall(
        r'<tr>\s*<td class="(?:bucket|signal)">([^<]+)</td>([\s\S]*?)</tr>', html[s0:e0]
    ):
        cells = re.findall(r"<td(?:\s[^>]*)?>([\s\S]*?)</td>", rest)
        recs = []
        for cell in cells[:3]:
            m = re.search(r"(\d+-\d+(?:-\d+)?)", re.sub(r"<[^>]+>", " ", cell))
            recs.append(m.group(1) if m else "0-0")
        if len(recs) == 3:
            out[_nhl_norm_label(label)] = recs
    return out


def nhl_sync_card_chips(html: str, cards_html: str) -> str:
    """Card chips read the tables: consensus = Past 7, PL vs Books = Past 30."""
    if not html or "line-chip-val" not in html or not cards_html:
        return html
    cons = _nhl_table_rows(cards_html, "Consensus Based Betting Records", "PL vs Sportsbook")
    books = _nhl_table_rows(cards_html, "PL vs Sportsbook", "</table>")
    if not cons and not books:
        return html

    def _cons(m: re.Match[str]) -> str:
        label = m.group(1).strip()
        key = _nhl_norm_label(re.sub(r"^(\d/6)\s+(all but)", r"\1 — \2", label, flags=re.I))
        rec = (cons.get(key) or ["", "0-0", ""])[1] if cons else None
        if rec is None:
            return m.group(0)
        return f'line-chip-val">Consensus Record: {label} ({rec})'

    html = re.sub(r'line-chip-val">Consensus Record: ([^<(]+?)\s*\((\d+-\d+(?:-\d+)?)\)', _cons, html)

    def _books(m: re.Match[str]) -> str:
        label = m.group(1).strip()
        recs = books.get(_nhl_norm_label(label))
        if not recs:
            return m.group(0)
        rec = recs[2]
        w, l = (int(x) for x in rec.split("-")[:2])
        pct = f" ({round(100 * w / (w + l))}%)" if (w + l) else ""
        return f'line-chip-val">{label}: {rec}{pct} — Past 30 Days'

    return re.sub(
        r'line-chip-val">([^<:]+):\s*(\d+-\d+(?:-\d+)?)(?:\s*\(\d+%\))?\s*—\s*Past 30 Days',
        _books,
        html,
    )


def _three_way_inside_main(html: str) -> str:
    """Saved chart pages can carry the three-way tables after the footer."""
    if not html:
        return html
    foot = html.find("<footer")
    if foot < 0:
        return html
    pat = re.compile(
        r'<section class="pl-consensus-records" id="three-way-[^"]+">[\s\S]*?</section>'
    )
    blocks = [m for m in pat.finditer(html) if m.start() > foot]
    if not blocks:
        return html
    moved = "".join(m.group(0) for m in blocks)
    for m in reversed(blocks):
        html = html[: m.start()] + html[m.end():]
    at = html.rfind("</main>")
    if at < 0:
        at = html.find("<footer")
    return html[:at] + moved + html[at:]


def nhl_final_pass(html: str, path: str, view: str) -> str:
    """Last NHL step: every record on picks, results and chart counts the same cards."""
    tail = (path or "").lower().rstrip("/")
    chartish = (view or "").strip().lower() in ("chart", "tabs", "markets", "tabbed", "spread", "totals")
    try:
        if tail.endswith("-picks"):
            html = nhl_unify_picks_recent(html)
            try:
                cards = _NHL_RESULTS_DISK.read_text(encoding="utf-8")
            except Exception:
                cards = ""
            return nhl_sync_card_chips(html, cards)
        if tail.endswith("-results") and chartish:
            return _three_way_inside_main(nhl_chart_consensus_from_cards_page(html))
        if tail.endswith("-results"):
            html = nhl_unify_results_from_cards(html)
            html = nhl_sync_card_chips(html, html)
            try:
                html = nhl_fill_totals_chart_from_picks(html)
            except Exception:
                pass
            write_nhl_results_disk(html)
            # The chart view lists games from this saved copy of the cards.
            # Keep it in step with the cards page so new finals reach the chart.
            if 'class="game-card' in html and "Last Night's NHL Results" in html:
                _persist_cards_html("NHL", html)
    except Exception:
        pass
    return html


def _nhl_unify_any(html: str) -> str:
    """Older Last 7 steps run after the final pass; put the card counts back."""
    try:
        if not html:
            return html
        if 'id="picks-recent-results"' in html:
            html = nhl_unify_picks_recent(html)
            try:
                cards = _NHL_RESULTS_DISK.read_text(encoding="utf-8")
            except Exception:
                cards = ""
            return nhl_sync_card_chips(html, cards)
        if "Last Night's NHL Results" in html and "game-card" in html:
            html = nhl_unify_results_from_cards(html)
            return nhl_sync_card_chips(html, html)
    except Exception:
        pass
    return html
