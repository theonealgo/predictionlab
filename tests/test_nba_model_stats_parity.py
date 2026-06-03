"""NBA results season/daily tallies should grade all five ML models on the same game set."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _make_game_row(game_date, *, glicko=None, trueskill=None, logistic=0.57, elo=0.54, xgb=0.56, ens=0.58):
    return {
        'game_id': f'NBA_{game_date}',
        'game_date': game_date,
        'home_team_id': 'Boston Celtics',
        'away_team_id': 'Los Angeles Lakers',
        'home_score': 110,
        'away_score': 105,
        'predicted_total': 220.0,
        'elo_home_prob': elo,
        'xgboost_home_prob': xgb,
        'logistic_home_prob': logistic,
        'win_probability': ens,
        'meta_home_prob': ens,
        'catboost_home_prob': None,
        'glicko_home_prob': glicko,
        'trueskill_home_prob': trueskill,
    }


def test_nba_weekly_uses_frozen_v2_for_old_games_without_live_v2(monkeypatch):
    import NHL77FINAL as N

    live_calls = []
    frozen_calls = []

    monkeypatch.setattr(
        N,
        'get_v2_prediction',
        lambda *args, **kwargs: live_calls.append(args) or None,
    )

    def _fake_frozen(sport, home, away, game_date=None):
        frozen_calls.append((sport, home, away, game_date))
        return {
            'glicko2_prob': 0.61,
            'trueskill_prob': 0.59,
            'xgboost_prob': 0.56,
            'home_prob': 0.58,
        }

    monkeypatch.setattr(N, '_frozen_get_v2_prediction', _fake_frozen)

    old_date = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
    row = _make_game_row(old_date)

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
    assert live_calls == [], 'live v2 must not run for NBA season grading'
    assert len(frozen_calls) == 1
    game = result[1]['games'][0]
    assert game['glicko2_prob'] == 61.0
    assert game['trueskill_prob'] == 57.0
    assert game['ens_prob'] == 58.0


def test_nba_weekly_prefers_stored_grinder_takedown_snapshots(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(
        N,
        '_frozen_get_v2_prediction',
        lambda *args, **kwargs: pytest.fail('frozen v2 should not run when DB snapshots exist'),
    )
    monkeypatch.setattr(N, 'get_v2_prediction', lambda *args, **kwargs: None)

    game_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    row = _make_game_row(game_date, glicko=0.62, trueskill=0.57)

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
    game = result[1]['games'][0]
    assert game['glicko2_prob'] == 62.0
    assert game['trueskill_prob'] == 57.0


def test_nba_season_stats_parity_against_edge(monkeypatch):
    """Grinder2/Takedown season totals should match Edge when frozen v2 fills historical gaps."""
    import NHL77FINAL as N

    monkeypatch.setattr(N, 'get_v2_prediction', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        N,
        '_frozen_get_v2_prediction',
        lambda sport, home, away, game_date=None: {
            'glicko2_prob': 0.55,
            'trueskill_prob': 0.54,
            'xgboost_prob': 0.56,
            'home_prob': 0.58,
        },
    )
    monkeypatch.setattr(N, 'update_nba_scores', lambda: None)
    monkeypatch.setattr(N, '_attach_book_odds_to_daily_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_cache_market_lines_for_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_attach_engine_odds_to_daily_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_compute_spread_total_for_daily', lambda *_a, **_k: {'spread_graded': 0, 'total_graded': 0})
    monkeypatch.setattr(N, '_finalize_daily_result_cards', lambda *_a, **_k: None)

    rows = [
        _make_game_row('2026-05-10'),
        _make_game_row('2026-05-11'),
        _make_game_row('2026-05-12'),
    ]
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    monkeypatch.setattr(N, 'get_db_connection', lambda: conn)
    monkeypatch.setattr(N, '_predictions_prob_select_sql', lambda _conn: (
        'p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability, '
        'p.catboost_home_prob, p.meta_home_prob, p.glicko_home_prob, p.trueskill_home_prob'
    ))
    monkeypatch.setattr(N, 'parse_date', lambda s: datetime.strptime(str(s)[:10], '%Y-%m-%d'))
    N._NBA_FROZEN_V2_RESULTS_CACHE.clear()

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(N, 'render_template_string', _fake_render)
    out = N.sport_results('NBA')
    assert out == 'ok'

    edge_total = captured['overall_stats']['elo']['total']
    assert captured['overall_stats']['glicko2']['total'] == edge_total
    assert captured['overall_stats']['trueskill']['total'] == edge_total
    assert captured['overall_stats']['xgboost']['total'] == edge_total
    assert captured['overall_stats']['ensemble']['total'] == edge_total
    assert captured['weekly_tally']['glicko2']['total'] == edge_total
    assert captured['weekly_tally']['trueskill']['total'] == edge_total
