import unittest
from unittest.mock import patch
from card_pagination import window
import picks_recent_results


class DiscardedPanelTests(unittest.TestCase):
    def test_cards_api_keeps_card_html_without_building_discarded_panel(self):
        html = '<html><body><div class="game-card">Original card information</div></body></html>'
        with window(0, 5), patch.object(picks_recent_results, "_cached_recent",
                                       side_effect=AssertionError("unused result rebuild")):
            self.assertEqual(picks_recent_results.inject_picks_recent_results(html, "NCAAB"), html)

    def test_normal_page_retains_recent_results_feature(self):
        html = '<html><body><div class="game-card">Original card information</div></body></html>'
        block = '<section id="picks-recent-results">Original results</section>'
        with patch.object(picks_recent_results, "_cached_recent", return_value=(block, "")):
            result = picks_recent_results.inject_picks_recent_results(html, "NCAAB")
        self.assertIn(block, result)
        self.assertIn('Original card information', result)
