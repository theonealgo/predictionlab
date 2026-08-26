"""Locked MLB results regressions — fail if a future change alters MLB behavior.

MLB LOCKED. Do not modify these expectations unless the user says UNLOCK MLB.
Moneyline strategy keys / queries are a regression check — do not change them.
"""
from collections import defaultdict
from datetime import datetime

from mlb_spread_pick import pick_spread_side
from sports.team_efficiency_attach import (
    _efficiency_spread_for_grading,
    apply_efficiency_ml_grading,
)


def test_mlb_efficiency_uses_our_spread_without_our_method():
    """H2H/PL our_spread must grade Efficiency even when our_method is unset."""
    g = {"our_spread": 1.0, "home_win": True, "home_score": 6, "away_score": 4}
    assert _efficiency_spread_for_grading(g, "MLB") == 1.0
    daily = {"2026-08-21": {"games": [g]}}
    apply_efficiency_ml_grading("MLB", daily)
    assert g["efficiency_prob"] is not None
    assert g["efficiency_correct"] is True


def test_mlb_efficiency_grades_pickem_zero_spread():
    g = {"our_spread": 0.0, "home_win": True, "home_score": 7, "away_score": 6}
    assert _efficiency_spread_for_grading(g, "MLB") == 0.0
    daily = {"2026-08-21": {"games": [g]}}
    apply_efficiency_ml_grading("MLB", daily)
    assert g.get("efficiency_correct") is True


def test_mlb_efficiency_does_not_use_faded_spread_pick():
    """Faded run-line dog (spread_pick=AWAY) must not become the Efficiency ML side."""
    g = {
        "our_spread": 2.0,
        "spread_pick": "AWAY",
        "home_win": True,
        "home_score": 6,
        "away_score": 4,
    }
    daily = {"2026-08-21": {"games": [g]}}
    apply_efficiency_ml_grading("MLB", daily)
    assert g["efficiency_pick"] == "home"
    assert g["efficiency_correct"] is True


def test_mlb_run_line_1_5_is_a_bet():
    picked = pick_spread_side(1.5, home="BAL", away="TB")
    assert picked["action"] == "BET"
    assert picked["side"] == "HOME"
    assert picked["line"] == -1.5


def test_mlb_ou_face_pct_matches_wl():
    """113-101 must display 52.8%, not a stale 55.4% total_pct."""
    import NHL77FINAL as N

    label, pct, wins, graded = N._pinned_market_side(
        {
            "total_correct": 113,
            "total_graded": 214,
            "total_pct": 55.4,
        },
        "total",
        "XSharp",
    )
    assert wins == 113
    assert graded == 214
    assert pct == 52.8
    assert label == "XSharp"


def test_mlb_last7_includes_calendar_start_date():
    """Yesterday 11:33 minus 6 days must not drop the start date's midnight games."""
    import NHL77FINAL as N

    daily = defaultdict(lambda: {"games": []})
    for dk, n in (
        ("2026-08-15", 15),
        ("2026-08-16", 15),
        ("2026-08-21", 15),
    ):
        for i in range(n):
            daily[dk]["games"].append(
                {
                    "home": f"Home{i}",
                    "away": f"Away{i}",
                    "ens_prob": 55.0,
                    "ens_correct": True,
                    "skip_grading": False,
                }
            )
    yesterday_dt = datetime(2026, 8, 21, 11, 33, 0)
    bundle = N._compute_results_tally_bundle(
        daily, yesterday_dt, sport="MLB",
    )
    assert bundle["weekly_tally_date_range"] == "2026-08-15 to 2026-08-21"
    assert bundle["weekly_tally_games"] == 45


def test_mlb_efficiency_counts_all_h2h_games_not_only_run_line_bets():
    """15 games with our_spread must all grade — not only the |spread|>=1.5 bets."""
    import NHL77FINAL as N

    games = []
    for i, sp in enumerate(
        (2.0, 0.0, 1.5, 1.0, 1.0, 1.0, 0.5, 2.5, -0.5, -2.5, -0.5, -0.5, 1.0, -1.0, 1.0)
    ):
        home_won = i % 3 != 0
        games.append(
            {
                "home": f"H{i}",
                "away": f"A{i}",
                "our_spread": sp,
                "home_score": 5 if home_won else 2,
                "away_score": 2 if home_won else 5,
                "home_win": home_won,
                "ens_prob": 55.0,
                "ens_correct": True,
            }
        )
    daily = {"2026-08-21": {"games": games}}
    N._grade_efficiency_ml_from_spread("MLB", daily)
    tally = N._tally_model_counts_from_games(games, sport="MLB")
    assert tally["efficiency"]["total"] == 15
    assert tally["ensemble"]["total"] == 15


def test_mlb_moneyline_tally_keys_unchanged():
    """Locked ML models stay on the same tally keys (regression check)."""
    import NHL77FINAL as N

    src = open(N.__file__, encoding="utf-8").read()
    assert "('glicko2',   'glicko2_correct', 'glicko2_prob')" in src
    assert "('trueskill', 'trueskill_correct', 'trueskill_prob')" in src
    assert "('elo',       'elo_correct', 'elo_prob')" in src
    assert "('xgboost',   'xgb_correct', 'xgb_prob')" in src
    assert "('ensemble',  'ens_correct', 'ens_prob')" in src
