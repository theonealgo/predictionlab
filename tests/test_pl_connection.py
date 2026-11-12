import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from odds_engine.model.prop_math import calculate, normal_probabilities, count_probabilities, VERSION
from prop_forecast_store import capture, read, ledger_path

PACKAGE = "pl_isolated_props"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "standalone-player-props/backend/app")]
sys.modules[PACKAGE] = package
lines = importlib.import_module(PACKAGE + ".pl_lines")
source = importlib.import_module(PACKAGE + ".data_sources")
engine = importlib.import_module(PACKAGE + ".engine")
from sports_props_hub import preview


def player():
    return {"player_id": "7", "name": "Fixture Player", "team": "Fixture Team", "team_id": "1",
            "league": "NBA", "role": "G", "position": "G", "history_verified": True,
            "stat_history": {"points": [30, 28, 31, 33, 29, 27, 30, 32, 28, 29]},
            "next_game": {"event_id": "123", "start_time": "2030-11-11T00:00:00Z"}}


class MathTests(unittest.TestCase):
    def test_real_threshold_controls_over_under(self):
        stats = {"recent_form": [30]*10}
        self.assertEqual(calculate(stats, "NBA", "points", 20.5)["pick"], "OVER")
        self.assertEqual(calculate(stats, "NBA", "points", 40.5)["pick"], "UNDER")
        self.assertTrue(calculate(stats, "NBA", "points", 20.5)["strong_pick"])

    def test_integer_push_and_half_line(self):
        for function in (normal_probabilities, count_probabilities):
            for line in (0, 0.5, 2, 2.5, 30, 30.5):
                probs = function(10, 20, line)
                self.assertAlmostEqual(sum(probs.values()), 1)
                self.assertTrue(all(0 <= value <= 1 for value in probs.values()))
                if line.is_integer() if isinstance(line, float) else True:
                    self.assertGreaterEqual(probs["push"], 0)
                else:
                    self.assertAlmostEqual(probs["push"], 0)

    def test_actual_stats_not_nba_minutes_proxy(self):
        data = calculate({"recent_form": [10]*10, "minutes_played": 40, "usage_rate": 0.38}, "NBA", "points")
        self.assertLess(data["projection"], 11)
        self.assertEqual(data["fair_line"], 10)
        yards = calculate({"recent_form": [250]*10}, "NFL", "passing_yards")
        self.assertEqual(yards["projection"], 250)
        self.assertEqual(yards["fair_line"], 250)

    def test_missing_stats_do_not_become_synthetic_lines(self):
        for values in ([], [1, 2], [1, 2, None, 4, 5], [float("nan")]*5):
            with self.assertRaises(ValueError):
                calculate({"recent_form": values}, "NBA", "points")

    def test_deterministic_and_distinct_distributions(self):
        data = {"recent_form": [0, 2, 5, 0, 1, 3, 6, 1, 2, 4]}
        a = calculate(data, "NBA", "threes", 2.5)
        self.assertEqual(a, calculate(data, "NBA", "threes", 2.5))
        self.assertNotEqual(a["model_forecasts"][0]["probabilities"], a["model_forecasts"][1]["probabilities"])

    def test_count_cdf_matches_scipy(self):
        from scipy.stats import nbinom
        for mean, variance, line in ((3, 7, 2.5), (30, 50, 29), (0.1, 0.2, 0.5)):
            var = max(variance, mean*1.05)
            shape = mean*mean/(var-mean)
            success = shape/(shape+mean)
            actual = count_probabilities(mean, variance, line)
            import math
            self.assertAlmostEqual(actual["over"], nbinom.sf(math.floor(line), shape, success), places=10)


class WiringTests(unittest.TestCase):
    def test_pl_fallback_and_real_line_evaluation(self):
        rows = lines.lines_from_players("NBA", [player()], ["points"], lambda *a: True)
        self.assertEqual(rows[0]["line_source"], "pl_model")
        market = {"player_id": "7", "prop_type": "points", "line": 40.5,
                  "line_source": "espn_props", "event_id": "123", "event_start": "2030-11-11T00:00:00Z",
                  "odds_over": None, "odds_under": None}
        rows = lines.lines_from_players("NBA", [player()], ["points"], lambda *a: True, market_lines=[market])
        self.assertEqual(rows[0]["line"], 40.5)
        self.assertEqual(rows[0]["line_source"], "espn_props")
        self.assertTrue(all(f["side"] == "UNDER" for f in rows[0]["model_forecasts"]))
        self.assertIsNone(rows[0]["odds_over"])

    def test_provider_priority_and_no_random_fallback(self):
        offer = {"player_id": "7", "prop_type": "points", "line": 40.5, "line_source": "espn_props",
                 "event_id": "123", "event_start": "2030-11-11T00:00:00Z",
                 "odds_over": None, "odds_under": None}
        with patch.object(source, "fetch_real_prop_lines", return_value=[offer]), \
             patch.object(source, "ODDS_ENGINE_URL", ""), \
             patch.object(source, "_internal_nba_prop_lines", side_effect=AssertionError("random fallback called")):
            result = source.fetch_prop_lines("NBA", [player()])
            self.assertEqual(result[0]["line"], 40.5)
            self.assertEqual(result[0]["model_version"], VERSION)
            self.assertEqual(source.fetch_prop_lines("NBA", [player()], use_real=False), [])

    def test_service_failure_uses_same_local_pl_math(self):
        def failed(*args, **kwargs):
            raise RuntimeError("offline")
        rows = lines.lines_from_players("NBA", [player()], ["points"], lambda *a: True,
                                       endpoint="http://fixture.invalid", post=failed)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["line_source"], "pl_model")

    def test_hub_exposes_nonempty_calculated_models(self):
        import time
        offer = lines.lines_from_players("NBA", [player()], ["points"], lambda *a: True)[0]
        row = engine._pl_forecast_row("NBA", player(), offer, [])
        result = preview("NBA", {"ts": time.time(), "payload": {"players": [player()], "props": [row]}})
        self.assertEqual(len(result["items"][0]["model_forecasts"]), 2)
        self.assertIn(result["items"][0]["pick"], ("OVER", "UNDER"))
        self.assertEqual(result["items"][0]["line_type"], "pl")

    def test_different_event_lines_not_overwritten(self):
        offers = [{"player_id": "7", "prop_type": "points", "line": line, "line_source": "espn_props",
                   "event_id": event} for event, line in (("123", 20.5), ("124", 40.5))]
        result = lines.lines_from_players("NBA", [player()], ["points"], lambda *a: True, market_lines=offers)
        self.assertEqual([row["event_id"] for row in result], ["123", "124"])
        self.assertEqual(result[0]["model_forecasts"][0]["side"], "OVER")
        self.assertEqual(result[1]["model_forecasts"][0]["side"], "UNDER")

    def test_partial_game_props_are_not_full_game_projections(self):
        self.assertEqual(source._espn_to_internal_prop("NBA", "Total Points"), "points")
        for label in ("First Half Points", "1st Quarter Points", "Points and Assists", "Fantasy Points"):
            self.assertIsNone(source._espn_to_internal_prop("NBA", label))


class CaptureTests(unittest.TestCase):
    def test_future_capture_is_immutable_and_original_db_unchanged(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            original = Path(temporary)/"original.sqlite"
            with sqlite3.connect(original) as conn:
                conn.execute("CREATE TABLE original_rows(id INTEGER)")
            before = hashlib.sha256(original.read_bytes()).hexdigest()
            game = player()
            offered = lines.lines_from_players("NBA", [game], ["points"], lambda *a: True)[0]
            row = engine._pl_forecast_row("NBA", game, offered, [])
            section = {"league": "NBA", "items": [row]}
            first = datetime.fromisoformat("2030-11-10T12:00:00+00:00")
            self.assertEqual(capture(original, [section], first), 2)
            saved = read(original, "2030-11-10", "NBA")
            self.assertEqual(len(saved), 2)
            section["items"][0]["model_forecasts"][0]["probabilities"] = {"over": .9, "under": .1, "push": 0}
            capture(original, [section], first+timedelta(hours=1))
            self.assertEqual(read(original, "2030-11-10", "NBA"), saved)
            self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), before)
            with sqlite3.connect(ledger_path(original)) as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM forecasts").fetchone()[0], 2)

    def test_late_capture_never_creates_a_ledger(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            original = Path(temporary)/"original.sqlite"
            row = engine._pl_forecast_row("NBA", player(),
                                         lines.lines_from_players("NBA", [player()], ["points"], lambda *a: True)[0], [])
            result = capture(original, [{"league": "NBA", "items": [row]}],
                             datetime.fromisoformat("2030-11-12T00:00:00+00:00"))
            self.assertEqual(result, 0)
            self.assertFalse(ledger_path(original).exists())
