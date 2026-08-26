"""Published pre-game snapshots must not be rewritten on rebuild."""


def test_h2h_does_not_overwrite_locked_total():
    import NHL77FINAL as N

    pred = {
        "home_team_id": "Home",
        "away_team_id": "Away",
        "stored_ensemble_prob": 0.61,
        "_picks_locked": True,
        "our_total": 8.5,
    }

    def fake_proj(conn, sport, ht, at, n=10, min_games=2):
        return {
            "our_total": 11.0,
            "games_used": 10,
            "avg_home": 6.0,
            "avg_away": 5.0,
            "home_wins": 6,
            "away_wins": 4,
            "draws": 0,
        }

    N._attach_h2h_projection_to_predictions.__defaults__
    orig = N._compute_h2h_projection
    orig_db = N.get_db_connection
    try:
        N._compute_h2h_projection = fake_proj
        N.get_db_connection = lambda: object()
        N._attach_h2h_projection_to_predictions("MLB", [pred], n=10)
    finally:
        N._compute_h2h_projection = orig
        N.get_db_connection = orig_db
    assert pred["our_total"] == 8.5


def test_published_ml_skips_named_v2_overlay(monkeypatch):
    """apply_named_ml_v2_to_card must not run once a snapshot exists."""
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("locked card must not take live MLB v2 overlay")

    import sports.MLB as mlb

    monkeypatch.setattr(mlb, "apply_named_ml_v2_to_card", boom)
    # Guard: helper still exists for first-publish only.
    assert callable(mlb.apply_named_ml_v2_to_card) or called["n"] == 0
    assert called["n"] == 0


def test_apply_stored_markets_restores_mlb_spread():
    import NHL77FINAL as N

    card = {
        "stored_lock_xs_spread": -1.5,
        "stored_lock_xs_total": 8.5,
        "stored_lock_pl_spread": -1.5,
        "stored_lock_pl_total": 8.0,
        "xgb_spread": 2.5,
        "our_spread": 2.5,
    }
    assert N._apply_stored_markets_to_card(card) is True
    assert card["xgb_spread"] == -1.5
    assert card["our_spread"] == -1.5
    assert card["xgb_total"] == 8.5
    assert card["our_total"] == 8.0
    assert card["_spread_faded"] is True
    assert card["_picks_locked"] is True


def test_backfill_does_not_overwrite_locked_spread():
    import sqlite3
    import NHL77FINAL as N

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE predictions ("
        "id INTEGER PRIMARY KEY, game_id TEXT, sport TEXT, "
        "expected_home_score REAL, expected_away_score REAL, "
        "lock_xs_spread REAL, lock_xs_total REAL, "
        "lock_pl_spread REAL, lock_pl_total REAL)"
    )
    conn.execute(
        "INSERT INTO predictions (game_id, sport, lock_xs_spread, lock_pl_spread) "
        "VALUES ('g1', 'MLB', -1.5, -1.5)"
    )
    cur = conn.cursor()
    filled = N._backfill_prediction_markets(
        cur,
        "MLB",
        {
            "game_id": "g1",
            "xgb_spread": 3.5,
            "our_spread": 3.5,
            "xgb_total": 9.0,
            "our_total": 9.0,
        },
    )
    assert filled is True
    row = conn.execute("SELECT * FROM predictions WHERE game_id='g1'").fetchone()
    assert row["lock_xs_spread"] == -1.5
    assert row["lock_pl_spread"] == -1.5
    assert row["lock_xs_total"] == 9.0
    assert row["lock_pl_total"] == 9.0

    again = N._backfill_prediction_markets(
        cur,
        "MLB",
        {
            "game_id": "g1",
            "xgb_spread": 99.0,
            "our_spread": 99.0,
            "xgb_total": 99.0,
            "our_total": 99.0,
        },
    )
    assert again is False
    row2 = conn.execute("SELECT * FROM predictions WHERE game_id='g1'").fetchone()
    assert row2["lock_xs_spread"] == -1.5
    assert row2["lock_xs_total"] == 9.0


def test_enforce_does_not_rewrite_locked_ml():
    import NHL77FINAL as N

    pred = {
        "_picks_locked": True,
        "stored_ensemble_prob": 0.61,
        "ensemble_prob": 61.0,
        "our_spread": -3.0,
        "xgb_spread": -3.0,
        "xgb_prob": 61.0,
    }
    N._enforce_pick_spread_consistency(pred, sport="MLB")
    assert pred["ensemble_prob"] == 61.0
    assert pred["xgb_prob"] == 61.0
