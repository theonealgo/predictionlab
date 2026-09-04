"""Serve the signed-off tennis picks/results HTML (hub /tennis/ snapshot)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_PAGES = Path(__file__).resolve().parent / "locked_pages" / "tennis"


def _rewrite_hub_paths(html: str) -> str:
    if not html:
        return html
    html = html.replace("/tennis/results", "/tennis-results")
    html = html.replace('href="/tennis/"', 'href="/tennis-picks"')
    html = html.replace("href='/tennis/'", "href='/tennis-picks'")
    html = re.sub(r'href=(["\'])/tennis/\1', r"href=\1/tennis-picks\1", html)
    return html


def _read_page(name: str) -> str:
    path = _PAGES / name
    if not path.is_file():
        raise RuntimeError(f"locked tennis snapshot missing: {path}")
    return _rewrite_hub_paths(path.read_text(encoding="utf-8", errors="replace"))


def render_tennis_picks() -> str:
    return _read_page("picks.html")


def render_tennis_results(*, view: str = "normal") -> str:
    return _read_page("results.html")


def build_tennis_share_jpeg_bytes() -> bytes | None:
    jpg = _PAGES / "share.jpg"
    if jpg.is_file() and jpg.stat().st_size > 500:
        data = jpg.read_bytes()
        if data[:2] == b"\xff\xd8":
            return data
    return None


def tennis_chart_payload() -> dict[str, Any]:
    return {"ok": False, "error": "tennis chart uses the locked results snapshot"}
