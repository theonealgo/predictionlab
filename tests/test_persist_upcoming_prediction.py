"""Unit tests for UNIQUE-safe prediction saves (_persist_upcoming_prediction_row)."""
import sqlite3
import unittest

from NHL77FINAL import _persist_upcoming_prediction_row


def _make_conn():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('''
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT NOT NULL,
            league TEXT NOT NULL,
            game_id TEXT NOT NULL,
            game_date DATE NOT NULL,
            home_team_id TEXT NOT NULL,
            away_team_id TEXT NOT NULL,
            elo_home_prob REAL,
            xgboost_home_prob REAL,
            win_probability REAL,
            locked INTEGER DEFAULT 0,
            UNIQUE(sport, game_date, home_team_id, away_team_id)
        )
    ''')
    return conn


def _pred(**overrides):
    base = {
        'game_id': 'MLB_401901849',
        'game_date': '2026-07-28',
        'home_team_id': 'New York Yankees',
        'away_team_id': 'Boston Red Sox',
        'league': 'MLB',
        'elo_prob': 55.0,
        'xgb_prob': 57.0,
        'ensemble_prob': 56.0,
        'home_score': None,
    }
    base.update(overrides)
    return base


class TestPersistUpcomingPrediction(unittest.TestCase):
    def test_insert_new_row(self):
        conn = _make_conn()
        cur = conn.cursor()
        self.assertTrue(_persist_upcoming_prediction_row(cur, 'MLB', _pred()))
        row = cur.execute('SELECT game_id, win_probability FROM predictions').fetchone()
        self.assertEqual(row['game_id'], 'MLB_401901849')
        self.assertAlmostEqual(row['win_probability'], 0.56)

    def test_same_game_id_is_noop(self):
        conn = _make_conn()
        cur = conn.cursor()
        self.assertTrue(_persist_upcoming_prediction_row(cur, 'MLB', _pred()))
        self.assertFalse(_persist_upcoming_prediction_row(cur, 'MLB', _pred()))
        self.assertEqual(cur.execute('SELECT COUNT(*) FROM predictions').fetchone()[0], 1)

    def test_unique_matchup_different_game_id_no_error(self):
        """Reproduce MLB UNIQUE collision: matchup exists under another game_id."""
        conn = _make_conn()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO predictions (
                game_id, sport, league, game_date, home_team_id, away_team_id,
                elo_home_prob, xgboost_home_prob, win_probability, locked
            ) VALUES (?, 'MLB', 'MLB', ?, ?, ?, 0.5, 0.5, 0.5, 1)
        ''', (
            'OLD_GAME_ID',
            '2026-07-28',
            'New York Yankees',
            'Boston Red Sox',
        ))
        # Old path: SELECT by game_id only would miss this, then INSERT → UNIQUE fail.
        changed = _persist_upcoming_prediction_row(cur, 'MLB', _pred(game_id='MLB_401901849'))
        self.assertTrue(changed)
        rows = cur.execute('SELECT game_id FROM predictions').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['game_id'], 'MLB_401901849')

    def test_skips_completed_games(self):
        conn = _make_conn()
        cur = conn.cursor()
        self.assertFalse(
            _persist_upcoming_prediction_row(cur, 'MLB', _pred(home_score=5))
        )
        self.assertEqual(cur.execute('SELECT COUNT(*) FROM predictions').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
