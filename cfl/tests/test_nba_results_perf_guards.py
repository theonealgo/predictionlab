"""Guards against NBA results-page worker timeouts (502 / Bad Gateway)."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_nba_weekly_skips_live_v2_for_old_games(monkeypatch):
    import NHL77FINAL as N

    live_calls = []
    frozen_calls = []

    def _fake_live_v2(sport, home, away, game_date=None):
        live_calls.append((sport, home, away, game_date))
        return {
            'glicko2_prob': 0.55,
            'trueskill_prob': 0.54,
            'xgboost_prob': 0.56,
            'home_prob': 0.55,
        }

    def _fake_frozen_v2(sport, home, away, game_date=None):
        frozen_calls.append((sport, home, away, game_date))
        return {
            'glicko2_prob': 0.61,
            'trueskill_prob': 0.59,
            'xgboost_prob': 0.56,
            'home_prob': 0.58,
        }

    monkeypatch.setattr(N, 'get_v2_prediction', _fake_live_v2)
    monkeypatch.setattr(N, '_frozen_get_v2_prediction', _fake_frozen_v2)

    old_date = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
    row = {
        'game_id': 'NBA_test_old',
        'game_date': old_date,
        'home_team_id': 'Boston Celtics',
        'away_team_id': 'Los Angeles Lakers',
        'home_score': 110,
        'away_score': 105,
        'elo_home_prob': 0.6,
        'xgboost_home_prob': 0.58,
        'logistic_home_prob': 0.57,
        'win_probability': 0.59,
        'meta_home_prob': 0.59,
        'catboost_home_prob': None,
        'glicko_home_prob': None,
        'trueskill_home_prob': None,
        'predicted_total': None,
    }

    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [row]
    monkeypatch.setattr(N, 'get_db_connection', lambda: conn)
    monkeypatch.setattr(N, '_predictions_prob_select_sql', lambda _conn: (
        'p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability, '
        'p.catboost_home_prob, p.meta_home_prob, p.glicko_home_prob, p.trueskill_home_prob'
    ))
    monkeypatch.setattr(N, 'parse_date', lambda s: datetime.strptime(str(s)[:10], '%Y-%m-%d'))
    N._NBA_FROZEN_V2_RESULTS_CACHE.clear()

    result = N.calculate_nba_weekly_performance()
    assert result is not None
    assert live_calls == [], 'live v2 should not run for NBA season grading'
    assert len(frozen_calls) == 1
    game = result[1]['games'][0]
    assert game['glicko2_prob'] == 61.0
    assert game['trueskill_prob'] == 57.0
    assert game['ens_prob'] == 59.0


def test_compute_spread_total_skips_heavy_models_for_large_batches(monkeypatch):
    import NHL77FINAL as N
    from collections import defaultdict

    monkeypatch.setattr(N, '_attach_h2h_projection_to_daily_results', lambda *a, **k: None)
    monkeypatch.setattr(N, '_attach_nba_efficiency_to_daily_results', lambda *a, **k: None)
    monkeypatch.setattr(N, 'get_db_connection', lambda: MagicMock(
        execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[]))),
        close=MagicMock(),
    ))

    get_xgb = MagicMock()
    monkeypatch.setattr(N, '_get_xgb_spread_model', get_xgb)

    daily = defaultdict(lambda: {'games': []})
    for i in range(501):
        daily['2026-01-01']['games'].append({
            'game_id': f'NBA_{i}',
            'date': '2026-01-01',
            'home': 'A',
            'away': 'B',
            'home_score': 100,
            'away_score': 98,
        })

    N._compute_spread_total_for_daily('NBA', daily)
    get_xgb.assert_not_called()
