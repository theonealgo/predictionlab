"""Regression tests: MLB 6/6 moneyline consensus is computed from raw model picks only."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ISO = ROOT / "iso_hub"
if str(ISO) not in sys.path:
    sys.path.insert(0, str(ISO))

from team_tabbed_results import (  # noqa: E402
    MODEL_ORDER,
    _consensus_agreements_from_finals,
    audit_mlb_ml_consensus,
    build_consensus_records_html,
)


def _game(
    *,
    picks: dict[str, str],
    home: str = "Home Team",
    away: str = "Away Team",
    hs: int = 5,
    aa: int = 3,
    game_id: str = "g1",
    game_date: str = "2026-08-21",
) -> dict:
    models = {
        name: {"pick": picks[name], "side": "home" if picks[name] == home else "away"}
        for name in MODEL_ORDER
    }
    return {
        "game_id": game_id,
        "game_date": game_date,
        "home_team_id": home,
        "away_team_id": away,
        "home_score": hs,
        "away_score": aa,
        "final": True,
        "models": models,
    }


def test_true_6_6_qualifies_and_wins():
    g = _game(picks={n: "Home Team" for n in MODEL_ORDER})
    rows = _consensus_agreements_from_finals([g])
    assert len(rows) == 1
    assert rows[0]["is_unanimous"] is True
    assert rows[0]["agree_n"] == 6
    assert rows[0]["grade"] == "WIN"


def test_5_6_does_not_count_as_unanimous():
    picks = {n: "Home Team" for n in MODEL_ORDER}
    picks["Efficiency"] = "Away Team"
    g = _game(picks=picks)
    rows = _consensus_agreements_from_finals([g])
    assert len(rows) == 1
    assert rows[0]["is_unanimous"] is False
    assert rows[0]["agree_n"] == 5


def test_4_6_does_not_count_as_unanimous():
    picks = {n: "Home Team" for n in MODEL_ORDER}
    picks["Efficiency"] = "Away Team"
    picks["Edge"] = "Away Team"
    g = _game(picks=picks)
    rows = _consensus_agreements_from_finals([g])
    assert rows[0]["is_unanimous"] is False
    assert rows[0]["agree_n"] == 4


def test_duplicate_rows_deduped_by_game_id():
    g = _game(picks={n: "Home Team" for n in MODEL_ORDER}, game_id="dup")
    rows = _consensus_agreements_from_finals([g, g, g])
    uni = [r for r in rows if r["is_unanimous"]]
    assert len(uni) == 1


def test_missing_model_excluded():
    g = _game(picks={n: "Home Team" for n in MODEL_ORDER})
    del g["models"]["Grinder2"]
    assert _consensus_agreements_from_finals([g]) == []


def test_ungraded_game_excluded():
    g = _game(picks={n: "Home Team" for n in MODEL_ORDER})
    g["home_score"] = None
    g["away_score"] = None
    g.pop("winner", None)
    assert _consensus_agreements_from_finals([g]) == []


def test_spread_block_not_used_for_ml_consensus():
    g = _game(picks={n: "Home Team" for n in MODEL_ORDER})
    g["spread"] = {"pick": "Away Team +1.5", "side": "away", "grade": "WIN"}
    rows = _consensus_agreements_from_finals([g])
    assert rows[0]["is_unanimous"] is True


def test_3_3_split_grades_winner_side_not_sharp_consensus():
    """3-3 ties grade the winning three-model side — not Edge or Sharp Consensus."""
    g = _game(
        picks={
            "Grinder2": "Home Team",
            "Takedown": "Home Team",
            "Edge": "Away Team",
            "XSharp": "Away Team",
            "Sharp Consensus": "Home Team",
            "Efficiency": "Away Team",
        },
        hs=2,
        aa=5,
    )
    rows = _consensus_agreements_from_finals([g])
    assert len(rows) == 1
    assert rows[0]["agree_n"] == 3
    assert rows[0]["is_unanimous"] is False
    assert rows[0]["grade"] == "WIN"


def test_50_pct_dissenter_buckets_as_5_6_not_4_6():
    picks = {n: "Home Team" for n in MODEL_ORDER}
    picks["XSharp"] = "Away Team"
    picks["Efficiency"] = "Away Team"
    g = _game(picks=picks)
    g["models"]["XSharp"]["prob"] = 50.0
    g["models"]["Efficiency"]["prob"] = 63.0
    rows = _consensus_agreements_from_finals([g])
    assert len(rows) == 1
    assert rows[0]["agree_n"] == 5
    assert rows[0]["is_unanimous"] is False


def test_6_6_row_only_includes_unanimous_in_html():
    uni = _game(picks={n: "Home Team" for n in MODEL_ORDER}, game_id="u1")
    split = _game(
        picks={**{n: "Home Team" for n in MODEL_ORDER}, "Efficiency": "Away Team"},
        game_id="s1",
        game_date="2026-08-22",
    )
    html = build_consensus_records_html([uni, split], sport="mlb")
    # Past 7 includes both dates; 6/6 cell should be 1-0 not 2-0
    assert "6/6 unanimous" in html or "6/6 agree" in html
    audit = audit_mlb_ml_consensus([uni, split], date_from="2026-08-21", date_to="2026-08-22")
    assert audit["unanimous_n"] == 1
    assert audit["w"] == 1
    assert audit["l"] == 0
