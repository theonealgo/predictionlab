"""PL pick-card spread display: sign must match PL moneyline favorite."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def _fmt_mlb_run_line(spread, home, away):
    """Mirror fmt_spread_line(..., force_run_line=True) for MLB."""
    raw = float(spread)
    s = 1.5 if raw >= 0 else -1.5
    if s == 0:
        return "PK"
    if s > 0:
        return f"{home} -1.5"
    return f"{away} -1.5"


def test_set_card_pl_spread_aligns_away_ml_royals_rangers(nhl):
    """Royals @ Rangers: H2H our_spread home+ but ensemble away → PL shows Royals RL."""
    card = {
        "home_team_id": "Texas Rangers",
        "away_team_id": "Kansas City Royals",
        "our_spread": 1.5,
        "our_total": 8.5,
        "ensemble_prob": 38.5,
        "xgb_spread": -1.5,
        "xgb_prob": 38.5,
        "_ensemble_prob_pre_enforce": 38.5,
    }
    nhl._set_card_pl_spread(card, sport="MLB")
    assert card["disp_pl_spread"] == pytest.approx(-1.5)
    line = _fmt_mlb_run_line(card["disp_pl_spread"], card["home_team_id"], card["away_team_id"])
    assert line == "Kansas City Royals -1.5"


def test_prepare_pred_card_mlb_royals_rangers_pipeline(nhl):
    card = {
        "home_team_id": "Texas Rangers",
        "away_team_id": "Kansas City Royals",
        "our_spread": 0.8,
        "our_total": 8.5,
        "ensemble_prob": 38.5,
        "xgb_spread": -1.5,
        "xgb_prob": 38.5,
    }
    card["_ensemble_prob_pre_enforce"] = 38.5
    nhl._enforce_pick_spread_consistency(card, sport="MLB")
    nhl._prepare_pred_card_display(card, sport="MLB")
    assert card["disp_pl_spread"] < 0
    assert _fmt_mlb_run_line(
        card["disp_pl_spread"], card["home_team_id"], card["away_team_id"],
    ) == "Kansas City Royals -1.5"


def test_set_card_pl_spread_keeps_home_when_ml_agrees(nhl):
    card = {
        "home_team_id": "Texas Rangers",
        "away_team_id": "Kansas City Royals",
        "our_spread": 1.5,
        "ensemble_prob": 62.0,
    }
    nhl._set_card_pl_spread(card, sport="MLB")
    assert card["disp_pl_spread"] == pytest.approx(1.5)
    assert _fmt_mlb_run_line(
        card["disp_pl_spread"], card["home_team_id"], card["away_team_id"],
    ) == "Texas Rangers -1.5"


def test_disp_book_spread_matches_book_convention(nhl):
    """book_spread < 0 = home favored; disp_book_spread flips for fmt_spread_line."""
    card = {"book_spread": -1.5, "book_total": 8.5}
    nhl._set_card_book_lines(card)
    assert card["disp_book_spread"] == pytest.approx(1.5)
    assert _fmt_mlb_run_line(
        card["disp_book_spread"], "Texas Rangers", "Kansas City Royals",
    ) == "Texas Rangers -1.5"
