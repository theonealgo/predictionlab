from soccer_nav_counts import counts
import unittest


class SoccerCountTests(unittest.TestCase):
    def test_distinct_days_duplicates_and_known_catalog(self):
        def card(identity, day, league):
            return {"date": day, "html": f'<div data-game-id="{identity}" data-league="{league}"></div>'}
        first = card("1", "2026-11-11", "English Premier League")
        result = counts({"cards": [first, first, card("2", "2026-11-12", "MLS"),
                                   card("3", "2026-11-13", "MLS"),
                                   card("4", "2026-11-12", "Unconfigured League")]}, "2026-11-11")
        self.assertEqual(result["today"], {"English Premier League": 1})
        self.assertEqual(result["tomorrow"], {"Major League Soccer": 1})
        self.assertTrue(result["available"])

    def test_no_cache_is_not_a_verified_zero(self):
        self.assertFalse(counts(None, "2026-11-11")["available"])
        self.assertTrue(counts({"cards": []}, "2026-11-11")["available"])


if __name__ == "__main__":
    unittest.main()
