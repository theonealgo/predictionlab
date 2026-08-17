"""MLB run-line sign + score-based −1.5 grading (no Elo retune)."""
from collections import defaultdict
from unittest.mock import MagicMock


def test_mlb_favorite_minus_1_5_not_dog_plus():
    import NHL77FINAL as N

    assert N._mlb_run_line_from_home_spread(0.4, "LAD", "MIL") == (
        "HOME",
        "LAD",
        -1.5,
    )
    assert N._mlb_run_line_from_home_spread(-0.4, "LAD", "MIL") == (
        "AWAY",
        "MIL",
        -1.5,
    )
    assert N._mlb_run_line_from_home_spread(0.0, "LAD", "MIL") is None
    assert N._mlb_run_line_from_home_spread(None, "LAD", "MIL") is None


def test_mlb_grade_minus_1_5_uses_scores():
    import NHL77FINAL as N

    # MIL @ LAD 6–2, pick away MIL −1.5 → cover
    assert N._mlb_grade_minus_1_5("AWAY", 2, 6) is True
    # Same game, pick home LAD −1.5 → no cover
    assert N._mlb_grade_minus_1_5("HOME", 2, 6) is False
    # If someone reads the chart as 2–6 away-home (wrong), away −1.5 would be Wrong
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
        our = -3.0 if i == 0 else (3.0 if i % 2 else -0.5)
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
                    "xgb_spread": -0.5,
                    "our_spread": -0.5,
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
    assert N._mlb_grade_minus_1_5("AWAY", 2, 6) is True


def test_all_same_side_checker_helper():
    import importlib.util
    from pathlib import Path

    helper = Path(__file__).resolve().parent / "mlb_run_line_check.py"
    spec = importlib.util.spec_from_file_location("mlb_run_line_check", helper)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fail_all_same_side = mod.fail_all_same_side
    fail_grade_mismatch = mod.fail_grade_mismatch

    picks = [{"side": "AWAY", "ok": True, "home_score": 2, "away_score": 6}] * 12
    assert fail_all_same_side(picks, min_n=10)
    mixed = picks[:6] + [{"side": "HOME", "ok": False, "home_score": 2, "away_score": 6}] * 6
    assert not fail_all_same_side(mixed, min_n=10)
    bad = [{"side": "AWAY", "ok": True, "home_score": 6, "away_score": 2}]
    assert fail_grade_mismatch(bad)
    good = [{"side": "AWAY", "ok": True, "home_score": 2, "away_score": 6}]
    assert not fail_grade_mismatch(good)
