import copy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from odds_engine.model.player_history import read_history
from test_pl_connection import lines, source, engine


def body(year=2026, count=6):
    payload = {"labels": ["MIN", "PTS"], "names": ["minutes", "points"], "events": {},
               "seasonTypes": [{"displayName": f"{year}-{str(year+1)[-2:]} Season",
                                "categories": [{"displayName": "Regular Season Stats", "splitType": 2, "events": []}]}]}
    for i in range(count):
        identity = str(year*100+i)
        payload["events"][identity] = {"gameDate": f"{year}-11-{i+1:02d}T18:00:00Z", "gameResult": "W"}
        payload["seasonTypes"][0]["categories"][0]["events"].append({"eventId": identity, "stats": ["25", str(10+i)]})
    return payload


class CollegeHistoryTests(unittest.TestCase):
    def test_college_regular_marker_is_in_category_not_parent(self):
        result = read_history(body(), "points", datetime(2026, 11, 11, tzinfo=timezone.utc))
        self.assertEqual(len(result["recent_form"]), 6)
        self.assertEqual(result["recent_form"][0], 15)

    def test_championship_games_are_not_mislabeled_as_regular(self):
        fixture = body()
        first = next(iter(fixture["events"].values()))
        first.update(gameDate="2026-03-05T18:00:00Z", eventNote="Horizon League Championship - 1st Round")
        result = read_history(fixture, "points", datetime(2026, 11, 11, tzinfo=timezone.utc))
        self.assertEqual(len(result["recent_form"]), 5)

    def test_prior_season_uses_basketball_end_year(self):
        current, previous = body(count=2), body(2025, count=6)
        def get(league, identity, season=None):
            self.assertIn(season, (None, 2026))
            return current if season is None else previous
        with patch.object(lines, "_history", side_effect=get):
            result = lines.history_stats("NCAAW", "7", "points")
        self.assertEqual(len(result["recent_form"]), 8)
        self.assertEqual(result["recent_form"][:2], [11, 10])

    def test_real_nhl_shots_name_is_not_discarded(self):
        fixture = body()
        fixture["names"] = ["minutes", "shotsTotal"]
        fixture["labels"] = ["MIN", "S"]
        fixture["seasonTypes"][0]["displayName"] = "2026-27 Regular Season"
        self.assertIsNotNone(read_history(fixture, "shots_on_goal", datetime(2026, 11, 11, tzinfo=timezone.utc)))

    def test_null_forecast_side_and_mlb_under_survive_legacy_filter(self):
        rows = [{"player_id": "7", "event_id": event, "prop_type": "hits", "league": "MLB",
                 "picked_side": side, "confidence_score": None} for event, side in (("1", None), ("2", "UNDER"))]
        self.assertEqual(len(engine.filter_props(rows)), 2)
        self.assertEqual(len(engine.filter_props(rows, side="under")), 1)

    def test_requested_date_does_not_probe_next_week(self):
        response = unittest.mock.Mock()
        response.json.return_value = {"events": []}
        with patch.object(source.requests, "get", return_value=response) as get:
            self.assertEqual(source.fetch_schedule_and_teams("NCAAB", datetime(2026, 11, 11).date()), [])
            self.assertEqual(get.call_count, 1)
            self.assertEqual(get.call_args.kwargs["params"]["groups"], 50)
