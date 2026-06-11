"""
WNBA — Women's professional basketball predictions & results.

Route shortcuts and results-page delegation for the WNBA.
Efficiency projections for WNBA picks still run via NBA helpers in main.
"""
from __future__ import annotations

from sports._sport_base import main, register_shortcut

SPORT = 'WNBA'
PICKS_SLUG = 'wnba-picks'
RESULTS_SLUG = 'wnba-results'


def register_routes(app) -> None:
    register_shortcut(app, '/wnba', PICKS_SLUG)


def render_sport_results_page(sport: str, *, season_start_dt=None):
    if sport != SPORT:
        return None
    return main()._render_daily_sport_results_page(sport, season_start_dt=season_start_dt)
