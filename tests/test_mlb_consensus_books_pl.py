"""Standalone PL vs Sportsbook chart (not consensus combos)."""

from mlb_consensus_hub import (
    _american_favorite_side,
    _pl_vs_books_rows_from_finals,
    _pl_vs_books_slices,
    build_consensus_records_html,
    build_pl_vs_books_records_html,
)


def test_american_favorite_side():
    assert _american_favorite_side(-125, +104) == "HOME"
    assert _american_favorite_side(+104, -125) == "AWAY"
    assert _american_favorite_side(-110, -110) is None


def test_pl_vs_books_disagree_grades_pl():
    rows = _pl_vs_books_rows_from_finals(
        [
            {
                "game_id": "T1",
                "game_date": "2026-08-30",
                "home_team_id": "Chicago Cubs",
                "away_team_id": "Milwaukee Brewers",
                "home_score": 2,
                "away_score": 5,
                "book_home_moneyline": -125,
                "book_away_moneyline": 104,
                "pl_model_home_ml": 505,
                "pl_model_away_ml": -698,
            }
        ]
    )
    assert len(rows) == 1
    a = rows[0]
    assert a["book_side"] == "HOME"
    assert a["pl_side"] == "AWAY"
    assert a["book_grade"] == "LOSS"
    assert a["pl_grade"] == "WIN"
    slices = _pl_vs_books_slices(rows)
    assert slices["books_pl_disagree"] == ["WIN"]  # PL graded on split
    assert slices["books_pl_agree"] == []
    assert slices["books"] == ["LOSS"]
    assert slices["pl"] == ["WIN"]


def test_charts_separate_and_simple():
    g = {
        "game_id": "T2",
        "game_date": "2026-08-30",
        "home_team_id": "A",
        "away_team_id": "B",
        "home_score": 4,
        "away_score": 1,
        "book_home_moneyline": -150,
        "book_away_moneyline": 130,
        "pl_model_home_ml": -140,
        "pl_model_away_ml": 120,
        "models": {
            n: {"pick": "A", "side": "home", "prob": 55.0}
            for n in (
                "Grinder2",
                "Takedown",
                "Edge",
                "XSharp",
                "Sharp Consensus",
                "Efficiency",
            )
        },
    }
    cons = build_consensus_records_html([g], last_night_key="2026-08-30", sport="mlb")
    books = build_pl_vs_books_records_html([g], last_night_key="2026-08-30", sport="mlb")
    assert "Consensus Based Betting Records" in cons
    assert "all but" not in books
    assert "Agreement" not in books or "Signal" in books
    assert "PL vs Sportsbook" in books
    assert "Books favorite" in books
    assert "PL favorite" in books
    assert "PL vs Books disagree" in books
    assert "PL and Books agree" in books
    assert "Cons + PL vs Books" not in books
    assert "American sportsbook odds" in books
