from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from prop_forecast_store import first_recorded_dates
from sports_props_hub import read_results


class HistoryDateTests(unittest.TestCase):
    def test_first_date_is_model_and_league_specific_and_readonly(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            original = root / 'original.sqlite'
            sqlite3.connect(original).close()
            ledger = root / 'prop_forecasts_v2.sqlite'
            with sqlite3.connect(ledger) as conn:
                conn.execute('CREATE TABLE forecasts(id TEXT, league TEXT, pick_date TEXT, payload TEXT)')
                conn.executemany('INSERT INTO forecasts VALUES(?,?,?,?)', [
                    ('a', 'NBA', '2026-11-11', json.dumps({'model_id': 'glicko2'})),
                    ('b', 'NHL', '2026-11-12', json.dumps({'model_id': 'glicko2'})),
                    ('c', 'NBA', '2026-11-13', json.dumps({'model_id': 'xgboost'})),
                ])
                conn.execute('CREATE TABLE settlements(id INTEGER, forecast_id TEXT, payload TEXT)')
            before = ledger.read_bytes()
            self.assertEqual(first_recorded_dates(original, 'NHL'), {'glicko2': '2026-11-12'})
            data = read_results(original, 'NBA', '2026-11-10', window='7')
            self.assertEqual(data['count'], 0)
            self.assertEqual(next(m for m in data['models'] if m['id'] == 'glicko2')['first_recorded_date'], '2026-11-11')
            self.assertEqual(ledger.read_bytes(), before)

    def test_missing_ledger_is_not_created(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            self.assertEqual(first_recorded_dates(Path(directory) / 'original.sqlite'), {})
            self.assertFalse((Path(directory) / 'prop_forecasts_v2.sqlite').exists())


if __name__ == '__main__':
    unittest.main()
