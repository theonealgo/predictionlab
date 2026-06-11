"""
SOCCER — Soccer predictions & results.

Route shortcuts and results-page delegation for curated soccer leagues.
League filters and soccer model bundle logic remain in NHL77FINAL.py.
"""
from __future__ import annotations

from sports._sport_base import main, register_shortcut

SPORT = 'SOCCER'
PICKS_SLUG = 'soccer-picks'
RESULTS_SLUG = 'soccer-results'


def register_routes(app) -> None:
    register_shortcut(app, '/soccer', PICKS_SLUG)


def render_sport_results_page(sport: str, *, season_start_dt=None):
    if sport != SPORT:
        return None
    return main()._render_daily_sport_results_page(sport, season_start_dt=season_start_dt)
