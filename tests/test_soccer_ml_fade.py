"""Soccer moneyline fade: other side of the same ML, draws stay losses."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_pick_ml_side_home_away(nhl):
    assert nhl.pick_ml_side(62.0) == "home"
    assert nhl.pick_ml_side(38.0) == "away"
    assert nhl.pick_ml_side(55.0, 45.0) == "home"


def test_soccer_draw_without_draw_prob_still_grades_as_loss(nhl):
    info = {}
    nhl._apply_soccer_ml_grading(
        info,
        draw_dec=None,
        glicko2_prob=0.62,
        trueskill_prob=0.58,
        elo_prob=0.55,
        xgb_prob=0.60,
        ens_prob=0.57,
        home_won=None,
        is_draw=True,
    )
    assert info['skip_grading'] is False
    assert info['ens_correct'] is False
    assert info['glicko2_correct'] is False


def test_grade_ml_draw_stays_loss(nhl):
    assert nhl.grade_ml_side("home", None, True) is False
    assert nhl.grade_ml_side("away", None, True) is False


def test_soccer_ml_fade_inverts_named_models(nhl):
    card = {
        "glicko2_prob": 62.0,
        "trueskill_prob": 58.0,
        "elo_prob": 55.0,
        "xgb_prob": 60.0,
        "ens_prob": 57.0,
        "home_win_prob": 42.0,
        "away_win_prob": 33.0,
        "home": "Arsenal",
        "away": "Chelsea",
        "predicted_winner": "Arsenal",
    }
    nhl._apply_soccer_ml_fade(card, keys=(
        'glicko2_prob', 'trueskill_prob', 'elo_prob', 'xgb_prob', 'ens_prob',
    ))
    assert card["glicko2_prob"] == pytest.approx(38.0)
    assert card["ens_prob"] == pytest.approx(43.0)
    assert card["home_win_prob"] == pytest.approx(33.0)
    assert card["away_win_prob"] == pytest.approx(42.0)
    assert card["predicted_winner"] == "Chelsea"
    assert card.get("our_total") is None


def test_soccer_ml_fade_skips_models_not_in_keys(nhl):
    card = {
        "glicko2_prob": 62.0,
        "trueskill_prob": 58.0,
        "ens_prob": 57.0,
    }
    nhl._apply_soccer_ml_fade(card, keys=('glicko2_prob',))
    assert card["glicko2_prob"] == pytest.approx(38.0)
    assert card["trueskill_prob"] == pytest.approx(58.0)
    assert card["ens_prob"] == pytest.approx(57.0)


def test_record_accuracy_pct_never_100_on_1_2(nhl):
    assert nhl._record_accuracy_pct(1, 2) == 33.3
    assert nhl._record_accuracy_pct(1, 2, min_n=1) == 33.3
    assert nhl._record_accuracy_pct(1, 0) == 100.0
    assert nhl._record_accuracy_pct(0, 1) == 0.0
    assert nhl._record_accuracy_pct(1, 2, min_n=5) is None
    assert nhl._record_accuracy_pct(6, 4, min_n=5) == 60.0


def test_soccer_season_perf_pins_consensus(nhl):
    overall = {
        "glicko2": {"total": 100, "correct": 70, "accuracy": 70.0},
        "ensemble": {"total": 100, "correct": 63, "accuracy": 63.0},
    }
    st = {
        "spread_graded": 20, "spread_covered": 8, "spread_pct": 40.0,
        "pl_spread_graded": 20, "pl_spread_covered": 9, "pl_spread_pct": 45.0,
        "total_graded": 20, "total_correct": 12, "total_pct": 60.0,
    }
    perf = nhl._build_season_performance_summary(overall, st, sport="SOCCER")
    assert perf["ml_model_label"] == "Sharp Consensus"
    assert perf["ml_accuracy"] == 63.0
    assert perf["spread_model_label"] == "Prediction Lab"
    assert perf["spread_pct"] == 45.0


def test_soccer_pl_spread_not_faded_when_n_under_10(nhl):
    daily = {
        "2026-08-13": {
            "games": [
                {
                    "our_spread": -1.5,
                    "market_spread": -1.0,
                    "home_score": 2,
                    "away_score": 0,
                    "pl_spread_correct": False,
                }
            ]
        }
    }
    st = {"pl_spread_covered": 1, "pl_spread_graded": 3, "pl_spread_pushes": 0}
    out = nhl._maybe_fade_soccer_pl_spread(daily, st)
    assert out["pl_spread_graded"] == 3
    assert daily["2026-08-13"]["games"][0]["our_spread"] == -1.5
    assert nhl._soccer_should_fade_pl_spread() is False


def test_soccer_ml_fade_keys_from_stats_under_55(nhl):
    stats = {
        'glicko2': {'correct': 40, 'total': 100},
        'trueskill': {'correct': 58, 'total': 100},
        'elo': {'correct': 48, 'total': 100},
        'xgboost': {'correct': 56, 'total': 100},
        'ensemble': {'correct': 63, 'total': 100},
        'efficiency': {'correct': 50, 'total': 100},
    }
    keys, before = nhl._soccer_ml_fade_keys_from_stats(stats)
    assert 'glicko2_prob' in keys
    assert 'elo_prob' in keys
    assert 'efficiency_prob' in keys
    assert 'trueskill_prob' not in keys
    assert 'xgb_prob' not in keys
    assert 'ens_prob' not in keys
    assert before['glicko2']['fade'] is True
    assert before['ensemble']['fade'] is False


def test_soccer_fade_regrade_inverts_decisive_keeps_draw_loss(nhl):
    win_row = {
        "glicko2_prob": 38.0,
        "_unfaded_glicko2_prob": 62.0,
        "trueskill_prob": 42.0,
        "_unfaded_trueskill_prob": 58.0,
        "elo_prob": 45.0,
        "_unfaded_elo_prob": 55.0,
        "xgb_prob": 40.0,
        "_unfaded_xgb_prob": 60.0,
        "ens_prob": 43.0,
        "_unfaded_ens_prob": 57.0,
        "home_win": True,
        "is_draw": False,
        "_soccer_ml_faded": True,
    }
    nhl._recompute_daily_ml_grading_after_fade(win_row, "SOCCER")
    assert win_row["glicko2_correct"] is False
    assert win_row["skip_grading"] is False

    draw_row = {
        "glicko2_prob": 38.0,
        "_unfaded_glicko2_prob": 62.0,
        "trueskill_prob": 42.0,
        "_unfaded_trueskill_prob": 58.0,
        "elo_prob": 45.0,
        "_unfaded_elo_prob": 55.0,
        "xgb_prob": 40.0,
        "_unfaded_xgb_prob": 60.0,
        "ens_prob": 43.0,
        "_unfaded_ens_prob": 57.0,
        "home_win": None,
        "is_draw": True,
        "_soccer_ml_faded": True,
    }
    nhl._recompute_daily_ml_grading_after_fade(draw_row, "SOCCER")
    assert draw_row["glicko2_correct"] is False
    assert draw_row["skip_grading"] is False
