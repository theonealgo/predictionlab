"""Final player-prop payload deduplication tests."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _dedupe_prop_rows(rows):
    """Load the same isolated props engine module used by the Flask app."""
    from NHL77FINAL import _load_props_modules

    engine, _ = _load_props_modules()
    return engine._dedupe_prop_rows(rows)


# ============================================================================
# PLAYER + PROP UNIQUE ROW CONTRACT
# ============================================================================

def test_dedupe_uses_normalized_name_when_player_id_is_missing():
    rows = [
        {
            "player_id": None,
            "player_name": "Jane Doe",
            "prop_type": "points",
            "picked_side": "OVER",
            "line": 18.5,
            "ev_over_percent": 4.0,
            "confidence_score": 60.0,
        },
        {
            "player_id": None,
            "player_name": "  jane   doe ",
            "prop_type": "points",
            "picked_side": "OVER",
            "line": 18.5,
            "ev_over_percent": 7.0,
            "confidence_score": 64.0,
        },
    ]

    result = _dedupe_prop_rows(rows)

    assert len(result) == 1
    assert result[0]["ev_over_percent"] == 7.0


def test_dedupe_keeps_different_prop_types_for_same_player():
    rows = [
        {"player_id": "7", "player_name": "Jane Doe", "prop_type": "points"},
        {"player_id": "7", "player_name": "Jane Doe", "prop_type": "assists"},
    ]

    assert len(_dedupe_prop_rows(rows)) == 2
