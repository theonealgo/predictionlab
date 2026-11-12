import ast
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from contextlib import nullcontext
from types import SimpleNamespace
import importlib.util
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[1]


def functions():
    names = {'_fill_college_card_gaps', '_set_card_book_lines',
             '_persist_upcoming_prediction_row', '_persist_lock_card_json'}
    tree = ast.parse((ROOT / 'NHL77FINAL.py').read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    def number(value):
        try:
            return float(value) if value is not None else None
        except (ValueError, TypeError):
            return None
    namespace = {'_safe_float': number, '_to_float_safe': number, '_round_to_half': lambda v: round(v*2)/2,
                 '_book_row_is_synthetic': lambda source: source == 'model',
                 '_bulk_load_book_lines_from_db': Mock()}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / 'NHL77FINAL.py'), 'exec'), namespace)
    return namespace


class MissingDataTests(unittest.TestCase):
    def setUp(self):
        self.env = functions()
        self.market = {'home_moneyline': 2500, 'away_moneyline': -9000,
                       'spread': 24.5, 'total': 139.5, 'source': 'ESPN Core'}
        self.env['_bulk_load_book_lines_from_db'].return_value = {'NCAAW_401907049': self.market}
        self.card = {'game_id': 'NCAAW_401907049', 'efficiency_prob': 18.4,
                     'elo_prob': 45.9, 'xgb_prob': 45.9, 'ensemble_prob': 45.9,
                     'pl_model_home_ml': 109, 'pl_model_away_ml': -129,
                     'book_home_moneyline': 109, 'book_away_moneyline': -129,
                     'stored_lock_card': {'elo_prob': 45.9}}

    def test_recovers_real_market_without_changing_models_or_lock(self):
        before = deepcopy(self.card)
        self.env['_fill_college_card_gaps'](self.card, 'NCAAW')
        self.assertEqual(self.card['book_home_moneyline'], 2500)
        self.assertEqual(self.card['book_away_moneyline'], -9000)
        self.assertEqual(self.card['disp_book_spread'], -24.5)
        self.assertEqual(self.card['disp_book_total'], 139.5)
        for key in ('elo_prob', 'xgb_prob', 'ensemble_prob', 'stored_lock_card', 'pl_model_home_ml'):
            self.assertEqual(self.card[key], before[key])

    def test_missing_prices_still_hydrate_and_real_saved_prices_stay(self):
        self.card.update(book_home_moneyline=None, book_away_moneyline=None)
        self.env['_fill_college_card_gaps'](self.card, 'NCAAW')
        self.assertEqual(self.card['book_home_moneyline'], 2500)
        self.card.update(book_home_moneyline=1800, book_away_moneyline=-4000)
        self.env['_fill_college_card_gaps'](self.card, 'NCAAW')
        self.assertEqual(self.card['book_home_moneyline'], 1800)

    def test_synthetic_and_completed_and_other_sports_do_not_fetch(self):
        self.market['source'] = 'model'
        self.env['_fill_college_card_gaps'](self.card, 'NCAAW')
        self.assertIsNone(self.card['book_home_moneyline'])
        self.env['_bulk_load_book_lines_from_db'].reset_mock()
        self.card['home_score'] = 0
        self.env['_fill_college_card_gaps'](self.card, 'NCAAW')
        self.card.pop('home_score')
        self.env['_fill_college_card_gaps'](self.card, 'NCAAB')
        self.env['_bulk_load_book_lines_from_db'].assert_not_called()

    def test_partial_forecast_cannot_be_persisted_or_locked(self):
        cursor = Mock()
        for name in ('_persist_upcoming_prediction_row', '_persist_lock_card_json'):
            self.assertFalse(self.env[name](cursor, 'NCAAW', self.card))
        cursor.execute.assert_not_called()

    def test_cache_invalidation_leaves_other_sports_intact(self):
        spec = importlib.util.spec_from_file_location('ncaaw_invalidation_fixture', ROOT / 'ncaaw_score_sync.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            directory = Path(folder)
            (directory / 'web_cards').mkdir()
            for name in ('served_picks_NCAAW.html', 'served_picks_NBA.html',
                         'web_cards/ncaaw-picks.member.v2.json.gz', 'web_cards/nba-picks.member.v2.json.gz'):
                (directory / name).write_text('fixture')
            api = SimpleNamespace(_CARDS_LOCK=threading.Lock(),
                                  _CARDS={('ncaaw-picks', 'member'): 1, ('nba-picks', 'member'): 2})
            site = SimpleNamespace(_SPORT_RESULTS_CACHE={'NCAAW': 1, 'NBA': 2},
                                   _SPORT_PREDICTIONS_PAGE_CACHE={'ncaaw': 1, 'nba': 2},
                                   app=SimpleNamespace(wsgi_app=object()))
            modules = {'web_api': api, 'served_cache': SimpleNamespace(cache_dir=lambda: directory),
                       'sport_variant': SimpleNamespace(sport_scope=lambda sport: nullcontext()),
                       'isolate_checker_fixes': SimpleNamespace(_SERVED={}),
                       'picks_recent_results': SimpleNamespace(_CACHE={}),
                       'team_results_charts': SimpleNamespace(_CHART_SOURCE_HTML={})}
            with patch.dict('sys.modules', modules):
                module.invalidate_site(site)
            self.assertEqual(api._CARDS, {('nba-picks', 'member'): 2})
            self.assertFalse((directory / 'served_picks_NCAAW.html').exists())
            self.assertFalse((directory / 'web_cards/ncaaw-picks.member.v2.json.gz').exists())
            self.assertTrue((directory / 'served_picks_NBA.html').exists())
            self.assertTrue((directory / 'web_cards/nba-picks.member.v2.json.gz').exists())


if __name__ == '__main__':
    unittest.main()
