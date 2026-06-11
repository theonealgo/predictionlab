"""
MLB — Baseball predictions & results.

Route shortcuts and results-page delegation for Major League Baseball.
Model grading and book lines remain in NHL77FINAL.py.
"""
from __future__ import annotations

from sports._sport_base import main, register_shortcut

SPORT = 'MLB'
PICKS_SLUG = 'mlb-picks'
RESULTS_SLUG = 'mlb-results'


def register_routes(app) -> None:
    register_shortcut(app, '/mlb', PICKS_SLUG)


def update_mlb_scores() -> None:
    return main().update_mlb_scores()


def render_sport_results_page(sport: str, *, season_start_dt=None):
    if sport != SPORT:
        return None
    return main()._render_daily_sport_results_page(sport, season_start_dt=season_start_dt)
