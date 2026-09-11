"""UFC on :5052 — exact HTML copied from the completed :5081 /ufc/ page.

Snapshots: `_sandbox_hub_run/locked_pages/ufc/` (fetched from 5081).
No fetch, no graft, no rebuild. Route remaps only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

_PAGES = Path(__file__).resolve().parent / "_sandbox_hub_run" / "locked_pages" / "ufc"
_HUB = Path(__file__).resolve().parent / "_sandbox_hub_run" / "hub"
_ROOT = _HUB.parent


def _rewrite_hub_paths(html: str) -> str:
    if not html:
        return html
    html = html.replace("/ufc/results", "/ufc-results")
    html = html.replace('href="/ufc/"', 'href="/ufc-picks"')
    html = html.replace("href='/ufc/'", "href='/ufc-picks'")
    html = re.sub(r'href=(["\'])/ufc/\1', r"href=\1/ufc-picks\1", html)
    html = re.sub(
        r"http://127\.0\.0\.1:5081/ufc/",
        "http://127.0.0.1:5052/ufc-picks",
        html,
    )
    html = re.sub(
        r"http://127\.0\.0\.1:5152/ufc-picks",
        "http://127.0.0.1:5052/ufc-picks",
        html,
    )
    return html


def _read_page(name: str) -> str:
    path = _PAGES / name
    if not path.is_file():
        raise RuntimeError(f"locked UFC snapshot missing: {path}")
    return _rewrite_hub_paths(path.read_text(encoding="utf-8", errors="replace"))


def render_ufc_picks() -> str:
    return _read_page("picks.html")


def render_ufc_results(*, view: str = "normal") -> str:
    view = (view or "normal").strip().lower()
    if view in ("chart", "tabs", "markets", "tabbed"):
        return _read_page("results_chart.html")
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

        payload = build_ufc_payload()
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["ok"] = True
            return payload
    except Exception as e:
        print(f"[ufc_live] chart api failed: {e}", flush=True)
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "empty payload"}
