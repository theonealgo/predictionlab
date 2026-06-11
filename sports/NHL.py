"""
NHL — Hockey predictions & results.

This file holds NHL-specific page rendering and route shortcuts.
Shared grading, database access, and book odds stay in NHL77FINAL.py.
"""
from __future__ import annotations

from sports._sport_base import main, register_shortcut

SPORT = 'NHL'
PICKS_SLUG = 'nhl-picks'
RESULTS_SLUG = 'nhl-results'


def register_routes(app) -> None:
    register_shortcut(app, '/nhl', PICKS_SLUG)


def update_nhl_scores() -> None:
    """Fetch and update NHL scores (ESPN + NHL API)."""
    return main().update_nhl_scores()


def render_sport_results_page(sport: str, *, season_start_dt=None):
    if sport != SPORT:
        return None
    return main()._render_nhl_results_page(sport, season_start_dt=season_start_dt)
