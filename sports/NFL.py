"""
NFL — Football predictions & results.

This file holds NFL-specific page rendering and route shortcuts.
Shared grading, database access, and book odds stay in NHL77FINAL.py.
"""
from __future__ import annotations

from sports._sport_base import main, register_shortcut

SPORT = 'NFL'
PICKS_SLUG = 'nfl-picks'
RESULTS_SLUG = 'nfl-results'


def register_routes(app) -> None:
    register_shortcut(app, '/nfl', PICKS_SLUG)


def update_nfl_scores() -> None:
    return main().update_nfl_scores()


def render_sport_results_page(sport: str, *, season_start_dt=None):
    if sport != SPORT:
        return None
    return main()._render_nfl_results_page(sport, season_start_dt=season_start_dt)
