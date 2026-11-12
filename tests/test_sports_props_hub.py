import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flask import Flask
from flask_login import LoginManager, UserMixin, current_user

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sports_props_hub import LEAGUES, preview, read_results, historical_catalog


class User(UserMixin):
    def __init__(self, identity):
        self.id = identity


class HubTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "props.sqlite"
        with sqlite3.connect(self.path) as conn:
            conn.executescript("""
                CREATE TABLE player_prop_picks(id INTEGER PRIMARY KEY,league TEXT,pick_date TEXT,
                    player_name TEXT,team TEXT,prop_type TEXT,pick TEXT,line REAL,created_at TEXT);
                CREATE TABLE player_prop_results(league TEXT,result_date TEXT,player_name TEXT,
                    team TEXT,prop_type TEXT,pick TEXT,line REAL,actual REAL,result TEXT);
                INSERT INTO player_prop_picks VALUES(1,'NBA','2026-11-10','Fixture Player','Team',
                    'points','UNDER',20.5,'2026-11-10T09:00:00-05:00');
                INSERT INTO player_prop_picks VALUES(2,'NHL','2026-11-10','Fixture Skater','Team',
                    'shots_on_goal','OVER',2.5,'2026-11-10T09:00:00-05:00');
                INSERT INTO player_prop_results VALUES('NBA','2026-11-10','Fixture Player','Team',
                    'points','UNDER',20.5,24,'MISS');
            """)

    def test_reader_is_readonly_and_does_not_clone_model_grades(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        data = read_results(self.path, "NBA", "2026-11-10")
        self.assertEqual(data["count"], 1)
        row = data["items"][0]
        self.assertEqual((row["pick"], row["line"], row["actual"], row["recorded_result"]),
                         ("UNDER", 20.5, 24, "MISS"))
        self.assertEqual(row["result"], "N/A")
        self.assertIsNone(row["model_id"])
        self.assertTrue(all(model["win_rate"] is None for model in data["models"]))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_all_sports_and_exact_date_filter(self):
        self.assertEqual(read_results(self.path, day="2026-11-10")["count"], 2)
        self.assertEqual(read_results(self.path, day="2026-11-09")["count"], 0)
        for sport in LEAGUES:
            self.assertEqual(read_results(self.path, sport, "2026-11-10")["league"], sport)

    def test_nightly_catalog_counts_props_not_models_or_games(self):
        data = historical_catalog(self.path, "2026-11-10", "NBA", limit=None)
        counts = {item["league"]: item["count"] for item in data["counts"]}
        self.assertEqual(counts["NBA"], 1)
        self.assertEqual(counts["NHL"], 1)
        self.assertEqual(counts["NFL"], 0)
        self.assertEqual(len(data["sections"]), 1)
        self.assertEqual(data["sections"][0]["total"], len(data["sections"][0]["items"]))
        self.assertEqual(data["sections"][0]["items"][0]["recorded_side"], "UNDER")
        empty = historical_catalog(self.path, "2026-11-09")
        self.assertTrue(all(item["count"] == 0 for item in empty["counts"]))

    def test_current_catalog_filters_by_et_night_and_requires_date_evidence(self):
        import time
        row = {"player_id": "7", "player_name": "Night Fixture", "line": 20.5,
               "line_source": "espn_props", "prop_type": "points"}
        entry = {"ts": time.time(), "payload": {"props": [row], "players": [
            {"player_id": "7", "next_game": {"start_time": "2026-11-11T01:00:00Z"}}]}}
        self.assertEqual(preview("NBA", entry, day="2026-11-10")["total"], 1)
        self.assertIsNone(preview("NBA", entry, day="2026-11-11")["total"])
        entry["payload"]["players"] = []
        self.assertIsNone(preview("NBA", entry, day="2026-11-10")["total"])

    def test_invalid_date_and_sport(self):
        for day in ("bad", "2026-02-30", "2026-1-1", "2026-11-10' OR 1=1"):
            with self.assertRaises(ValueError):
                read_results(self.path, day=day)
        with self.assertRaises(ValueError):
            read_results(self.path, league="UNKNOWN", day="2026-11-10")

    def test_missing_db_is_not_created(self):
        target = self.path.with_name("absent.sqlite")
        with self.assertRaises(sqlite3.OperationalError):
            read_results(target)
        self.assertFalse(target.exists())

    def test_saved_picks_survive_absent_result_table(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TABLE player_prop_results")
        self.assertEqual(read_results(self.path, day="2026-11-10")["count"], 2)

    def test_current_real_market_only_no_confidence_clones(self):
        import time
        rows = [{"player_name": "Fixture Player", "line": 20.5, "prop_type": "points",
                 "line_source": "espn_props", "model_confidence": {"xsharp": 99}},
                {"player_name": "Fake", "line": 1.5, "line_source": "synthetic"}]
        data = preview("NBA", {"ts": time.time(), "payload": {"props": rows}})
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["model_forecasts"], [])
        self.assertNotIn("model_confidence", json.dumps(data))
        self.assertFalse(preview("NBA", {"ts": 0, "payload": {"props": rows}})["available"])
        self.assertFalse(preview("NBA", {"ts": 1e99})["available"])

    def app(self):
        spec = importlib.util.spec_from_file_location("props_hub_api_fixture", ROOT / "web_api.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        app = Flask(__name__)
        app.config.update(SECRET_KEY="test-only-fixture", TESTING=True)
        login = LoginManager(app)
        login.user_loader(lambda identity: User(identity))
        app.register_blueprint(module.api)
        return app

    def test_guest_free_expired_cannot_access_private_data_on_any_host(self):
        app = self.app()
        fake_auth = SimpleNamespace(is_premium_user=lambda: current_user.get_id() == "paid")
        with patch.dict(sys.modules, {"auth_system": fake_auth}):
            for host in ("localhost", "127.0.0.1", "predictionlab.io"):
                for role in (None, "free", "expired"):
                    client = app.test_client()
                    if role:
                        with client.session_transaction(base_url="http://" + host) as session:
                            session["_user_id"] = role
                    for path in ("/api/web/props-hub", "/api/web/props-results"):
                        response = client.get(path, base_url="http://" + host)
                        self.assertEqual(response.status_code, 403 if role else 401)
                        self.assertNotIn(b"Fixture Player", response.data)
                        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_member_reader_and_cached_hub_never_call_backfill_or_build_all_sports(self):
        app = self.app()
        auth = SimpleNamespace(is_premium_user=lambda: current_user.get_id() == "paid")
        site = SimpleNamespace(DATABASE=str(self.path), _PROPS_ENGINE_MODULE=None,
                               _props_disk_load=lambda league: None)
        client = app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = "paid"
        with patch.dict(sys.modules, {"auth_system": auth, "NHL77FINAL": site}):
            result = client.get("/api/web/props-results?date=2026-11-10", headers={"Accept-Encoding": "gzip"})
            self.assertEqual(result.status_code, 200)
            self.assertIn("Cookie", result.headers["Vary"])
            raw = gzip.decompress(result.data) if result.headers.get("Content-Encoding") == "gzip" else result.data
            self.assertEqual(json.loads(raw)["count"], 2)
            response = client.get("/api/web/props-hub")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.json["sections"]), len(LEAGUES))

    def test_global_boundary_allows_only_navigation_metadata(self):
        import paywall
        app = self.app()
        paywall.install_access_policy(app)
        auth = SimpleNamespace(is_premium_user=lambda: current_user.get_id() == "paid")
        site = SimpleNamespace(DATABASE=str(self.path), _PROPS_ENGINE_MODULE=None,
                               _props_disk_load=lambda league: None)
        with patch.dict(sys.modules, {"auth_system": auth, "NHL77FINAL": site}):
            for identity in (None, "free", "expired"):
                client = app.test_client()
                if identity:
                    with client.session_transaction() as session:
                        session["_user_id"] = identity
                response = client.get("/api/web/props-navigation?date=2026-11-10")
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Fixture Player", response.get_data(as_text=True))
                self.assertNotIn("model_forecasts", response.get_data(as_text=True))
                for path in ("/api/web/props-hub", "/api/web/props-results"):
                    denied = client.get(path)
                    self.assertEqual(denied.status_code, 403 if identity else 401)


if __name__ == "__main__":
    unittest.main()
