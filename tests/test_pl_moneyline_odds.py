"""PL pick-card moneyline: model % → American odds with sane bounds."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_normalize_accepts_fraction_and_percent(nhl):
    assert nhl._normalize_home_win_prob_pct(0.55) == pytest.approx(55.0)
    assert nhl._normalize_home_win_prob_pct(55) == pytest.approx(55.0)
    assert nhl._normalize_home_win_prob_pct(99.5) == pytest.approx(99.0)


def test_compute_odds_55pct_home(nhl):
    ml = nhl._compute_odds_from_prob(55, apply_vig=False, clamp_ml=True)
    assert ml['moneyline_home'] == -122
    assert ml['moneyline_away'] == 122


def test_compute_odds_no_absurd_values(nhl):
    for raw in (0.55, 99.5, 99.2):
        ml = nhl._compute_odds_from_prob(raw, apply_vig=False, clamp_ml=True)
        assert ml is not None
        for side in ('moneyline_home', 'moneyline_away'):
            v = ml[side]
            assert nhl._PL_ML_CLAMP_MIN <= v <= nhl._PL_ML_CLAMP_MAX


def test_set_card_pl_moneylines_from_ensemble(nhl):
    card = {'ensemble_prob': 55.0}
    nhl._set_card_pl_moneylines(card)
    assert card['pl_model_home_ml'] == -122
    assert card['pl_model_away_ml'] == 122
