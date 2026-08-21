"""Soccer Continent/League scope line under the filter chrome."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from soccer_ui_fixup import (  # noqa: E402
    soccer_league_dropdown_html,
    soccer_showing_scope_label,
)


def test_showing_all_continents_all_leagues():
    assert soccer_showing_scope_label(selected_region="all") == (
        "Showing: All continents · All leagues"
    )
    assert soccer_showing_scope_label(selected_region="") == (
        "Showing: All continents · All leagues"
    )


def test_showing_europe_all_leagues():
    assert soccer_showing_scope_label(selected_region="europe") == (
        "Showing: Europe · All leagues"
    )


def test_showing_live_specific_league():
    opts = [
        {"slug": "", "label": "All", "selected": ""},
        {"slug": "spanish-laliga", "label": "Spanish LaLiga", "selected": "1"},
    ]
    assert soccer_showing_scope_label(
        selected_region="live", options=opts,
    ) == "Showing: Live · Spanish LaLiga"


def test_dropdown_html_includes_showing_line():
    opts = [
        {"slug": "", "label": "All", "selected": "1", "href": "/soccer-results"},
        {
            "slug": "english-premier-league",
            "label": "English Premier League",
            "selected": "",
            "href": "/soccer-results?league=english-premier-league",
        },
    ]
    html = soccer_league_dropdown_html(
        opts, kind="results", selected_region="all",
    )
    assert 'id="soccer-showing-scope"' in html
    assert "Showing: All continents · All leagues" in html

    opts2 = [
        {"slug": "", "label": "All", "selected": "", "href": "/soccer-results?region=europe"},
        {
            "slug": "spanish-laliga",
            "label": "Spanish LaLiga",
            "selected": "1",
            "href": "/soccer-results?region=europe&league=spanish-laliga",
        },
    ]
    html2 = soccer_league_dropdown_html(
        opts2, kind="results", selected_region="europe",
    )
    assert "Showing: Europe · Spanish LaLiga" in html2
