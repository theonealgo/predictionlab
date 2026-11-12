from datetime import datetime
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from props_navigation import navigation, bind_games
from prop_settlement import settle_event, _actuals
from prop_forecast_store import capture, read, ledger_path
from sports_props_hub import historical_catalog
from odds_engine.model.prop_math import calculate, VERSION


class NavigationSettlementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name)/"original.sqlite"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE games(sport TEXT,game_id TEXT,home_team_id TEXT,away_team_id TEXT,game_date TEXT)")
            conn.execute("INSERT INTO games VALUES('NBA','NBA_123','Home Team','Away Team','2030-11-10')")
        calc = calculate({"recent_form": [30]*10}, "NBA", "points", 40.5)
        self.row = {"player_id": "7", "player_name": "Player", "team": "Home Team", "opponent": "Away Team",
                    "event_id": "123", "event_start": "2030-11-11T00:00:00Z",
                    "prop_type": "points", "line": 40.5, "line_source": "espn_props",
                    "line_type": "market", "model_forecasts": calc["model_forecasts"],
                    "input_hash": calc["input_hash"], "schema_version": VERSION}

    def publish(self, row=None):
        capture(self.db, [{"league": "NBA", "items": [row or self.row]}],
                datetime.fromisoformat("2030-11-10T12:00:00+00:00"))

    def official(self, actual=42, final=True, dnp=False):
        return {"header": {"id": "123", "competitions": [{"status": {
            "type": {"completed": final, "name": "STATUS_FINAL" if final else "STATUS_IN_PROGRESS"}}}]},
            "boxscore": {"players": [{"statistics": [{"labels": ["PTS"], "athletes": [{
                "athlete": {"id": "7"}, "stats": [actual], "didNotPlay": dnp}]}]}]}}

    def test_game_binding_does_not_guess_doubleheader(self):
        section = {"league": "NBA", "date": "2030-11-10", "items": [copy.deepcopy(self.row)]}
        catalog = [{"league": "NBA", "id": "NBA_123", "home": "Home Team", "away": "Away Team"}]
        bind_games(section, catalog)
        self.assertEqual(section["items"][0]["game_id"], "NBA_123")
        section["items"][0].pop("event_id")
        section["items"][0].pop("game_id")
        catalog.append({"league": "NBA", "id": "NBA_124", "home": "Home Team", "away": "Away Team"})
        bind_games(section, catalog)
        self.assertNotIn("game_id", section["items"][0])
        section["items"][0]["event_id"] = "999"
        bind_games(section, catalog[:1])
        self.assertNotIn("game_id", section["items"][0])

    def test_navigation_has_counts_and_games_but_no_private_numbers(self):
        entry = {"ts": time.time(), "payload": {"props": [self.row]}}
        result = navigation(self.db, "2030-11-10", {"NBA": entry})
        nba = next(row for row in result["sports"] if row["league"] == "NBA")
        self.assertEqual(nba["count"], 1)
        self.assertEqual(nba["games"][0]["count"], 1)
        self.assertEqual(nba["games"][0]["name"], "Away Team @ Home Team")
        text = json.dumps(result)
        for private in ('"line":', '"probabilities":', '"model_forecasts":', '"player_name":'):
            self.assertNotIn(private, text)

    def test_actual_score_settles_original_pick_not_projection(self):
        self.publish()
        self.assertEqual(settle_event(self.db, "NBA", "123", self.official()), 2)
        rows = read(self.db, "2030-11-10")
        self.assertTrue(all(row["pick"] == "UNDER" and row["actual"] == 42 and row["result"] == "Loss" for row in rows))
        with sqlite3.connect(self.db) as conn:
            self.assertEqual({r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}, {"games"})

    def test_not_final_and_wrong_event_never_graded(self):
        self.publish()
        self.assertEqual(settle_event(self.db, "NBA", "123", self.official(final=False)), 0)
        self.assertTrue(all(row["result"] == "Pending" for row in read(self.db, "2030-11-10")))
        with self.assertRaises(ValueError):
            settle_event(self.db, "NBA", "999", self.official())

    def test_zero_missing_and_push_are_distinct(self):
        self.publish()
        settle_event(self.db, "NBA", "123", self.official(actual=0))
        self.assertTrue(all(row["result"] == "Win" for row in read(self.db, "2030-11-10")))
        settle_event(self.db, "NBA", "123", self.official(actual=None))
        self.assertTrue(all(row["result"] == "N/A" for row in read(self.db, "2030-11-10")))
        changed = copy.deepcopy(self.row)
        changed["line"] = 30.0
        changed["model_forecasts"] = calculate({"recent_form": [30]*10}, "NBA", "points", 30)["model_forecasts"]
        self.publish(changed)
        settle_event(self.db, "NBA", "123", self.official(actual=30))
        self.assertTrue(any(row["result"] == "Push" for row in read(self.db, "2030-11-10")))

    def test_models_do_not_double_offer_counts(self):
        self.publish()
        data = historical_catalog(self.db, "2030-11-10", "NBA", limit=None)
        self.assertEqual(data["sections"][0]["total"], 1)
        self.assertEqual(len(data["sections"][0]["items"][0]["model_forecasts"]), 2)

    def test_input_history_saved_once_and_first_quote_is_performance_cohort(self):
        from sports_props_hub import read_results
        self.row["input_values"] = [30]*10
        self.publish()
        changed = copy.deepcopy(self.row)
        changed["line"] = 20.5
        changed["model_forecasts"] = calculate({"recent_form": [30]*10}, "NBA", "points", 20.5)["model_forecasts"]
        capture(self.db, [{"league": "NBA", "items": [changed]}],
                datetime.fromisoformat("2030-11-10T13:00:00+00:00"))
        with sqlite3.connect(ledger_path(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM forecast_inputs").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT count(*) FROM forecasts").fetchone()[0], 4)
            self.assertNotIn("input_values", conn.execute("SELECT payload FROM forecasts LIMIT 1").fetchone()[0])
        results = read_results(self.db, "NBA", "2030-11-10")
        self.assertEqual(results["count"], 2)
        self.assertTrue(all(row["line"] == 40.5 for row in results["items"]))

    def test_market_dnp_does_not_assume_book_rule(self):
        self.publish()
        settle_event(self.db, "NBA", "123", self.official(dnp=True))
        self.assertTrue(all(row["result"] == "N/A" for row in read(self.db, "2030-11-10")))

    def test_mlb_strikeouts_are_pitching_not_batting(self):
        payload = {"boxscore": {"players": [{"statistics": [
            {"name": "batting", "labels": ["SO"], "athletes": [{"athlete": {"id": "1"}, "stats": ["4"]}]},
            {"name": "pitching", "labels": ["K"], "athletes": [{"athlete": {"id": "2"}, "stats": ["8"]}]},
        ]}]}}
        parsed = _actuals(payload, "MLB")
        self.assertNotIn("strikeouts", parsed["1"]["stats"])
        self.assertEqual(parsed["2"]["stats"]["strikeouts"], 8)

    def test_real_mlb_type_key_and_nhl_points_from_goals_plus_assists(self):
        payload = {"boxscore": {"players": [{"statistics": [
            {"type": "batting", "labels": ["H", "R", "RBI", "HR"], "athletes": [
                {"athlete": {"id": "1"}, "stats": ["0", "1", "2", "0"]}]},
            {"type": "pitching", "labels": ["K"], "athletes": [
                {"athlete": {"id": "2"}, "stats": ["6"]}]},
        ]}]}}
        self.assertEqual(_actuals(payload, "MLB")["1"]["stats"]["hits"], 0)
        self.assertEqual(_actuals(payload, "MLB")["2"]["stats"]["strikeouts"], 6)
        payload = {"boxscore": {"players": [{"statistics": [
            {"labels": ["G", "A", "S"], "athletes": [
                {"athlete": {"id": "3"}, "stats": ["0", "0", "2"]}]}]}]}}
        parsed = _actuals(payload, "NHL")["3"]["stats"]
        self.assertEqual(parsed["points"], 0)
        self.assertEqual(parsed["shots_on_goal"], 2)

    def test_publication_captures_models_and_removes_private_history(self):
        from flask import Flask
        import web_api
        row = copy.deepcopy(self.row)
        row["input_values"] = [30] * 10
        payload = {"schema_version": VERSION, "props": [row]}
        engine = SimpleNamespace(_CACHE={"NBA": {"ts": time.time(), "payload": payload}})
        site = SimpleNamespace(
            DATABASE=str(self.db), _PROPS_ENGINE_MODULE=engine,
            _props_disk_load=lambda league: None,
            _load_props_modules=lambda: (engine, None),
            _props_league_data=lambda *args: payload,
        )
        app = Flask(__name__)
        app.testing = True
        app.register_blueprint(web_api.api)
        with patch.dict(sys.modules, {"NHL77FINAL": site}), patch.object(web_api, "_props_hub_access", return_value=None):
            response = app.test_client().get("/api/web/props-hub?league=NBA&date=2030-11-10")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["captured_forecasts"], 2)
            self.assertTrue(response.json["tracking_available"])
            self.assertNotIn("input_values", response.get_data(as_text=True))
            self.assertNotIn("input_hash", response.get_data(as_text=True))
            result = app.test_client().get("/api/web/props-results?league=NBA&date=2030-11-10")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json["count"], 2)
            self.assertNotIn("input_values", result.get_data(as_text=True))
            self.assertTrue(all(item["result"] == "Pending" for item in result.json["items"]))
