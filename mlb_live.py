"""Serve the working local MLB picks HTML when live rebuild is empty."""
from __future__ import annotations

from pathlib import Path

_PAGES = Path(__file__).resolve().parent / "locked_pages" / "mlb"


def _rewrite_local_urls(html: str) -> str:
    if not html:
        return html
    html = html.replace("http%3A//127.0.0.1%3A5052/mlb-picks", "https%3A//predictionlab.io/mlb-picks")
    html = html.replace("http://127.0.0.1:5052/mlb-picks", "https://predictionlab.io/mlb-picks")
    return html


def render_mlb_picks() -> str:
    path = _PAGES / "picks.html"
    if not path.is_file():
        raise RuntimeError(f"MLB picks snapshot missing: {path}")
    html = path.read_text(encoding="utf-8", errors="replace")
    if "game-card-stack" not in html:
        raise RuntimeError("MLB picks snapshot has no game cards")
    return _rewrite_local_urls(html)
