"""Landing page Top Value Picks — exclude TBD / placeholder matchups."""
import sqlite3
from datetime import datetime

import pytest

import NHL77FINAL as app_mod


def _seed_top_picks_db(db_path, today):
    conn = sqlite3.connect(db_path)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sport TEXT, league TEXT, game_id TEXT UNIQUE,
            season INTEGER, game_date TEXT,
            home_team_id TEXT, away_team_id TEXT,
            home_score REAL, away_score REAL, status TEXT
        );
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT, sport TEXT, league TEXT,
            game_date TEXT, home_team_id TEXT, away_team_id TEXT,
            elo_home_prob REAL, xgboost_home_prob REAL,
            logistic_home_prob REAL, meta_home_prob REAL,
            win_probability REAL, locked INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS betting_odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT, home_implied_prob REAL, away_implied_prob REAL
        );
    ''')
    conn.execute('DELETE FROM predictions')
    conn.execute('DELETE FROM games')
    conn.execute('DELETE FROM betting_odds')
    # Placeholder NBA Finals slot — should never appear on landing
    conn.execute('''
        INSERT INTO predictions (game_id, sport, game_date, home_team_id, away_team_id, win_probability)
        VALUES ('NBA_TBD', 'NBA', ?, 'TBD', 'TBD', 0.556)
    ''', (today,))
    # Valid MLB game with names only on games row
    conn.execute('''
        INSERT INTO games (game_id, sport, game_date, home_team_id, away_team_id, home_score, away_score)
        VALUES ('MLB_1', 'MLB', ?, 'Chicago Cubs', 'Athletics', NULL, NULL)
    ''', (today,))
    conn.execute('''
        INSERT INTO predictions (game_id, sport, game_date, home_team_id, away_team_id, win_probability)
        VALUES ('MLB_1', 'MLB', ?, 'TBD', 'TBD', 0.532)
    ''', (today,))
    conn.commit()
    conn.close()


def test_build_todays_top_picks_skips_tbd_matchups(tmp_path, monkeypatch):
    today = datetime.now(app_mod.ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    db_path = str(tmp_path / 'test.db')
    _seed_top_picks_db(db_path, today)
    monkeypatch.setattr(app_mod, 'DATABASE', db_path)

    picks = app_mod.build_todays_top_picks()

    assert picks, 'expected at least one valid pick from seeded MLB game'
    for p in picks:
        assert p['home'] != 'TBD', p
        assert p['away'] != 'TBD', p
        assert p['pick'] != 'TBD', p
    assert not any(p['sport'] == 'NBA' for p in picks)


def test_landing_page_html_has_no_tbd_vs_tbd(monkeypatch):
    fake_picks = [
        {'away': 'Athletics', 'home': 'Chicago Cubs', 'pick': 'Chicago Cubs',
         'prob': 53.2, 'sport': 'MLB', 'slug': 'mlb-picks'},
    ]
    monkeypatch.setattr(app_mod, 'build_todays_top_picks', lambda: fake_picks)
    client = app_mod.app.test_client()
    resp = client.get('/')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'TBD vs TBD' not in html
    assert 'Chicago Cubs' in html
    assert 'Athletics' in html
