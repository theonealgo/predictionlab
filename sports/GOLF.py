"""
GOLF — PGA Tour tournament predictions & results.

Route shortcuts and ESPN scoreboard fetch for tournament pairings.
"""
from __future__ import annotations

from sports._individual_sport import fetch_espn_api_games, load_upcoming_games_with_dates
from sports._sport_base import main, register_shortcut

SPORT = 'GOLF'
PICKS_SLUG = 'golf-picks'
RESULTS_SLUG = 'golf-results'
ESPN_DAYS_BACK = 7
ESPN_DAYS_FORWARD = 60
OFFSEASON_HINT = (
    'No PGA Tour events are scheduled in the current window. '
    'Check back when the next tournament tee times are posted.'
)
ESPN_CONFIGS = [('golf', 'pga', True)]


def register_routes(app) -> None:
    register_shortcut(app, '/golf', PICKS_SLUG)


def fetch_api_games():
    """ESPN scoreboard for PGA Tour tournament fields."""
    return fetch_espn_api_games(
        SPORT, ESPN_CONFIGS, days_back=ESPN_DAYS_BACK, days_forward=ESPN_DAYS_FORWARD,
    )


def load_upcoming_games():
    """Games with dates for get_upcoming_predictions delegation."""
    return load_upcoming_games_with_dates(
        SPORT, ESPN_CONFIGS, days_back=ESPN_DAYS_BACK, days_forward=ESPN_DAYS_FORWARD,
    )


def render_sport_results_page(sport: str, *, season_start_dt=None):
    if sport != SPORT:
        return None
    return main()._render_daily_sport_results_page(sport, season_start_dt=season_start_dt)
