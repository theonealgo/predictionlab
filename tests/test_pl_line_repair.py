from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import pl_line_repair as repair


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name)
        self.database = self.cache / "test.db"
        with sqlite3.connect(self.database) as conn:
            conn.execute("CREATE TABLE predictions (sport,game_id,home_team_id,away_team_id,"
                         "lock_pl_spread,lock_pl_total,lock_card_json,actual_home_score,actual_away_score)")
            conn.execute("INSERT INTO predictions VALUES ('NHL','NHL_123','Vancouver Canucks',"
                         "'New York Rangers',NULL,4,?,NULL,NULL)",
                         (json.dumps({"our_total": 4, "elo_prob": 49.9}),))
        self.event = {
            "id": "123", "date": (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat(),
            "competitions": [{"status": {"type": {"state": "pre"}}, "competitors": [
                {"homeAway": "home", "team": {"displayName": "Vancouver Canucks"}},
                {"homeAway": "away", "team": {"displayName": "New York Rangers"}}]}]}

    def row(self):
        with sqlite3.connect(self.database) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM predictions").fetchone())

    def run_repair(self, result=None):
        response = Mock()
        response.json.return_value = {"header": self.event}
        with patch("requests.get", return_value=response), patch("odds_engine_espn.get_odds",
                return_value=result or {"spread_home": 2.5}) as engine, patch("os.nice"), patch("signal.alarm"):
            count = repair.run(self.database, self.cache, ["NHL_123"])
        return count, engine

    def test_only_missing_spread_changes(self):
        before = self.row()
        count, engine = self.run_repair()
        after = self.row()
        self.assertEqual(count, 1)
        self.assertEqual(after["lock_pl_spread"], -2.5)
        self.assertEqual({k: v for k, v in before.items() if k not in ("lock_pl_spread", "lock_card_json")},
                         {k: v for k, v in after.items() if k not in ("lock_pl_spread", "lock_card_json")})
        self.assertEqual(json.loads(after["lock_card_json"])["our_total"], 4)
        self.assertEqual(json.loads(after["lock_card_json"])["elo_prob"], 49.9)
        engine.assert_called_once_with("NHL", "Vancouver Canucks", "New York Rangers")
        self.assertEqual(self.run_repair()[0], 0)

    def test_final_and_live_are_not_recomputed(self):
        for state in ("post", "in"):
            self.event["competitions"][0]["status"]["type"]["state"] = state
            before = self.row()
            count, engine = self.run_repair()
            self.assertEqual(count, 0)
            engine.assert_not_called()
            self.assertEqual(self.row(), before)

    def test_mismatched_teams_are_rejected(self):
        self.event["competitions"][0]["competitors"][0]["team"]["displayName"] = "Other"
        self.assertEqual(self.run_repair()[0], 0)

    def test_unavailable_engine_does_not_invent_a_line(self):
        before = self.row()
        self.assertEqual(self.run_repair({"spread_home": None})[0], 0)
        self.assertEqual(self.row(), before)

    def test_blank_html_only(self):
        html = '<div data-pl-spread=""><tr><td class="market-k">Puck Line</td><td class="val-pl">—</td><td class="val-xs">Vancouver Canucks -1.5</td></tr><tr><td class="market-k">Total</td><td class="val-pl">4</td></tr></div>'
        card = {"html": html, "home": "Vancouver Canucks", "away": "New York Rangers"}
        result = repair.fill_html(card, -2.5)["html"]
        self.assertIn('<td class="val-pl">New York Rangers -2.5</td>', result)
        self.assertIn('<td class="val-pl">4</td>', result)
        self.assertIn('<td class="val-xs">Vancouver Canucks -1.5</td>', result)
        self.assertEqual(repair.fill_html({**card, "html": result}, 1.5)["html"], result)

    def test_refresh_uses_saved_fields_without_a_model_call(self):
        self.run_repair()
        card = {"html": '<div data-game-id="NHL_123" data-pl-spread=""><tr><td class="market-k">Puck Line</td><td class="val-pl">—</td></tr></div>',
                "home": "Vancouver Canucks", "away": "New York Rangers", "isFinal": False}
        with patch("odds_engine_espn.get_odds") as engine, patch.object(repair, "start") as start:
            result = repair.refresh({"cards": [card]}, self.database)
            engine.assert_not_called()
            start.assert_called_once_with(self.database, [])
        self.assertIn("New York Rangers -2.5", result["cards"][0]["html"])

    def test_pl_and_efficiency_fill_together_without_copying_another_model(self):
        html = ('<div data-pl-spread="" data-m-efficiency="">'
                '<tr><td class="market-k">Puck Line</td><td class="val-pl">—</td></tr>'
                '<div class="pc-name">Grinder2</div><div class="pc-val">59.3%</div><div class="pc-side away">Rangers</div>'
                '<div class="pc-box "><div class="pc-name">Efficiency</div>'
                '<div class="pc-val" title="unavailable">N/A</div>'
                '<div class="pc-side" title="unavailable">N/A</div></div></div>')
        card = {"html": html, "home": "Vancouver Canucks", "away": "New York Rangers"}
        result = repair.fill_html(card, -2.5)["html"]
        self.assertIn('<td class="val-pl">New York Rangers -2.5</td>', result)
        self.assertIn('<div class="pc-val">98.1%</div><div class="pc-side away">Rangers</div>', result)
        self.assertIn('data-m-efficiency="1.9"', result)
        self.assertIn('<div class="pc-val">59.3%</div>', result)
        self.assertEqual(repair.fill_html({**card, "html": result}, 1.5)["html"], result)


if __name__ == "__main__":
    unittest.main()
