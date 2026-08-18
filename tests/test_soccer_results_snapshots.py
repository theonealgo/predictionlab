"""Soccer results must use pre-game snapshots, not leaked finals."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_soccer_xg_leaks_actual_detects_final_and_swap(nhl):
    assert nhl._soccer_xg_leaks_actual(2, 4, 2, 4) is True
    assert nhl._soccer_xg_leaks_actual(4, 2, 2, 4) is True
    assert nhl._soccer_xg_leaks_actual(0, 1, 1, 0) is True
    assert nhl._soccer_xg_leaks_actual(1.4, 1.1, 2, 4) is False
    assert nhl._soccer_xg_leaks_actual(1.5, 1.0, 2, 3) is False


def test_apply_market_lines_rejects_dumped_final_pts(nhl):
    g = {
        'home': 'Al Sadd',
        'away': 'Shabab Al-Ahli',
        'home_score': 4,
        'away_score': 2,
        'our_home_pts': 4,
        'our_away_pts': 2,
    }
    assert nhl._apply_soccer_model_market_lines(g) is False
    assert g.get('our_total') != 6


def test_historical_card_does_not_stamp_final_as_projection(nhl):
    g = {
        'home': 'Al Sadd',
        'away': 'Shabab Al-Ahli',
        'home_team_id': 'Al Sadd',
        'away_team_id': 'Shabab Al-Ahli',
        'date': '2025-12-23',
        'home_score': 4,
        'away_score': 2,
        'ens_prob': 55.0,
        'ensemble_prob': 55.0,
        'xgb_prob': 58.0,
        'draw_prob': 26.0,
        'home_win_prob': 42.0,
        'away_win_prob': 32.0,
        'v2_expected_home': 1.4,
        'v2_expected_away': 1.1,
        'book_spread': -0.5,
        'book_total': 2.5,
        'league': 'AFC Champions League Elite',
    }
    nhl._apply_soccer_model_market_lines(g)
    nhl._prepare_result_card_display(g, 'SOCCER')
    assert g.get('pl_proj_home_pts') == pytest.approx(1.5) or g.get('pl_proj_home_pts') == pytest.approx(1.4)
    assert g.get('pl_proj_away_pts') != 2 or g.get('pl_proj_home_pts') != 4
    assert g.get('disp_pl_total') != 6
    assert g.get('disp_xs_total') != 6
    assert g.get('pl_proj_home_pts') != 4 or g.get('pl_proj_away_pts') != 2
    assert g.get('xs_proj_home_pts') != 2 or g.get('xs_proj_away_pts') != 4
    assert g.get('spread_pick_reason') != 'model score unavailable'
    assert g.get('disp_pl_total') == pytest.approx(2.5)
    # Same pre-game xG for PL and XSharp — not a home/away swap of the final.
    assert g.get('pl_proj_home_pts') == g.get('xs_proj_home_pts')
    assert g.get('pl_proj_away_pts') == g.get('xs_proj_away_pts')


def test_leaked_final_projection_is_stripped(nhl):
    g = {
        'home': 'Al Ittihad',
        'away': 'Nasaf Qarshi',
        'home_score': 1,
        'away_score': 0,
        'our_total': 1,
        'xgb_total': 1,
        'pl_proj_home_pts': 1,
        'pl_proj_away_pts': 0,
        'xs_proj_home_pts': 0,
        'xs_proj_away_pts': 1,
        'disp_pl_total': 1,
        'disp_xs_total': 1,
    }
    nhl._soccer_sanitize_result_lines(g)
    assert g.get('disp_pl_total') is None
    assert g.get('pl_proj_home_pts') is None
    assert g.get('xs_proj_home_pts') is None


def test_align_does_not_swap_completed_xg(nhl):
    eh, ea = nhl._soccer_align_expected_to_card_ml(
        1.4, 1.1,
        {'home_score': 1, 'away_score': 0, 'ensemble_prob': 35.0},
    )
    assert eh == pytest.approx(1.4)
    assert ea == pytest.approx(1.1)


def test_stored_expected_goals_used_when_present(nhl):
    row = {
        'expected_home_score': 1.35,
        'expected_away_score': 1.05,
        'home_score': 4,
        'away_score': 2,
    }
    snap = nhl._soccer_stored_expected_goals(row)
    assert snap['expected_home_score'] == pytest.approx(1.35)
    assert snap['expected_away_score'] == pytest.approx(1.05)
    assert nhl._soccer_stored_expected_goals({'home_score': 4, 'away_score': 2}) is None
