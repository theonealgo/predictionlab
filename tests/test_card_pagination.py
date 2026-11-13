import concurrent.futures
import unittest
from unittest.mock import patch

import card_pagination as paging
from slate_freshness import current_payload


class CardPaginationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(game_id=str(i), game_date="2099-01-01", home_score=None,
                          ensemble_prob=64.2, custom_metadata={"preserved": True}) for i in range(13)]
        self.grouped = {"2099-01-01": self.rows}

    def select(self):
        return paging.select(self.rows, self.grouped, ["2099-01-01"], "2099-01-01")

    def test_original_path_is_unchanged_without_window(self):
        result = self.select()
        self.assertIs(result[0], self.rows)
        self.assertIs(result[1], self.grouped)

    def test_at_most_five_original_card_objects_reach_preparation(self):
        with paging.window(0, 5) as page:
            rows, grouped, dates, default = self.select()
            self.assertEqual(len(rows), 5)
            self.assertEqual(sum(map(len, grouped.values())), 5)
            self.assertIs(rows[0], self.rows[0])
            self.assertEqual(rows[0]["custom_metadata"], {"preserved": True})
            self.assertEqual((page["total"], page["nextOffset"]), (13, 5))
        self.assertFalse(paging.active())

    def test_pages_have_no_duplicates_or_omissions(self):
        seen = []
        for offset in (0, 5, 10):
            with paging.window(offset, 5) as page:
                seen.extend(row["game_id"] for row in self.select()[0])
        self.assertIsNone(page["nextOffset"])
        self.assertEqual(seen, [str(i) for i in range(13)])

    def test_context_does_not_leak_to_other_threads(self):
        with paging.window(0, 5):
            with concurrent.futures.ThreadPoolExecutor(1) as executor:
                self.assertFalse(executor.submit(paging.active).result())

    def test_partial_html_never_replaces_full_page_cache(self):
        import isolate_checker_fixes as fixes
        with paging.window(0, 5), patch.object(fixes, "_viewer_tier", side_effect=AssertionError):
            self.assertEqual(fixes.lookup_served_picks("NBA"), "")
            fixes.store_served_picks("NBA", "<html>partial</html>")
            fixes._persist_cards_html("NBA", "<html>partial</html>")

    def test_date_filter_preserves_total_and_cursor_for_a_page(self):
        payload = {"cards": [{"date": "2099-01-01", "html": ""} for _ in range(5)],
                   "offset": 0, "total": 13, "nextOffset": 5, "hasMore": True}
        result = current_payload(payload, day="2099-01-01")
        self.assertEqual(result["total"], 13)
        self.assertEqual(result["nextOffset"], 5)
        self.assertEqual(len(result["cards"]), 5)


if __name__ == "__main__":
    unittest.main()
