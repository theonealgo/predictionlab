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


def render_tennis_picks() -> str:
    tennis_page = _load_locked_tennis()
    page, _meta = tennis_page.render_tennis_with_chrome("", which="picks")
    if not page:
        raise RuntimeError("locked tennis picks rendered empty")
    return _rewrite_hub_paths(page)


def render_tennis_results(*, view: str = "normal") -> str:
    tennis_page = _load_locked_tennis()
    page, _meta = tennis_page.render_tennis_with_chrome("", which="results")
    if not page:
        raise RuntimeError("locked tennis results rendered empty")
    return _rewrite_hub_paths(page)
