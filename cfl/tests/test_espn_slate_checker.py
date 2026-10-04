"""Wrong-opponent slates (Toronto vs KC when ESPN has Athletics) must fail."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "qa"))

from espn_slate_checker import (  # noqa: E402
    espn_pairs_from_payload,
    golf_name_issues,
    page_pairs_for_day,
    slate_mismatch_issues,
    team_id,
)


def test_mlb_aliases_collapse_city_and_nickname():
    assert team_id("Toronto Blue Jays", "MLB") == team_id("Blue Jays", "MLB") == "bluejays"
    assert team_id("Kansas City Royals", "MLB") == team_id("Royals", "MLB") == "royals"
    assert team_id("Athletics", "MLB") == team_id("Oakland Athletics", "MLB") == "athletics"


def test_toronto_kc_on_page_vs_athletics_on_espn_fails():
    espn = [("Toronto Blue Jays", "Athletics")]
    page = [("Toronto Blue Jays", "Kansas City Royals")]
    issues = slate_mismatch_issues(espn, page, sport="MLB")
    assert issues
    assert any("Athletics" in i and ("Kansas" in i or "Royals" in i) for i in issues)


def test_matching_mlb_slate_passes():
    espn = [("Toronto Blue Jays", "Athletics"), ("Yankees", "Red Sox")]
    page = [("Blue Jays", "Athletics"), ("New York Yankees", "Boston Red Sox")]
    assert slate_mismatch_issues(espn, page, sport="MLB") == []


def test_page_pairs_reads_data_date():
    html = """
    <div data-pick-card data-date="2026-09-09" data-away="Toronto Blue Jays"
         data-home="Kansas City Royals"></div>
    <div data-pick-card data-date="2026-09-08" data-away="Yankees" data-home="Red Sox"></div>
    """
    pairs = page_pairs_for_day(html, "2026-09-09")
    assert len(pairs) == 1
    assert team_id(pairs[0][0], "MLB") == "bluejays"
    assert team_id(pairs[0][1], "MLB") == "royals"


def test_espn_payload_extracts_home_away():
    data = {
        "events": [
            {
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "team": {"displayName": "Athletics"},
                            },
                            {
                                "homeAway": "away",
                                "team": {"displayName": "Toronto Blue Jays"},
                            },
                        ]
                    }
                ]
            }
        ]
    }
    assert espn_pairs_from_payload(data) == [("Toronto Blue Jays", "Athletics")]


def test_date_section_id_anywhere_on_tag():
    html = """
    <div class="date-section visible" id="date-2026-09-09">
      <div data-pick-card data-away="Toronto Blue Jays" data-home="Athletics"></div>
    </div>
    <div class="date-section" id="date-2026-09-08">
      <div data-pick-card data-away="Yankees" data-home="Red Sox"></div>
    </div>
    """
    pairs = page_pairs_for_day(html, "2026-09-09", "MLB")
    assert len(pairs) == 1
    assert team_id(pairs[0][0], "MLB") == "bluejays"
    assert team_id(pairs[0][1], "MLB") == "athletics"


def test_soccer_rangers_do_not_use_mlb_alias():
    assert team_id("Queens Park Rangers", "Soccer") != team_id("Texas Rangers", "MLB")
    assert team_id("Queens Park Rangers", "Soccer") == "queensparkrangers"


def test_tennis_groupings_only_keep_today():
    data = {
        "events": [
            {
                "name": "US Open",
                "groupings": [
                    {
                        "grouping": {"displayName": "Men's Singles"},
                        "competitions": [
                            {
                                "date": "2026-09-09T17:00Z",
                                "competitors": [
                                    {"athlete": {"displayName": "Karen Khachanov"}},
                                    {"athlete": {"displayName": "Alexander Blockx"}},
                                ],
                            },
                            {
                                "date": "2026-09-07T17:00Z",
                                "competitors": [
                                    {"athlete": {"displayName": "Old Match A"}},
                                    {"athlete": {"displayName": "Old Match B"}},
                                ],
                            },
                        ],
                    }
                ],
            }
        ]
    }
    pairs = espn_pairs_from_payload(data, "2026-09-09")
    assert len(pairs) == 1
    assert {team_id(pairs[0][0], "Tennis"), team_id(pairs[0][1], "Tennis")} == {
        "khachanov",
        "blockx",
    }


def test_page_pairs_reads_tennis_home_before_away():
    html = """
    <div class="date-section visible" id="date-2026-09-09">
      <div class="game-card-stack" data-pick-card data-league="TENNIS"
           data-home="Coco Gauff" data-away="Mirra Andreeva"></div>
    </div>
    """
    pairs = page_pairs_for_day(html, "2026-09-09", "Tennis")
    assert len(pairs) == 1
    assert {team_id(pairs[0][0], "Tennis"), team_id(pairs[0][1], "Tennis")} == {
        "gauff",
        "andreeva",
    }


def test_tennis_empty_page_fails_when_espn_has_matches():
    espn = [("Karen Khachanov", "Alexander Blockx")]
    issues = slate_mismatch_issues(espn, [], sport="Tennis")
    assert issues
    assert any("page has none" in i for i in issues)


def test_golf_event_name_must_appear():
    assert golf_name_issues(["Biltmore Championship Asheville"], "<title>Biltmore Championship Asheville</title>") == []
    assert golf_name_issues(["US Open"], "<title>Sandbox Open</title>")
