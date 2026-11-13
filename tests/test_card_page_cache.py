import time
import unittest
from unittest.mock import patch
from flask import Flask
import web_api


class CardPageCacheTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.payload = {"cards": [{"date": "2099-01-01", "html": str(i)} for i in range(13)],
                        "css": "original", "shareImage": "original"}

    def test_cold_miss_is_retryable_and_does_not_render_on_request_thread(self):
        with self.app.test_request_context("/api/web/sports/nba-picks/cards?limit=5"), \
                patch.object(web_api, "_saved_cards", return_value=None), \
                patch.object(web_api, "_start_card_page", return_value=True), \
                patch.object(web_api, "_build_cards", side_effect=AssertionError):
            payload, status = web_api._card_page("nba-picks", "guest", self.app, 0, 5)
        self.assertEqual(status, 202)
        self.assertTrue(payload["building"])
        self.assertEqual(payload["nextOffset"], 0)

    def test_old_complete_feed_paints_but_revalidates_only_requested_five(self):
        old = (time.time()-120, self.payload)
        with self.app.test_request_context("/api/web/sports/nba-picks/cards?limit=5"), \
                patch.object(web_api, "_saved_cards", side_effect=[old, None]), \
                patch.object(web_api, "_start_card_page", return_value=True) as start:
            payload, status = web_api._card_page("nba-picks", "member", self.app, 5, 5)
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["cards"]), 5)
        self.assertEqual(payload["total"], 13)
        self.assertEqual(payload["nextOffset"], 10)
        self.assertTrue(payload["refreshing"])
        self.assertEqual(start.call_args.args[:3], ("nba-picks", "member", "member-page-5-5"))
        self.assertEqual(start.call_args.args[-2:], (5, 5))

    def test_role_specific_page_cache_is_not_shared(self):
        with self.app.test_request_context("/api/web/sports/nba-picks/cards?limit=5"), \
                patch.object(web_api, "_saved_cards", return_value=None) as saved, \
                patch.object(web_api, "_start_card_page", return_value=True):
            web_api._card_page("nba-picks", "guest", self.app, 0, 5)
        self.assertEqual([call.args for call in saved.call_args_list],
                         [("nba-picks", "guest"), ("nba-picks", "guest-page-0-5")])


if __name__ == "__main__":
    unittest.main()
