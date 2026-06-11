"""
TENNIS — ATP & WTA match predictions & results.

Route shortcuts and ESPN scoreboard fetch for individual matchups.
"""
from __future__ import annotations

from sports._individual_sport import fetch_espn_api_games, load_upcoming_games_with_dates
from sports._sport_base import main, register_shortcut

SPORT = 'TENNIS'
PICKS_SLUG = 'tennis-picks'
RESULTS_SLUG = 'tennis-results'
ESPN_DAYS_BACK = 3
ESPN_DAYS_FORWARD = 21
OFFSEASON_HINT = (
    'No tennis matches are scheduled in the current window. '
    'Check back when ATP or WTA tournaments are underway.'
)
ESPN_CONFIGS = [('tennis', 'atp', False), ('tennis', 'wta', False)]


def register_routes(app) -> None:
    register_shortcut(app, '/tennis', PICKS_SLUG)


def fetch_api_games():
    """ESPN scoreboard for ATP and WTA draws."""
    return fetch_espn_api_games(
        SPORT, ESPN_CONFIGS, tennis_groupings=True,
        days_back=ESPN_DAYS_BACK, days_forward=ESPN_DAYS_FORWARD,
    )


def load_upcoming_games():
    """Games with dates for get_upcoming_predictions delegation."""
    return load_upcoming_games_with_dates(
        SPORT, ESPN_CONFIGS, tennis_groupings=True,
        days_back=ESPN_DAYS_BACK, days_forward=ESPN_DAYS_FORWARD,
    )


def render_sport_results_page(sport: str, *, season_start_dt=None):
    if sport != SPORT:
        return None
    return main()._render_daily_sport_results_page(sport, season_start_dt=season_start_dt)
