import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prop_actuals import recover, read_actuals, actuals_path
from sports_props_hub import read_results, model_type_summary, preview
from props_service import build_one, entries, _QUEUE


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name)/"original.sqlite"
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE player_prop_picks(id INTEGER PRIMARY KEY,league TEXT,pick_date TEXT,
                player_name TEXT,team TEXT,prop_type TEXT,pick TEXT,line REAL,created_at TEXT,line_source TEXT)""")
            conn.execute("""INSERT INTO player_prop_picks VALUES(1,'NBA','2026-11-10','Alex Caruso',
                'Oklahoma City Thunder','points','UNDER',6.5,'2026-11-10T22:37:00-05:00','espn_props')""")
        self.comp = {"date": "2026-11-11T04:00Z", "status": {"type": {"completed": True, "name": "STATUS_FINAL"}},
                     "competitors": [{"team": {"displayName": "Oklahoma City Thunder"}},
                                     {"team": {"displayName": "Portland Trail Blazers"}}]}
        self.event = {"id": "123", "date": self.comp["date"], "status": self.comp["status"], "competitions": [self.comp]}
        self.payload = {"header": {"id": "123", "competitions": [self.comp]},
                        "boxscore": {"players": [{"team": {"displayName": "Oklahoma City Thunder"},
                            "statistics": [{"labels": ["PTS"], "athletes": [{
                                "athlete": {"id": "7", "displayName": "Alex Caruso"}, "stats": ["5"]}]}]}]}}

    def get(self, url):
        return {"events": [self.event]} if "scoreboard?" in url else self.payload

    def athlete(self):
        return self.payload["boxscore"]["players"][0]["statistics"][0]["athletes"][0]

    def test_actual_recovery_preserves_original_and_model_attribution(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.assertEqual(recover(self.db, "NBA", "2026-11-10", self.get)["actuals"], 1)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        result = read_results(self.db, "NBA", "2026-11-10")
        row = result["items"][0]
        self.assertEqual((row["actual"], row["selection_result"], row["pregame_verified"]), (5, "Win", True))
        self.assertEqual(row["result"], "N/A")
        self.assertIsNone(row["model_id"])
        self.assertEqual(result["by_model_prop_type"], [])
        recover(self.db, "NBA", "2026-11-10", self.get)
        with sqlite3.connect(actuals_path(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM recovered_actuals").fetchone()[0], 1)

    def test_zero_is_a_real_actual_and_push_is_not_a_loss(self):
        self.athlete()["stats"] = ["0"]
        recover(self.db, "NBA", "2026-11-10", self.get)
        self.assertEqual(read_actuals(self.db, "2026-11-10")[1]["actual"], 0)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE player_prop_picks SET line=0")
        self.assertEqual(read_actuals(self.db, "2026-11-10"), {})
        recover(self.db, "NBA", "2026-11-10", self.get)
        self.assertEqual(read_actuals(self.db, "2026-11-10")[1]["selection_result"], "Push")

    def test_missing_stats_and_dnp_are_not_zero(self):
        self.athlete()["stats"] = [None]
        recover(self.db, "NBA", "2026-11-10", self.get)
        self.assertIsNone(read_actuals(self.db, "2026-11-10")[1]["actual"])
        self.athlete()["didNotPlay"] = True
        recover(self.db, "NBA", "2026-11-10", self.get)
        row = read_actuals(self.db, "2026-11-10")[1]
        self.assertIsNone(row["actual"])
        self.assertEqual(row["selection_result"], "N/A")
        self.assertIn("void rule", row["reason"])

    def test_wrong_date_wrong_player_wrong_team_are_not_guessed(self):
        self.athlete()["athlete"]["displayName"] = "Different Player"
        recover(self.db, "NBA", "2026-11-10", self.get)
        self.assertIsNone(read_actuals(self.db, "2026-11-10")[1]["actual"])
        self.athlete()["athlete"]["displayName"] = "Alex Caruso"
        self.payload["boxscore"]["players"][0]["team"]["displayName"] = "Different Team"
        recover(self.db, "NBA", "2026-11-10", self.get)
        self.assertIsNone(read_actuals(self.db, "2026-11-10")[1]["actual"])
        self.event["date"] = "2026-11-12T04:00Z"
        recover(self.db, "NBA", "2026-11-10", self.get)
        self.assertIsNone(read_actuals(self.db, "2026-11-10")[1]["actual"])

    def test_doubleheader_is_ambiguous_and_late_capture_is_not_pregame(self):
        event2 = copy.deepcopy(self.event)
        event2["id"] = "124"
        def two(url):
            if "scoreboard?" in url:
                return {"events": [self.event, event2]}
            payload = copy.deepcopy(self.payload)
            payload["header"]["id"] = "124" if "event=124" in url else "123"
            return payload
        recover(self.db, "NBA", "2026-11-10", two)
        row = read_actuals(self.db, "2026-11-10")[1]
        self.assertIsNone(row["actual"])
        self.assertIn("More than one", row["reason"])
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE player_prop_picks SET created_at='2026-11-11T12:00:00Z'")
        recover(self.db, "NBA", "2026-11-10", self.get)
        row = read_actuals(self.db, "2026-11-10")[1]
        self.assertEqual(row["actual"], 5)
        self.assertFalse(row["pregame_verified"])

    def test_inclusive_windows_and_sport_specific_seasons(self):
        with sqlite3.connect(self.db) as conn:
            for identity, day in enumerate(["2026-11-03", "2026-11-04", "2026-10-12", "2026-10-11",
                                            "2026-06-30", "2026-07-01"], 2):
                conn.execute("""INSERT INTO player_prop_picks
                    SELECT ?,'NBA',?,player_name,team,prop_type,pick,line,created_at,line_source
                    FROM player_prop_picks WHERE id=1""", (identity, day))
        self.assertEqual(read_results(self.db, "NBA", "2026-11-10", "7")["count"], 2)
        self.assertEqual(read_results(self.db, "NBA", "2026-11-10", "30")["count"], 4)
        self.assertEqual(read_results(self.db, "NBA", "2026-11-10", "season")["count"], 6)
        with self.assertRaises(ValueError):
            read_results(self.db, "NBA", "2026-11-10", "all-time")

    def test_model_type_counts_do_not_mix_sports_or_unassigned_records(self):
        rows = [{"league": "NBA", "model_id": "m", "model_name": "Model", "prop_type": "points", "result": state}
                for state in ["Win", "Loss", "Push", "Pending", "Void", "N/A"]]
        rows.append({"league": "NBA", "model_id": None, "prop_type": "points", "result": "Win"})
        rows.append({"league": "NHL", "model_id": "m", "model_name": "Model", "prop_type": "points", "result": "Win"})
        nba, nhl = model_type_summary(rows)
        self.assertEqual((nba["recorded"], nba["decided"], nba["win_rate"], nba["pending"]), (6, 2, 50, 1))
        self.assertEqual(nhl["win_rate"], 100)

    def test_no_fixtures_differs_from_unsupported_or_failed_data(self):
        empty = {"ts": __import__("time").time(), "payload": {"requested_date": "2030-11-10",
                  "availability": "no_fixtures", "availability_reason": "No fixtures."}}
        self.assertEqual(preview("NFL", empty, day="2030-11-10")["total"], 0)
        empty["payload"]["availability"] = "unavailable"
        self.assertIsNone(preview("NFL", empty, day="2030-11-10")["total"])
        site = SimpleNamespace(DATABASE=str(self.db), _PROPS_ENGINE_MODULE=None, _props_disk_load=lambda league: None)
        before = _QUEUE.qsize()
        data = entries(site, "2030-11-10")
        self.assertEqual(_QUEUE.qsize(), before)
        self.assertIn("TENNIS", data)
        self.assertIn("GOLF", data)
        self.assertIn("not connected", data["GOLF"]["payload"]["availability_reason"])

    def test_refresh_error_preserves_verified_cache_and_backoff(self):
        import props_service
        queue = unittest.mock.Mock()
        site = SimpleNamespace(DATABASE=str(self.db))
        queue.get.side_effect = [(0, 0, site, "NBA", "2030-11-10"), StopIteration]
        previous = {"ts": __import__("time").time(), "payload": {
            "requested_date": "2030-11-10", "availability": "no_fixtures", "props": []}}
        with patch.object(props_service, "_QUEUE", queue), \
             patch.object(props_service, "entries", return_value={"NBA": previous}), \
             patch.object(props_service, "build_one", side_effect=RuntimeError("offline")), \
             patch.dict(props_service._CACHE, clear=True), patch.dict(props_service._STATE, clear=True):
            with self.assertLogs(props_service.logger, level="ERROR"), self.assertRaises(StopIteration):
                props_service._loop()
            key = props_service._key(site, "NBA", "2030-11-10")
            self.assertEqual(props_service._CACHE[key]["payload"]["availability"], "no_fixtures")
            self.assertIn("last verified", props_service._CACHE[key]["payload"]["refresh_notice"])
            self.assertEqual(props_service._STATE[key]["status"], "unavailable")

    def test_daily_publication_starts_once_per_database(self):
        import props_service
        site = SimpleNamespace(DATABASE=str(self.db))
        with patch.dict(props_service._ACTIVE, clear=True), patch.object(props_service.threading, "Thread") as thread:
            props_service._DAILY.discard(str(self.db.resolve()))
            try:
                props_service.start_daily(site)
                props_service.start_daily(site)
                self.assertEqual(thread.call_count, 1)
                thread.return_value.start.assert_called_once()
            finally:
                props_service._DAILY.discard(str(self.db.resolve()))
