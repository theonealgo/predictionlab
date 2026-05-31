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
