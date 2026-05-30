"""PL pick-card spread display: MLB negates model spread on picks only."""
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


def test_mlb_pick_card_flips_positive_model_spread(nhl):
    """our_spread home+ → display away run line (inverted model)."""
    card = {
        "home_team_id": "Texas Rangers",
        "away_team_id": "Kansas City Royals",
        "our_spread": 1.5,
        "our_total": 8.5,
        "ensemble_prob": 62.0,
    }
    nhl._prepare_pred_card_display(card, sport="MLB")
    assert card["disp_pl_spread"] == pytest.approx(-1.5)
    assert _fmt_mlb_run_line(
        card["disp_pl_spread"], card["home_team_id"], card["away_team_id"],
    ) == "Kansas City Royals -1.5"
    assert card.get("spread_pick_label") == "Kansas City Royals -1.5"


def test_mlb_pick_card_flips_negative_model_spread(nhl):
    card = {
        "home_team_id": "Texas Rangers",
        "away_team_id": "Kansas City Royals",
        "our_spread": -1.5,
        "ensemble_prob": 38.5,
    }
    nhl._prepare_pred_card_display(card, sport="MLB")
    assert card["disp_pl_spread"] == pytest.approx(1.5)
    assert _fmt_mlb_run_line(
        card["disp_pl_spread"], card["home_team_id"], card["away_team_id"],
    ) == "Texas Rangers -1.5"


def test_mlb_results_card_does_not_flip(nhl):
    """Completed-game display path keeps model spread sign."""
    card = {
        "home_team_id": "Texas Rangers",
        "away_team_id": "Kansas City Royals",
        "our_spread": 1.5,
        "home_score": 5,
        "away_score": 3,
        "ensemble_prob": 62.0,
    }
    nhl._prepare_result_card_display(card, sport="MLB")
    assert card["disp_pl_spread"] == pytest.approx(1.5)


def test_format_card_game_time_from_event_date(nhl):
    card = {"event_date": "2026-05-30T23:10:00Z"}
    assert nhl._format_card_game_time(card) == "7:10 PM ET"
    nhl._set_card_game_time(card)
    assert card["game_time"] == "7:10 PM ET"


def test_disp_book_spread_matches_book_convention(nhl):
    """book_spread < 0 = home favored; disp_book_spread flips for fmt_spread_line."""
    card = {"book_spread": -1.5, "book_total": 8.5}
    nhl._set_card_book_lines(card)
    assert card["disp_book_spread"] == pytest.approx(1.5)
    assert _fmt_mlb_run_line(
        card["disp_book_spread"], "Texas Rangers", "Kansas City Royals",
    ) == "Texas Rangers -1.5"
