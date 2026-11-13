import copy
import unittest
from market_card_refresh import repair_card, real_source


class MarketTests(unittest.TestCase):
    def test_only_missing_market_fields_change(self):
        html = ('<div data-books-spread="" data-books-total="" data-m-edge="62.3">'
                '<div class="ml-line face-books-ml"><span>Market</span><span class="ml-num">N/A</span><button>i</button></div>'
                '<div class="ml-line face-books-ml"><span>Market</span><span class="ml-num">N/A</span></div>'
                '<table><tr><td class="market-k">Puck line</td><td class="val-books">N/A<button>i</button></td>'
                '<td class="val-pl">Home -1.5</td></tr><tr><td class="market-k">Total</td>'
                '<td class="val-books">N/A</td></tr></table></div>')
        card = {"html": html, "home": "Home", "away": "Away"}
        original = copy.deepcopy(card)
        row = dict(home_moneyline=-120, away_moneyline=100, spread=-1.5, total=6.5)
        result = repair_card(card, row)["html"]
        for value in ('>+100<', '>-120<', '>Home -1.5<', '>6.5<', 'data-m-edge="62.3"'):
            self.assertIn(value, result)
        self.assertEqual(card, original)
        self.assertEqual(repair_card({"html": result, "home": "Home", "away": "Away"},
                                     dict(home_moneyline=-900, away_moneyline=800, spread=2.5, total=9))["html"], result)
        final = {**card, "html": '<span class="game-time">FINAL</span>'+html}
        self.assertEqual(repair_card(final, row), final)

    def test_only_genuine_sources_are_eligible(self):
        for source in ("pl_book_odds_api", "ESPN Core API (live fallback)", "DraftKings"):
            self.assertTrue(real_source(source))
        for source in ("model fallback", "internal_odds_api", "engine", "", None):
            self.assertFalse(real_source(source))


if __name__ == "__main__":
    unittest.main()
