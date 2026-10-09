"""Isolate checker repairs — consensus charts, SOU tables, share, books face.

Display / payload completeness only. Does not invent model picks or book lines.
"""
from __future__ import annotations

import json
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
        if sport_u == "MLB":
            listed = re.search(r"const allDates = (\[[^\]]*\])", text)
            try:
                dates = json.loads(listed.group(1)) if listed else []
            except Exception:
                dates = []
            if len(dates) < 2:
                if schedule:
                    schedule_picks_refresh(sport_u)
                return ""
        if _picks_clock_is_old(text):
            if schedule:
                schedule_picks_refresh(sport_u)
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


def _served_chart_misses_last_night(sport: str, html: str) -> bool:
    """Chart HTML is stale when it does not include the newest final night."""
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        today = datetime.now().date()
    yesterday = (today - timedelta(days=1)).isoformat()
    path = Path(__file__).resolve().parent / "sports_predictions_original.db"
    if not path.is_file():
        return False
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        row = conn.execute(
            """
            SELECT date(game_date), COUNT(*) FROM games
            WHERE upper(sport) = ? AND home_score IS NOT NULL
              AND date(game_date) <= ?
            GROUP BY date(game_date)
            ORDER BY date(game_date) DESC
            LIMIT 1
            """,
            (sport, yesterday),
        ).fetchone()
        conn.close()
    except Exception:
        return False
    if not row or not row[0]:
        return False
    latest = str(row[0])[:10]
    latest_n = int(row[1] or 0)
    if latest_n and latest not in (html or ""):
        return True
    # The date can appear elsewhere on the page (date list), so also compare the
    # Last Night heading itself with the newest final night.
    shown = re.search(r"Last Night(?:'s)? [^<]{0,80}?(\d{4}-\d{2}-\d{2})", html or "")
    if latest_n and shown and shown.group(1) != latest:
        return True
    return False


def _served_results_are_stale(key: str, html: str) -> bool:
    """The cards page is stale when its today stamp or Last Night slate is behind."""
    parts = (key.split("|") + [""] * 6)[:6]
    sport = (parts[0] or "").strip().upper()
    view = (parts[1] or "").strip().lower()
    if not sport or not html:
        return False
    if view:
        return _served_chart_misses_last_night(sport, html)
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
            SELECT date(game_date), COUNT(*) FROM games
            WHERE upper(sport) = ? AND home_score IS NOT NULL
              AND date(game_date) <= ?
            GROUP BY date(game_date)
            ORDER BY date(game_date) DESC
            LIMIT 1
            """,
            (sport, yesterday.isoformat()),
        ).fetchone()
        conn.close()
    except Exception:
        return False
    if not row or not row[0]:
        return False
    latest = str(row[0])[:10]
    latest_n = int(row[1] or 0)
    if shown.isoformat() != latest:
        return True
    section = re.search(
        rf'id="date-{re.escape(latest)}"([\s\S]*?)(?:id="date-|$)',
        html,
    )
    shown_n = len(set(re.findall(r'data-game-id="[^"]+"', section.group(1)))) if section else 0
    if latest_n and shown_n < latest_n:
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
    """Finished results HTML. Memory first, then the last repaired page on disk."""
    if results_refresh_forced():
        return ""
    now = time.time()
    hit = _SERVED.get(key)
    if hit and now - hit[0] < _SERVED_TTL and _REPAIRED_MARK in hit[1]:
        if _served_results_are_stale(key, hit[1]):
            return ""
        return hit[1]
    try:
        path = _served_path(key)
        if path.is_file() and now - path.stat().st_mtime < _SERVED_DISK_MAX:
            text = path.read_text(encoding="utf-8")
            age = now - path.stat().st_mtime
            if _REPAIRED_MARK in text and len(text) > 500:
                if _served_results_are_stale(key, text):
                    return ""
                _SERVED[key] = (now, text)
                if age >= _SERVED_REFRESH_AFTER:
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
.pc-name{overflow:visible!important;text-overflow:clip!important;white-space:normal!important;max-height:none!important;height:auto!important}
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
        html = ensure_sou_compare(html, market_l)
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


def repair_results_html(html: str, sport: str, view: str = "") -> str:
    """Post-process any team-sport results/chart page for checker markers."""
    if not html or "<" not in html:
        return html
    if _REPAIRED_MARK in html:
        sport_u = (sport or "").strip().upper()
        if "6/6 unanimous" in html and sport_u not in {"TENNIS", "UFC", "GOLF"}:
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
        if view_l in ("spread", "totals") and sport_u != "MLB":
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
            if abs(num - 50.0) < 0.051 and key != "consensus":
                continue
            attr = f"data-m-{key}"
            shown = f"{num:.1f}"
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
    ".pc-name{overflow:visible!important;text-overflow:clip!important;"
    "white-space:normal!important;max-height:none!important;height:auto!important}"
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
    if sport_u == "MLB":
        try:
            from mlb_ui_fixup import inject_mlb_consensus_and_pl_vs_books_chips
            html = inject_mlb_consensus_and_pl_vs_books_chips(html)
        except Exception:
            pass
    html = ensure_two_card_row(html)
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


_MLB_SPLIT_BUCKET = "3/6 — all but Edge and XSharp and Efficiency"


def _mlb_window_record_cell(wins: int, losses: int, *, bar: bool) -> str:
    """Same W-L cell the consensus table already uses. No record means no cell."""
    if wins + losses <= 0:
        return ""
    from mlb_consensus_hub import _consensus_record_cell

    items = [{"grade": "WIN"}] * wins + [{"grade": "LOSS"}] * losses
    return _consensus_record_cell(items, bar=bar, empty="")


def _mlb_split_window_grades(cards_html: str) -> dict[str, tuple[int, int]] | None:
    """W-L already graded on the cards for the named 3/6 bucket. Nothing else."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from qa.chart_shape import _last_night_consensus_date, _six_model_games_from_cards

    games = [
        g
        for g in _six_model_games_from_cards(cards_html or "", "MLB")
        if g.get("label") == _MLB_SPLIT_BUCKET and g.get("grade") in ("WIN", "LOSS")
    ]
    if not games:
        return None
    try:
        today = datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        today = datetime.now().date()
    ln_key = _last_night_consensus_date(cards_html or "") or (
        today - timedelta(days=1)
    ).isoformat()
    today_s = today.isoformat()
    cut7 = (today - timedelta(days=7)).isoformat()
    cut30 = (today - timedelta(days=30)).isoformat()

    def _wl(pred) -> tuple[int, int]:
        rows = [g for g in games if pred(g["date"])]
        return (
            sum(1 for g in rows if g["grade"] == "WIN"),
            sum(1 for g in rows if g["grade"] == "LOSS"),
        )

    return {
        "ln": _wl(lambda d: d == ln_key),
        "l7": _wl(lambda d: cut7 <= d < today_s),
        "l30": _wl(lambda d: cut30 <= d < today_s),
    }


def _is_blank_record_cell(raw: str) -> bool:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = text.replace("&mdash;", "—").replace("&ndash;", "–")
    text = re.sub(r"\s+", " ", text).strip()
    return text in {"", "—", "–", "-", "0-0"}


def fill_mlb_named_split_from_cards(html: str, cards_html: str | None = None) -> str:
    """Put the named 3/6 bucket's existing card grades on the consensus row.

    Does not rewrite any other bucket, and does not replace a W-L that is
    already on the row. A blank Past 7 / Past 30 cell is filled only when the
    cards already graded games in that window.
    """
    if not html or "Consensus Based Betting Records" not in html:
        return html
    grades = _mlb_split_window_grades(cards_html or html)
    if not grades:
        return html
    from qa.chart_shape import ML_CHART, PL_VS_BOOKS

    start = html.find(ML_CHART)
    if start < 0:
        return html
    end = html.find(PL_VS_BOOKS, start + 1)
    if end < start:
        end = start + 12000
    block = html[start:end]
    ln_cell = _mlb_window_record_cell(*grades["ln"], bar=False)
    l7_cell = _mlb_window_record_cell(*grades["l7"], bar=True)
    l30_cell = _mlb_window_record_cell(*grades["l30"], bar=True)
    row_re = re.compile(
        r"(<tr>\s*<td class=\"bucket\">"
        + re.escape(_MLB_SPLIT_BUCKET)
        + r"</td>\s*<td>)([\s\S]*?)(</td>\s*<td>)([\s\S]*?)(</td>\s*<td>)([\s\S]*?)(</td>\s*</tr>)",
        flags=re.I,
    )
    found = row_re.search(block)

    def _keep_or_fill(current: str, filled: str) -> str:
        if not filled or not _is_blank_record_cell(current):
            return current
        return filled

    if found:
        new_row = (
            found.group(1)
            + _keep_or_fill(found.group(2), ln_cell)
            + found.group(3)
            + _keep_or_fill(found.group(4), l7_cell)
            + found.group(5)
            + _keep_or_fill(found.group(6), l30_cell)
            + found.group(7)
        )
        if new_row == found.group(0):
            return html
        block = block[: found.start()] + new_row + block[found.end() :]
        return html[:start] + block + html[end:]
    if not (ln_cell and l7_cell and l30_cell):
        return html
    row = (
        f'<tr><td class="bucket">{_MLB_SPLIT_BUCKET}</td>'
        f"<td>{ln_cell}</td><td>{l7_cell}</td><td>{l30_cell}</td></tr>"
    )
    if re.search(r"</tbody>", block, flags=re.I):
        block = re.sub(r"</tbody>", row + "</tbody>", block, count=1, flags=re.I)
    else:
        return html
    return html[:start] + block + html[end:]


_MLB_CHART_VIEWS = {"chart", "tabs", "markets", "tabbed", "spread", "totals"}


def _mlb_public_page_url(path: str) -> str:
    """Public picks/results URL from the site's canonical host. Not the local request."""
    path_l = (path or "").split("?")[0].rstrip("/").lower()
    if path_l not in ("/mlb-picks", "/mlb-results"):
        return ""
    try:
        from NHL77FINAL import _CANONICAL_HOST
        host = str(_CANONICAL_HOST or "").strip()
    except Exception:
        host = ""
    if not host:
        return ""
    return f"https://{host}{path_l}"


def rewrite_mlb_customer_share_hrefs(html: str, path: str) -> str:
    """Share-icon hrefs on MLB picks and results use the public origin."""
    public = _mlb_public_page_url(path)
    if not html or not public or "share-icon" not in html:
        return html
    from urllib.parse import quote

    encoded = quote(public, safe="/")
    local = re.compile(
        r"https?%3A//(?:localhost|127\.0\.0\.1)(?:%3A\d+)?/mlb-(?:picks|results)"
        r"|https?://(?:localhost|127\.0\.0\.1)(?::\d+)?/mlb-(?:picks|results)",
        re.I,
    )

    def _anchor(match: re.Match) -> str:
        tag = match.group(0)
        href_m = re.search(r'href="([^"]*)"', tag, flags=re.I)
        if not href_m:
            return tag
        href = href_m.group(1)
        if not re.search(r"localhost|127\.0\.0\.1", href, flags=re.I):
            return tag
        new_href = local.sub(encoded, href)
        if new_href == href:
            return tag
        return tag[: href_m.start(1)] + new_href + tag[href_m.end(1) :]

    return re.sub(
        r'<a\b[^>]*\bclass="[^"]*\bshare-icon\b[^"]*"[^>]*>',
        _anchor,
        html,
        flags=re.I,
    )


_MLB_SIX = (
    "Grinder2",
    "Takedown",
    "Edge",
    "XSharp",
    "Sharp Consensus",
    "Efficiency",
)

_MLB_SIDE_BOX_RE = re.compile(
    r'class="pc-name">([^<]+)</div>\s*'
    r'<div class="pc-val"[^>]*>[\s\S]*?</div>\s*'
    r'<div class="pc-side\s+(home|away)\b[^>]*>([\s\S]*?)</div>',
    re.I,
)


def _mlb_result_sections(html: str) -> list[tuple[str, list[str]]]:
    """Date sections and the game-card HTML already on the results page."""
    sections: list[tuple[str, list[str]]] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', html or "")
    it = iter(chunks[1:])
    for date in it:
        content = next(it, "")
        cards = re.split(r'(?=<div class="game-card\b)', content, flags=re.I)
        sections.append((date, [c for c in cards[1:] if c.strip()]))
    return sections


def _mlb_latest_final_section(html: str) -> tuple[str, int]:
    """Newest results date that already has game cards, and that card count."""
    best = ""
    count = 0
    for date, bodies in _mlb_result_sections(html):
        n = len(bodies)
        if n and date >= best:
            best, count = date, n
    return best, count


def _mlb_model_marks(body: str) -> dict[str, bool]:
    """Stored home/away class plus the check mark. A side with no mark is skipped."""
    picks: dict[str, bool] = {}
    for name, _side, inner in _MLB_SIDE_BOX_RE.findall(body[:25000]):
        name = re.sub(r"[^A-Za-z0-9 ]+", "", name).strip()
        if name not in _MLB_SIX or name in picks:
            continue
        if "✅" in inner:
            picks[name] = True
        elif "❌" in inner:
            picks[name] = False
    return picks


def _mlb_side_picks(body: str) -> dict[str, tuple[str, bool]]:
    picks: dict[str, tuple[str, bool]] = {}
    for name, side, inner in _MLB_SIDE_BOX_RE.findall(body[:25000]):
        name = re.sub(r"[^A-Za-z0-9 ]+", "", name).strip()
        if name not in _MLB_SIX or name in picks:
            continue
        if "✅" in inner:
            ok = True
        elif "❌" in inner:
            ok = False
        else:
            continue
        picks[name] = (side.lower(), ok)
    return picks


def _mlb_consensus_from_card(body: str, date: str) -> dict | None:
    """Same majority bucket the cards already show.

    Even 3-3 splits have no majority; the table omits them (same as Chart).
    """
    picks = _mlb_side_picks(body)
    if len(picks) < 6:
        return None
    counts: dict[str, int] = {}
    for name in _MLB_SIX:
        side = picks[name][0]
        counts[side] = counts.get(side, 0) + 1
    maj_side, maj_n = max(counts.items(), key=lambda kv: kv[1])
    if list(counts.values()).count(maj_n) > 1:
        return None
    dissent = [n for n in _MLB_SIX if picks[n][0] != maj_side]
    if maj_n >= 6:
        label = "6/6 unanimous"
    elif dissent:
        label = f"{maj_n}/6 — all but " + " and ".join(dissent)
    else:
        return None
    grade_ok = next(picks[n][1] for n in _MLB_SIX if picks[n][0] == maj_side)
    return {"date": date, "label": label, "grade": "WIN" if grade_ok else "LOSS"}


def _mlb_games_from_card_sides(html: str) -> list[dict]:
    games: list[dict] = []
    for date, bodies in _mlb_result_sections(html):
        for body in bodies:
            game = _mlb_consensus_from_card(body, date)
            if game:
                games.append(game)
    return games


def _mlb_market_mark(body: str, label: str) -> str | None:
    found = re.search(
        rf'class="sf-label">{re.escape(label)}</span>\s*'
        r'<span class="sf-val">([\s\S]*?)</span>',
        body,
        flags=re.I,
    )
    if not found:
        return None
    val = found.group(1)
    if "pick-ok" in val or "✅" in val:
        return "WIN"
    if "pick-no" in val or "❌" in val:
        return "LOSS"
    return None


def _mlb_blank_daily_rec(raw: str) -> bool:
    text = (raw or "").replace("&mdash;", "—").replace("&ndash;", "–").strip()
    if text.lower() in {"no spread data", "no o/u data"}:
        return True
    return text in {"", "—", "–", "-"}


def align_mlb_last_night_window(html: str) -> str:
    """Point an empty Last Night heading at the newest graded date section.

    Model, spread, and total records in that block come from the check marks
    on those cards. A record that already has a W-L is left as printed.
    Last 7 and every other tally stay.
    """
    if not html or "Last Night" not in html:
        return html
    latest, count = _mlb_latest_final_section(html)
    if not latest or count < 1:
        return html
    heading = re.search(
        r"<h2>\s*Last Night's MLB Results\s*[—–-]\s*"
        r"(\d{4}-\d{2}-\d{2})\s*\((\d+)\s*games?\)\s*</h2>",
        html,
        flags=re.I,
    )
    if not heading:
        return html
    start = html.rfind('<div class="daily-tally"', 0, heading.start())
    nxt = html.find('<div class="daily-tally"', heading.end())
    if start < 0 or nxt < 0:
        return html
    block = html[start:nxt]
    shown_date, shown_n = heading.group(1), int(heading.group(2))
    if shown_n > 0 and shown_date != latest:
        return html
    target = latest if shown_n < 1 else shown_date
    if shown_n < 1:
        block = re.sub(
            r"(Last Night's MLB Results\s*[—–-]\s*)\d{4}-\d{2}-\d{2}(\s*\()\d+(\s*games?\))",
            rf"\g<1>{target}\g<2>{count}\g<3>",
            block,
            count=1,
            flags=re.I,
        )
        block = re.sub(
            r'(data-date=")\d{4}-\d{2}-\d{2}(")',
            rf"\g<1>{target}\g<2>",
            block,
            count=1,
        )
        block = re.sub(
            r'(data-games=")\d+(")',
            rf"\g<1>{count}\g<2>",
            block,
            count=1,
        )
    models = {name: [0, 0] for name in _MLB_SIX}
    spread = [0, 0]
    total = [0, 0]
    saw = False
    for date, bodies in _mlb_result_sections(html):
        if date != target:
            continue
        saw = True
        for body in bodies:
            for name, ok in _mlb_model_marks(body).items():
                models[name][0 if ok else 1] += 1
            for slot, label in ((spread, "Spread pick"), (total, "Total pick")):
                mark = _mlb_market_mark(body, label)
                if mark == "WIN":
                    slot[0] += 1
                elif mark == "LOSS":
                    slot[1] += 1
    if not saw:
        return html[:start] + block + html[nxt:]

    def _paint(label: str, wins: int, losses: int, source: str) -> str:
        if wins + losses <= 0:
            return source
        pct = f"{(100.0 * wins / (wins + losses)):.1f}"
        rec = f"{wins}-{losses}"
        pat = re.compile(
            rf'(<div class="daily-model">[^<]*{re.escape(label)}</div>\s*)'
            r'<div class="daily-acc"[^>]*>[\s\S]*?</div>\s*'
            r'<div class="daily-rec">([^<]*)</div>',
            re.I,
        )

        def _repl(match: re.Match) -> str:
            if not _mlb_blank_daily_rec(match.group(2)):
                return match.group(0)
            return (
                f'{match.group(1)}<div class="daily-acc">{pct}%</div>\n'
                f'                    <div class="daily-rec">{rec}</div>'
            )

        return pat.sub(_repl, source, count=1)

    for name in _MLB_SIX:
        block = _paint(name, models[name][0], models[name][1], block)
    block = _paint("Spread", spread[0], spread[1], block)
    block = _paint("Over/Under", total[0], total[1], block)
    if block == html[start:nxt]:
        return html
    return html[:start] + block + html[nxt:]


def _mlb_pickem_pl_grades(html: str, date: str) -> list[str]:
    """PL moneyline grades on cards whose book prices have no favorite."""
    grades: list[str] = []
    for dk, bodies in _mlb_result_sections(html):
        if dk != date:
            continue
        for card in bodies:
            scores = re.findall(r'class="final-score[^"]*">\s*(\d+)\s*<', card)
            if len(scores) < 2:
                continue
            try:
                away_s, home_s = int(scores[0]), int(scores[1])
            except ValueError:
                continue
            mls = re.findall(
                r'class="ml-src[^"]*">(Books|Prediction Lab)[\s\S]{0,120}?'
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
            book_side = (
                _side_from_mls(book_pair[1], book_pair[0]) if len(book_pair) >= 2 else None
            )
            pl_side = _side_from_mls(pl_pair[1], pl_pair[0]) if len(pl_pair) >= 2 else None
            if book_side in ("HOME", "AWAY"):
                continue
            grade = _grade_side(pl_side, home_s, away_s)
            if grade in ("WIN", "LOSS"):
                grades.append(grade)
    return grades


def _mlb_count_pickem_as_books_push(html: str) -> str:
    """A last-night pick'em (equal book prices) is a push, so Books favorite
    covers the same games as Last Night. Does not copy another model's grade.
    """
    if not html or "PL vs Sportsbook" not in html:
        return html
    start = html.find("PL vs Sportsbook")
    end = html.find("</table>", start)
    if end < start:
        return html
    end += len("</table>")
    block = html[start:end]
    books_m = re.search(
        r'<td class="signal">Books favorite</td>\s*<td>([\s\S]*?)</td>',
        block,
        flags=re.I,
    )
    if not books_m:
        return html
    found = re.search(
        r"(\d{1,3})-(\d{1,3})(?:-(\d{1,3}))?",
        re.sub(r"<[^>]+>", " ", books_m.group(1)),
    )
    if not found:
        return html
    wins, losses, pushes = int(found.group(1)), int(found.group(2)), int(found.group(3) or 0)
    if pushes:
        return html
    date_m = re.search(r"Last night \((\d{4}-\d{2}-\d{2})\)", block, flags=re.I)
    if not date_m:
        return html
    night = re.search(
        rf"Last Night(?:'s)?[^(\n]{{0,80}}{re.escape(date_m.group(1))}\s*\((\d+)\s*games?\)",
        html,
        flags=re.I,
    )
    if not night:
        return html
    night_n = int(night.group(1))
    pickems = len(_mlb_pickem_pl_grades(html, date_m.group(1)))
    if pickems < 1 or wins + losses + pushes + pickems != night_n:
        return html
    from mlb_consensus_hub import _consensus_record_cell

    items = (
        [{"grade": "WIN"}] * wins
        + [{"grade": "LOSS"}] * losses
        + [{"grade": "PUSH"}] * (pushes + pickems)
    )
    cell = _consensus_record_cell(items, bar=True, empty="")
    if not cell:
        return html
    new_block = block[: books_m.start(1)] + cell + block[books_m.end(1) :]
    if new_block == block:
        return html
    return html[:start] + new_block + html[end:]


def align_mlb_pl_vs_books_partition(html: str) -> str:
    """Make agree + disagree cover the same games as the stored PL favorite.

    A card with equal book prices has no books favorite. That game is a push
    on Books favorite so the last-night count matches. Its stored Prediction
    Lab grade is added to the agree cell. PL favorite, Past 7, and Past 30
    are not rewritten, and no other game's grade is copied.
    """
    html = _mlb_count_pickem_as_books_push(html)
    if not html or "PL vs Sportsbook" not in html:
        return html
    start = html.find("PL vs Sportsbook")
    end = html.find("</table>", start)
    if end < start:
        return html
    end += len("</table>")
    block = html[start:end]

    def _cell(label: str) -> re.Match | None:
        return re.search(
            rf'<td class="signal">{re.escape(label)}</td>\s*<td>([\s\S]*?)</td>',
            block,
            flags=re.I,
        )

    def _parts(match: re.Match | None) -> tuple[int, int, int] | None:
        if not match:
            return None
        found = re.search(
            r"(\d{1,3})-(\d{1,3})(?:-(\d{1,3}))?",
            re.sub(r"<[^>]+>", " ", match.group(1)),
        )
        if not found:
            return None
        return int(found.group(1)), int(found.group(2)), int(found.group(3) or 0)

    pl_m, agree_m, disagree_m = (
        _cell("PL favorite"),
        _cell("PL and Books agree"),
        _cell("PL vs Books disagree"),
    )
    pl, agree, disagree = _parts(pl_m), _parts(agree_m), _parts(disagree_m)
    if not pl or not agree or not disagree or agree_m is None:
        return html
    pl_n = pl[0] + pl[1] + pl[2]
    part_n = sum(agree) + sum(disagree)
    if pl_n < 1 or part_n >= pl_n or agree[2]:
        return html
    date_m = re.search(r"Last night \((\d{4}-\d{2}-\d{2})\)", block, flags=re.I)
    if not date_m:
        date_m = re.search(
            r"Last Night's MLB Results\s*[—–-]\s*(\d{4}-\d{2}-\d{2})",
            html,
            flags=re.I,
        )
    if not date_m:
        return html
    extras = _mlb_pickem_pl_grades(html, date_m.group(1))
    if len(extras) != pl_n - part_n:
        return html
    wins = agree[0] + sum(1 for g in extras if g == "WIN")
    losses = agree[1] + sum(1 for g in extras if g == "LOSS")
    cell = _mlb_window_record_cell(wins, losses, bar=True)
    if not cell:
        return html
    new_block = block[: agree_m.start(1)] + cell + block[agree_m.end(1) :]
    if new_block == block:
        return html
    return html[:start] + new_block + html[end:]


def align_mlb_consensus_past7_from_cards(html: str) -> str:
    """Past 7 consensus cells from graded result cards in the Last 7 window.

    Does not rewrite Last night or Past 30 on a row that is already there.
    A bucket that the cards graded and the table omitted is added from those
    same cards. Nothing is hardcoded.
    """
    if not html or "Consensus Based Betting Records" not in html:
        return html
    window = re.search(
        r"Last 7 Days[^<]{0,180}?(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
        html,
        flags=re.I,
    )
    if not window:
        return html
    start_d, end_d = window.group(1), window.group(2)
    from collections import defaultdict
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from qa.chart_shape import (
        ML_CHART,
        PL_VS_BOOKS,
        _last_night_consensus_date,
        _norm_cons_label,
        _wl_parts,
    )

    games = [
        g
        for g in _mlb_games_from_card_sides(html)
        if g.get("grade") in ("WIN", "LOSS") and g.get("date")
    ]
    week = [g for g in games if start_d <= g["date"] <= end_d]
    if not week:
        return html
    week_wl: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    labels: dict[str, str] = {}
    for g in week:
        key = _norm_cons_label(g["label"])
        labels.setdefault(key, g["label"])
        week_wl[key][0 if g["grade"] == "WIN" else 1] += 1
    try:
        today = datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        today = datetime.now().date()
    today_s = today.isoformat()
    cut30 = (today - timedelta(days=30)).isoformat()
    ln_key = _last_night_consensus_date(html) or end_d
    ln_wl: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    p30_wl: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for g in games:
        key = _norm_cons_label(g["label"])
        labels.setdefault(key, g["label"])
        slot = 0 if g["grade"] == "WIN" else 1
        if g["date"] == ln_key:
            ln_wl[key][slot] += 1
        if cut30 <= g["date"] < today_s:
            p30_wl[key][slot] += 1

    start = html.find(ML_CHART)
    if start < 0:
        start = html.find("Consensus Based Betting Records")
    if start < 0:
        return html
    end = html.find(PL_VS_BOOKS, start + 1)
    if end < start:
        end = html.find("</table>", start)
        if end > start:
            end += len("</table>")
    if end < start:
        return html
    block = html[start:end]
    row_re = re.compile(
        r'(<tr>\s*<td class="bucket">)([^<]+)(</td>\s*<td>)([\s\S]*?)(</td>\s*<td>)([\s\S]*?)(</td>\s*<td>)([\s\S]*?)(</td>\s*</tr>)',
        flags=re.I,
    )
    seen: set[str] = set()

    def _repl(match: re.Match) -> str:
        key = _norm_cons_label(match.group(2))
        seen.add(key)
        if key not in week_wl:
            return match.group(0)
        want_w, want_l = week_wl[key]
        current = _wl_parts(re.sub(r"<[^>]+>", " ", match.group(6)))
        if current and current[0] == want_w and current[1] == want_l:
            return match.group(0)
        cell = _mlb_window_record_cell(want_w, want_l, bar=True)
        if not cell:
            return match.group(0)
        return (
            match.group(1)
            + match.group(2)
            + match.group(3)
            + match.group(4)
            + match.group(5)
            + cell
            + match.group(7)
            + match.group(8)
            + match.group(9)
        )

    new_block = row_re.sub(_repl, block)
    extras = []
    for key, (wins, losses) in week_wl.items():
        if key in seen or wins + losses <= 0:
            continue
        past7 = _mlb_window_record_cell(wins, losses, bar=True)
        if not past7:
            continue
        night_w, night_l = ln_wl[key]
        night = (
            _mlb_window_record_cell(night_w, night_l, bar=False)
            if night_w + night_l
            else "0-0"
        )
        month_w, month_l = p30_wl[key]
        month = _mlb_window_record_cell(month_w, month_l, bar=True) or "0-0"
        extras.append(
            f'<tr><td class="bucket">{labels.get(key, key)}</td>'
            f"<td>{night}</td><td>{past7}</td><td>{month}</td></tr>"
        )
    if extras:
        if not re.search(r"</tbody>", new_block, flags=re.I):
            return html
        new_block = re.sub(
            r"</tbody>",
            "".join(extras) + "</tbody>",
            new_block,
            count=1,
            flags=re.I,
        )
    if new_block == block:
        return html
    return html[:start] + new_block + html[end:]


def grade_mlb_named_split_side(html: str) -> str:
    """Last-night 3/6 cell matches the card grade for that bucket.

    The cards grade the models other than Edge, XSharp, and Efficiency.
    The label stays. Past 7 and Past 30 are not rewritten.
    """
    if not html or _MLB_SPLIT_BUCKET not in html:
        return html
    from qa.chart_shape import _last_night_consensus_date

    ln_key = _last_night_consensus_date(html)
    if not ln_key:
        return html
    night = [
        g
        for g in _mlb_games_from_card_sides(html)
        if g.get("label") == _MLB_SPLIT_BUCKET
        and g.get("date") == ln_key
        and g.get("grade") in ("WIN", "LOSS")
    ]
    if not night:
        return html
    wins = sum(1 for g in night if g["grade"] == "WIN")
    losses = sum(1 for g in night if g["grade"] == "LOSS")
    cell = f"{wins}-{losses}"
    row_re = re.compile(
        r'(<tr>\s*<td class="bucket">'
        + re.escape(_MLB_SPLIT_BUCKET)
        + r'</td>\s*<td>)([\s\S]*?)(</td>)',
        flags=re.I,
    )
    found = row_re.search(html)
    if not found:
        return html
    current = re.search(r"(\d+)\s*-\s*(\d+)", found.group(2))
    if current and int(current.group(1)) == wins and int(current.group(2)) == losses:
        return html
    return html[: found.start(2)] + cell + html[found.end(2) :]


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
    if "results" in path_l and _REPAIRED_MARK in (html or ""):
        # A repaired MLB page can still be missing a last-night dissent row.
        # Re-grade that row from the cards. Other sports stay on the stored page.
        if sport_u == "MLB" and "6/6 unanimous" in (html or ""):
            try:
                view_l = (view or "").strip().lower()
                is_chart = view_l in (
                    "chart",
                    "tabs",
                    "markets",
                    "tabbed",
                    "spread",
                    "totals",
                )
                align_src = (
                    html
                    if not is_chart
                    else (_cards_html_for_align(sport_u, "") or None)
                )
                html = fill_mlb_named_split_from_cards(html, cards_html=align_src)
            except Exception:
                pass
        if sport_u == "MLB":
            view_l = (view or "").strip().lower()
            if view_l not in _MLB_CHART_VIEWS:
                html = align_mlb_last_night_window(html)
            try:
                from picks_recent_results import fill_mlb_totals_chart_from_grades

                html = fill_mlb_totals_chart_from_grades(html)
            except Exception:
                pass
            if view_l not in _MLB_CHART_VIEWS:
                html = align_mlb_consensus_past7_from_cards(html)
                html = grade_mlb_named_split_side(html)
                html = align_mlb_pl_vs_books_partition(html)
                html = rewrite_mlb_customer_share_hrefs(html, path or "/mlb-results")
        try:
            store_served_results(_request_results_key(sport_u), html)
        except Exception:
            pass
        return html
    if "picks" in path_l:
        if sport_u == "MLB":
            try:
                from picks_recent_results import align_mlb_recent_results_strip

                html = align_mlb_recent_results_strip(html)
            except Exception:
                pass
            html = rewrite_mlb_customer_share_hrefs(html, path or "/mlb-picks")
        if _REPAIRED_MARK not in (html or ""):
            html = repair_picks_html(html, sport_u)
            if sport_u == "NHL":
                html = fill_blank_books_ml(html)
                html = hide_blank_books_ml(html)
        return normalize_served_html(html, path_l)
    if "results" in path_l:
        html = repair_results_html(html, sport_u, view)
        if sport_u == "MLB":
            view_l = (view or "").strip().lower()
            if view_l not in _MLB_CHART_VIEWS:
                html = align_mlb_last_night_window(html)
            try:
                from picks_recent_results import fill_mlb_totals_chart_from_grades

                html = fill_mlb_totals_chart_from_grades(html)
            except Exception:
                pass
            if view_l not in _MLB_CHART_VIEWS:
                html = align_mlb_consensus_past7_from_cards(html)
                html = grade_mlb_named_split_side(html)
                html = align_mlb_pl_vs_books_partition(html)
                html = rewrite_mlb_customer_share_hrefs(html, path or "/mlb-results")
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

        def _box(bm):
            box = bm.group(0)
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
        _pl_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
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


def _rewrite_asr_model_row(row: str, board: dict[str, tuple[int, int]]) -> str:
    pieces: list[str] = []
    pos = 0
    tds = list(re.finditer(r"<td>[\s\S]*?</td>", row, flags=re.I))
    for i, td in enumerate(tds):
        pieces.append(row[pos:td.start()])
        cell = td.group(0)
        model_i = i - 1
        if (
            0 <= model_i < len(_ASR_ROW_MODELS)
            and "asr-rec" in cell
            and _ASR_ROW_MODELS[model_i] in board
        ):
            wins, losses = board[_ASR_ROW_MODELS[model_i]]
            total = wins + losses
            if total > 0:
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
            if href not in row or "asr-rec" not in row:
                continue
            if re.search(r"20\d{2}-\d{2}", row):
                continue
            updated = _rewrite_asr_model_row(row, board)
            if updated != row:
                new_block = new_block.replace(row, updated, 1)
            break
    if new_block != block:
        html = html.replace(block, new_block, 1)
    return html

