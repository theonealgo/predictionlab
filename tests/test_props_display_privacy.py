import copy
from datetime import datetime
import importlib.util
from pathlib import Path
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clean_props_hub", ROOT / "sports_props_hub.py")
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)


class PrivacyTests(unittest.TestCase):
    def test_rank_details_remain_server_side_and_category_previews_survive(self):
        day = datetime.now(hub.ET).date().isoformat()
        entry = {"ts": time.time(), "payload": {"requested_date": day, "player_pool_version": "internal",
                 "eligibility_audit": {"pool": {"by_market": {"points": {"selected": 74}}}},
                 "props": [{"player_id": str(index), "player_name": "Fixture", "prop_type": market,
                            "line": 20.5, "line_source": "pl_model", "event_start": day+"T23:00:00-05:00",
                            "category_ranking": {"rank": 1, "average": 20, "sample": 10}}
                           for market in ("points", "rebounds") for index in range(5)]}}
        before = copy.deepcopy(entry)
        result = hub.preview("NBA", entry, limit=3, day=day)
        self.assertEqual(len(result["items"]), 6)
        self.assertEqual(result["total"], 10)
        self.assertNotIn("category_pools", result)
        self.assertNotIn("player_pool_version", result)
        self.assertTrue(all("category_ranking" not in row for row in result["items"]))
        self.assertEqual(entry, before)

    def test_customer_ui_omits_pool_details_and_pick_rules(self):
        source = (ROOT / "web/src/pages/PropsPage.jsx").read_text()
        for phrase in ("Top 100 players", "ranked players", "verified appearances",
                       "category_ranking", "Strong picks require", "section.count_kind"):
            self.assertNotIn(phrase, source)


if __name__ == "__main__":
    unittest.main()
