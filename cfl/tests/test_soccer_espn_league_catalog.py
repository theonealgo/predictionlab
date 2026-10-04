"""Soccer checker must fail when active browse leagues are dropped."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "qa"))

from soccer_league_catalog import (  # noqa: E402
    ESPN_BROWSE_HEADINGS,
    ESPN_BROWSE_LEAGUES,
    SOCCER_LEAGUE_ORDER,
    _SOCCER_LEAGUE_CANONICAL,
    espn_browse_league_pages,
    league_page_selection_issues,
    missing_espn_browse_headings,
    missing_espn_browse_leagues,
    missing_in_season_green_leagues,
    soccer_league_option_labels,
)


def test_active_catalog_is_fifteen_leagues():
    assert len(SOCCER_LEAGUE_ORDER) == 15
    assert len(ESPN_BROWSE_LEAGUES) == 15


def test_every_espn_browse_name_maps_to_catalog():
    unmapped = [
        name
        for name in ESPN_BROWSE_LEAGUES
        if name.lower() not in _SOCCER_LEAGUE_CANONICAL
    ]
    assert unmapped == []
    assert len(SOCCER_LEAGUE_ORDER) == len(ESPN_BROWSE_LEAGUES)


def test_missing_espn_leagues_empty_when_full_catalog_listed():
    options = "".join(
        f'<option value="{i}">{name}</option>'
        for i, name in enumerate(SOCCER_LEAGUE_ORDER)
    )
    html = (
        '<select id="soccer-region">'
        + "".join(f"<option>{h}</option>" for h in ESPN_BROWSE_HEADINGS)
        + "</select>"
        f'<select id="league">{options}</select>'
    )
    assert soccer_league_option_labels(html) == list(SOCCER_LEAGUE_ORDER)
    assert missing_espn_browse_leagues(html) == []
    assert missing_espn_browse_headings(html) == []


def test_missing_espn_leagues_fails_when_active_league_dropped():
    keep = [n for n in SOCCER_LEAGUE_ORDER if n != "Swiss Super League"]
    options = "".join(f"<option>{name} · Live</option>" for name in keep)
    html = f'<select id="league">{options}</select>'
    missing = missing_espn_browse_leagues(html)
    assert "Swiss Super League" in missing
    assert missing_espn_browse_headings(html) == list(ESPN_BROWSE_HEADINGS)


def test_chrome_gaps_fail_thin_picks_results_bar():
    from site_chrome import chrome_gaps

    thin = (
        '<header class="pl2-header"><nav><a href="/x">Picks</a>'
        '<a href="/y">Results</a></nav></header>'
    )
    gaps = chrome_gaps(thin)
    assert any("Picks|Results" in g or "Sports" in g for g in gaps)


def test_heading_check_does_not_match_european_substring():
    html = (
        '<select id="soccer-region">'
        "<option>All</option>"
        "<option>Top Competitions</option>"
        "<option>USA &amp; Canada</option>"
        "<option>Europe</option>"
        "<option>South America</option>"
        "</select>"
        '<select id="league"><option>English Premier League</option></select>'
    )
    assert missing_espn_browse_headings(html) == []


def test_every_espn_browse_league_has_own_picks_and_results_url():
    pages = espn_browse_league_pages()
    assert len(pages) == 15
    slugs = [p["slug"] for p in pages]
    assert all(slugs)
    assert all(p["picks"].startswith("/soccer-picks?league=") for p in pages)
    assert all(p["results"].startswith("/soccer-results?league=") for p in pages)
    assert len(set(slugs)) == len(slugs)


def test_league_page_must_select_that_league():
    html = (
        '<section id="league-controls">'
        '<select id="league">'
        '<option value="" selected>All leagues</option>'
        '<option value="english-premier-league">English Premier League</option>'
        "</select>"
        '<p id="soccer-showing-scope">Showing: All continents · All leagues</p>'
        "</section>"
    )
    issues = league_page_selection_issues(
        html,
        slug="english-premier-league",
        espn_name="English Premier League",
        catalog_name="English Premier League",
    )
    assert any("did not select" in i for i in issues)
    assert any("All leagues" in i for i in issues)


def test_in_season_not_green_is_listed():
    html = (
        '<option data-in-season="1">● English Premier League</option>'
        '<button class="soccer-dd-opt">● English Premier League</button>'
    )
    assert missing_in_season_green_leagues(html) == ["English Premier League"]
