"""Site search must index pages, sports, teams, and players."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _routes(payload):
    return [item["route"] for item in payload.get("page_results", [])]


def _labels(payload):
    return [item["label"] for item in payload.get("page_results", [])]


def test_search_direct_sport_terms_return_pick_pages():
    import NHL77FINAL as N

    nba = N._build_search_payload("NBA picks")
    mlb = N._build_search_payload("MLB")

    assert nba["suggested_route"] == "/nba-picks"
    assert "/nba-picks" in _routes(nba)
    assert mlb["suggested_route"] == "/mlb-picks"
    assert "/mlb-picks" in _routes(mlb)


def test_search_indexes_visible_site_copy_and_blog():
    import NHL77FINAL as N

    transparent = N._build_search_payload("tracked transparent verified")
    blog = N._build_search_payload("Prediction Lab Blog")
    daily = N._build_search_payload("Daily Betting Results Report")

    assert "/daily-report" in _routes(transparent) or "/all-sports-results" in _routes(transparent)
    assert blog["suggested_route"] == "/blog"
    assert "/blog" in _routes(blog)
    assert daily["suggested_route"] == "/daily-report"


def test_search_indexes_database_players_and_teams():
    import NHL77FINAL as N

    player = N._build_search_payload("Karl-Anthony Towns")
    team = N._build_search_payload("New York Knicks")

    assert any("Karl-Anthony Towns" in label for label in _labels(player))
    assert "/player-props" in _routes(player)
    assert any("New York Knicks" in label for label in _labels(team))
    assert "/nba-picks" in _routes(team)


def test_search_route_redirects_to_best_match():
    import NHL77FINAL as N

    with N.app.test_client() as client:
        sport = client.get("/search?query=NBA%20picks", follow_redirects=False)
        player = client.get("/search?query=Karl-Anthony%20Towns", follow_redirects=False)

    assert sport.status_code == 302
    assert sport.headers["Location"].endswith("/nba-picks")
    assert player.status_code == 302
    assert player.headers["Location"].endswith("/player-props")
