"""Grinder2/Takedown grading uses DB snapshots + frozen v2, not 21-day live v2."""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _make_row(sport, game_date, *, glicko=None, trueskill=None, logistic=None, elo=0.54, xgb=0.56, ens=0.58):
    return {
        'game_id': f'{sport}_{game_date}',
        'game_date': game_date,
        'home_team_id': 'New York Yankees',
        'away_team_id': 'Boston Red Sox',
        'home_score': 5,
        'away_score': 3,
        'elo_home_prob': elo,
        'xgboost_home_prob': xgb,
        'logistic_home_prob': logistic,
        'win_probability': ens,
        'meta_home_prob': ens,
        'catboost_home_prob': None,
        'glicko_home_prob': glicko,
        'trueskill_home_prob': trueskill,
    }


def test_mlb_grading_uses_frozen_v2_for_old_games(monkeypatch):
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
            'home_prob': 0.54,
        }

    monkeypatch.setattr(N, '_frozen_get_v2_prediction', _fake_frozen)

    old_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    row = _make_row('MLB', old_date)
    N._FROZEN_V2_RESULTS_GRADING_CACHE.clear()

    g2, ts, el, xg, ens = N._model_probs_for_grading(
        'MLB', row, row['home_team_id'], row['away_team_id'], old_date,
    )

    assert live_calls == []
    assert len(frozen_calls) == 1
    assert frozen_calls[0][0] == 'MLB'
    assert g2 == 0.61
    assert ts == 0.59
    assert ens == 0.58


def test_ncaab_banner_grading_skips_live_v2(monkeypatch):
    import NHL77FINAL as N

    live_calls = []
    monkeypatch.setattr(
        N,
        'get_v2_prediction',
        lambda *args, **kwargs: live_calls.append(args) or None,
    )
    monkeypatch.setattr(
        N,
        '_frozen_get_v2_prediction',
        lambda sport, home, away, game_date=None: {
            'glicko2_prob': 0.55,
            'trueskill_prob': 0.54,
            'xgboost_prob': 0.56,
            'home_prob': 0.53,
        },
    )

    old_date = (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d')
    row = _make_row('NCAAB', old_date)
    row['home_team_id'] = 'Duke'
    row['away_team_id'] = 'UNC'
    N._FROZEN_V2_RESULTS_GRADING_CACHE.clear()

    from collections import defaultdict
    from unittest.mock import MagicMock

    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [row]
    monkeypatch.setattr(N, 'get_db_connection', lambda: conn)
    monkeypatch.setattr(N, '_predictions_prob_select_sql', lambda _conn: (
        'p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability, '
        'p.catboost_home_prob, p.meta_home_prob, p.glicko_home_prob, p.trueskill_home_prob'
    ))

    start = datetime.now() - timedelta(days=60)
    end = datetime.now()
    daily = N._banner_daily_results_for_range('NCAAB', start, end, skip_v2=False)
    assert daily is not None
    assert live_calls == []
    game = next(iter(daily.values()))['games'][0]
    assert game['glicko2_prob'] == 55.0
    assert game['trueskill_prob'] == 54.0


def test_nhl_consensus_computed_when_win_probability_missing(monkeypatch):
    """Consensus ML grades full season when only component probs exist (not win_probability)."""
    import NHL77FINAL as N

    monkeypatch.setattr(N, 'get_v2_prediction', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        N,
        '_frozen_get_v2_prediction',
        lambda sport, home, away, game_date=None: {
            'glicko2_prob': 0.55,
            'trueskill_prob': 0.54,
            'xgboost_prob': 0.56,
            'home_prob': 0.53,
        },
    )

    row = _make_row('NHL', '2025-11-01', glicko=None, trueskill=None, ens=None, elo=0.52, xgb=0.51)
    row['win_probability'] = None
    row['meta_home_prob'] = None
    N._FROZEN_V2_RESULTS_GRADING_CACHE.clear()

    g2, ts, el, xg, ens = N._model_probs_for_grading(
        'NHL', row, row['home_team_id'], row['away_team_id'], '2025-11-01',
    )
    assert g2 == 0.55
    assert ts == 0.54
    assert ens is not None
    assert ens == N._compute_ensemble_prob(g2, ts, xg, el, fallback=None)
