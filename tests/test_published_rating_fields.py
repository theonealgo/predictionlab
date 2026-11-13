import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from published_rating_fields import refresh


class RatingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name)/"test.db")
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE predictions (sport,game_id,game_date,lock_card_json,"
                         "glicko_home_prob,trueskill_home_prob,actual_home_score,actual_away_score)")
            conn.execute("INSERT INTO predictions VALUES ('NCAAB','game','2099-01-01',?,NULL,NULL,NULL,NULL)",
                         (json.dumps({"elo_prob": 55.7, "our_total": 140}),))
        self.payload = {"cards": [{"home": "Home", "away": "Away",
            "html": '<div data-game-id="game" data-m-grinder2="" data-m-takedown="">'
                    '<div class="game-time">7:00 PM ET</div>'
                    '<div class="pc-box"><div class="pc-name">Grinder2</div><div class="pc-val">N/A</div><div class="pc-side">N/A</div></div>'
                    '<div class="pc-box"><div class="pc-name">Takedown</div><div class="pc-val">N/A</div><div class="pc-side">N/A</div></div></div>'}]}

    def test_first_fill_uses_own_models_and_preserves_original_fields(self):
        model = Mock(return_value={"glicko2_prob": .5191, "trueskill_prob": .7093})
        result = refresh(self.payload, "NCAAB", self.db, model)
        self.assertIn("51.9%", result["cards"][0]["html"])
        self.assertIn("70.9%", result["cards"][0]["html"])
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT lock_card_json,glicko_home_prob,trueskill_home_prob FROM predictions").fetchone()
        snap = json.loads(row[0])
        self.assertEqual(snap["elo_prob"], 55.7)
        self.assertEqual(snap["our_total"], 140)
        self.assertEqual(row[1:], (.5191, .7093))

    def test_missing_historical_forecasts_are_never_created_after_the_game(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE predictions SET game_date='2000-01-01',actual_home_score=80,actual_away_score=70")
        model = Mock()
        result = refresh(self.payload, "NCAAB", self.db, model)
        model.assert_not_called()
        self.assertNotIn("✅", result["cards"][0]["html"])
        self.assertIn(">N/A<", result["cards"][0]["html"])

    def test_saved_historical_forecasts_can_be_restored_without_recalculation(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE predictions SET game_date='2000-01-01',glicko_home_prob=.60,trueskill_home_prob=.70,actual_home_score=80")
        model = Mock()
        result = refresh(self.payload, "NCAAB", self.db, model)
        model.assert_not_called()
        self.assertIn("60%", result["cards"][0]["html"])
        self.assertIn("70%", result["cards"][0]["html"])
