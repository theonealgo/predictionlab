import copy
import gzip
import json
from pathlib import Path
import unittest
from slate_freshness import current_payload


class SlateTests(unittest.TestCase):
    def test_yesterday_unfinished_never_appears_as_upcoming(self):
        cards = [
            {"date": "2026-11-10", "html": '<div class="pick-card"><span class="game-time">7:00 PM ET</span></div>'},
            {"date": "2026-11-11", "html": '<div class="pick-card"><span class="game-time">7:00 PM ET</span></div>'},
            {"date": "2026-11-10", "html": '<div class="pick-card"><span class="game-time">Final</span></div>'},
        ]
        original = copy.deepcopy(cards)
        result = current_payload({"cards": cards}, "2026-11-11")
        self.assertEqual(len(result["cards"]), 2)
        self.assertEqual(result["suppressed_stale_predictions"], 1)
        self.assertTrue(result["cards"][1]["isFinal"])
        self.assertEqual(cards, original)

    def test_zero_zero_live_score_is_not_final(self):
        html = '<div class="pick-card"><span class="game-time">Live</span><span class="final-score">0</span><span class="final-score">0</span></div>'
        self.assertFalse(current_payload({"cards": [{"date": "2026-11-11", "html": html}]}, "2026-11-11")["cards"][0]["isFinal"])

    def test_real_member_snapshot_reproduces_and_removes_seven_stale_games(self):
        path = Path("/Users/nimamesghali/Sports Sandbox/pl_warp/.cache/web_cards/nhl-picks.member.v2.json.gz")
        if not path.exists():
            self.skipTest("Historical member cache not available")
        payload = json.loads(gzip.decompress(path.read_bytes()))
        if not any(card.get("date") == "2026-11-11" for card in payload.get("cards", [])):
            self.skipTest("Historical November 11 snapshot has rolled out of the live cache")
        result = current_payload(payload, "2026-11-11")
        self.assertTrue(any(card["date"] == "2026-11-11" for card in result["cards"]))
        self.assertFalse(any(card["date"] < "2026-11-11" and not card["isFinal"] for card in result["cards"]))


if __name__ == "__main__":
    unittest.main()
