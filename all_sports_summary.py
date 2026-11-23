"""Public summaries of published results boards; never calculate or grade picks."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import tempfile
import threading
from html import unescape
from pathlib import Path

from html_blocks import blocks

MODELS = (
    ("glicko2", "Grinder2"), ("trueskill", "Takedown"), ("elo", "Edge"),
    ("xgboost", "XSharp"), ("ensemble", "Market Consensus"), ("efficiency", "Efficiency"),
)
SPORTS = (
    ("NHL", "NHL"), ("NBA", "NBA"), ("MLB", "MLB"), ("NFL", "NFL"),
    ("NCAAB", "NCAAB"), ("NCAAW", "NCAAW"), ("NCAAF", "NCAAF"),
    ("WNBA", "WNBA"), ("SOCCER", "Soccer"), ("TENNIS", "Tennis"),
    ("UFC", "UFC"), ("GOLF", "Golf"), ("CFL", "CFL"),
)
_ALIASES = {label.lower(): key for key, label in MODELS}
_ALIASES.update({"sharp consensus": "ensemble", "prediction lab": "pl", "pl": "pl"})
_RECORD = re.compile(r"(?<![\d.])(\d+)\s*[-–]\s*(\d+)(?:\s*[-–]\s*(\d+))?(?![\d.])")
_MARKETS = {
    "spread": ("pl-spread-records", "pl-spread-vs-books"),
    "ou": ("pl-totals-records", "pl-ou-records", "pl-totals-vs-books"),
}
_PARSED = {}
_LOCK = threading.RLock()
_BUILDING = set()
_RETRY_AT = {}
VERSION = 1


def _classes(attrs):
    return set((attrs.get("class") or "").split())


def _text(source):
    source = re.sub(r"<(?:script|style)\b[^>]*>[\s\S]*?</(?:script|style)>", "", source, flags=re.I)
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", source)).split())


def _contents(source, block):
    return source[block.start:block.end]


def _model(label):
    label = _text(label).lower()
    return next((key for alias, key in _ALIASES.items()
                 if re.search(r"\b" + re.escape(alias) + r"\b", label)), None)


def unavailable(reason, status="unavailable"):
    return {"status": status, "record": "N/A", "pct": None, "n": None,
            "wins": None, "losses": None, "pushes": None, "reason": reason}


def record_cell(source):
    match = _RECORD.search(_text(source))
    if not match:
        return unavailable("A settled win–loss record is not published for this entry.")
    wins, losses, pushes = (int(value or 0) for value in match.groups())
    n = wins + losses
    return {
        "status": "graded" if n + pushes else "empty",
        "record": f"{wins}-{losses}" + (f"-{pushes}" if pushes else ""),
        "pct": round(100 * wins / n, 1) if n else None,
        "n": n, "wins": wins, "losses": losses, "pushes": pushes,
        "reason": ("Pushes are excluded from accuracy." if pushes else
                   "Accuracy is wins divided by settled wins and losses.") if n else
                  ("Only pushes are recorded in this window." if pushes else
                   "The results board reports no graded decisions in this window."),
    }


def parse_board(source, sport):
    """Read season model cards and explicitly labelled past-30-day market tables."""
    selected = blocks(source, lambda tag, attrs:
                      tag in {"h2", "h3"} or
                      bool(_classes(attrs) & {"model-grid", "daily-tally"}) or
                      any(attrs.get("id") in ids for ids in _MARKETS.values()))
    models = {}
    heading = ""
    for block in selected:
        chunk = _contents(source, block)
        if block.tag in {"h2", "h3"}:
            heading = _text(chunk)
        elif "model-grid" in _classes(block.attrs) and re.search(
                r"Moneyline Accuracy|Season", heading, re.I):
            for card in blocks(chunk, lambda tag, attrs: "model-card" in _classes(attrs)):
                card_html = _contents(chunk, card)
                labels = blocks(card_html, lambda tag, attrs: "model-label" in _classes(attrs))
                key = _model(_contents(card_html, labels[0])) if labels else None
                if key in dict(MODELS):
                    models[key] = record_cell(card_html)
        elif "daily-tally" in _classes(block.attrs):
            titles = blocks(chunk, lambda tag, attrs: tag in {"h2", "h3"})
            if not titles or not re.search(r"\bSeason\b", _text(_contents(chunk, titles[0])), re.I):
                continue
            for card in blocks(chunk, lambda tag, attrs: "daily-tally-card" in _classes(attrs)):
                card_html = _contents(chunk, card)
                labels = blocks(card_html, lambda tag, attrs: "daily-model" in _classes(attrs))
                key = _model(_contents(card_html, labels[0])) if labels else None
                if key in dict(MODELS):
                    models[key] = record_cell(card_html)
    markets = {"spread": {}, "ou": {}}
    for market, identifiers in _MARKETS.items():
        for identifier in identifiers:
            container = next((b for b in selected if b.attrs.get("id") == identifier), None)
            if not container:
                continue
            chunk = _contents(source, container)
            for table in blocks(chunk, lambda tag, attrs: tag == "table"):
                table_html = _contents(chunk, table)
                rows = blocks(table_html, lambda tag, attrs: tag == "tr")
                column = None
                for row in rows:
                    row_html = _contents(table_html, row)
                    headers = blocks(row_html, lambda tag, attrs: tag == "th")
                    if headers:
                        column = next((index for index, cell in enumerate(headers)
                                       if re.fullmatch(r"(?:Past|Last)\s+30\s+days",
                                                       _text(_contents(row_html, cell)), re.I)), None)
                        continue
                    cells = blocks(row_html, lambda tag, attrs: tag == "td")
                    if column is None or len(cells) <= column:
                        continue
                    label = _text(_contents(row_html, cells[0])).lower()
                    if label not in {"prediction lab", "pl", "xsharp"}:
                        continue
                    key = "pl" if label in {"prediction lab", "pl"} else "xgboost"
                    markets[market][key] = record_cell(_contents(row_html, cells[column]))
            if markets[market]:
                break
    dates = re.findall(r"Last night\s*\((\d{4}-\d{2}-\d{2})\)", _text(source), re.I)
    return {"sport": sport, "ml": models, **markets, "through": dates[0] if dates else ""}


def _cache_dir():
    from served_cache import cache_dir
    return cache_dir()


def remember_response(response):
    """Observe final public board HTML without altering its content or grades."""
    from flask import request
    match = re.fullmatch(r"/([a-z]+)-results/?", request.path)
    if (not match or request.args or request.method != "GET" or response.status_code != 200
            or response.direct_passthrough or response.mimetype != "text/html"):
        return response
    sport = match[1].upper()
    if sport not in dict(SPORTS):
        return response
    try:
        from flask import current_app
        if hasattr(current_app, "login_manager"):
            from auth_system import is_premium_user
            if is_premium_user():
                return response
        raw = response.get_data()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        digest = hashlib.sha256(raw).hexdigest()
        path = _cache_dir() / "results_summary" / f"{sport}.json"
        with _LOCK:
            previous = _PARSED.get(("response", sport))
            if previous == digest:
                return response
        parsed = parse_board(raw.decode("utf-8"), sport)
        if not parsed["ml"] and not any(parsed[market] for market in _MARKETS):
            return response
        payload = {"version": VERSION, "board": parsed}
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=f"{sport}.", suffix=".tmp", delete=False) as saved:
                json.dump(payload, saved)
            Path(saved.name).replace(path)
            _PARSED[("response", sport)] = digest
    except Exception:
        from flask import current_app
        current_app.logger.warning("Could not remember %s results summary", sport, exc_info=True)
    return response


def _read_board(sport, directory):
    public = directory / f"served_{sport}___free.html"
    canonical = public if public.is_file() else directory / f"served_{sport}_.html"
    # A response already includes every display/consistency pass; an intermediate
    # HTML cache can be newer while still differing from the published board.
    candidates = [directory / "results_summary" / f"{sport}.json", canonical]
    if sport == "NHL" and not canonical.is_file():
        candidates.append(directory / "nhl_results_page.html")
    candidates = [p for p in candidates if p.is_file()]
    for path in candidates:
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            with _LOCK:
                hit = _PARSED.get(str(path))
            if hit and hit[0] == signature:
                return hit[1]
            if path.suffix == ".json":
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("version") != VERSION:
                    continue
                board = payload["board"]
            else:
                board = parse_board(path.read_text(encoding="utf-8"), sport)
            if board.get("sport") != sport:
                continue
            if not board.get("ml") and not any(board.get(market) for market in _MARKETS):
                continue
            with _LOCK:
                _PARSED[str(path)] = (signature, board)
            return board
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return {"sport": sport, "ml": {}, "spread": {}, "ou": {}, "through": ""}


def summary_rows(directory=None):
    directory = Path(directory) if directory is not None else _cache_dir()
    rows = []
    for sport, name in SPORTS:
        board = _read_board(sport, directory)
        individual = sport in {"TENNIS", "UFC", "GOLF"}
        row = {
            "sport": sport, "name": name, "results_url": f"/{sport.lower()}-results",
            "through": board.get("through", ""), "ml": {},
            "spread": {}, "ou": {}, "available": bool(board["ml"]),
        }
        for key, _label in MODELS:
            reason = ("Golf reports tournament finishes rather than comparable two-way model records."
                      if sport == "GOLF" else
                      "This model's season record is not published on the results board.")
            row["ml"][key] = board["ml"].get(key, unavailable(reason))
        for market in _MARKETS:
            for key in ("xgboost", "pl"):
                row[market][key] = board[market].get(key, unavailable(
                    "This sport does not publish spread or total results." if individual else
                    "A past-30-day record is not available on this sport's results board.",
                    "not_applicable" if individual else "unavailable"))
        rows.append(row)
    return rows


def warm_missing(app, rows):
    """Only missing boards are requested, one at a time, off the visitor's request."""
    import time
    missing = [row["sport"] for row in rows if not row["available"] and row["sport"] != "GOLF"]
    with _LOCK:
        missing = [sport for sport in missing if sport not in _BUILDING
                   and time.monotonic() >= _RETRY_AT.get(sport, 0)]
        if not missing:
            return
        _BUILDING.update(missing)

    def run():
        for sport in missing:
            try:
                with app.test_client() as client:
                    client.get(f"/{sport.lower()}-results", base_url="http://results-summary.local")
            except Exception:
                app.logger.warning("Could not load %s results board", sport, exc_info=True)
            finally:
                with _LOCK:
                    _BUILDING.discard(sport)
                    _RETRY_AT[sport] = time.monotonic() + 300

    threading.Thread(target=run, daemon=True, name="results-summary").start()


def render_fragment(rows=None):
    from flask import render_template
    from site_words import rewrite_html
    return rewrite_html(render_template("partials/all_sports_summary.html",
                                        rows=rows if rows is not None else summary_rows(),
                                        models=MODELS))


def render_page():
    from flask import current_app, render_template
    from markupsafe import Markup
    rows = summary_rows()
    warm_missing(current_app._get_current_object(), rows)
    return render_template("all_sports_results.html", summary=Markup(render_fragment(rows)))
