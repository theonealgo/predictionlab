"""MLB run-line sign + score-based +1.5 fade grading (no Elo retune)."""
from collections import defaultdict
from unittest.mock import MagicMock


def test_mlb_favorite_is_minus_1_5():
    import NHL77FINAL as N

    assert N._mlb_run_line_from_home_spread(1.5, "LAD", "MIL") == (
        "HOME",
        "LAD",
        -1.5,
    )
    assert N._mlb_run_line_from_home_spread(-2.0, "LAD", "MIL") == (
        "AWAY",
        "MIL",
        -1.5,
    )
    assert N._mlb_run_line_from_home_spread(0.4, "LAD", "MIL") is None
    assert N._mlb_run_line_from_home_spread(-0.5, "LAD", "MIL") is None
    assert N._mlb_run_line_from_home_spread(0.0, "LAD", "MIL") is None
    assert N._mlb_run_line_from_home_spread(None, "LAD", "MIL") is None


def test_mlb_grade_minus_1_5_uses_scores():
    import NHL77FINAL as N

    # MIL @ LAD 6–2, pick away MIL +1.5 → cover
    assert N._mlb_grade_minus_1_5("AWAY", 2, 6) is True
    # Same game, pick home LAD +1.5 → no cover (lost by 4)
    assert N._mlb_grade_minus_1_5("HOME", 2, 6) is False
    # 1-run: +1.5 covers (would be a −1.5 loss on the same side)
    assert N._mlb_grade_minus_1_5("AWAY", 5, 4) is True
    assert N._mlb_grade_minus_1_5("HOME", 5, 4) is True
    # If someone reads the chart as 2–6 away-home (wrong), away +1.5 would be Wrong
    assert N._mlb_grade_minus_1_5("AWAY", 6, 2) is False


def test_skip_heavy_does_not_leak_first_spread(monkeypatch):
    """Full-season n>500 must not copy the first game's xs onto every card."""
    import NHL77FINAL as N

    monkeypatch.setattr(N, "_attach_h2h_projection_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(N, "_attach_nba_efficiency_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(N, "_apply_fades_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(
        N,
        "get_db_connection",
        lambda: MagicMock(
            execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[]))),
            close=MagicMock(),
        ),
    )
    monkeypatch.setattr(N, "_get_xgb_spread_model", MagicMock())

    daily = defaultdict(lambda: {"games": []})
    for i in range(501):
        our = -3.0 if i == 0 else (3.0 if i % 2 else -2.5)
        daily["2026-06-01"]["games"].append(
            {
                "game_id": f"MLB_{i}",
                "date": "2026-06-01",
                "home": f"Home{i}",
                "away": f"Away{i}",
                "home_score": 5 if i % 2 == 0 else 1,
                "away_score": 1 if i % 2 == 0 else 5,
                "our_spread": our,
                "our_total": 9.0,
            }
        )

    N._compute_spread_total_for_daily("MLB", daily, skip_efficiency=True)
    games = daily["2026-06-01"]["games"]
    sides = {g.get("spread_pick") for g in games if g.get("spread_pick")}
    assert sides == {"HOME", "AWAY"}, f"expected both sides after leak fix, got {sides}"
    assert games[0]["spread_pick"] == "AWAY"
    assert games[1]["spread_pick"] == "HOME"
    home_n = sum(1 for g in games if g.get("spread_pick") == "HOME")
    away_n = sum(1 for g in games if g.get("spread_pick") == "AWAY")
    assert home_n >= 10 and away_n >= 10


def test_displayed_correct_matches_score_grade(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(N, "_apply_fades_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(N, "_attach_h2h_projection_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(N, "_attach_nba_efficiency_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(N, "_get_xgb_spread_model", lambda _s: None)
    monkeypatch.setattr(N, "_score_predictor_instance", lambda _s: None)
    monkeypatch.setattr(
        N,
        "get_db_connection",
        lambda: MagicMock(
            execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[]))),
            close=MagicMock(),
        ),
    )

    daily = {
        "2026-08-16": {
            "games": [
                {
                    "game_id": "MLB_401816560",
                    "date": "2026-08-16",
                    "home": "Los Angeles Dodgers",
                    "away": "Milwaukee Brewers",
                    "home_score": 2,
                    "away_score": 6,
                    "xgb_spread": -2.5,
                    "our_spread": -2.5,
                    "our_total": 8.5,
                }
            ]
        }
    }

    N._compute_spread_total_for_daily("MLB", daily, skip_efficiency=True)
    g = daily["2026-08-16"]["games"][0]
    assert g["spread_pick"] == "AWAY"
    assert g["spread_pick_label"] == "Milwaukee Brewers -1.5"
    assert g["spread_correct"] is True
    assert g["pl_spread_correct"] is True
    assert N.grade_spread_cover("AWAY", 2, 6, line=-1.5) is True


def test_pickem_is_no_bet_not_forced(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(N, "_apply_fades_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(N, "_attach_h2h_projection_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(N, "_attach_nba_efficiency_to_daily_results", lambda *a, **k: None)
    monkeypatch.setattr(N, "_get_xgb_spread_model", lambda _s: None)
    monkeypatch.setattr(N, "_score_predictor_instance", lambda _s: None)
    monkeypatch.setattr(
        N,
        "get_db_connection",
        lambda: MagicMock(
            execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[]))),
            close=MagicMock(),
        ),
    )

    daily = {
        "2026-08-16": {
            "games": [
                {
                    "game_id": "MLB_PE",
                    "date": "2026-08-16",
                    "home": "Los Angeles Dodgers",
                    "away": "Milwaukee Brewers",
                    "home_score": 2,
                    "away_score": 6,
                    "our_spread": 0.5,
                    "our_total": 8.5,
                }
            ]
        }
    }
    stats = N._compute_spread_total_for_daily("MLB", daily, skip_efficiency=True)
    g = daily["2026-08-16"]["games"][0]
    assert g.get("spread_pick") in (None, "")
    assert g.get("spread_correct") is None
    assert stats.get("spread_graded", 0) == 0
    assert stats.get("pl_spread_graded", 0) == 0


def test_unify_checker_helpers():
    from tests.mlb_run_line_check import (
        fail_forced_bet_all_games,
        fail_last_night_not_subset_of_last7,
        fail_season_label_xsharp_spread,
    )

    assert fail_last_night_not_subset_of_last7(["a", "b"], ["a", "b", "c"]) == []
    assert fail_last_night_not_subset_of_last7(["a", "z"], ["a", "b"]) == ["z"]
    assert fail_season_label_xsharp_spread("XSharp")
    assert fail_season_label_xsharp_spread("XSharp run line")
    assert not fail_season_label_xsharp_spread("Prediction Lab")
    pickems = [{"our_spread": 0.4, "side": "HOME", "action": "BET"}] * 8
    edges = [{"our_spread": 2.5, "side": "AWAY", "action": "BET"}] * 4
    assert fail_forced_bet_all_games(pickems + edges)
    honest = [{"our_spread": 0.4, "side": None, "action": "NO BET"}] * 8 + edges
    assert not fail_forced_bet_all_games(honest)


def test_all_same_side_checker_helper():
    from tests.mlb_run_line_check import fail_all_same_side, fail_grade_mismatch

    picks = [{"side": "AWAY", "ok": True, "home_score": 2, "away_score": 6}] * 12
    assert fail_all_same_side(picks, min_n=10)
    mixed = picks[:6] + [{"side": "HOME", "ok": False, "home_score": 2, "away_score": 6}] * 6
    assert not fail_all_same_side(mixed, min_n=10)
    bad = [{"side": "AWAY", "ok": True, "home_score": 6, "away_score": 2}]
    assert fail_grade_mismatch(bad)
    good = [{"side": "AWAY", "ok": True, "home_score": 2, "away_score": 6}]
    assert not fail_grade_mismatch(good)
