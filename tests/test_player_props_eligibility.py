"""Unit tests for props player eligibility (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "standalone-player-props" / "backend"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from app.player_eligibility import (  # noqa: E402
    filter_props_by_eligibility,
    market_allowed,
    position_to_role,
    primary_market_for_role,
    select_top_pool,
)


def test_nfl_blocks_ol_db_kicker_for_rush():
    assert position_to_role("NFL", "OT") is None
    assert position_to_role("NFL", "CB") is None
    assert position_to_role("NFL", "K") is None
    assert position_to_role("NFL", "QB") == "QB"
    assert position_to_role("NFL", "RB") == "RB"
    assert market_allowed("NFL", "QB", "passing_yards")
    assert market_allowed("NFL", "RB", "rushing_yards")
    assert not market_allowed("NFL", "TE", "passing_yards")
    assert not market_allowed("NFL", "WR", "passing_yards")


def test_wr_rush_requires_real_book_line():
    # Synthetic/internal must NOT invent WR Rush Yds.
    assert not market_allowed("NFL", "WR", "rushing_yards")
    assert not market_allowed("NFL", "WR", "rushing_yards", line_source="internal_odds_api")
    assert not market_allowed("NFL", "WR", "rushing_yards", line_source="synthetic")
    # Real sportsbook line may attach an unusual WR rush market.
    assert market_allowed("NFL", "WR", "rushing_yards", line_source="espn_props")
    assert primary_market_for_role("NFL", "WR") == "receiving_yards"
    assert primary_market_for_role("NFL", "RB") == "rushing_yards"
    assert primary_market_for_role("NFL", "QB") == "passing_yards"


def test_top_pool_caps_per_role_not_by_ev():
    players = []
    for i in range(50):
        players.append(
            {
                "player_id": f"qb{i}",
                "name": f"QB {i}",
                "team": "BUF",
                "position": "QB",
                "role": "QB",
                "experience_years": 50 - i,
                "depth_rank": 1 if i == 0 else 3,
                "usage_score": 0.1,
            }
        )
    for i in range(10):
        players.append(
            {
                "player_id": f"ot{i}",
                "name": f"OT {i}",
                "team": "BUF",
                "position": "OT",
                "experience_years": 20,
            }
        )
    kept, audit = select_top_pool(players, league="NFL", per_role=35)
    assert all(p["role"] == "QB" for p in kept)
    assert len(kept) == 35
    assert audit["removed_no_role"] == 10
    assert kept[0]["name"] == "QB 0"


def test_depth_rank_beats_hash_noise():
    players = [
        {
            "player_id": "1",
            "name": "Jahmyr Gibbs",
            "team": "DET",
            "position": "RB",
            "role": "RB",
            "depth_rank": 1,
            "experience_years": 3,
        },
        {
            "player_id": "2",
            "name": "Jabari Small",
            "team": "DET",
            "position": "RB",
            "role": "RB",
            "depth_rank": 5,
            "experience_years": 8,
            "usage_score": 0.99,
        },
    ]
    kept, _ = select_top_pool(players, league="NFL", per_role=35)
    assert kept[0]["name"] == "Jahmyr Gibbs"


def test_filter_props_suppresses_ineligible_market():
    players = {
        "1": {"player_id": "1", "name": "Josh Allen", "position": "QB", "role": "QB", "depth_rank": 1},
        "2": {"player_id": "2", "name": "Dion Dawkins", "position": "OT", "role": None},
        "3": {
            "player_id": "3",
            "name": "Amon-Ra St. Brown",
            "position": "WR",
            "role": "WR",
            "depth_rank": 1,
        },
        "4": {
            "player_id": "4",
            "name": "Jabari Small",
            "position": "RB",
            "role": "RB",
            "depth_rank": 5,
        },
        "5": {
            "player_id": "5",
            "name": "Keon Coleman",
            "position": "WR",
            "role": "WR",
            "depth_rank": 3,
        },
    }
    props = [
        {"player_id": "1", "prop_type": "passing_yards", "line": 250.5, "line_source": "internal_odds_api"},
        {"player_id": "1", "prop_type": "rushing_yards", "line": 35.5, "line_source": "internal_odds_api"},
        {"player_id": "2", "prop_type": "rushing_yards", "line": 10.5, "line_source": "internal_odds_api"},
        {"player_id": "3", "prop_type": "rushing_yards", "line": 12.5, "line_source": "internal_odds_api"},
        {"player_id": "3", "prop_type": "receiving_yards", "line": 70.5, "line_source": "internal_odds_api"},
        {"player_id": "3", "prop_type": "rushing_yards", "line": 12.5, "line_source": "espn_props"},
        {"player_id": "4", "prop_type": "rushing_yards", "line": 40.5, "line_source": "internal_odds_api"},
        {"player_id": "5", "prop_type": "receiving_yards", "line": 45.5, "line_source": "internal_odds_api"},
    ]
    out, audit = filter_props_by_eligibility(props, players, league="NFL")
    pts = {(r["player_id"], r["prop_type"], r.get("line_source")) for r in out}
    assert ("1", "passing_yards", "internal_odds_api") in pts
    assert ("1", "rushing_yards", "internal_odds_api") in pts
    assert ("3", "receiving_yards", "internal_odds_api") in pts
    assert ("3", "rushing_yards", "espn_props") in pts
    assert ("3", "rushing_yards", "internal_odds_api") not in pts
    assert ("2", "rushing_yards", "internal_odds_api") not in pts
    assert ("4", "rushing_yards", "internal_odds_api") not in pts  # RB depth 5
    assert ("5", "receiving_yards", "internal_odds_api") in pts  # WR depth 3 OK
    assert audit["suppressed"] >= 3


def test_mlb_pitcher_vs_hitter_markets():
    assert position_to_role("MLB", "SP") == "P"
    assert position_to_role("MLB", "SS") == "H"
    assert market_allowed("MLB", "P", "strikeouts")
    assert not market_allowed("MLB", "P", "hits")
    assert market_allowed("MLB", "H", "home_runs")
    assert not market_allowed("MLB", "H", "strikeouts")
