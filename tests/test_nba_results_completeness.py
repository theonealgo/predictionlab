from datetime import datetime, timedelta
import importlib.util
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).with_name("isolate_checker_fixes.py")
if not SOURCE.is_file():
    SOURCE = ROOT / "sport_variants/nba/isolate_checker_fixes.py"


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("nba_snapshot_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = tmp_path / "finals.sqlite"
    day = (datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)).isoformat()
    conn = sqlite3.connect(database)
    conn.execute("CREATE TABLE games (game_id TEXT, sport TEXT, league TEXT, game_date TEXT,"
                 "away_score REAL, home_score REAL)")
    conn.executemany("INSERT INTO games VALUES (?,?,?,?,?,?)",
                     [(f"NBA_{n}", "NBA", "NBA", day, 85+n, 117+n) for n in range(6)])
    conn.commit()
    conn.close()
    monkeypatch.setattr(module, "_app_db_path", lambda: database)
    monkeypatch.setattr(module, "_CARDS_CACHE_DIR", tmp_path)
    monkeypatch.setattr(module, "_viewer_tier", lambda: "paid")
    monkeypatch.setattr(module, "_schedule_served_refresh", lambda key: None)
    return module, database, day


def page(module, day, ids=range(6), count=6, wrong_score=False):
    cards = "".join(
        f'<div class="game-card pick-card" data-game-id="NBA_{n}">'
        f'<div class="final-score">{0 if wrong_score else 85+n}</div>'
        f'<div class="final-score">{117+n}</div></div>' for n in ids)
    return (
        f'<html><h2>Last Night\'s NBA Results — {day} ({count} games)</h2>'
        f'<div id="date-{day}" class="date-section">{cards}</div>'
        + (" " * 600) + module._REPAIRED_MARK + "</html>"
    )


def test_same_date_partial_snapshot_is_rejected(fixture):
    module, _, day = fixture
    assert module._served_results_are_stale("NBA|||||", page(module, day, range(2), 2))


def test_changing_only_the_heading_does_not_pass(fixture):
    module, _, day = fixture
    assert module._served_results_are_stale("NBA|||||", page(module, day, range(2), 6))


def test_all_six_correct_final_cards_are_accepted(fixture):
    module, _, day = fixture
    assert not module._served_results_are_stale("NBA|||||", page(module, day))


def test_wrong_score_or_wrong_identity_is_rejected(fixture):
    module, _, day = fixture
    assert module._served_results_are_stale("NBA|||||", page(module, day, wrong_score=True))
    assert module._served_results_are_stale("NBA|||||", page(module, day, range(1, 7)))


def test_chart_count_is_checked_without_requiring_card_markup(fixture):
    module, _, day = fixture
    assert module._served_results_are_stale("NBA|chart||||", page(module, day, [], 2))
    assert not module._served_results_are_stale("NBA|chart||||", page(module, day, [], 6))


def test_memory_and_disk_reject_partial_then_accept_complete(fixture):
    module, _, day = fixture
    key = "NBA|||||"
    partial = page(module, day, range(2), 2)
    module._SERVED[key] = (time.time(), partial)
    path = module._served_path(key)
    path.write_text(partial)
    assert module.lookup_served_results(key) == ""
    module._SERVED.pop(key, None)
    assert module.lookup_served_results(key) == ""
    complete = page(module, day)
    path.write_text(complete)
    assert module.lookup_served_results(key) == complete
    assert module.lookup_served_results(key) == complete


def test_check_is_read_only_and_does_not_affect_other_sports(fixture):
    module, database, day = fixture
    before = database.read_bytes()
    assert not module._nba_results_snapshot_incomplete("NHL|||||", page(module, day, [], 0))
    module._served_results_are_stale("NBA|||||", page(module, day))
    assert database.read_bytes() == before


def test_new_final_on_same_date_invalidates_previously_complete_snapshot(fixture):
    module, database, day = fixture
    html = page(module, day)
    assert not module._served_results_are_stale("NBA|||||", html)
    conn = sqlite3.connect(database)
    conn.execute("INSERT INTO games VALUES ('NBA_6','NBA','NBA',?,100,110)", (day,))
    conn.commit()
    conn.close()
    assert module._served_results_are_stale("NBA|||||", html)
