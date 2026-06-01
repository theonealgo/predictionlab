"""PL moneyline on pick card face + soccer 3-way draw."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_pick_card_sets_pl_model_moneylines(nhl):
    card = {
        "home_team_id": "Boston Celtics",
        "away_team_id": "New York Knicks",
        "ensemble_prob": 63.1,
        "book_home_moneyline": -205,
        "book_away_moneyline": 170,
    }
    nhl._prepare_pred_card_display(card, sport="NBA")
    assert card.get("pl_model_home_ml") is not None
    assert card.get("pl_model_away_ml") is not None
    assert card["pl_model_home_ml"] < 0
    assert card["pl_model_away_ml"] > 0


def test_soccer_threeway_probs_from_binary(nhl):
    hw, dw, aw = nhl._soccer_threeway_probs(0.625, 0.25)
    assert hw == pytest.approx(0.5)
    assert dw == pytest.approx(0.25)
    assert aw == pytest.approx(0.25)


def test_soccer_pick_card_draw_and_pl_ml(nhl):
    card = {
        "home_team_id": "Arsenal",
        "away_team_id": "Chelsea",
        "ensemble_prob": 55.0,
        "draw_prob": 25.0,
        "home_win_prob": 42.5,
        "away_win_prob": 32.5,
        "xgb_prob": 55.0,
    }
    nhl._prepare_pred_card_display(card, sport="SOCCER")
    assert card.get("face_draw_prob") == pytest.approx(25.0)
    assert card.get("pl_model_draw_ml") is not None
    assert card.get("pl_model_home_ml") is not None
    assert card.get("pl_model_away_ml") is not None


def test_soccer_binary_probs_render_without_draw_fields(nhl):
    card = {
        "home_team_id": "Granada",
        "away_team_id": "Almeria",
        "xgb_prob": 58.0,
        "ensemble_prob": 55.0,
        "book_home_moneyline": -110,
        "book_away_moneyline": -110,
    }
    nhl._prepare_pred_card_display(card, sport="SOCCER")
    assert card.get("face_home_prob") == pytest.approx(58.0)
    assert card.get("face_away_prob") == pytest.approx(42.0)
    assert card.get("pl_model_home_ml") is not None
    assert card.get("pl_model_away_ml") is not None


def test_soccer_missing_model_data_no_fake_fifty_fifty(nhl):
    card = {
        "home_team_id": "Ceuta",
        "away_team_id": "Albacete",
        "elo_prob": 50.0,
        "xgb_prob": None,
        "ensemble_prob": None,
    }
    nhl._prepare_pred_card_display(card, sport="SOCCER")
    assert card.get("face_home_prob") is None
    assert card.get("face_away_prob") is None
    assert card.get("face_pick_confidence") is None
    assert card.get("pl_model_home_ml") is None
    assert card.get("pl_model_away_ml") is None


def test_soccer_ml_grading_draw_pick(nhl):
    info = {
        "glicko2_prob": 55.0,
        "ens_prob": 55.0,
    }
    nhl._apply_soccer_ml_grading(
        info,
        draw_dec=0.30,
        glicko2_prob=0.55,
        trueskill_prob=0.52,
        elo_prob=0.50,
        xgb_prob=0.48,
        ens_prob=0.55,
        home_won=None,
        is_draw=True,
    )
    assert info.get("skip_grading") is False
    assert info.get("draw_prob") == pytest.approx(30.0, abs=0.2)


def test_soccer_ml_grading_home_win(nhl):
    assert nhl._soccer_ml_pick_correct(55.0, 20.0, 25.0, True, False) is True
    assert nhl._soccer_ml_pick_correct(30.0, 20.0, 50.0, True, False) is False


def test_nba_unaffected_by_soccer_draw(nhl):
    card = {"ensemble_prob": 60.0, "home_team_id": "A", "away_team_id": "B"}
    nhl._prepare_pred_card_display(card, sport="NBA")
    assert "face_draw_prob" not in card or card.get("face_draw_prob") is None
    assert card.get("pl_model_home_ml") is not None
