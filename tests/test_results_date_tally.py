"""Results page date normalization and tally fallback guards."""
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_normalize_game_date_key_mixed_formats():
    import NHL77FINAL as N

    assert N._normalize_game_date_key('2026-05-12') == '2026-05-12'
    assert N._normalize_game_date_key('31/08/2025') == '2025-08-31'
    assert N._normalize_game_date_key('31/10/2025 00:15') == '2025-10-31'


def test_recent_result_dates_uses_parsed_dates_not_strings():
    import NHL77FINAL as N

    daily = {
        '2026-05-29': {'games': [{'id': 1}]},
        '31/08/2025': {'games': [{'id': 2}]},
    }
    recent = N._recent_result_dates(
        daily, yesterday='2026-05-30', limit=7, recent_window_days=21,
    )
    assert recent == ['2026-05-29']


def test_compute_results_tally_bundle_falls_back_to_latest_week():
    import NHL77FINAL as N

    daily = defaultdict(lambda: {'games': []})
    daily['2026-05-12']['games'].append({
        'skip_grading': False,
        'ens_prob': 55.0,
        'ens_correct': True,
        'elo_prob': 52.0,
        'elo_correct': True,
        'xgb_prob': 48.0,
        'xgb_correct': False,
    })
    yesterday_dt = datetime(2026, 5, 30)
    bundle = N._compute_results_tally_bundle(daily, yesterday_dt)

    # Calendar yesterday empty → fall back to latest gradable slate (stale notice on)
    assert bundle['results_stale_notice'] is True
    assert bundle['weekly_tally_games'] == 1
    assert bundle['weekly_tally_date_range'] == '2026-05-06 to 2026-05-12'
    assert bundle['daily_tally_date'] == '2026-05-12'
    assert bundle['daily_tally']['ensemble']['total'] == 1


def test_compute_results_tally_bundle_skips_exhibition_only_day():
    """WNBA All-Star (TEAM SPOON @ TEAM COOP) must not become Last Night."""
    import NHL77FINAL as N

    daily = defaultdict(lambda: {'games': []})
    daily['2026-07-25']['games'].append({
        'home': 'TEAM COOP',
        'away': 'TEAM SPOON',
        'ens_prob': 50.0,
        'ens_correct': False,
        'elo_prob': 50.0,
        'elo_correct': False,
        'xgb_prob': 50.0,
        'xgb_correct': False,
        'skip_grading': False,
    })
    daily['2026-07-22']['games'].append({
        'home': 'Indiana Fever',
        'away': 'Connecticut Sun',
        'ens_prob': 55.0,
        'ens_correct': True,
        'elo_prob': 52.0,
        'elo_correct': True,
        'xgb_prob': 48.0,
        'xgb_correct': False,
        'skip_grading': False,
    })
    yesterday_dt = datetime(2026, 7, 27)
    bundle = N._compute_results_tally_bundle(daily, yesterday_dt, sport='WNBA')

    assert bundle['daily_tally_date'] == '2026-07-22'
    assert bundle['daily_tally_games'] == 1
    assert bundle['daily_tally']['ensemble']['total'] == 1
    assert bundle['weekly_tally_games'] == 1
    # Exhibition day must not appear in dated fallback
    dated = N._dated_games_in_daily_results(daily)
    assert [dk for _, dk in dated] == ['2026-07-22']


def test_sort_game_rows_by_date_desc_mixed_formats():
    import NHL77FINAL as N

    rows = [
        {'game_date': '31/08/2025', 'game_id': 'old'},
        {'game_date': '2026-05-29', 'game_id': 'new'},
    ]
    sorted_rows = N._sort_game_rows_by_date_desc(rows)
    assert sorted_rows[0]['game_id'] == 'new'


def test_nfl_sport_results_offseason_fallback_context(monkeypatch):
    import NHL77FINAL as N

    stale_date = '2020-01-01'
    weekly_results = {
        1: {
            'games': [
                {
                    'date': stale_date,
                    'skip_grading': False,
                    'home_score': 24,
                    'away_score': 17,
                    'glicko2_prob': None,
                    'glicko2_correct': None,
                    'trueskill_prob': None,
                    'trueskill_correct': None,
                    'elo_prob': 54.0,
                    'elo_correct': True,
                    'xgb_prob': 46.0,
                    'xgb_correct': False,
                    'ens_prob': 58.0,
                    'ens_correct': True,
                }
            ],
            'glicko2': {'correct': 0, 'total': 0, 'accuracy': 0.0},
            'trueskill': {'correct': 0, 'total': 0, 'accuracy': 0.0},
            'elo': {'correct': 1, 'total': 1, 'accuracy': 100.0},
            'xgboost': {'correct': 0, 'total': 1, 'accuracy': 0.0},
            'ensemble': {'correct': 1, 'total': 1, 'accuracy': 100.0},
        }
    }

    monkeypatch.setattr(N, 'update_nfl_scores', lambda: None)
    monkeypatch.setattr(N, 'update_espn_scores', lambda _sport: None)
    monkeypatch.setattr(N, 'calculate_nfl_weekly_performance', lambda: weekly_results)
    monkeypatch.setattr(N, '_attach_engine_odds_to_daily_results', lambda *_args, **_kwargs: None)

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(N, 'render_template_string', _fake_render)
    out = N.sport_results('NFL')

    assert out == "ok"
    # Offseason / empty yesterday → fallback slate (stale notice on)
    assert captured['results_stale_notice'] is True
    assert captured['daily_tally_date'] == stale_date
    assert captured['daily_tally_games'] == 1
    assert captured['weekly_tally_games'] == 1
    assert captured['weekly_tally']['glicko2']['total'] == 0
    assert captured['weekly_tally']['trueskill']['total'] == 0
    assert captured['weekly_tally']['elo']['total'] == 1
    assert captured['weekly_tally']['xgboost']['total'] == 1
    assert captured['weekly_tally']['ensemble']['total'] == 1
    assert captured['weekly_tally_date_range'].endswith(f"to {stale_date}")


def test_wnba_results_uses_real_model_columns_for_tallies_and_cards(monkeypatch):
    import NHL77FINAL as N

    row = {
        'game_id': 'WNBA_1',
        'game_date': '2026-05-30',
        'sport': 'WNBA',
        'home_team_id': 'Seattle Storm',
        'away_team_id': 'Las Vegas Aces',
        'home_score': 84,
        'away_score': 79,
        'league': 'WNBA',
        'elo_home_prob': 0.54,
        'xgboost_home_prob': 0.57,
        'logistic_home_prob': 0.56,
        'win_probability': 0.58,
        'meta_home_prob': 0.58,
        'catboost_home_prob': 0.61,
        'glicko_home_prob': 0.61,
        'trueskill_home_prob': 0.56,
    }

    class _Cursor:
        def fetchall(self):
            return [row]

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return _Cursor()

        def close(self):
            return None

    monkeypatch.setattr(N, 'update_espn_scores', lambda _sport: None)
    monkeypatch.setattr(N, 'get_db_connection', lambda: _Conn())
    monkeypatch.setattr(N, 'get_v2_prediction', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_attach_book_odds_to_daily_results', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_cache_market_lines_for_results', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_attach_engine_odds_to_daily_results', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_compute_spread_total_for_daily', lambda *_args, **_kwargs: {'spread_graded': 0, 'total_graded': 0})
    monkeypatch.setattr(N, '_finalize_daily_result_cards', lambda *_args, **_kwargs: None)

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(N, 'render_template_string', _fake_render)
    out = N.sport_results('WNBA')

    assert out == "ok"
    assert captured['overall_stats']['glicko2']['total'] == 1
    assert captured['overall_stats']['trueskill']['total'] == 1
    assert captured['overall_stats']['elo']['total'] == 1
    assert captured['overall_stats']['xgboost']['total'] == 1
    assert captured['overall_stats']['ensemble']['total'] == 1
    assert captured['daily_tally']['glicko2']['total'] == 1
    assert captured['weekly_tally']['trueskill']['total'] == 1
    first_date = captured['sorted_dates'][0]
    first_game = captured['daily_results'][first_date]['games'][0]
    assert first_game['glicko2_prob'] == 61.0
    assert first_game['trueskill_prob'] == 56.0


def test_nba_results_uses_stale_tally_bundle(monkeypatch):
    import NHL77FINAL as N

    stale_date = '2026-05-25'
    weekly_results = {
        1: {
            'games': [
                {
                    'date': stale_date,
                    'skip_grading': False,
                    'home_score': 110,
                    'away_score': 102,
                    'glicko2_prob': 58.0,
                    'glicko2_correct': True,
                    'trueskill_prob': 55.0,
                    'trueskill_correct': True,
                    'elo_prob': 54.0,
                    'elo_correct': True,
                    'xgb_prob': 52.0,
                    'xgb_correct': True,
                    'ens_prob': 57.0,
                    'ens_correct': True,
                }
            ],
            'glicko2': {'correct': 1, 'total': 1, 'accuracy': 100.0},
            'trueskill': {'correct': 1, 'total': 1, 'accuracy': 100.0},
            'elo': {'correct': 1, 'total': 1, 'accuracy': 100.0},
            'xgboost': {'correct': 1, 'total': 1, 'accuracy': 100.0},
            'ensemble': {'correct': 1, 'total': 1, 'accuracy': 100.0},
        }
    }

    monkeypatch.setattr(N, 'update_nba_scores', lambda: None)
    monkeypatch.setattr(N, 'calculate_nba_weekly_performance', lambda: weekly_results)
    monkeypatch.setattr(N, '_attach_book_odds_to_daily_results', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_cache_market_lines_for_results', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_attach_engine_odds_to_daily_results', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_compute_spread_total_for_daily', lambda *_args, **_kwargs: {'spread_graded': 0, 'total_graded': 0})
    monkeypatch.setattr(N, '_finalize_daily_result_cards', lambda *_args, **_kwargs: None)

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(N, 'render_template_string', _fake_render)
    out = N.sport_results('NBA')

    assert out == "ok"
    assert captured['results_stale_notice'] is True
    assert captured['daily_tally_date'] == stale_date
    assert captured['daily_tally_games'] == 1
    assert captured['weekly_tally_games'] == 1
    assert captured['weekly_tally']['glicko2']['total'] == 1
    assert captured['weekly_tally']['trueskill']['total'] == 1


def test_wnba_results_does_not_fabricate_model_probs_when_absent(monkeypatch):
    import NHL77FINAL as N

    row = {
        'game_id': 'WNBA_2',
        'game_date': '2026-05-30',
        'sport': 'WNBA',
        'home_team_id': 'Seattle Storm',
        'away_team_id': 'Las Vegas Aces',
        'home_score': 81,
        'away_score': 80,
        'league': 'WNBA',
        'elo_home_prob': None,
        'xgboost_home_prob': None,
        'logistic_home_prob': None,
        'win_probability': None,
        'meta_home_prob': None,
        'catboost_home_prob': None,
        'glicko_home_prob': None,
        'trueskill_home_prob': None,
    }

    class _Cursor:
        def fetchall(self):
            return [row]

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return _Cursor()

        def close(self):
            return None

    monkeypatch.setattr(N, 'update_espn_scores', lambda _sport: None)
    monkeypatch.setattr(N, 'get_db_connection', lambda: _Conn())
    monkeypatch.setattr(N, 'get_v2_prediction', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_attach_book_odds_to_daily_results', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_cache_market_lines_for_results', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_attach_engine_odds_to_daily_results', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(N, '_compute_spread_total_for_daily', lambda *_args, **_kwargs: {'spread_graded': 0, 'total_graded': 0})
    monkeypatch.setattr(N, '_finalize_daily_result_cards', lambda *_args, **_kwargs: None)

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(N, 'render_template_string', _fake_render)
    out = N.sport_results('WNBA')

    assert out == "ok"
    for key in ('glicko2', 'trueskill', 'elo', 'xgboost', 'ensemble'):
        assert captured['overall_stats'][key]['total'] == 0
        assert captured['daily_tally'][key]['total'] == 0
        assert captured['weekly_tally'][key]['total'] == 0
    first_date = captured['sorted_dates'][0]
    first_game = captured['daily_results'][first_date]['games'][0]
    assert first_game['glicko2_prob'] is None
    assert first_game['trueskill_prob'] is None
