"""NBA props injury availability helpers (no live network)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "standalone-player-props" / "backend"
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from app import data_sources as source  # noqa: E402


def test_props_status_unavailable_normalization():
    assert source.props_status_unavailable("Out")
    assert source.props_status_unavailable("OUT")
    assert source.props_status_unavailable("Doubtful")
    assert source.props_status_unavailable("Injured Reserve")
    assert source.props_status_unavailable("IR")
    assert source.props_status_unavailable("Inactive")
    assert source.props_status_unavailable("Suspended")
    assert source.props_status_unavailable({"description": "out"})
    assert not source.props_status_unavailable("Questionable")
    assert not source.props_status_unavailable("Day-To-Day")
    assert not source.props_status_unavailable("Probable")
    assert not source.props_status_unavailable("GTD")
    assert not source.props_status_unavailable("")


def test_athlete_out_on_injury_list_even_when_roster_active():
    athlete = {
        "status": {"type": "active", "name": "Active"},
        "injuries": [{"status": "Out", "type": {"description": "out"}}],
        "displayName": "Anthony Edwards",
    }
    assert source._athlete_props_unavailable(athlete)


def test_playing_leaders_skips_out_and_backfills():
    schedule = [{
        "home_team_id": "16", "home_team": "Minnesota Timberwolves",
        "away_team_id": "17", "away_team": "Brooklyn Nets",
        "event_id": "1", "date": "2026-11-23",
    }]
    leaders = {
        "categories": [{
            "name": "pointsPerGame",
            "leaders": [
                {"athlete": {"$ref": "http://x/athletes/1"}, "team": {"$ref": "http://x/teams/16"}},
                {"athlete": {"$ref": "http://x/athletes/2"}, "team": {"$ref": "http://x/teams/16"}},
                {"athlete": {"$ref": "http://x/athletes/3"}, "team": {"$ref": "http://x/teams/17"}},
            ],
        }]
    }
    athletes = {
        "1": {"displayName": "Anthony Edwards", "position": {"abbreviation": "G"},
              "status": {"type": "active"},
              "injuries": [{"status": "Out"}]},
        "2": {"displayName": "Available Wolf", "position": {"abbreviation": "G"},
              "status": {"type": "active"}, "injuries": []},
        "3": {"displayName": "Available Net", "position": {"abbreviation": "F"},
              "status": {"type": "active"}, "injuries": []},
    }

    def fake_get(url, timeout=12):
        class Resp:
            def raise_for_status(self):
                return None
            def json(self):
                if "leaders" in url:
                    return leaders
                for athlete_id, body in athletes.items():
                    if url.endswith("/athletes/" + athlete_id):
                        return {"athlete": body}
                return {}
        return Resp()

    with patch.object(source, "fetch_unavailable_prop_names", return_value={"anthony edwards"}), \
         patch.object(source.requests, "get", side_effect=fake_get):
        players, audit = source.playing_leaders(
            "NBA", schedule, ["points"], per_market=2, board=10, names=True
        )

    names = {p["name"] for p in players}
    assert "Anthony Edwards" not in names
    assert names == {"Available Wolf", "Available Net"}
    assert audit["selected"]["points"] == 2
    assert audit["excluded_unavailable"] >= 1
    assert players[0]["market_rankings"]["points"] == 1
