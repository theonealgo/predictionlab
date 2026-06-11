"""
NCAAF — College football predictions & results.

Route shortcuts and results-page delegation for NCAA Division I FBS football.
"""
from __future__ import annotations

from sports._sport_base import main, register_shortcut

SPORT = 'NCAAF'
PICKS_SLUG = 'ncaaf-picks'
RESULTS_SLUG = 'ncaaf-results'


def register_routes(app) -> None:
    register_shortcut(app, '/ncaaf', PICKS_SLUG)


def render_sport_results_page(sport: str, *, season_start_dt=None):
    if sport != SPORT:
        return None
    return main()._render_daily_sport_results_page(sport, season_start_dt=season_start_dt)
