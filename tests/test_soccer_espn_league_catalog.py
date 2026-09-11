"""Soccer checker must fail when ESPN browse leagues are dropped."""
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
    missing_espn_browse_headings,
    missing_espn_browse_leagues,
    soccer_league_option_labels,
)


def test_every_espn_browse_name_maps_to_catalog():
    unmapped = [
        name
        for name in ESPN_BROWSE_LEAGUES
        if name.lower() not in _SOCCER_LEAGUE_CANONICAL
    ]
    assert unmapped == []
    assert len(ESPN_BROWSE_LEAGUES) == 148
    assert len(SOCCER_LEAGUE_ORDER) == 148


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


def test_missing_espn_leagues_fails_when_asia_dropped():
    keep = [n for n in SOCCER_LEAGUE_ORDER if n != "Japanese J.League"]
    options = "".join(f"<option>{name} · Live</option>" for name in keep)
    html = f'<select id="league">{options}</select>'
    missing = missing_espn_browse_leagues(html)
    assert "Japanese J.League" in missing
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
        "<option>USA, Mexico &amp; CONCACAF</option>"
        "<option>Europe</option>"
        "<option>Internationals</option>"
        "<option>South America</option>"
        "<option>Asia</option>"
        "<option>Africa</option>"
        "</select>"
        '<select id="league"><option>UEFA European Championship</option></select>'
    )
    assert missing_espn_browse_headings(html) == []
