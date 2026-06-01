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

    assert bundle['results_stale_notice'] is True
    assert bundle['weekly_tally_games'] == 1
    assert bundle['weekly_tally_date_range'] == '2026-05-06 to 2026-05-12'
    assert bundle['daily_tally_date'] == '2026-05-12'
    assert bundle['daily_tally']['ensemble']['total'] == 1


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
