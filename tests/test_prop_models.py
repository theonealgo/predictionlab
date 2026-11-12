import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prop_models import forecast, observations, train_xgboost, REGISTRY

AS_OF = datetime(2030, 12, 1, 12, tzinfo=timezone.utc)


def player(identity=1):
    return {"player_id": str(identity), "stat_observations": {"points": [
        {"event_id": str(identity*1000+i), "date": (AS_OF-timedelta(days=40-i)).isoformat(),
         "actual": 20+identity%3+(10 if i%2 else 0)} for i in range(36)]}}


class PropModelTests(unittest.TestCase):
    def test_rating_families_are_real_distinct_algorithms(self):
        rows, missing = forecast(player(), "points", 25.5, AS_OF)
        self.assertEqual({row["model_id"] for row in rows}, {"elo", "glicko2", "trueskill"})
        self.assertGreater(len({round(row["probabilities"]["over"], 6) for row in rows}), 1)
        self.assertIn("efficiency", {row["model_id"] for row in missing})
        self.assertIn(("xgboost", "XSharp"), REGISTRY)

    def test_integer_push_half_lines_and_zero_bound(self):
        for line in (0, 0.5, 21, 21.5, 25, 25.5):
            rows, missing = forecast(player(), "points", line, AS_OF)
            self.assertEqual(len(rows), 3, missing)
            for row in rows:
                probs = row["probabilities"]
                self.assertAlmostEqual(sum(probs.values()), 1)
                self.assertTrue(all(0 <= value <= 1 for value in probs.values()))
                if line%1:
                    self.assertAlmostEqual(probs["push"], 0)
                if line == 0:
                    self.assertEqual(probs["under"], 0)

    def test_future_outcomes_cannot_change_forecasts(self):
        before = forecast(player(), "points", 25.5, AS_OF)
        altered = player()
        altered["stat_observations"]["points"].append({
            "event_id": "999999", "date": (AS_OF+timedelta(days=1)).isoformat(), "actual": 999})
        self.assertEqual(before, forecast(altered, "points", 25.5, AS_OF))

    def test_missing_undated_history_does_not_get_named_model_labels(self):
        rows, unavailable = forecast({"stat_history": {"points": [30]*20}}, "points", 25.5, AS_OF)
        self.assertEqual(rows, [])
        self.assertEqual(len(unavailable), 6)

    def test_real_xgboost_training_and_chronological_gate(self):
        players = [player(i) for i in range(1, 9)]
        artifact = train_xgboost("NBA", "points", players, AS_OF)
        self.assertTrue(artifact["available"], {k:v for k,v in artifact.items() if k != "model"})
        self.assertEqual(type(artifact["model"]).__name__, "XGBClassifier")
        self.assertLess(artifact["training_last_date"], AS_OF.date().isoformat())
        self.assertLessEqual(artifact["brier"], .25)
        self.assertNotIn("win_rate", artifact)
        rows, missing = forecast(players[0], "points", 25.5, AS_OF, artifact)
        self.assertEqual(len(rows), 5)
        self.assertEqual({row["model_id"] for row in missing}, {"efficiency"})
        altered = copy.deepcopy(players)
        altered[0]["stat_observations"]["points"].append({
            "event_id": "999999", "date": (AS_OF+timedelta(days=1)).isoformat(), "actual": 999})
        self.assertIs(artifact, train_xgboost("NBA", "points", altered, AS_OF))
