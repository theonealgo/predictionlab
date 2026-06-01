"""MLB spread fade at model-parameter level (picks + results)."""
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


def test_mlb_fade_spread_negates():
    import NHL77FINAL as N
    assert N._mlb_fade_spread(1.5) == pytest.approx(-1.5)
    assert N._mlb_fade_spread(-2.0) == pytest.approx(2.0)
    assert N._mlb_fade_spread(None) is None


def test_mlb_pick_card_shows_faded_spread(nhl):
    """our_spread home+ → faded away run line on pick card."""
    card = {
        "home_team_id": "Texas Rangers",
        "away_team_id": "Kansas City Royals",
        "our_spread": 1.5,
        "our_total": 8.5,
        "ensemble_prob": 62.0,
    }
    nhl._apply_mlb_spread_fade(card)
    nhl._prepare_pred_card_display(card, sport="MLB")
    assert card["our_spread"] == pytest.approx(-1.5)
    assert card["disp_pl_spread"] == pytest.approx(-1.5)
    assert _fmt_mlb_run_line(
        card["disp_pl_spread"], card["home_team_id"], card["away_team_id"],
    ) == "Kansas City Royals -1.5"
    assert card.get("spread_pick_label") == "Kansas City Royals -1.5"


def test_mlb_pick_card_faded_negative_model_spread(nhl):
    card = {
        "home_team_id": "Texas Rangers",
        "away_team_id": "Kansas City Royals",
        "our_spread": -1.5,
        "ensemble_prob": 38.5,
    }
    nhl._apply_mlb_spread_fade(card)
    nhl._prepare_pred_card_display(card, sport="MLB")
    assert card["disp_pl_spread"] == pytest.approx(1.5)
    assert _fmt_mlb_run_line(
        card["disp_pl_spread"], card["home_team_id"], card["away_team_id"],
    ) == "Texas Rangers -1.5"


def test_mlb_results_card_uses_same_faded_spread(nhl):
    """Results display uses faded spread (not raw model sign)."""
    card = {
        "home_team_id": "Texas Rangers",
        "away_team_id": "Kansas City Royals",
        "our_spread": 1.5,
        "home_score": 5,
        "away_score": 3,
        "ensemble_prob": 62.0,
    }
    nhl._apply_mlb_spread_fade(card)
    nhl._prepare_result_card_display(card, sport="MLB")
    assert card["disp_pl_spread"] == pytest.approx(-1.5)


def test_mlb_spread_fade_idempotent(nhl):
    card = {"xgb_spread": 2.0, "our_spread": 1.0}
    nhl._apply_mlb_spread_fade(card)
    first = card["xgb_spread"]
    nhl._apply_mlb_spread_fade(card)
    assert card["xgb_spread"] == first


def test_ncaab_spread_faded_via_batch(nhl):
    card = {"our_spread": 4.5, "xgb_spread": 3.5}
    nhl._apply_model_fades_batch("NCAAB", [card])
    assert card["our_spread"] == pytest.approx(-4.5)
    assert card["xgb_spread"] == pytest.approx(-3.5)


def test_soccer_spread_faded_via_batch(nhl):
    card = {"our_spread": -1.5, "xgb_spread": 0.5}
    nhl._apply_model_fades_batch("SOCCER", [card])
    assert card["our_spread"] == pytest.approx(1.5)
    assert card["xgb_spread"] == pytest.approx(-0.5)


def test_nhl_spread_not_faded(nhl):
    """NHL ATS grading is invariant under spread sign flip vs market line — skip fade."""
    card = {"xgb_spread": 1.5}
    nhl._apply_model_fades_batch("NHL", [card])
    assert card["xgb_spread"] == pytest.approx(1.5)


def test_wnba_ml_faded(nhl):
    card = {"ensemble_prob": 62.0, "ens_prob": 62.0}
    nhl._apply_ml_fade(card)
    assert card["ensemble_prob"] == pytest.approx(38.0)
    assert card["ens_prob"] == pytest.approx(38.0)


def test_mlb_ou_faded_around_book(nhl):
    card = {"xgb_total": 9.5, "our_total": 9.0, "book_total": 8.5}
    nhl._apply_ou_fade(card, market_total=8.5)
    assert card["xgb_total"] == pytest.approx(7.5)  # 2*8.5 - 9.5
    assert card["our_total"] == pytest.approx(8.0)   # 2*8.5 - 9.0


def test_nba_spread_not_faded(nhl):
    card = {"our_spread": 4.5, "xgb_spread": 3.5}
    nhl._apply_model_fades_batch("NBA", [card])
    assert card["our_spread"] == pytest.approx(4.5)
    assert card["xgb_spread"] == pytest.approx(3.5)


def test_enforce_pick_spread_consistency_fixes_nba_contradiction(nhl):
    card = {
        "home_team_id": "New York Knicks",
        "away_team_id": "Cleveland Cavaliers",
        "our_spread": 8.0,
        "ensemble_prob": 38.0,
        "predicted_winner": "Cleveland Cavaliers",
    }
    nhl._enforce_pick_spread_consistency(card, sport="NBA")
    nhl._prepare_pred_card_display(card, sport="NBA")
    assert card["ensemble_prob"] > 50.0
    assert card["predicted_winner"] == "New York Knicks"
    assert card["disp_pl_spread"] > 0


def test_pred_card_pl_spread_ignores_pre_enforce_prob(nhl):
    """Display spread must align to enforced/visible PL probability, not stale pre-enforce."""
    card = {
        "home_team_id": "Seattle Storm",
        "away_team_id": "Las Vegas Aces",
        "our_spread": 6.0,  # home favorite in card convention
        "ensemble_prob": 64.0,  # enforced probability
        "_ensemble_prob_pre_enforce": 42.0,  # stale pre-correction value
    }
    nhl._prepare_pred_card_display(card, sport="WNBA")
    assert card["disp_pl_spread"] == pytest.approx(6.0)
    assert card.get("face_pick_team") == "Seattle Storm"


def test_mlb_daily_grading_uses_faded_xgb_spread(nhl, monkeypatch):
    """Positive raw xgb_spread → faded → away run-line pick in results grading."""
    monkeypatch.setattr(nhl, "_get_xgb_spread_model", lambda _s: None)
    monkeypatch.setattr(nhl, "_score_predictor_instance", lambda _s: None)
    monkeypatch.setattr(nhl, "_attach_h2h_projection_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(nhl, "_attach_nba_efficiency_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(nhl, "get_db_connection", lambda: _FakeConn())

    daily = {
        "2026-05-01": {
            "games": [{
                "home": "Texas Rangers",
                "away": "Kansas City Royals",
                "date": "2026-05-01",
                "home_score": 2,
                "away_score": 5,
                "xgb_spread": 1.5,
            }],
        },
    }
    stats = nhl._compute_spread_total_for_daily("MLB", daily)
    g = daily["2026-05-01"]["games"][0]
    assert g["xgb_spread"] == pytest.approx(-1.5)
    assert "Royals" in (g.get("spread_pick_label") or "")
    assert stats["spread_graded"] == 1


class _FakeConn:
    def execute(self, *a, **k):
        return self

    def fetchall(self):
        return []

    def close(self):
        pass


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
