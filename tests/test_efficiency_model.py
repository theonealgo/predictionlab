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


def test_pick_card_efficiency_from_ensemble_fallback(nhl):
    from sports.team_efficiency_attach import fill_efficiency_spread_on_predictions

    card = {
        "home_team_id": "Arsenal",
        "away_team_id": "Chelsea",
        "ensemble_prob": 58.0,
    }
    fill_efficiency_spread_on_predictions("SOCCER", [card])
    nhl._prepare_pred_card_display(card, sport="SOCCER")
    assert card.get("efficiency_prob") is not None
    assert card["efficiency_prob"] >= 50.0


def test_pick_card_efficiency_nhl_from_ensemble_fallback(nhl):
    from sports.team_efficiency_attach import fill_efficiency_spread_on_predictions

    card = {
        "home_team_id": "Boston Bruins",
        "away_team_id": "Toronto Maple Leafs",
        "ensemble_prob": 62.0,
    }
    fill_efficiency_spread_on_predictions("NHL", [card])
    nhl._prepare_pred_card_display(card, sport="NHL")
    assert card.get("efficiency_prob") is not None
    assert card["efficiency_prob"] >= 50.0


def test_efficiency_ml_grading(nhl):
    from sports.team_efficiency_attach import apply_efficiency_ml_grading

    daily = {
        "2026-05-01": {
            "games": [{
                "home": "New York Knicks",
                "away": "Boston Celtics",
                "home_score": 110,
                "away_score": 105,
                "home_win": True,
                "efficiency_spread": 4.0,
            }],
        },
    }
    apply_efficiency_ml_grading("NBA", daily)
    g = daily["2026-05-01"]["games"][0]
    assert g["efficiency_prob"] is not None
    assert g["efficiency_prob"] >= 50.0
    assert g["efficiency_pick"] == "home"
    assert g["efficiency_correct"] is True


def test_efficiency_ml_grading_our_spread_fallback():
    from sports.team_efficiency_attach import apply_efficiency_ml_grading

    daily = {
        "2026-05-01": {
            "games": [{
                "home": "A",
                "away": "B",
                "home_score": 98,
                "away_score": 102,
                "home_win": False,
                "our_spread": -3.5,
                "our_method": "efficiency",
            }],
        },
    }
    apply_efficiency_ml_grading("NBA", daily)
    g = daily["2026-05-01"]["games"][0]
    assert g["efficiency_prob"] is not None
    assert g["efficiency_prob"] < 50.0
    assert g["efficiency_pick"] == "away"
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


def test_nba_snapshot_efficiency_total_positive():
    import json
    from pathlib import Path
    import NHL77FINAL as N

    snap_path = Path(N._all_sports_snapshot_dir()) / "NBA_2025-26_regular.json"
    if not snap_path.is_file():
        pytest.skip("NBA season snapshot not built yet")
    with snap_path.open(encoding="utf-8") as fh:
        snap = json.load(fh)
    eff = (snap.get("overall_stats") or {}).get("efficiency") or {}
    total = int(eff.get("total") or 0)
    assert total > 1000, eff
    assert float(eff.get("accuracy") or 0) > 50.0


def test_nba_overall_stats_efficiency_after_spread_attach(nhl, monkeypatch):
    """NBA results page must tally efficiency after spread/efficiency attach."""
    from sports import team_efficiency_attach as eff

    daily = {
        "2026-05-01": {
            "games": [{
                "home": "A",
                "away": "B",
                "home_score": 110,
                "away_score": 100,
                "home_win": True,
                "glicko2_prob": 55.0,
                "glicko2_correct": True,
            }],
        },
    }

    def _fake_attach(sport, dr):
        g = dr["2026-05-01"]["games"][0]
        g["efficiency_spread"] = 4.0
        eff.apply_efficiency_ml_grading(sport, dr)

    monkeypatch.setattr(nhl, "_compute_spread_total_for_daily", lambda *a, **k: None)
    monkeypatch.setattr(eff, "attach_efficiency_to_daily_results", _fake_attach)
    monkeypatch.setattr(eff, "apply_efficiency_ml_grading", eff.apply_efficiency_ml_grading)

    stats_before = nhl.compute_overall_stats_from_daily(daily)
    assert stats_before["efficiency"]["total"] == 0
    _fake_attach("NBA", daily)
    stats_after = nhl.compute_overall_stats_from_daily(daily)
    assert stats_after["efficiency"]["total"] == 1


def test_efficiency_sports_set():
    from sports.team_efficiency_attach import EFFICIENCY_SPORTS, EFFICIENCY_GRADING_SPORTS
    assert "NBA" in EFFICIENCY_SPORTS
    assert "NHL" in EFFICIENCY_SPORTS
    assert "SOCCER" not in EFFICIENCY_SPORTS
    assert "SOCCER" in EFFICIENCY_GRADING_SPORTS
    assert "NCAAW" in EFFICIENCY_GRADING_SPORTS


def test_soccer_efficiency_fallback_from_ensemble_only():
    """Soccer grades efficiency from ensemble prob alone (no book line required)."""
    from sports.team_efficiency_attach import (
        fill_efficiency_spread_fallback,
        apply_efficiency_ml_grading,
    )

    daily = {
        "2026-05-01": {
            "games": [{
                "home": "Arsenal",
                "away": "Chelsea",
                "home_score": 2,
                "away_score": 1,
                "home_win": True,
                "ens_prob": 58.0,
            }],
        },
    }
    fill_efficiency_spread_fallback("SOCCER", daily)
    apply_efficiency_ml_grading("SOCCER", daily)
    g = daily["2026-05-01"]["games"][0]
    assert g.get("efficiency_spread") is not None
    assert g.get("efficiency_prob") is not None
    assert g.get("efficiency_correct") is True


def test_soccer_efficiency_fallback_from_ensemble_spread():
    from sports.team_efficiency_attach import (
        fill_efficiency_spread_fallback,
        apply_efficiency_ml_grading,
    )

    daily = {
        "2026-05-01": {
            "games": [{
                "home": "Arsenal",
                "away": "Chelsea",
                "home_score": 2,
                "away_score": 1,
                "home_win": True,
                "book_spread": -0.5,
                "ens_prob": 58.0,
            }],
        },
    }
    fill_efficiency_spread_fallback("SOCCER", daily)
    apply_efficiency_ml_grading("SOCCER", daily)
    g = daily["2026-05-01"]["games"][0]
    assert g.get("efficiency_spread") is not None
    assert g.get("efficiency_prob") is not None
    assert g.get("efficiency_correct") is True


def test_snapshot_build_efficiency_soccer_mock(nhl, monkeypatch):
    """Low-data sport gets non-zero efficiency totals in season snapshot path."""
    from sports import team_efficiency_attach as eff

    daily = {
        "2026-05-01": {
            "games": [{
                "home": "A",
                "away": "B",
                "home_score": 1,
                "away_score": 0,
                "home_win": True,
                "book_spread": 0.0,
                "ens_prob": 55.0,
                "glicko2_prob": 55.0,
                "glicko2_correct": True,
            }],
        },
    }
    monkeypatch.setattr(nhl, '_fill_pl_model_lines_for_results', lambda *a, **k: None)
    monkeypatch.setattr(
        eff, 'attach_efficiency_to_daily_results', lambda *a, **k: None,
    )
    eff.grade_efficiency_for_daily_results("SOCCER", daily)
    stats = nhl.compute_overall_stats_from_daily(daily)
    assert stats["efficiency"]["total"] == 1
    assert stats["efficiency"]["correct"] == 1
