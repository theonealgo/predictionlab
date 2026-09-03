"""MLB chart API — graded game rows for Spread/Totals tables.

Live ``mlb_results_ui.markets_from_live_html`` sets ``finals = []`` to avoid
regex cost on the cards page request path. That leaves chart view with
empty game tables.

Chart API / SSR must extract a bounded slice of result cards.
"""
from __future__ import annotations

from typing import Any

CHART_FINALS_LIMIT = 200


def extract_chart_finals(html: str, *, limit: int = CHART_FINALS_LIMIT) -> list[dict[str, Any]]:
    # Premerge: mlb_consensus_hub (staging team_tabbed extractors).
    from mlb_consensus_hub import _extract_game_rows, synthesize_missing_ml_models

    if not html:
        return []
    return synthesize_missing_ml_models(_extract_game_rows(html, limit=limit))
