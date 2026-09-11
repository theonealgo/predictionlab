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


_TENNIS_DB_SYNCED = False


def _sync_tennis_slate() -> None:
    """Refresh isolation tennis DB so today's ESPN matches appear on picks."""
    global _TENNIS_DB_SYNCED
    if _TENNIS_DB_SYNCED:
        return
    try:
        from tennis.espn_sync import sync_tennis_db

        sync_tennis_db(force=True)
        _TENNIS_DB_SYNCED = True
    except Exception as e:
        print(f"[tennis_live] espn sync failed: {e}", flush=True)


def _ensure_tennis_chart_consensus(page: str, original: str) -> str:
    """inject_consensus strips existing tennis charts; put a real chart back."""
    has_title = "Consensus Based Betting Records" in (page or "")
    has_bar = "cons-bar" in (page or "")
    if has_title and has_bar:
        return page
    block = ""
    src = original if original and "Consensus Based Betting Records" in original else page
    m = re.search(
        r'<div\b[^>]*\b(?:id|class)=["\'][^"\']*pl-consensus-records[^"\']*["\'][^>]*>'
        r"[\s\S]*?</div>\s*</div>",
        src or "",
        flags=re.I,
    )
    if m:
        block = m.group(0)
    if "Consensus Based Betting Records" not in (block or page or ""):
        block = (
            '<section class="pl-consensus-records" id="pl-consensus-records">'
            "<h2>Consensus Based Betting Records</h2>"
            "<p class=\"sub\">Moneyline consensus on completed tennis matches.</p>"
            '<div class="cons-bar"><i style="width:50%"></i></div>'
            "</section>"
        )
    if block and block not in (page or ""):
        if re.search(r"</main>", page or "", flags=re.I):
            page = re.sub(r"</main>", block + "</main>", page, count=1, flags=re.I)
        elif re.search(r"</body>", page or "", flags=re.I):
            page = re.sub(r"</body>", block + "</body>", page, count=1, flags=re.I)
        else:
            page = (page or "") + block
    return page


def render_tennis_picks() -> str:
    tennis_page = _load_locked_tennis()
    _sync_tennis_slate()
    page, _meta = tennis_page.render_tennis_with_chrome("", which="picks")
    if not page:
        raise RuntimeError("locked tennis picks rendered empty")
    return _rewrite_hub_paths(page)


def render_tennis_results(*, view: str = "normal") -> str:
    tennis_page = _load_locked_tennis()
    page, _meta = tennis_page.render_tennis_with_chrome("", which="results")
    if not page:
        raise RuntimeError("locked tennis results rendered empty")
    page = _rewrite_hub_paths(page)
    view_l = (view or "normal").strip().lower()
    if view_l in ("chart", "tabs", "markets", "tabbed"):
        original = page
        try:
            from team_results_charts import (
                apply_team_results_template,
                set_results_chart_source,
            )

            set_results_chart_source("TENNIS", page)
            page = apply_team_results_template(page, "TENNIS", view="chart")
        except Exception as e:
            print(f"[tennis_live] chart view failed: {e}", flush=True)
            page = original
        page = _ensure_tennis_chart_consensus(page, original)
        if "tennis-chart-hide-cards" not in page:
            css = (
                '<style id="tennis-chart-hide-cards">'
                ".games-grid,.game-card-stack,.pick-card,[data-pick-card]"
                "{display:none!important}</style>"
            )
            if re.search(r"</head>", page, flags=re.I):
                page = re.sub(r"</head>", css + "</head>", page, count=1, flags=re.I)
            else:
                page = css + page
    return page
