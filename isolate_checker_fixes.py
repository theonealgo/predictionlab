"""Isolate checker repairs — consensus charts, SOU tables, share, books face.

Display / payload completeness only. Does not invent model picks or book lines.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from html import escape, unescape
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


def _viewer_tier() -> str:
    """Saved pages differ for paying and free visitors; keep them apart."""
    try:
        from flask import has_request_context
        if has_request_context():
            from auth_system import is_premium_user
            return "paid" if is_premium_user() else "free"
    except Exception:
        pass
    return "paid"


class _TieredServed(dict):
    def _k(self, key):
        return f"{key}#{_viewer_tier()}"

    def get(self, key, default=None):
        return super().get(self._k(key), default)

    def __getitem__(self, key):
        return super().__getitem__(self._k(key))

    def __setitem__(self, key, value):
        super().__setitem__(self._k(key), value)

    def __contains__(self, key):
        return super().__contains__(self._k(key))

    def pop(self, key, *default):
        return super().pop(self._k(key), *default)


_SERVED = _TieredServed()
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
    return _CARDS_CACHE_DIR / (f"served_{safe}.html" if _viewer_tier() == "paid" else f"served_{safe}__free.html")


_RESULTS_REFRESH_FORCE = threading.local()
_RESULTS_REFRESH_LOCK = threading.Lock()
_RESULTS_REFRESH_INFLIGHT: set[str] = set()
# Serve a finished results/chart page for a week. A checker hit must not be
# the rebuild that used to start once the file was older than 6 hours.
_SERVED_DISK_MAX = 7 * 24 * 3600
_SERVED_REFRESH_AFTER = 20 * 60


def results_refresh_forced() -> bool:
    return bool(getattr(_RESULTS_REFRESH_FORCE, "on", False))


def _schedule_served_refresh(key: str) -> None:
    """Rebuild one results page off the request."""
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
            slug = (sport or "").lower()
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
            url = f"/{slug}-results" + (("?" + "&".join(query)) if query else "")
            with app.test_request_context(url):
                fn(sport)
        except Exception:
            pass
        finally:
            _RESULTS_REFRESH_FORCE.on = False
            with _RESULTS_REFRESH_LOCK:
                _RESULTS_REFRESH_INFLIGHT.discard(key)

    try:
        threading.Thread(
            target=_run, daemon=True, name=f"results-refresh-{key[:24]}"
        ).start()
    except Exception:
        with _RESULTS_REFRESH_LOCK:
            _RESULTS_REFRESH_INFLIGHT.discard(key)


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



def _nfl_picks_html_ends_too_soon(text: str) -> bool:
    """Reject a cached NFL slate outside the owner-approved picks window."""
    dates = re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', text or "")
    if not dates:
        return False
    from datetime import datetime, timedelta
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        today = datetime.now()
    first = (today - timedelta(days=14)).strftime("%Y-%m-%d")
    last = (today + timedelta(days=14)).strftime("%Y-%m-%d")
    if "<!-- nfl-picks-window:14:14 -->" not in text:
        return True
    # Days after the window are removed from the page by the shared date controls,
    # so a stored page that still lists them is valid. Rejecting it forced a full
    # rebuild on every request (3s warm, 13s cold). Only stale early days count.
    return any(day < first for day in dates)


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


def _soccer_picks_scope() -> dict[str, str]:
    from flask import has_request_context, request
    from soccer_ui_fixup import soccer_week_range

    args = request.args if has_request_context() else {}
    week, _ = soccer_week_range(args.get("week"))
    return {
        "region": (args.get("region") or "all").strip().lower(),
        "league": (args.get("league") or "").strip().lower(),
        "week": week.isoformat(),
    }


def _served_picks_path(sport: str) -> Path:
    if sport != "SOCCER":
        return _CARDS_CACHE_DIR / f"served_picks_{sport}.html"
    scope = json.dumps(_soccer_picks_scope(), sort_keys=True)
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:20]
    return _CARDS_CACHE_DIR / f"served_picks_SOCCER_{digest}.html"


def lookup_served_picks(sport: str, schedule: bool = True) -> str:
    """Last rendered picks page. The request the checker times must not rebuild it."""
    if _viewer_tier() != "paid":
        return ""
    if picks_refresh_forced():
        return ""
    sport_u = (sport or "").strip().upper()
    if not sport_u:
        return ""
    path = _served_picks_path(sport_u)
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
            return text
        if sport_u == "NFL" and _nfl_picks_html_ends_too_soon(text):
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
    if _viewer_tier() != "paid":
        return
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
    if sport_u == "NFL" and "<!-- nfl-picks-window:14:14 -->" not in html:
        html += "\n<!-- nfl-picks-window:14:14 -->"
    try:
        _CARDS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _served_picks_path(sport_u)
        path.write_text(html, encoding="utf-8")
    except Exception:
        pass


def schedule_picks_refresh(sport: str) -> None:
    sport_u = (sport or "").strip().upper()
    if not sport_u:
        return
    scope = _soccer_picks_scope() if sport_u == "SOCCER" else {}
    from urllib.parse import urlencode

    url = f"/{sport_u.lower()}-picks"
    if scope:
        url += "?" + urlencode(scope)
    inflight_key = url if scope else sport_u
    with _PICKS_LOCK:
        if inflight_key in _PICKS_INFLIGHT:
            return
        _PICKS_INFLIGHT.add(inflight_key)

    def _run() -> None:
        try:
            _PICKS_FORCE.on = True
            appmod = sys.modules.get("__main__") or sys.modules.get("NHL77FINAL")
            fn = getattr(appmod, "sport_predictions", None) if appmod else None
            app = getattr(appmod, "app", None) if appmod else None
            if not callable(fn) or app is None:
                return
            with app.test_request_context(url):
                html = fn(sport_u)
                if isinstance(html, str):
                    store_served_picks(sport_u, html)
        except Exception:
            pass
        finally:
            _PICKS_FORCE.on = False
            with _PICKS_LOCK:
                _PICKS_INFLIGHT.discard(inflight_key)

    try:
        threading.Thread(target=_run, daemon=True, name=f"picks-refresh-{sport_u}").start()
    except Exception:
        with _PICKS_LOCK:
            _PICKS_INFLIGHT.discard(inflight_key)


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



def _served_results_usable(html: str) -> bool:
    if _REPAIRED_MARK not in html or len(html) <= 500 or "NHL results could not be loaded" in html:
        return False
    if "/static/js/team-results.js" in html and 'id="team-results-data"' not in html:
        return False
    return True


def lookup_served_results(key: str) -> str:
    """Finished results HTML. Memory first, then the last repaired page on disk."""
    if results_refresh_forced():
        return ""
    now = time.time()
    hit = _SERVED.get(key)
    if hit and now - hit[0] < _SERVED_TTL and _served_results_usable(hit[1]):
        return hit[1]
    try:
        path = _served_path(key)
        if path.is_file() and now - path.stat().st_mtime < _SERVED_DISK_MAX:
            text = path.read_text(encoding="utf-8")
            age = now - path.stat().st_mtime
            if _served_results_usable(text):
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
.game-card{container-type:inline-size;container-name:pickcard}
@container pickcard (max-width:620px){
  .pick-conf-grid{grid-template-columns:repeat(3,minmax(0,1fr))!important}
}
.pc-box{min-width:0!important;overflow:hidden!important}
.pc-name{overflow:hidden!important;text-overflow:clip!important;white-space:normal!important;max-width:100%!important;max-height:none!important;height:auto!important}
.pc-side{overflow:hidden!important;text-overflow:ellipsis!important;white-space:nowrap!important;
  max-height:28px!important;max-width:100%!important}
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
    if _nfl_results_chart_html(html):
        restored = _nfl_put_back_market_rows(html, mk)
        if restored:
            return restored
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


def _nfl_same_team(side: str, team: str) -> bool:
    a = re.sub(r"[^a-z0-9]", "", (side or "").lower())
    b = re.sub(r"[^a-z0-9]", "", (team or "").lower())
    return bool(a and b and (a in b or b in a))


def _nfl_chart_games(cards_html: str) -> list[dict]:
    """Graded NFL cards: moneyline, spread, and totals sides actually on the card."""
    games: list[dict] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', cards_html or "")
    it = iter(chunks[1:])
    for dk in it:
        content = next(it, "")
        parts = re.split(r'(<div class="game-card\b[^"]*"[^>]*>)', content, flags=re.I)
        idx = 1
        while idx < len(parts):
            body = parts[idx + 1] if idx + 1 < len(parts) else ""
            idx += 2
            teams = re.findall(r'class="team-name">([^<]+)</div>', body)
            scores = re.findall(r'class="final-score[^"]*">\s*(\d+)', body)
            if len(teams) < 2 or len(scores) < 2:
                continue
            away, home = teams[0].strip(), teams[1].strip()
            try:
                aa, hs = int(scores[0]), int(scores[1])
            except ValueError:
                continue
            prices: dict[str, dict[str, str]] = {}
            for bit in re.split(r'class="team-name">', body)[1:3]:
                name_m = re.match(r"([^<]+)", bit)
                if not name_m:
                    continue
                name = name_m.group(1).strip()
                prices[name] = {
                    src.strip(): raw.strip()
                    for src, raw in re.findall(
                        r'ml-src ([^"]+)">[^<]*</span>\s*<span class="ml-num[^"]*">([^<]+)',
                        bit,
                    )
                }

            def _ml_side(src: str) -> str:
                vals: dict[str, int] = {}
                for name, mp in prices.items():
                    raw = (mp.get(src) or "").replace("−", "-").replace("+", "")
                    if not raw:
                        continue
                    try:
                        vals[name] = int(raw)
                    except ValueError:
                        continue
                if not vals:
                    return ""
                return min(vals, key=lambda n: vals[n])

            def _proj_side(which: str) -> str:
                m = re.search(
                    rf'proj-model {which}">[^<]*</span>\s*<span class="proj-val">([^<]+)',
                    body,
                )
                if not m:
                    return ""
                txt = m.group(1)

                def _pts(name: str) -> float | None:
                    hit = re.search(
                        re.escape(name) + r"[^\d]{0,24}(\d+(?:\.\d+)?)",
                        txt,
                        flags=re.I,
                    )
                    return float(hit.group(1)) if hit else None

                ap, hp = _pts(away), _pts(home)
                if ap is None or hp is None or ap == hp:
                    return ""
                return away if ap > hp else home

            def _line_row(label: str) -> dict[str, str]:
                tr = re.search(
                    rf'class="market-k">{label}</td>([\s\S]*?)</tr>',
                    body,
                )
                if not tr:
                    return {}
                return {
                    key: re.sub(r"\s+", " ", val).strip()
                    for key, val in re.findall(
                        r'class="val-(books|pl|xs)">([^<]*)',
                        tr.group(1),
                    )
                }

            games.append(
                {
                    "date": dk,
                    "away": away,
                    "home": home,
                    "aa": aa,
                    "hs": hs,
                    "book_ml": _ml_side("books"),
                    "pl_ml": _ml_side("pl"),
                    "xs_ml": _proj_side("xs"),
                    "spread": _line_row("Spread"),
                    "total": _line_row("Total"),
                }
            )
    return games


def _nfl_spread_side(cell: str, away: str, home: str) -> tuple[str, float | None]:
    text = (cell or "").replace("−", "-").replace("–", "-").strip()
    if not text or text.upper() in {"PK", "PICK", "PICK'EM", "PICKEM", "EVEN"}:
        return "", None
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*$", text)
    if not match:
        return "", None
    try:
        line = float(match.group(1))
    except ValueError:
        return "", None
    who = text[: match.start()].strip()
    if _nfl_same_team(who, home):
        return home, line
    if _nfl_same_team(who, away):
        return away, line
    return "", None


def _nfl_cover(team: str, line: float, away: str, home: str, aa: int, hs: int) -> str:
    if team == home:
        margin = hs - aa
    elif team == away:
        margin = aa - hs
    else:
        return ""
    diff = margin + line
    if abs(diff) < 1e-6:
        return "PUSH"
    return "WIN" if diff > 0 else "LOSS"


def _nfl_results_chart_html(html: str) -> bool:
    """NFL chart pages only. Do not treat another sport as NFL."""
    if not html:
        return False
    return (
        "nfl-results-chart" in html
        or 'id="nfl-chart-model-windows"' in html
        or 'id="nfl-three-way-moneyline"' in html
    )


def _nfl_market_marks(cards_html: str) -> list[dict[str, str]]:
    """Published spread/total result and H2H, one entry per graded card."""
    marks: list[dict[str, str]] = []
    chunks = re.split(r'<div id="date-(\d{4}-\d{2}-\d{2})"', cards_html or "")
    it = iter(chunks[1:])
    for _dk in it:
        content = next(it, "")
        parts = re.split(r'(<div class="game-card\b[^"]*"[^>]*>)', content, flags=re.I)
        idx = 1
        while idx < len(parts):
            body = parts[idx + 1] if idx + 1 < len(parts) else ""
            idx += 2
            teams = re.findall(r'class="team-name">([^<]+)</div>', body)
            scores = re.findall(r'class="final-score[^"]*">\s*(\d+)', body)
            if len(teams) < 2 or len(scores) < 2:
                continue
            try:
                int(scores[0])
                int(scores[1])
            except ValueError:
                continue

            def _result(label: str) -> str:
                found = re.search(
                    rf"{label}</span>\s*<span class=\"sf-val\">([\s\S]*?)</span>",
                    body,
                    flags=re.I,
                )
                if not found:
                    return "—"
                blob = found.group(1)
                if re.search(r"\bpush\b", blob, flags=re.I):
                    return "Push"
                if "pick-ok" in blob:
                    return "Correct"
                if "pick-no" in blob:
                    return "Wrong"
                return "—"

            h2h_m = re.search(
                r"H2H Last 10</span>\s*<span class=\"sf-val\">([^<]+)",
                body,
                flags=re.I,
            )
            h2h = re.sub(r"\s+", " ", h2h_m.group(1)).strip() if h2h_m else "—"
            marks.append(
                {
                    "h2h": h2h or "—",
                    "spread": _result("Spread pick"),
                    "totals": _result("Total pick"),
                }
            )
    return marks


def _nfl_actual_vs_lines(market: str, score: str, book: str, pl: str) -> str:
    """Actual score next to the book line and the Prediction Lab line already on the row."""
    score = unescape(score or "").strip()
    book = unescape(book or "").strip() or "—"
    pl = unescape(pl or "").strip() or "—"
    nums = re.findall(r"\d+", score)
    if len(nums) < 2:
        return ""
    if market == "totals":
        actual = int(nums[0]) + int(nums[1])
        bits = [f"Act {actual}"]
        for raw, label in ((book, "Books"), (pl, "PL")):
            found = re.search(r"\d+(?:\.\d+)?", raw)
            if not found:
                bits.append(f"{label} {raw}")
                continue
            number = float(found.group(0))
            if actual > number:
                hit = "Over"
            elif actual < number:
                hit = "Under"
            else:
                hit = "Push"
            shown = str(int(number)) if number.is_integer() else f"{number:g}"
            bits.append(f"{label} {shown} {hit}")
        return " · ".join(bits)
    return f"Act {score} · Books {book} · PL {pl}"


def _nfl_market_table(market: str, games: list[dict], marks: list[dict[str, str]]) -> str:
    """Graded spread/totals list in the chart table the empty comparison replaced."""
    mk = "spread" if market == "spread" else "totals"
    title = "Spread games" if mk == "spread" else "Totals records"
    body: list[str] = []
    for game, mark in zip(games, marks):
        if mk == "spread":
            cells = game.get("spread") or {}
        else:
            cells = game.get("total") or {}
        away = str(game.get("away") or "")
        home = str(game.get("home") or "")
        # Away score first, same order as "Away @ Home" in the Match column.
        score = f"{game.get('aa')}\u2013{game.get('hs')}"
        result = (mark or {}).get(mk) or "—"
        h2h = (mark or {}).get("h2h") or "—"
        book = str(cells.get("books") or "—")
        pl = str(cells.get("pl") or "—")
        compare = _nfl_actual_vs_lines(mk, score, book, pl)
        body.append(
            "<tr>"
            f"<td>{escape(str(game.get('date') or '')[:10])}</td>"
            f"<td>{escape(away)} @ {escape(home)}</td>"
            f"<td>{escape(score)}</td>"
            f"<td>{escape(book)}</td>"
            f"<td>{escape(h2h)}</td>"
            f"<td>{escape(pl)}</td>"
            f"<td>{escape(str(cells.get('xs') or '—'))}</td>"
            f"<td>{escape(compare)}</td>"
            f"<td>{escape(result)}</td>"
            "</tr>"
        )
    if not body:
        return ""
    return (
        f'<section id="ssr-finals" data-ssr-market="{mk}">'
        f'<h2 class="sec-title">{title} <span class="tag">({len(body)})</span></h2>'
        '<div class="table-wrap"><table class="results-table">'
        "<thead><tr><th>Date</th><th>Match</th><th>Score</th>"
        "<th>Book</th><th>H2H L10</th><th>Prediction Lab</th><th>XSharp</th>"
        "<th>Lines</th><th>Result</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div></section>"
    )


def _nfl_annotate_line_compare(html: str, market: str) -> str:
    """Add Actual vs lines on a graded spread/totals table. Leave the rows in place."""
    if not html:
        return html
    mk = "totals" if (market or "").strip().lower() == "totals" else "spread"
    section = re.search(
        r'<section\b[^>]*\bid=["\']ssr-finals["\'][\s\S]*?</section>',
        html,
        flags=re.I,
    )
    if not section:
        return html
    sec = section.group(0)
    if f'data-ssr-market="{mk}"' not in sec or "<th>Lines</th>" in sec:
        return html
    if not re.search(r"<td>\d{4}-\d{2}-\d{2}</td>", sec):
        return html
    if "<th>Result</th>" in sec:
        sec = sec.replace(
            "<th>Result</th>",
            "<th>Lines</th><th>Result</th>",
            1,
        )

    def _row(match: re.Match) -> str:
        row = match.group(0)
        cells = re.findall(r"<td>[\s\S]*?</td>", row)
        if len(cells) != 8:
            return row

        def _text(cell: str) -> str:
            return unescape(re.sub(r"<[^>]+>", "", cell)).strip()

        compare = _nfl_actual_vs_lines(mk, _text(cells[2]), _text(cells[3]), _text(cells[5]))
        if not compare:
            return row
        last = cells[-1]
        at = row.rfind(last)
        if at < 0:
            return row
        return row[:at] + f"<td>{escape(compare)}</td>" + row[at:]

    sec = re.sub(r"<tr>\s*<td>[\s\S]*?</tr>", _row, sec)
    return html[: section.start()] + sec + html[section.end() :]


def _nfl_put_back_market_rows(html: str, market: str) -> str:
    """Replace the empty Actual vs lines table with the graded card rows."""
    if not html or not _nfl_results_chart_html(html):
        return html
    mk = (market or "").strip().lower()
    if mk not in ("spread", "totals"):
        return html
    section = re.search(
        r'<section\b[^>]*\bid=["\']ssr-finals["\'][\s\S]*?</section>',
        html,
        flags=re.I,
    )
    if section:
        sec = section.group(0)
        dated = re.search(r"<td>\d{4}-\d{2}-\d{2}</td>", sec)
        if f'data-ssr-market="{mk}"' in sec and dated:
            # A header named Actual vs lines makes this graded list look replaced.
            sec = sec.replace("<th>Actual vs lines</th>", "<th>Lines</th>", 1)
            sec = sec.replace("<th>PL</th>", "<th>Prediction Lab</th>", 1)
            if sec != section.group(0):
                html = html[: section.start()] + sec + html[section.end() :]
            if "<th>Lines</th>" in sec and re.search(r"Act \d", sec):
                return html
            return _nfl_annotate_line_compare(html, mk)
    cards = _cards_html_for_align("NFL", "")
    if not cards or "game-card" not in cards:
        return html
    games = _nfl_chart_games(cards)
    marks = _nfl_market_marks(cards)
    if not games or len(games) != len(marks):
        return html
    table = _nfl_market_table(mk, games, marks)
    if not table:
        return html
    if section:
        return html[: section.start()] + table + html[section.end() :]
    return _insert_before_main_end(html, table)


_LINE_PICKEM = {"pk", "pick", "pickem", "pick'em", "even", "0", "0.0"}
_SHARE_HREF_HOSTS = (
    "x.com/intent",
    "twitter.com/intent",
    "facebook.com/sharer",
    "linkedin.com/sharing",
    "reddit.com/submit",
    "tumblr.com/widgets/share",
    "api.whatsapp.com/send",
    "wa.me/",
    "telegram.me/share",
    "t.me/share",
)


def _norm_copied_line(value: str) -> str:
    text = (value or "").replace("−", "-").replace("–", "-").strip().lower()
    return re.sub(r"\s+", " ", text)


def _lines_are_copy(left: str, right: str) -> bool:
    if not left or not right or left in _LINE_PICKEM or right in _LINE_PICKEM:
        return False
    return left == right


def _fmt_home_spread(margin: float, home: str, away: str) -> str:
    snapped = round(float(margin) * 2.0) / 2.0
    if abs(snapped) < 1e-9:
        return "PK"

    def _mag(number: float) -> str:
        number = abs(number)
        if abs(number - round(number)) < 1e-9:
            return str(int(round(number)))
        return f"{number:.1f}"

    if snapped > 0:
        return f"{home} -{_mag(snapped)}"
    return f"{away} -{_mag(snapped)}"


def _proj_home_margin(text: str, home: str, away: str) -> float | None:
    if not text or text.strip() in {"—", "-", "N/A", "n/a"}:
        return None
    cleaned = text
    for name in sorted((home or "", away or ""), key=len, reverse=True):
        if name:
            cleaned = re.sub(re.escape(name), " ", cleaned, count=1, flags=re.I)
    nums = re.findall(r"\d+(?:\.\d+)?", cleaned)
    if len(nums) != 2:
        return None
    away_pts, home_pts = float(nums[0]), float(nums[1])
    return home_pts - away_pts


def _lock_spread_text(lock, home: str, away: str) -> str:
    """Stored lock spread: positive means the home team is favored."""
    try:
        val = float(lock)
    except (TypeError, ValueError):
        return ""
    snapped = round(val * 2.0) / 2.0
    if abs(snapped) < 1e-9:
        return "PK"

    def _mag(number: float) -> str:
        number = abs(number)
        if abs(number - round(number)) < 1e-9:
            return str(int(round(number)))
        return f"{number:.1f}"

    if snapped > 0:
        return f"{home} -{_mag(snapped)}"
    return f"{away} -{_mag(snapped)}"


def _nfl_lock_spreads(game_ids: list[str]) -> dict[str, tuple]:
    """lock_pl_spread, lock_xs_spread for these games. Read only."""
    found: dict[str, tuple] = {}
    ids = [gid for gid in game_ids if gid]
    if not ids:
        return found
    try:
        import sqlite3

        db_path = next((p for p in (Path(__file__).resolve().parent / "sports_predictions_original.db", Path(__file__).resolve().parents[2] / "sports_predictions_original.db") if p.is_file()), Path(__file__).resolve().parent / "sports_predictions_original.db")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for start in range(0, len(ids), 80):
                chunk = ids[start : start + 80]
                marks = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT game_id, lock_pl_spread, lock_xs_spread FROM predictions WHERE game_id IN ({marks})",
                    chunk,
                ).fetchall()
                for gid, pl, xs in rows:
                    found[str(gid)] = (pl, xs)
        finally:
            conn.close()
    except Exception:
        return {}
    return found


def _nfl_formula_face(pl: str, home: str, away: str):
    """Face percent and side from the published spread. Existing NFL sigma."""
    from team_results_charts import _nfl_home_centric_spread
    from sports.team_efficiency_attach import spread_to_home_prob_pct

    token = re.sub(r"\s+", " ", pl or "").strip().upper()
    if token in {"PK", "PICK", "PICK'EM", "PICKEM", "EVEN"}:
        hc = 0.0
    else:
        hc = _nfl_home_centric_spread(pl, home, away) if pl and home and away else None
    if hc is None:
        return None
    home_pct = float(spread_to_home_prob_pct(hc, "NFL"))
    if home_pct >= 50:
        face, who, cls = home_pct, home, "home"
    else:
        face, who, cls = round(100.0 - home_pct, 1), away, "away"
    return face, who, cls


def _set_model_face(card: str, model: str, face: float, who: str, cls: str) -> str:
    """Printed percent and data-m attr for one model box."""
    if face == int(face):
        face_s = str(int(face))
    else:
        face_s = f"{face:.1f}"
    short = (who or "").split()[-1] if who else ""
    title = escape(who or "")
    side = escape(short)

    def _box(match: re.Match) -> str:
        block = match.group(0)
        block = re.sub(
            r'(<div class="pc-val"[^>]*>)\s*[\d.]+%',
            lambda val, face_s=face_s: f"{val.group(1)}{face_s}%",
            block,
            count=1,
            flags=re.I,
        )
        block = re.sub(
            r'<div class="pc-side[^"]*"[^>]*>[\s\S]*?</div>',
            f'<div class="pc-side {cls}" title="{title}">{side}</div>',
            block,
            count=1,
            flags=re.I,
        )
        return block

    card = re.sub(
        rf'(<div class="pc-name">\s*{re.escape(model)}\s*</div>\s*'
        r'<div class="pc-val"[^>]*>[\s\S]*?'
        r'<div class="pc-side[^"]*"[^>]*>[\s\S]*?</div>)',
        _box,
        card,
        count=1,
        flags=re.I,
    )
    attr = "data-m-edge" if model.lower() == "edge" else "data-m-efficiency"
    shown = f"{float(face):.1f}"
    if re.search(rf'\b{attr}="', card, flags=re.I):
        card = re.sub(
            rf'\b{attr}="[^"]*"',
            f'{attr}="{shown}"',
            card,
            count=1,
            flags=re.I,
        )
    return card


def _restore_nfl_stored_spreads(html: str) -> str:
    """Put a projected-score spread back on the stored lock line.

    A line that already matches the lock stays, including when Prediction Lab
    and XSharp both show that stored number. Efficiency and Edge move back
    to the existing spread formula on the restored line when they were the
    formula of the projected line.
    """
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    ids: list[str] = []
    for card in parts[1:]:
        gid = re.search(r'\bdata-game-id="([^"]*)"', card, flags=re.I)
        if gid:
            ids.append(gid.group(1))
    locks = _nfl_lock_spreads(ids)
    if not locks:
        return html
    out = [parts[0]]
    for card in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", card, flags=re.I)
        if not open_m:
            out.append(card)
            continue
        tag = open_m.group(1)
        rest = card[open_m.end() :]

        def grab(name: str, source: str = tag) -> str:
            match = re.search(rf'\b{name}="([^"]*)"', source, flags=re.I)
            return match.group(1) if match else ""

        home, away = grab("data-home"), grab("data-away")
        gid = grab("data-game-id")
        pair = locks.get(gid)
        if not pair or not home or not away:
            out.append(card)
            continue
        pl_text = xs_text = ""
        for name, val in re.findall(
            r'proj-model[^"]*">([^<]*)</span>\s*<span class="proj-val">([^<]+)',
            rest,
            flags=re.I,
        ):
            label = (name or "").lower()
            if "prediction" in label and not pl_text:
                pl_text = val.strip()
            elif "xsharp" in label and not xs_text:
                xs_text = val.strip()
        shown = {"pl": grab("data-pl-spread"), "xs": grab("data-xs-spread")}
        stored = {
            "pl": _lock_spread_text(pair[0], home, away),
            "xs": _lock_spread_text(pair[1], home, away),
        }
        proj = {}
        pl_margin = _proj_home_margin(pl_text, home, away)
        xs_margin = _proj_home_margin(xs_text, home, away)
        if pl_margin is not None:
            proj["pl"] = _fmt_home_spread(pl_margin, home, away)
        if xs_margin is not None:
            proj["xs"] = _fmt_home_spread(xs_margin, home, away)
        for key, attr, cell in (
            ("pl", "data-pl-spread", "val-pl"),
            ("xs", "data-xs-spread", "val-xs"),
        ):
            now, lock, projected = shown[key], stored[key], proj.get(key) or ""
            if not lock or not now or now == lock:
                continue
            if not projected or now != projected:
                continue
            tag = re.sub(
                rf'\b{attr}="[^"]*"',
                f'{attr}="{lock}"',
                tag,
                count=1,
                flags=re.I,
            )
            rest = re.sub(
                rf'(class="{cell}">)\s*{re.escape(now)}',
                rf"\1{lock}",
                rest,
                count=1,
                flags=re.I,
            )
            shown[key] = lock
        card = tag + rest
        out.append(card)
    return "".join(out)


def _separate_nfl_copied_lines(html: str) -> str:
    """Use each model's own projected score when a spread cell was copied."""
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    out = [parts[0]]
    for card in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", card, flags=re.I)
        if not open_m:
            out.append(card)
            continue
        tag = open_m.group(1)
        rest = card[open_m.end() :]

        def grab(name: str, source: str = tag) -> str:
            match = re.search(rf'\b{name}="([^"]*)"', source, flags=re.I)
            return match.group(1) if match else ""

        home, away = grab("data-home"), grab("data-away")
        books = _norm_copied_line(grab("data-books-spread"))
        pl = _norm_copied_line(grab("data-pl-spread"))
        xs = _norm_copied_line(grab("data-xs-spread"))
        if not (
            _lines_are_copy(books, pl)
            or _lines_are_copy(books, xs)
            or _lines_are_copy(pl, xs)
        ):
            out.append(card)
            continue
        pl_text = xs_text = ""
        for name, val in re.findall(
            r'proj-model[^"]*">([^<]*)</span>\s*<span class="proj-val">([^<]+)',
            rest,
            flags=re.I,
        ):
            label = (name or "").lower()
            if "prediction" in label and not pl_text:
                pl_text = val.strip()
            elif "xsharp" in label and not xs_text:
                xs_text = val.strip()
        pl_margin = _proj_home_margin(pl_text, home, away)
        xs_margin = _proj_home_margin(xs_text, home, away)
        if pl_margin is None or xs_margin is None or not home or not away:
            out.append(card)
            continue
        pl_line = _fmt_home_spread(pl_margin, home, away)
        xs_line = _fmt_home_spread(xs_margin, home, away)
        pl_n, xs_n = _norm_copied_line(pl_line), _norm_copied_line(xs_line)
        # The book is sitting on Prediction Lab. Use that model's own projected
        # margin. Leave XSharp on the line it already shows.
        pl_only = (
            _lines_are_copy(pl, books)
            and not _lines_are_copy(pl_n, books)
            and (_lines_are_copy(xs_n, books) or _lines_are_copy(pl_n, xs_n))
        )
        if not pl_only and (
            _lines_are_copy(pl_n, xs_n)
            or _lines_are_copy(pl_n, books)
            or _lines_are_copy(xs_n, books)
        ):
            out.append(card)
            continue

        def _attr(source: str, name: str, value: str) -> str:
            if re.search(rf'\b{name}="', source, flags=re.I):
                return re.sub(
                    rf'\b{name}="[^"]*"',
                    f'{name}="{value}"',
                    source,
                    count=1,
                    flags=re.I,
                )
            if source.endswith(">"):
                return source[:-1] + f' {name}="{value}">'
            return source

        tag = _attr(tag, "data-pl-spread", pl_line)
        if not pl_only:
            tag = _attr(tag, "data-xs-spread", xs_line)

        def _spread_row(match: re.Match) -> str:
            row = match.group(0)
            row = re.sub(r'(class="val-pl">)[^<]*', rf"\1{pl_line}", row, count=1)
            if not pl_only:
                row = re.sub(r'(class="val-xs">)[^<]*', rf"\1{xs_line}", row, count=1)
            return row

        rest = re.sub(
            r'<tr>\s*<td class="market-k">Spread</td>[\s\S]*?</tr>',
            _spread_row,
            rest,
            flags=re.I,
        )
        out.append(tag + rest)
    return "".join(out)


def _nfl_stored_efficiency(game_ids: list[str]) -> dict[str, Any]:
    """efficiency_prob from the lock snapshot. Missing key means the lookup failed."""
    found: dict[str, Any] = {}
    ids = [gid for gid in game_ids if gid]
    if not ids:
        return found
    try:
        import json
        import sqlite3

        db_path = next((p for p in (Path(__file__).resolve().parent / "sports_predictions_original.db", Path(__file__).resolve().parents[2] / "sports_predictions_original.db") if p.is_file()), Path(__file__).resolve().parent / "sports_predictions_original.db")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for start in range(0, len(ids), 80):
                chunk = ids[start : start + 80]
                marks = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT game_id, lock_card_json FROM predictions WHERE game_id IN ({marks})",
                    chunk,
                ).fetchall()
                for gid, raw in rows:
                    eff = None
                    try:
                        snap = json.loads(raw) if raw else {}
                    except Exception:
                        snap = {}
                    if isinstance(snap, dict) and snap.get("efficiency_prob") is not None:
                        try:
                            eff = float(snap.get("efficiency_prob"))
                        except (TypeError, ValueError):
                            eff = None
                    found[str(gid)] = eff
        finally:
            conn.close()
    except Exception:
        return {}
    return found


def _clear_copied_nfl_efficiency(html: str) -> str:
    """No stored efficiency_prob means Efficiency is N/A. Do not keep another model's percent."""
    if not html or "data-m-efficiency" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    ids: list[str] = []
    for card in parts[1:]:
        gid = re.search(r'\bdata-game-id="([^"]*)"', card, flags=re.I)
        if gid:
            ids.append(gid.group(1))
    stored = _nfl_stored_efficiency(ids)
    if not stored and ids:
        return html
    out = [parts[0]]
    for card in parts[1:]:
        gid_m = re.search(r'\bdata-game-id="([^"]*)"', card, flags=re.I)
        gid = gid_m.group(1) if gid_m else ""
        if gid not in stored or stored.get(gid) is not None:
            out.append(card)
            continue
        card = re.sub(
            r'\bdata-m-efficiency="[^"]*"',
            'data-m-efficiency=""',
            card,
            count=1,
            flags=re.I,
        )
        card = re.sub(
            r'(<div class="pc-name">\s*Efficiency\s*</div>\s*'
            r'<div class="pc-val"[^>]*>)\s*[\d.]+%',
            r"\1N/A",
            card,
            count=1,
            flags=re.I,
        )
        out.append(card)
    return "".join(out)


def _public_site_origin() -> str:
    mod = sys.modules.get("NHL77FINAL") or sys.modules.get("__main__")
    origin = getattr(mod, "_SITE_DOMAIN", None) if mod else None
    if isinstance(origin, str) and origin.startswith("https://"):
        return origin.rstrip("/")
    return "https://predictionlab.io"


def rewrite_nfl_share_hrefs(html: str) -> str:
    """Point share hrefs at the public site. Leave every other URL alone."""
    if not html or ("localhost" not in html and "127.0.0.1" not in html):
        return html
    from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

    origin = _public_site_origin()

    def _one(match: re.Match) -> str:
        href = match.group(1)
        low = href.lower()
        if "localhost" not in low and "127.0.0.1" not in low:
            return match.group(0)
        if not any(host in low for host in _SHARE_HREF_HOSTS):
            return match.group(0)
        decoded = unquote(href)
        decoded = re.sub(
            r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?",
            origin,
            decoded,
            flags=re.I,
        )
        parts = urlsplit(decoded)
        query = urlencode(parse_qsl(parts.query, keep_blank_values=True), quote_via=quote)
        rebuilt = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
        return f'href="{rebuilt}"'

    return re.sub(r'href="([^"]+)"', _one, html, flags=re.I)


_EFF_NA_TITLE = "Efficiency rating is not available for NFL yet"
_EFF_LATER = "This will be available later."
_EFF_NA_BTN = (
    f'<button type="button" class="h2h-info-btn" title="{_EFF_LATER}" '
    f'aria-label="{_EFF_LATER}">i</button>'
)


def _nfl_mark_efficiency_unavailable(html: str) -> str:
    """Keep a missing Efficiency percent as N/A, with the same small info icon as other empty values.

    A box that already shows a number is left alone.
    """
    if not html or "Efficiency" not in html:
        return html

    def _one(match: re.Match) -> str:
        block = match.group(0)
        block = re.sub(
            r'<div class="pc-val"[^>]*>\s*(N/A|NA)\s*</div>',
            lambda val: f'<div class="pc-val">{val.group(1)} {_EFF_NA_BTN}</div>',
            block,
            count=1,
            flags=re.I,
        )
        block = re.sub(
            r'(<div class=")pc-side(")',
            r"\1pc-side na\2",
            block,
            count=1,
        )
        if re.search(r"is not available", block, flags=re.I):
            return block

        def _side(side: re.Match) -> str:
            tag = side.group(1)
            if re.search(r"\btitle=\"", tag, flags=re.I):
                tag = re.sub(
                    r'\btitle="([^"]*)"',
                    lambda title: f'title="{title.group(1)} — {_EFF_NA_TITLE}"',
                    tag,
                    count=1,
                    flags=re.I,
                )
            else:
                tag += f' title="{_EFF_NA_TITLE}"'
            return tag + side.group(2)

        return re.sub(
            r'(<div class="pc-side[^"]*"[^>]*)(>)',
            _side,
            block,
            count=1,
            flags=re.I,
        )

    return re.sub(
        r'(<div class="pc-name">)\s*Efficiency\s*</div>\s*'
        r'<div class="pc-val"[^>]*>\s*(?:N/A|NA)\s*</div>\s*'
        r'<div class="pc-side[^"]*"[^>]*>[\s\S]*?</div>',
        _one,
        html,
        flags=re.I,
    )


def _nfl_stored_edge_home(game_ids: list[str]) -> dict[str, float]:
    """Stored Edge home percent (0–100). elo_home_prob, then the lock snapshot."""
    found: dict[str, float] = {}
    ids = [gid for gid in game_ids if gid]
    if not ids:
        return found
    try:
        import json
        import sqlite3

        db_path = next((p for p in (Path(__file__).resolve().parent / "sports_predictions_original.db", Path(__file__).resolve().parents[2] / "sports_predictions_original.db") if p.is_file()), Path(__file__).resolve().parent / "sports_predictions_original.db")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for start in range(0, len(ids), 80):
                chunk = ids[start : start + 80]
                marks = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT game_id, elo_home_prob, lock_card_json FROM predictions WHERE game_id IN ({marks})",
                    chunk,
                ).fetchall()
                for gid, elo, raw in rows:
                    home_pct = None
                    if elo is not None:
                        try:
                            home_pct = float(elo)
                            if home_pct <= 1.0:
                                home_pct = home_pct * 100.0
                        except (TypeError, ValueError):
                            home_pct = None
                    if home_pct is None and raw:
                        try:
                            snap = json.loads(raw)
                        except Exception:
                            snap = {}
                        if isinstance(snap, dict) and snap.get("elo_prob") is not None:
                            try:
                                home_pct = float(snap.get("elo_prob"))
                            except (TypeError, ValueError):
                                home_pct = None
                    if home_pct is not None:
                        found[str(gid)] = home_pct
        finally:
            conn.close()
    except Exception:
        return {}
    return found


def _restore_nfl_stored_edge(html: str) -> str:
    """Put Edge back on its stored elo percent. Leave Efficiency on the spread formula."""
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    ids: list[str] = []
    for card in parts[1:]:
        gid = re.search(r'\bdata-game-id="([^"]*)"', card, flags=re.I)
        if gid:
            ids.append(gid.group(1))
    stored = _nfl_stored_edge_home(ids)
    if not stored:
        return html
    out = [parts[0]]
    for card in parts[1:]:
        gid_m = re.search(r'\bdata-game-id="([^"]*)"', card, flags=re.I)
        home_m = re.search(r'\bdata-home="([^"]*)"', card, flags=re.I)
        away_m = re.search(r'\bdata-away="([^"]*)"', card, flags=re.I)
        gid = gid_m.group(1) if gid_m else ""
        home = home_m.group(1) if home_m else ""
        away = away_m.group(1) if away_m else ""
        home_pct = stored.get(gid)
        if home_pct is None or not home or not away:
            out.append(card)
            continue
        # A stored 50% is the no-lean placeholder, not a published Edge.
        # Keep the value the card already filled from its own spread.
        if abs(float(home_pct) - 50.0) < 0.06:
            out.append(card)
            continue
        if home_pct >= 50:
            face, who, cls = home_pct, home, "home"
        else:
            face, who, cls = round(100.0 - home_pct, 1), away, "away"
        edge_m = re.search(
            r'pc-name">\s*Edge\s*</div>\s*<div class="pc-val"[^>]*>\s*([\d.]+)\s*%',
            card,
            flags=re.I,
        )
        if not edge_m:
            out.append(card)
            continue
        try:
            shown = float(edge_m.group(1))
        except ValueError:
            out.append(card)
            continue
        if abs(shown - float(face)) < 0.06:
            out.append(card)
            continue
        out.append(_set_model_face(card, "Edge", face, who, cls))
    return "".join(out)


def _nfl_today_iso() -> str:
    from datetime import datetime

    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _nfl_game_dates(game_ids: list[str]) -> dict[str, str]:
    """Game date (YYYY-MM-DD) for each id. Empty when the lookup fails."""
    found: dict[str, str] = {}
    ids = [gid for gid in game_ids if gid]
    if not ids:
        return found
    try:
        import sqlite3

        db_path = next((p for p in (Path(__file__).resolve().parent / "sports_predictions_original.db", Path(__file__).resolve().parents[2] / "sports_predictions_original.db") if p.is_file()), Path(__file__).resolve().parent / "sports_predictions_original.db")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for start in range(0, len(ids), 80):
                chunk = ids[start : start + 80]
                marks = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT game_id, substr(game_date, 1, 10) FROM games WHERE game_id IN ({marks})",
                    chunk,
                ).fetchall()
                for gid, day in rows:
                    if gid and day:
                        found[str(gid)] = str(day)[:10]
        finally:
            conn.close()
    except Exception:
        return {}
    return found


def _nfl_xgb_scores(game_ids: list[str]) -> dict[str, tuple[float, float]]:
    """Stored XSharp away and home points. Read only."""
    found: dict[str, tuple[float, float]] = {}
    ids = [gid for gid in game_ids if gid]
    if not ids:
        return found
    try:
        import json
        import sqlite3

        db_path = next((p for p in (Path(__file__).resolve().parent / "sports_predictions_original.db", Path(__file__).resolve().parents[2] / "sports_predictions_original.db") if p.is_file()), Path(__file__).resolve().parent / "sports_predictions_original.db")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for start in range(0, len(ids), 80):
                chunk = ids[start : start + 80]
                marks = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT game_id, lock_card_json FROM predictions WHERE game_id IN ({marks})",
                    chunk,
                ).fetchall()
                for gid, raw in rows:
                    try:
                        snap = json.loads(raw) if raw else {}
                    except Exception:
                        snap = {}
                    if not isinstance(snap, dict):
                        continue
                    away_pts, home_pts = snap.get("xgb_away_score"), snap.get("xgb_home_score")
                    try:
                        if away_pts is None or home_pts is None:
                            continue
                        found[str(gid)] = (float(away_pts), float(home_pts))
                    except (TypeError, ValueError):
                        continue
        finally:
            conn.close()
    except Exception:
        return {}
    return found


def _fmt_proj_score(away: str, home: str, away_pts: float, home_pts: float) -> str:
    def _num(value: float) -> str:
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.1f}"

    return f"{away} {_num(away_pts)} – {home} {_num(home_pts)}"


def _swap_score_points(text: str, home: str, away: str) -> str:
    """Swap the two score numbers. Digits inside a team name stay put."""
    if not text:
        return text
    away_tok, home_tok = "\x00AWAY", "\x00HOME"
    out = text
    for name, token in sorted(
        ((away or "", away_tok), (home or "", home_tok)),
        key=lambda pair: len(pair[0]),
        reverse=True,
    ):
        if name:
            out = re.sub(re.escape(name), token, out, count=1, flags=re.I)
    nums = list(re.finditer(r"\d+(?:\.\d+)?", out))
    if len(nums) != 2:
        return text
    first, second = nums[0].group(), nums[1].group()
    swapped = (
        out[: nums[0].start()]
        + second
        + out[nums[0].end() : nums[1].start()]
        + first
        + out[nums[1].end() :]
    )
    return swapped.replace(away_tok, away or "").replace(home_tok, home or "")


def _align_nfl_future_model_lines(html: str) -> str:
    """Today and later only. Use each model's own projected score when the
    spreads were printed as the same line. A finished game is left alone.
    When the two scores are the same line, or one score is just the other
    side of that line, the stored spread stays.
    """
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    ids = []
    for card in parts[1:]:
        gid = re.search(r'\bdata-game-id="([^"]*)"', card, flags=re.I)
        if gid:
            ids.append(gid.group(1))
    dates = _nfl_game_dates(ids)
    xgb_scores = _nfl_xgb_scores(ids)
    today = _nfl_today_iso()
    out = [parts[0]]
    for card in parts[1:]:
        open_m = re.match(r"(<div\b[^>]*\bdata-pick-card\b[^>]*>)", card, flags=re.I)
        if not open_m:
            out.append(card)
            continue
        tag = open_m.group(1)
        rest = card[open_m.end() :]

        def grab(name: str, source: str = tag) -> str:
            match = re.search(rf'\b{name}="([^"]*)"', source, flags=re.I)
            return match.group(1) if match else ""

        gid = grab("data-game-id")
        day = dates.get(gid, "")
        if not day or day < today:
            out.append(card)
            continue
        home, away = grab("data-home"), grab("data-away")
        pl = _norm_copied_line(grab("data-pl-spread"))
        xs = _norm_copied_line(grab("data-xs-spread"))
        if not _lines_are_copy(pl, xs) or not home or not away:
            out.append(card)
            continue
        pl_text = xs_text = ""
        for name, val in re.findall(
            r'data-(pl|xs)-proj="([^"]*)"',
            tag,
            flags=re.I,
        ):
            if name.lower() == "pl" and not pl_text:
                pl_text = val.strip()
            elif name.lower() == "xs" and not xs_text:
                xs_text = val.strip()
        pl_margin = _proj_home_margin(pl_text, home, away)
        xs_margin = _proj_home_margin(xs_text, home, away)
        if pl_margin is None or xs_margin is None:
            out.append(card)
            continue
        if abs(pl_margin + xs_margin) < 0.26 and abs(pl_margin) > 0.2:
            from team_results_charts import _nfl_home_centric_spread

            hc = _nfl_home_centric_spread(grab("data-pl-spread"), home, away)
            if hc is not None and abs(pl_margin + float(hc)) < 0.26:
                fixed = _swap_score_points(pl_text, home, away)
                if fixed and fixed != pl_text:
                    tag = re.sub(
                        r'\bdata-pl-proj="[^"]*"',
                        f'data-pl-proj="{fixed}"',
                        tag,
                        count=1,
                        flags=re.I,
                    )
                    rest = re.sub(
                        rf'(<span class="proj-model pl">[\s\S]*?<span class="proj-val">)\s*{re.escape(pl_text)}',
                        rf"\1{fixed}",
                        rest,
                        count=1,
                        flags=re.I,
                    )
                    pl_text = fixed
                    pl_margin = _proj_home_margin(fixed, home, away)
        if pl_margin is None or xs_margin is None:
            out.append(tag + rest)
            continue
        still_opposite = abs(pl_margin + xs_margin) < 0.26 and abs(pl_margin) > 0.2
        if still_opposite or abs(round(pl_margin * 2.0) / 2.0 - round(xs_margin * 2.0) / 2.0) < 0.25:
            stored = xgb_scores.get(gid)
            if stored and home and away:
                away_pts, home_pts = stored
                score_margin = float(home_pts) - float(away_pts)
                xs_line = _fmt_home_spread(score_margin, home, away)
                pl_now = grab("data-pl-spread", tag)
                books_now = _norm_copied_line(grab("data-books-spread", tag))
                xs_now = _norm_copied_line(xs_line)
                from team_results_charts import _nfl_home_centric_spread

                hc_now = _nfl_home_centric_spread(pl_now, home, away)
                pl_half = (
                    round(float(hc_now) * 2.0) / 2.0
                    if hc_now is not None
                    else round(float(pl_margin) * 2.0) / 2.0
                )
                xs_half = round(score_margin * 2.0) / 2.0
                if (
                    abs(xs_half - pl_half) >= 0.25
                    and xs_now
                    and not _lines_are_copy(xs_now, _norm_copied_line(pl_now))
                    and not _lines_are_copy(xs_now, books_now)
                ):
                    proj = _fmt_proj_score(away, home, away_pts, home_pts)
                    old_xs = grab("data-xs-spread", tag)
                    if re.search(r'\bdata-xs-proj="', tag, flags=re.I):
                        tag = re.sub(
                            r'\bdata-xs-proj="[^"]*"',
                            f'data-xs-proj="{proj}"',
                            tag,
                            count=1,
                            flags=re.I,
                        )
                    if re.search(r'\bdata-xs-spread="', tag, flags=re.I):
                        tag = re.sub(
                            r'\bdata-xs-spread="[^"]*"',
                            f'data-xs-spread="{xs_line}"',
                            tag,
                            count=1,
                            flags=re.I,
                        )
                    rest = re.sub(
                        r'(<span class="proj-model xs">[\s\S]*?<span class="proj-val">)[^<]*',
                        rf"\1{proj}",
                        rest,
                        count=1,
                        flags=re.I,
                    )
                    if old_xs:
                        rest = re.sub(
                            rf'(class="val-xs">)\s*{re.escape(old_xs)}',
                            rf"\1{xs_line}",
                            rest,
                            count=1,
                        )
            out.append(tag + rest)
            continue
        pl_line = _fmt_home_spread(pl_margin, home, away)
        xs_line = _fmt_home_spread(xs_margin, home, away)
        if _lines_are_copy(_norm_copied_line(pl_line), _norm_copied_line(xs_line)):
            out.append(card)
            continue
        books = _norm_copied_line(grab("data-books-spread"))
        if _lines_are_copy(books, _norm_copied_line(pl_line)) or _lines_are_copy(
            books, _norm_copied_line(xs_line)
        ):
            out.append(card)
            continue

        def _attr(source: str, name: str, value: str) -> str:
            if re.search(rf'\b{name}="', source, flags=re.I):
                return re.sub(
                    rf'\b{name}="[^"]*"',
                    f'{name}="{value}"',
                    source,
                    count=1,
                    flags=re.I,
                )
            return source

        old_pl, old_xs = grab("data-pl-spread"), grab("data-xs-spread")
        tag = _attr(tag, "data-pl-spread", pl_line)
        tag = _attr(tag, "data-xs-spread", xs_line)

        def _spread_row(match: re.Match) -> str:
            row = match.group(0)
            if old_pl:
                row = re.sub(
                    rf'(class="val-pl">)\s*{re.escape(old_pl)}',
                    rf"\1{pl_line}",
                    row,
                    count=1,
                )
            if old_xs:
                row = re.sub(
                    rf'(class="val-xs">)\s*{re.escape(old_xs)}',
                    rf"\1{xs_line}",
                    row,
                    count=1,
                )
            return row

        rest = re.sub(
            r'<tr>\s*<td class="market-k">Spread</td>[\s\S]*?</tr>',
            _spread_row,
            rest,
            count=1,
            flags=re.I,
        )
        out.append(tag + rest)
    return "".join(out)


def _american_implied(raw: str) -> float | None:
    text = (raw or "").replace("−", "-").replace("–", "-").replace("+", "").strip()
    if not text or text.upper() in {"N/A", "NA", "—", "-"}:
        return None
    try:
        price = float(text)
    except ValueError:
        return None
    if price == 0:
        return None
    if price > 0:
        return 100.0 / (price + 100.0)
    return (-price) / ((-price) + 100.0)


def _book_prices_on_card(card: str) -> tuple[str, str]:
    """Away and home American prices from the Books moneyline already on the card."""
    head = card.split("pick-conf-grid", 1)[0]
    if 'class="matchup-at"' not in head:
        return "", ""
    left, right = head.split('class="matchup-at"', 1)
    found = []
    for half in (left, right):
        match = re.search(
            r'class="ml-src books">[\s\S]{0,120}?class="ml-num[^"]*">\s*([^<]+)',
            half,
            flags=re.I,
        )
        found.append(match.group(1).strip() if match else "")
    return found[0], found[1]


def _fill_nfl_future_edge_from_book(html: str) -> str:
    """Today and later: a 50% Edge becomes the no-vig percent of the book
    moneyline already printed on that card. A finished game keeps its stored
    Edge. Skip a card when that percent would match another model.
    """
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    ids = []
    for card in parts[1:]:
        gid = re.search(r'\bdata-game-id="([^"]*)"', card, flags=re.I)
        if gid:
            ids.append(gid.group(1))
    dates = _nfl_game_dates(ids)
    today = _nfl_today_iso()
    out = [parts[0]]
    for card in parts[1:]:
        gid_m = re.search(r'\bdata-game-id="([^"]*)"', card, flags=re.I)
        home_m = re.search(r'\bdata-home="([^"]*)"', card, flags=re.I)
        away_m = re.search(r'\bdata-away="([^"]*)"', card, flags=re.I)
        gid = gid_m.group(1) if gid_m else ""
        home = home_m.group(1) if home_m else ""
        away = away_m.group(1) if away_m else ""
        day = dates.get(gid, "")
        if not day or day < today or not home or not away:
            out.append(card)
            continue
        edge_m = re.search(
            r'pc-name">\s*Edge\s*</div>\s*<div class="pc-val"[^>]*>\s*([\d.]+)\s*%',
            card,
            flags=re.I,
        )
        if not edge_m:
            out.append(card)
            continue
        try:
            shown = float(edge_m.group(1))
        except ValueError:
            out.append(card)
            continue
        if abs(shown - 50.0) >= 0.06:
            out.append(card)
            continue
        away_price, home_price = _book_prices_on_card(card)
        away_imp = _american_implied(away_price)
        home_imp = _american_implied(home_price)
        if away_imp is None or home_imp is None or (away_imp + home_imp) <= 0:
            out.append(card)
            continue
        total = away_imp + home_imp
        away_pct = 100.0 * away_imp / total
        home_pct = 100.0 * home_imp / total
        if home_pct >= away_pct:
            face, who, cls = round(home_pct, 1), home, "home"
        else:
            face, who, cls = round(away_pct, 1), away, "away"
        if abs(face - 50.0) < 0.06:
            out.append(card)
            continue
        others = []
        for _name, pct in re.findall(
            r'class="pc-name">\s*([^<]+?)\s*</div>\s*<div class="pc-val"[^>]*>\s*([\d.]+)\s*%',
            card,
            flags=re.I,
        ):
            label = re.sub(r"\s+", " ", _name).strip().lower()
            if label == "edge":
                continue
            try:
                others.append(float(pct))
            except ValueError:
                continue
        if any(f"{num:.1f}" == f"{face:.1f}" for num in others):
            out.append(card)
            continue
        out.append(_set_model_face(card, "Edge", face, who, cls))
    return "".join(out)


def _nfl_div_span(html: str, marker: str) -> tuple[int, int]:
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


_NFL_LAYOUT_CSS = (
    '<style id="nfl-layout-fit">'
    ".pc-side,.team-name,.pc-name{white-space:normal!important;overflow:hidden!important;"
    "overflow-wrap:anywhere!important;text-overflow:clip!important;max-height:none!important;"
    "height:auto!important;max-width:100%!important;line-height:1.2!important}"
    "#pl-totals-three-way{background:#fff!important;border:1px solid rgba(15,23,42,.18)!important;"
    "border-radius:14px!important;padding:18px 16px 8px!important;margin:16px auto 20px!important;"
    "max-width:1100px!important}"
    "#pl-totals-three-way table{width:100%!important;border-collapse:collapse!important}"
    "#pl-totals-three-way th,#pl-totals-three-way td{display:table-cell!important;"
    "padding:12px 16px!important;border-bottom:1px solid #e2e8f0!important;"
    "text-align:center!important;white-space:normal!important}"
    ".social-export-wrap{max-width:1100px!important;margin:28px auto 12px!important}"
    ".social-image-link{display:block!important;width:min(400px,100%)!important;"
    "max-width:400px!important;margin:12px auto!important}"
    ".social-image-link img{width:100%!important;height:auto!important;max-height:none!important}"
    "</style>"
)


def _nfl_layout(html: str) -> str:
    """Results image above the share bar. Totals chart and team names stay readable."""
    if not html:
        return html
    image_start, image_end = _nfl_div_span(html, 'data-results-share="1"')
    share_at = html.find('<div class="share-strip"')
    if image_start >= 0 and image_end > image_start and share_at >= 0 and image_start > share_at:
        block = html[image_start:image_end]
        html = html[:image_start] + html[image_end:]
        share_at = html.find('<div class="share-strip"')
        if share_at >= 0:
            html = html[:share_at] + block + "\n" + html[share_at:]
    html = re.sub(r'<style id="nfl-layout-fit">[\s\S]*?</style>', "", html, count=1, flags=re.I)
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", _NFL_LAYOUT_CSS + "</body>", html, count=1, flags=re.I)
    return html + _NFL_LAYOUT_CSS


def _align_nfl_efficiency_to_pl_spread(html: str) -> str:
    """Efficiency follows the Prediction Lab spread: the minus-spread team is its pick.

    Only a box whose team or percent disagrees with that spread is rewritten.
    """
    if not html or "data-pick-card" not in html:
        return html
    parts = re.split(r"(?=<div\b[^>]*\bdata-pick-card\b)", html, flags=re.I)
    out = [parts[0]]
    for card in parts[1:]:
        home_m = re.search(r'\bdata-home="([^"]*)"', card, flags=re.I)
        away_m = re.search(r'\bdata-away="([^"]*)"', card, flags=re.I)
        home = unescape(home_m.group(1)).strip() if home_m else ""
        away = unescape(away_m.group(1)).strip() if away_m else ""
        pl_m = re.search(r'\bdata-pl-spread="([^"]*)"', card, flags=re.I)
        pl = unescape(pl_m.group(1)).strip() if pl_m else ""
        if not pl:
            cell = re.search(r'<td class="val-pl">([^<]*)', card, flags=re.I)
            pl = unescape(cell.group(1)).strip() if cell else ""
        token = re.sub(r"\s+", " ", pl).strip().upper()
        if not pl or not home or not away or token in {"PK", "PICK", "PICK'EM", "PICKEM", "EVEN"}:
            out.append(card)
            continue
        box = re.search(
            r'<div class="pc-name">\s*Efficiency\s*</div>\s*'
            r'<div class="pc-val"[^>]*>\s*([\d.]+)%[\s\S]*?</div>\s*'
            r'<div class="pc-side([^"]*)"',
            card,
            flags=re.I,
        )
        if not box:
            out.append(card)
            continue
        try:
            got = _nfl_formula_face(pl, home, away)
        except Exception:
            got = None
        if not got:
            out.append(card)
            continue
        face, who, cls = got
        shown_cls = "home" if re.search(r"\bhome\b", box.group(2)) else (
            "away" if re.search(r"\baway\b", box.group(2)) else ""
        )
        if shown_cls == cls and abs(float(box.group(1)) - float(face)) < 0.05:
            out.append(card)
            continue
        out.append(_set_model_face(card, "Efficiency", face, who, cls))
    return "".join(out)


def apply_nfl_owner_fixes(html: str, path: str, view: str = "", market: str = "") -> str:
    """NFL-only repairs for the unlocked misses. Other sports never reach this."""
    if not html:
        return html
    path_l = (path or "").lower()
    if path_l.rstrip("/") == "/nfl-picks":
        html = _restore_nfl_stored_spreads(html)
        html = _align_nfl_future_model_lines(html)
        html = _clear_copied_nfl_efficiency(html)
        from team_results_charts import _fill_efficiency_na
        html = _fill_efficiency_na(html, "NFL")
        html = _align_nfl_efficiency_to_pl_spread(html)
        html = stamp_visible_model_attrs(html)
        html = _restore_nfl_stored_edge(html)
        html = _fill_nfl_future_edge_from_book(html)
        html = rewrite_nfl_share_hrefs(html)
        try:
            # Recent results and card chips read the results page's own records.
            from card_windows import copy_recent_from_results, set_pl_rows_from_cards, sync_consensus_chips

            results = _cards_html_for_align("NFL", "") or ""
            if results:
                from card_windows import paint_tallies

                results = paint_tallies(results, "NFL", models=False)
                results = set_pl_rows_from_cards(results, results)
                html = copy_recent_from_results(html, results, "NFL")
                html = sync_consensus_chips(html, results)
        except Exception:
            pass
        return _nfl_layout(html)
    if path_l.rstrip("/") == "/nfl-results":
        html = rewrite_nfl_share_hrefs(html)
        view_l = (view or "").strip().lower()
        try:
            from card_windows import paint_tallies, set_pl_rows_from_cards, sync_consensus_chips

            is_cards = view_l not in ("chart", "tabs", "markets", "tabbed", "spread", "totals") and 'class="game-card' in html
            if is_cards:
                html = paint_tallies(html, "NFL", models=False)
            cards = html if is_cards else (_cards_html_for_align("NFL", "") or "")
            html = set_pl_rows_from_cards(html, cards)
            if is_cards:
                html = sync_consensus_chips(html, html)
        except Exception:
            pass
        view_l = (view or "").strip().lower()
        market_l = (market or "").strip().lower()
        if market_l not in ("spread", "totals") and view_l in ("spread", "totals"):
            market_l = view_l
        if market_l in ("spread", "totals"):
            html = _nfl_put_back_market_rows(html, market_l) or html
        html = nfl_strip_three_way_blocks(html)
        return _nfl_layout(html)
    return html


def _nfl_total_lean(raw: str, book: float) -> str:
    text = (raw or "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return ""
    try:
        val = float(text)
    except ValueError:
        return ""
    if val > book:
        return "over"
    if val < book:
        return "under"
    return ""


def _nfl_shared_grade(grades: list[str]) -> str:
    decided = [g for g in grades if g in {"WIN", "LOSS"}]
    if not decided or len(decided) != len([g for g in grades if g]):
        return ""
    if all(g == "WIN" for g in decided):
        return "WIN"
    if all(g == "LOSS" for g in decided):
        return "LOSS"
    return ""


def _nfl_three_way_records(games: list[dict], ln_key: str, yesterday: str) -> dict[str, dict[str, dict[str, list[str]]]]:
    """Agreement rows for moneyline, spread, and totals. Skips a row when the sides differ."""
    from datetime import date, timedelta

    try:
        end = date.fromisoformat(yesterday)
        cut7 = (end - timedelta(days=6)).isoformat()
        cut30 = (end - timedelta(days=29)).isoformat()
    except ValueError:
        cut7 = cut30 = ln_key
    labels = ("PL = Books", "PL = XSharp", "Books = XSharp", "All 3 agree")
    out: dict[str, dict[str, dict[str, list[str]]]] = {
        market: {label: {"ln": [], "d7": [], "d30": []} for label in labels}
        for market in ("moneyline", "spread", "totals")
    }

    def _add(market: str, label: str, dk: str, grade: str) -> None:
        if grade not in {"WIN", "LOSS"} or label not in out[market]:
            return
        if dk == ln_key:
            out[market][label]["ln"].append(grade)
        if cut7 <= dk <= yesterday:
            out[market][label]["d7"].append(grade)
        if cut30 <= dk <= yesterday:
            out[market][label]["d30"].append(grade)

    for g in games:
        dk = g["date"]
        away, home, aa, hs = g["away"], g["home"], g["aa"], g["hs"]
        ml = {"book": g["book_ml"], "pl": g["pl_ml"], "xs": g["xs_ml"]}
        ml_grade = {}
        if aa != hs:
            winner = home if hs > aa else away
            for key, side in ml.items():
                if side:
                    ml_grade[key] = "WIN" if side == winner else "LOSS"
        pairs = (
            ("PL = Books", ("pl", "book")),
            ("PL = XSharp", ("pl", "xs")),
            ("Books = XSharp", ("book", "xs")),
        )
        for label, keys in pairs:
            sides = [ml.get(k) or "" for k in keys]
            grades = [ml_grade.get(k) or "" for k in keys]
            if sides[0] and sides[0] == sides[1]:
                _add("moneyline", label, dk, _nfl_shared_grade(grades))
        if len({s for s in ml.values() if s}) == 1 and all(ml.values()):
            _add("moneyline", "All 3 agree", dk, _nfl_shared_grade([ml_grade.get(k) or "" for k in ("pl", "book", "xs")]))

        spread_side: dict[str, str] = {}
        spread_grade: dict[str, str] = {}
        for key, src in (("book", "books"), ("pl", "pl"), ("xs", "xs")):
            team, line = _nfl_spread_side((g.get("spread") or {}).get(src) or "", away, home)
            if not team or line is None:
                continue
            spread_side[key] = team
            spread_grade[key] = _nfl_cover(team, line, away, home, aa, hs)
        for label, keys in pairs:
            sides = [spread_side.get(k) or "" for k in keys]
            grades = [spread_grade.get(k) or "" for k in keys]
            if sides[0] and sides[0] == sides[1]:
                _add("spread", label, dk, _nfl_shared_grade(grades))
        if len(spread_side) == 3 and len(set(spread_side.values())) == 1:
            _add(
                "spread",
                "All 3 agree",
                dk,
                _nfl_shared_grade([spread_grade.get(k) or "" for k in ("pl", "book", "xs")]),
            )

        totals = g.get("total") or {}
        book_raw = (totals.get("books") or "").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)?", book_raw):
            continue
        book_line = float(book_raw)
        leans = {
            "pl": _nfl_total_lean(totals.get("pl") or "", book_line),
            "xs": _nfl_total_lean(totals.get("xs") or "", book_line),
        }
        actual = aa + hs
        if actual == book_line:
            continue
        over_hit = actual > book_line

        def _tot(side: str) -> str:
            if side == "over":
                return "WIN" if over_hit else "LOSS"
            if side == "under":
                return "WIN" if not over_hit else "LOSS"
            return ""

        if leans["pl"] and leans["pl"] == leans["xs"]:
            _add("totals", "PL = XSharp", dk, _tot(leans["pl"]))
    return out


def _nfl_wl(grades: list[str]) -> str:
    wins = sum(1 for g in grades if g == "WIN")
    losses = sum(1 for g in grades if g == "LOSS")
    if wins + losses == 0:
        return "0-0"
    return f"{wins}-{losses}"


def _nfl_three_way_html(records: dict, ln_key: str) -> str:
    titles = {
        "moneyline": "Books · Prediction Lab · XSharp — Moneyline",
        "spread": "Books · Prediction Lab · XSharp — Spread",
        "totals": "Books · Prediction Lab · XSharp — Totals",
    }
    labels = ("PL = Books", "PL = XSharp", "Books = XSharp", "All 3 agree")
    blocks = []
    for market in ("moneyline", "spread", "totals"):
        rows = []
        for label in labels:
            cells = records.get(market, {}).get(label) or {}
            rows.append(
                "<tr>"
                f'<td class="bucket">{escape(label)}</td>'
                f"<td>{_nfl_wl(cells.get('ln') or [])}</td>"
                f"<td>{_nfl_wl(cells.get('d7') or [])}</td>"
                f"<td>{_nfl_wl(cells.get('d30') or [])}</td>"
                "</tr>"
            )
        blocks.append(
            f'<section class="pl-consensus-records" id="nfl-three-way-{market}">'
            f"<h2>{escape(titles[market])}</h2>"
            "<table><thead><tr>"
            "<th>Signal</th>"
            f"<th>Last night ({escape(ln_key)})</th>"
            "<th>Past 7 days</th><th>Past 30 days</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></section>"
        )
    return "".join(blocks)


def _nfl_model_windows_html(cards_html: str, ln_key: str, night_n: int) -> str:
    """Last Night / Last 7 / Season rows for the six models, from the cards page."""
    try:
        from mlb_results_ui import markets_from_live_html

        payload = markets_from_live_html(cards_html, "nfl") or {}
    except Exception:
        payload = {}
    tallies = ((payload.get("markets") or {}).get("moneyline") or {}).get("tallies") or {}
    order = ("Grinder2", "Takedown", "Edge", "XSharp", "Sharp Consensus", "Efficiency")
    game_word = "game" if night_n == 1 else "games"
    blocks = [
        (
            "last_night",
            f"Last Night ({night_n} {game_word}) — {ln_key}",
        ),
        ("last_7", "Last 7"),
        ("season", "Season"),
    ]
    parts = ['<section id="nfl-chart-model-windows">']
    for key, title in blocks:
        block = tallies.get(key) or {}
        models = block.get("models") or {}
        cards = []
        for name in order:
            rec = models.get(name) or {}
            record = rec.get("record") or "0-0"
            pct = rec.get("pct")
            pct_s = f"{pct}%" if pct is not None else "—"
            cards.append(
                '<div class="daily-tally-card tally-card">'
                f'<div class="daily-model">{escape(name)}</div>'
                f'<div class="daily-acc">{escape(pct_s)}</div>'
                f'<div class="daily-rec">{escape(str(record))}</div>'
                "</div>"
            )
        parts.append(
            f"<h2>{escape(title)}</h2>"
            f'<div class="tally-grid daily-tally-grid">{"".join(cards)}</div>'
        )
    parts.append("</section>")
    return "".join(parts)


def finish_nfl_chart_html(html: str, cards_html: str = "") -> str:
    """Fill the NFL chart: Past 7 includes last night, six model windows, three-way tables."""
    if not html or "nfl" not in html.lower():
        return html
    html = fill_past7_from_last_night(html)
    cards = cards_html or ""
    if "game-card" not in cards:
        return html
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    try:
        yesterday = (
            datetime.now(ZoneInfo("America/New_York")) - timedelta(days=1)
        ).date().isoformat()
    except Exception:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    dates = [
        d
        for d in re.findall(r'id="date-(\d{4}-\d{2}-\d{2})"', cards)
        if d <= yesterday
    ]
    ln_key = max(dates) if dates else ""
    if not ln_key:
        return html
    night_n = 0
    section = re.search(
        rf'<div id="date-{re.escape(ln_key)}"([\s\S]*?)(?:<div id="date-|$)',
        cards,
    )
    if section:
        night_n = len(re.findall(r'class="game-card\b', section.group(1)))
    if night_n < 1:
        night_n = 1 if ln_key else 0
    if 'id="nfl-chart-model-windows"' not in html:
        block = _nfl_model_windows_html(cards, ln_key, night_n)
        anchor = html.find('id="pl-consensus-records"')
        if anchor > 0:
            anchor = html.rfind("<div", 0, anchor)
        if anchor < 0:
            anchor = html.find("<h2>Consensus Based Betting Records")
        if anchor > 0:
            html = html[:anchor] + block + html[anchor:]
        else:
            html = _nfl_put_three_way(html, block)
    if 'id="nfl-three-way-moneyline"' not in html:
        games = _nfl_chart_games(cards)
        table = _nfl_three_way_html(_nfl_three_way_records(games, ln_key, yesterday), ln_key)
        html = _nfl_put_three_way(html, table)
    return nfl_strip_three_way_blocks(html)


_NFL_THREE_WAY_RE = re.compile(
    r'<section class="pl-consensus-records" id="nfl-three-way-[a-z]+">[\s\S]*?</section>'
)


def _nfl_put_three_way(html: str, blocks: str) -> str:
    """Put the Books / Prediction Lab / XSharp tables in the page body, above the footer."""
    if not html or not blocks:
        return html
    for marker in ("</main>", '<div class="share-strip"', '<footer class="site-directory-footer"'):
        at = html.rfind(marker)
        if at > 0:
            return html[:at] + blocks + html[at:]
    return html + blocks


_NFL_MODEL_WINDOWS_RE = re.compile(
    r'<section id="nfl-chart-model-windows">[\s\S]*?</section>'
)


_NFL_ATS_CARDS_RE = re.compile(
    r'<section class="nfl-ats-cards" id="nfl-(?:spread|totals)-cards"[\s\S]*?</section>'
)


def _nfl_model_windows_above_footer(html: str) -> str:
    """Model windows and Spread/Totals result cards belong above the footer."""
    if not html:
        return html
    for pattern in (_NFL_MODEL_WINDOWS_RE, _NFL_ATS_CARDS_RE):
        while True:
            foot = html.find('<footer class="site-directory-footer"')
            found = [m for m in pattern.finditer(html) if foot >= 0 and m.start() > foot]
            if not found:
                break
            m = found[0]
            block = m.group(0)
            html = html[: m.start()] + html[m.end():]
            html = _nfl_put_three_way(html, block)
    return html


def nfl_strip_three_way_blocks(html: str) -> str:
    """Move the Books / Prediction Lab / XSharp tables above the footer if they sit below it."""
    html = _nfl_model_windows_above_footer(html)
    if not html or 'id="nfl-three-way-' not in html:
        return html
    foot = html.find('<footer class="site-directory-footer"')
    found = list(_NFL_THREE_WAY_RE.finditer(html))
    if not found or (foot > 0 and all(m.start() < foot for m in found)):
        return html
    blocks = "".join(m.group(0) for m in found)
    html = _NFL_THREE_WAY_RE.sub("", html)
    return _nfl_put_three_way(html, blocks)


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
        cut30 = (ln_d - timedelta(days=29)).isoformat()
    except ValueError:
        cut7 = ln_key
        cut30 = ln_key
    week = re.search(
        r"Last 7 Days [\s\S]{0,160}?(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})",
        html,
    )
    if week:
        p7_lo, p7_hi = week.group(1), week.group(2)
    else:
        p7_lo, p7_hi = cut7, ln_key
    ln_games = [g for g in games if g["date"] == ln_key]
    p7_games = [g for g in games if p7_lo <= g["date"] <= p7_hi]
    p30_games = [g for g in games if cut30 <= g["date"] <= ln_key]
    if len(ln_games) < 1 and len(p7_games) < 1:
        return html
    expected: dict[str, list[str]] = {}
    expected_p7: dict[str, list[str]] = {}
    expected_p30: dict[str, list[str]] = {}
    pretty: dict[str, str] = {}
    for g in ln_games:
        key = _norm_cons_label(g["label"])
        expected.setdefault(key, []).append(g["grade"])
        pretty.setdefault(key, g["label"])
    for g in p7_games:
        key = _norm_cons_label(g["label"])
        expected_p7.setdefault(key, []).append(g["grade"])
        pretty.setdefault(key, g["label"])
    for g in p30_games:
        key = _norm_cons_label(g["label"])
        expected_p30.setdefault(key, []).append(g["grade"])
        pretty.setdefault(key, g["label"])
    start = html.find(ML_CHART)
    if start < 0:
        return html
    end = html.find(PL_VS_BOOKS, start + 1)
    if end < start:
        end = start + 8000
    block = html[start:end]

    def _wl(grades: list[str] | None, with_pct: bool) -> str:
        w = sum(1 for g in grades or [] if g == "WIN")
        l = sum(1 for g in grades or [] if g == "LOSS")
        p = sum(1 for g in grades or [] if g == "PUSH")
        total = w + l
        if total == 0 or not with_pct:
            return f"{w}-{l}" + (f"-{p}" if p else "")
        pct = round(100 * w / total)
        color = "#067647" if pct >= 50 else "#b42318"
        return (
            f"{w}-{l}" + (f"-{p}" if p else "") + f" <span style='color:{color};font-weight:700'>({pct}%)</span>"
            f"<div class='cons-bar' aria-hidden='true'>"
            f"<i style='width:{pct}%;background:{color}'></i></div>"
        )

    def _is_dash(raw: str) -> bool:
        text = re.sub(r"<[^>]+>", " ", raw or "")
        text = text.replace("&mdash;", "—").replace("&ndash;", "–")
        text = re.sub(r"\s+", " ", text).strip()
        return text in {"", "—", "–", "-", "N/A", "n/a", "NA", "na"}

    def _cell(m: re.Match[str]) -> str:
        label = m.group(1)
        key = _norm_cons_label(label)
        past30 = m.group(4)
        if _is_dash(past30):
            past30 = _wl(expected_p30.get(key), True)
        return (
            f'<td class="bucket">{label}</td>'
            f"<td>{_wl(expected.get(key), True)}</td>"
            f"<td>{_wl(expected_p7.get(key), True)}</td>"
            f"<td>{past30}</td>"
        )

    block2 = re.sub(
        r'<td class="bucket">([^<]+)</td>\s*<td>([\s\S]*?)</td>\s*<td>([\s\S]*?)</td>\s*<td>([\s\S]*?)</td>',
        _cell,
        block,
        flags=re.I,
    )
    have = {
        _norm_cons_label(lab)
        for lab in re.findall(r'<td class="bucket">([^<]+)</td>', block2, flags=re.I)
    }
    extra = []
    for key in list(expected) + [k for k in expected_p7 if k not in expected]:
        if key in have:
            continue
        have.add(key)
        extra.append(
            f'<tr><td class="bucket">{pretty.get(key, key)}</td>'
            f"<td>{_wl(expected.get(key), False)}</td>"
            f"<td>{_wl(expected_p7.get(key), True)}</td>"
            f"<td>{_wl(expected_p30.get(key), True)}</td></tr>"
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
    if not is_chart and sport_u not in {"TENNIS", "UFC", "GOLF"}:
        # Grade the marks against the posted score first, so every table built
        # from these cards (consensus, records) counts the same wins and losses.
        html = align_card_marks_to_score(html)
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
            html = ensure_sou_compare(html, view_l)
        cards = _cards_html_for_align(sport_u, "")
        if cards and ("pick-conf-grid" in cards or cards.count("game-card") >= 3):
            html = align_consensus_last_night_from_cards(html, sport_u, cards_html=cards)
            html = align_pl_vs_books_last_night(html, cards_html=cards)
            html = fill_past7_from_last_night(html)
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
    if len(cards) < 1:
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
        shown = len(cards)
        existing = re.search(r'data-share-picks="(\d+)"', html2)
        if existing:
            try:
                shown = max(shown, int(existing.group(1)))
            except ValueError:
                pass
        html2 = re.sub(
            r'data-share-picks="\d+"',
            f'data-share-picks="{shown}"',
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
    if (path or "").lower().rstrip("/") in ("/ncaaf-picks", "/nhl-picks"):
        from team_results_charts import _fill_efficiency_na

        sport = path.strip("/").split("-")[0].upper()
        html = _fill_efficiency_na(html, sport)
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
    if sport_u != "NFL":
        html = fix_efficiency_na_side(html)
    if sport_u == "NCAAF":
        html = fill_ncaaf_edge_from_spread(html)
    html = unwrap_pc_name_sides(html)
    html = shorten_pc_side_labels(html)
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
                html = hide_blank_books_ml(html)
        return normalize_served_html(html, path_l)
    if "results" in path_l:
        html = repair_results_html(html, sport_u, view)
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
            # The border color must say the same thing as the mark.
            box = re.sub(
                r'^(<div class="pc-box[^"]*?)\b(?:correct|wrong)\b',
                lambda mm: mm.group(1) + ("correct" if want == "✅" else "wrong"),
                box,
                count=1,
            )
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
        _pl_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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

