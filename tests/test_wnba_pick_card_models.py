"""WNBA pick cards should expose Grinder2/Takedown probs and ESPN team logos."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_wnba_team_logo_urls(nhl):
    assert nhl.team_logo_url('WNBA', 'Minnesota Lynx').endswith('/wnba/500/min.png')
    assert nhl.team_logo_url('WNBA', 'Golden State Valkyries').endswith('/wnba/500/gs.png')
    assert nhl.team_logo_url('WNBA', 'Unknown Team') == '/static/pl-logo.svg'


def test_wnba_v2_attaches_grinder2_takedown(nhl):
    v2 = nhl.get_v2_prediction(
        'WNBA', 'Minnesota Lynx', 'Golden State Valkyries', '2026-06-04',
    )
    assert v2 is not None
    assert v2.get('glicko2_prob') is not None
    assert v2.get('trueskill_prob') is not None
    assert 0.0 < v2['glicko2_prob'] < 1.0
    assert 0.0 < v2['trueskill_prob'] < 1.0


def test_wnba_upcoming_predictions_populates_model_rows(nhl, monkeypatch):
    nhl._PREDICTIONS_CACHE.clear()
    preds = nhl.get_upcoming_predictions('WNBA') or []
    if not preds:
        pytest.skip('No WNBA upcoming games in local data window')
    lynx = next(
        (
            p for p in preds
            if p.get('home_team_id') == 'Minnesota Lynx'
            and p.get('away_team_id') == 'Golden State Valkyries'
        ),
        None,
    )
    assert lynx is not None, 'expected Valkyries @ Lynx card'
    assert lynx.get('glicko2_prob') is not None
    assert lynx.get('trueskill_prob') is not None
    assert nhl.team_logo_url('WNBA', lynx['away_team_id']).endswith('/wnba/500/gs.png')
    assert nhl.team_logo_url('WNBA', lynx['home_team_id']).endswith('/wnba/500/min.png')


def test_wnba_grinder2_parameter_is_faded_before_grading(nhl):
    game = {
        'glicko2_prob': 60.0,
        'trueskill_prob': 52.0,
        'elo_prob': 49.0,
        'xgb_prob': 40.0,
        'ens_prob': 57.0,
        'home_win': False,
    }
    nhl._apply_model_fades_for_sport('WNBA', game)
    nhl._recompute_daily_ml_grading_after_fade(game, 'WNBA')
    assert game['glicko2_prob'] == 40.0
    assert game['glicko2_correct'] is True
    assert game['trueskill_prob'] == 48.0
    assert game['xgb_prob'] == 60.0
    assert game['ens_prob'] == 57.0
