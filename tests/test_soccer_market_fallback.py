"""Soccer unknown-team fallback and model-line independence tests."""

from sports.SOCCER import market_informed_fallback


# ============================================================================
# UNKNOWN TOURNAMENT TEAM FALLBACK
# ============================================================================

def test_market_fallback_avoids_identical_fifty_percent_models():
    game = {
        "home_team_id": "Tournament Home",
        "away_team_id": "Tournament Away",
        "book_home_moneyline": -135,
        "book_away_moneyline": 115,
        "book_spread": -0.5,
        "book_total": 2.5,
    }

    result = market_informed_fallback(game)
    probs = {
        round(result[key], 4)
        for key in (
            "poisson_xg_prob",
            "poisson_reg_prob",
            "markov_prob",
            "elo_prob",
            "ensemble_prob",
        )
    }

    assert len(probs) > 1
    assert result["ensemble_prob"] != 0.5
    assert result["note"].startswith("Market-informed fallback")


def test_model_spread_is_not_a_raw_copy_of_book_spread():
    game = {
        "home_team_id": "Tournament Home",
        "away_team_id": "Tournament Away",
        "book_spread": -1.0,
        "book_total": 2.75,
    }

    result = market_informed_fallback(game)
    model_home_margin = result["expected_home_score"] - result["expected_away_score"]

    assert round(model_home_margin, 3) != 1.0
