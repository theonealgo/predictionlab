"""Soccer week pages: one Mon–Sun week on picks, one on results."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from soccer_ui_fixup import (  # noqa: E402
    _soccer_filter_href,
    soccer_week_monday,
    soccer_week_nav_html,
    soccer_week_range,
)


def test_week_snaps_to_monday():
    monday, sunday = soccer_week_range("2026-09-11")
    assert monday == date(2026, 9, 7)
    assert sunday == date(2026, 9, 13)
    assert soccer_week_monday("2026-09-07") == date(2026, 9, 7)
    assert soccer_week_monday("2026-09-13") == date(2026, 9, 7)


def test_filter_href_keeps_week():
    href = _soccer_filter_href(
        kind="picks", region="europe", league="english-premier-league", week="2026-09-07",
    )
    assert href.startswith("/soccer-picks?")
    assert "week=2026-09-07" in href
    assert "region=europe" in href
    assert "league=english-premier-league" in href
    res = _soccer_filter_href(kind="results", week="2026-08-31")
    assert res.startswith("/soccer-results?")
    assert "week=2026-08-31" in res


def test_dropdown_africa_options_have_region_and_green_menu():
    from soccer_ui_fixup import _curated_soccer_league_options, soccer_league_dropdown_html

    opts = _curated_soccer_league_options(kind="picks", selected_region="all", week="2026-09-07")
    africa = [o for o in opts if "africa" in (o.get("regions") or "").split(",")]
    assert africa, "Africa leagues must carry regions=africa"
    html = soccer_league_dropdown_html(opts, kind="picks", selected_region="all", week="2026-09-07")
    assert 'data-region="africa"' in html or "data-region=\"africa" in html
    assert "filterLeagues" in html
    assert "go(true)" not in html
    assert ".soccer-dd-opt.in-season" in html
    assert html.count('data-region=') >= 10


def test_week_nav_has_prev_this_next():
    html = soccer_week_nav_html(kind="picks", region="all", week="2026-09-07")
    assert 'id="soccer-week-nav"' in html
    assert "week=2026-08-31" in html
    assert "week=2026-09-07" in html
    assert "week=2026-09-14" in html
    assert "This week" in html
    results = soccer_week_nav_html(kind="results", week="2026-08-31")
    assert "Last week" in results or "Week of" in results
    assert "/soccer-results" in results


def test_results_card_dates_stay_in_one_week():
    import NHL77FINAL as nhl

    daily = {
        "2026-09-06": {"games": [{"home": "A"}]},
        "2026-09-07": {"games": [{"home": "B"}]},
        "2026-09-10": {"games": [{"home": "C"}]},
        "2026-09-13": {"games": [{"home": "D"}]},
        "2026-09-14": {"games": [{"home": "E"}]},
    }
    start = nhl.datetime(2026, 9, 7)
    end = nhl.datetime(2026, 9, 13, 23, 59, 59)
    dates = nhl.soccer_results_card_dates(daily, start, end)
    assert set(dates) == {"2026-09-07", "2026-09-10", "2026-09-13"}
    assert "2026-09-06" not in dates
    assert "2026-09-14" not in dates
