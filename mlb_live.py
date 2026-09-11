"""Serve the working :5052 MLB picks and results HTML."""
from __future__ import annotations

from pathlib import Path

_PAGES = Path(__file__).resolve().parent / "locked_pages" / "mlb"


def _rewrite_local_urls(html: str) -> str:
    if not html:
        return html
    html = html.replace("http%3A//127.0.0.1%3A5052/mlb-picks", "https%3A//predictionlab.io/mlb-picks")
    html = html.replace("http://127.0.0.1:5052/mlb-picks", "https://predictionlab.io/mlb-picks")
    html = html.replace("http%3A//127.0.0.1%3A5052/mlb-results", "https%3A//predictionlab.io/mlb-results")
    html = html.replace("http://127.0.0.1:5052/mlb-results", "https://predictionlab.io/mlb-results")
    html = html.replace("http://127.0.0.1:5052", "https://predictionlab.io")
    return html


def _read_page(name: str) -> str:
    path = _PAGES / name
    if not path.is_file():
        raise RuntimeError(f"MLB snapshot missing: {path}")
    return _rewrite_local_urls(path.read_text(encoding="utf-8", errors="replace"))


def render_mlb_picks() -> str:
    html = _read_page("picks.html")
    if "game-card-stack" not in html:
        raise RuntimeError("MLB picks snapshot has no game cards")
    return html


def render_mlb_results(*, view: str = "normal") -> str:
    view = (view or "normal").strip().lower()
    name = "results_chart.html" if view in ("chart", "tabs", "markets", "tabbed") else "results.html"
    html = _read_page(name)
    if "Consensus Based Betting Records" not in html:
        raise RuntimeError("MLB results snapshot is missing consensus charts")
    if "Books · Prediction Lab · XSharp — Run Line" not in html:
        raise RuntimeError("MLB results snapshot is missing the run-line consensus chart")
    return html
