"""
NCAAB — Men's college basketball predictions & results.

Route shortcuts and results-page delegation for NCAA Division I men's basketball.
"""
from __future__ import annotations

from sports._sport_base import main, register_shortcut

SPORT = 'NCAAB'
PICKS_SLUG = 'ncaab-picks'
RESULTS_SLUG = 'ncaab-results'


def register_routes(app) -> None:
    register_shortcut(app, '/ncaab', PICKS_SLUG)


def render_sport_results_page(sport: str, *, season_start_dt=None):
    if sport != SPORT:
        return None
    return main()._render_daily_sport_results_page(sport, season_start_dt=season_start_dt)
