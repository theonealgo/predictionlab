from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from prop_forecast_store import first_recorded_dates, latest_results_date
from sports_props_hub import default_results_day, read_results


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
                conn.execute('CREATE TABLE settlements(id INTEGER PRIMARY KEY, forecast_id TEXT, payload TEXT)')
                conn.execute(
                    "INSERT INTO settlements(forecast_id, payload) VALUES(?, ?)",
                    ('a', json.dumps({'result': 'Win'})),
                )
            before = ledger.read_bytes()
            self.assertEqual(first_recorded_dates(original, 'NHL'), {'glicko2': '2026-11-12'})
            self.assertEqual(latest_results_date(original), '2026-11-11')
            self.assertEqual(default_results_day(original, 'NBA'), '2026-11-11')
            data = read_results(original, 'NBA', '2026-11-10', window='7')
            self.assertEqual(data['count'], 0)
            self.assertEqual(next(m for m in data['models'] if m['id'] == 'glicko2')['first_recorded_date'], '2026-11-11')
            self.assertEqual(ledger.read_bytes(), before)

    def test_missing_ledger_is_not_created(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            self.assertEqual(first_recorded_dates(Path(directory) / 'original.sqlite'), {})
            self.assertIsNone(latest_results_date(Path(directory) / 'original.sqlite'))
            self.assertFalse((Path(directory) / 'prop_forecasts_v2.sqlite').exists())

    def test_read_results_defaults_to_latest_settled_night(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            original = root / 'original.sqlite'
            sqlite3.connect(original).close()
            ledger = root / 'prop_forecasts_v2.sqlite'
            with sqlite3.connect(ledger) as conn:
                conn.execute('CREATE TABLE forecasts(id TEXT PRIMARY KEY, league TEXT, pick_date TEXT, payload TEXT)')
                conn.execute('CREATE TABLE settlements(id INTEGER PRIMARY KEY, forecast_id TEXT, payload TEXT)')
                conn.execute(
                    "INSERT INTO forecasts VALUES(?,?,?,?)",
                    ('f1', 'NBA', '2026-11-12', json.dumps({
                        'model_id': 'glicko2', 'model_name': 'Grinder2', 'league': 'NBA',
                        'pick_date': '2026-11-12', 'player_id': '1', 'player_name': 'A',
                        'team': 'X', 'prop_type': 'points', 'pick': 'OVER', 'line': 20,
                        'event_id': 'e1', 'line_type': 'market', 'probability': 55.0,
                    })),
                )
                conn.execute(
                    "INSERT INTO settlements(forecast_id, payload) VALUES(?, ?)",
                    ('f1', json.dumps({'actual': 22, 'result': 'Win', 'reason': 'ok'})),
                )
            data = read_results(original, 'NBA', None, window='night')
            self.assertEqual(data['result_date'], '2026-11-12')
            self.assertEqual(data['count'], 1)


if __name__ == '__main__':
    unittest.main()
