"""
UFC — MMA fight card predictions & results.

Route shortcuts and ESPN scoreboard fetch for UFC bouts.
"""
from __future__ import annotations

from sports._individual_sport import fetch_espn_api_games, load_upcoming_games_with_dates
from sports._sport_base import main, register_shortcut

SPORT = 'UFC'
PICKS_SLUG = 'ufc-picks'
RESULTS_SLUG = 'ufc-results'
ESPN_DAYS_BACK = 7
ESPN_DAYS_FORWARD = 60
OFFSEASON_HINT = (
    'No UFC fights are scheduled in the current window. '
    'Check back on fight week when the next card is posted.'
)
ESPN_CONFIGS = [('mma', 'ufc', False)]


def register_routes(app) -> None:
    register_shortcut(app, '/ufc', PICKS_SLUG)


def fetch_api_games():
    """ESPN scoreboard for UFC fight cards."""
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
