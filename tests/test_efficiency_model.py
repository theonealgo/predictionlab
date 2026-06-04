"""Team Efficiency model on pick cards and results grading."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_pick_card_has_efficiency_row(nhl):
    card = {
        "home_team_id": "New York Knicks",
        "away_team_id": "San Antonio Spurs",
        "our_spread": 5.5,
        "ensemble_prob": 35.9,
        "our_method": "efficiency",
    }
    nhl._prepare_pred_card_display(card, sport="NBA")
    assert card.get("efficiency_prob") is not None
    assert card["efficiency_prob"] >= 50.0
    assert card["disp_pl_spread"] == pytest.approx(5.5)


def test_efficiency_prob_none_without_spread(nhl):
    card = {
        "home_team_id": "Team A",
        "away_team_id": "Team B",
        "ensemble_prob": 55.0,
    }
    nhl._prepare_pred_card_display(card, sport="SOCCER")
    assert card.get("efficiency_prob") is None


def test_efficiency_ml_grading(nhl):
    from src.team_efficiency_attach import apply_efficiency_ml_grading

    daily = {
        "2026-05-01": {
            "games": [{
                "home": "New York Knicks",
                "away": "Boston Celtics",
                "home_score": 110,
                "away_score": 105,
                "home_win": True,
                "our_spread": 4.0,
            }],
        },
    }
    apply_efficiency_ml_grading("NBA", daily)
    g = daily["2026-05-01"]["games"][0]
    assert g["efficiency_prob"] is not None
    assert g["efficiency_prob"] >= 50.0
    assert g["efficiency_correct"] is True


def test_overall_stats_includes_efficiency(nhl):
    daily = {
        "2026-05-01": {
            "games": [{
                "home": "A",
                "away": "B",
                "efficiency_prob": 62.0,
                "efficiency_correct": True,
                "glicko2_prob": 55.0,
                "glicko2_correct": True,
            }],
        },
    }
    stats = nhl.compute_overall_stats_from_daily(daily)
    assert "efficiency" in stats
    assert stats["efficiency"]["total"] == 1
    assert stats["efficiency"]["correct"] == 1


def test_efficiency_sports_set():
    from src.team_efficiency_attach import EFFICIENCY_SPORTS
    assert "NBA" in EFFICIENCY_SPORTS
    assert "NHL" in EFFICIENCY_SPORTS
    assert "SOCCER" not in EFFICIENCY_SPORTS
