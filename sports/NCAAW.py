"""
NCAAW — Women's college basketball predictions & results.

Route shortcuts and results-page delegation for NCAA Division I women's basketball.
"""
from __future__ import annotations

from sports._sport_base import main, register_shortcut

SPORT = 'NCAAW'
PICKS_SLUG = 'ncaaw-picks'
RESULTS_SLUG = 'ncaaw-results'


def register_routes(app) -> None:
    register_shortcut(app, '/ncaaw', PICKS_SLUG)


def render_sport_results_page(sport: str, *, season_start_dt=None):
    if sport != SPORT:
        return None
    return main()._render_daily_sport_results_page(sport, season_start_dt=season_start_dt)
