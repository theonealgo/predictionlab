"""NHL results page regular-season scope (82 games/team, Oct–Apr window)."""
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_nhl_regular_season_bounds_midseason():
    import NHL77FINAL as N

    ref = datetime(2025, 12, 15)
    start, end = N._nhl_results_regular_season_bounds(ref)
    assert start == datetime(2025, 10, 1)
    assert end == datetime(2026, 4, 30)


def test_nhl_regular_season_bounds_offseason_after_playoffs():
    import NHL77FINAL as N

    ref = datetime(2026, 6, 2)
    start, end = N._nhl_results_regular_season_bounds(ref)
    assert start == datetime(2025, 10, 1)
    assert end == datetime(2026, 4, 30)


def test_nhl_regular_season_bounds_january():
    import NHL77FINAL as N

    ref = datetime(2026, 1, 20)
    start, end = N._nhl_results_regular_season_bounds(ref)
    assert start == datetime(2025, 10, 1)
    assert end == datetime(2026, 4, 30)


def test_nhl_league_game_constant():
    import NHL77FINAL as N

    assert N.SPORT_REGULAR_SEASON_GAMES_PER_TEAM['NHL'] == 82
    assert N.SPORT_REGULAR_SEASON_LEAGUE_GAMES['NHL'] == 1312


def test_subset_daily_results_filters_dates():
    import NHL77FINAL as N

    daily = defaultdict(lambda: {'games': []})
    daily['2025-10-05'] = {'games': [{'game_id': 'a'}]}
    daily['2026-05-10'] = {'games': [{'game_id': 'playoff'}]}
    daily['2026-01-02'] = {'games': [{'game_id': 'b'}]}
    start = datetime(2025, 10, 1)
    end = datetime(2026, 4, 30)
    sub = N._subset_daily_results(daily, start, end)
    assert '2026-05-10' not in sub
    assert len(sub['2025-10-05']['games']) == 1
    assert len(sub['2026-01-02']['games']) == 1


def test_dedupe_nhl_game_rows_removes_duplicate_matchups():
    import NHL77FINAL as N

    rows = [
        {
            'game_id': 'NHL_2025_269',
            'game_date': '2025-11-13',
            'home_team_id': 'Columbus Blue Jackets',
            'away_team_id': 'Edmonton Oilers',
            'home_score': 5,
            'away_score': 4,
            'elo_home_prob': None,
        },
        {
            'game_id': 'NHL_2025020274',
            'game_date': '2025-11-13',
            'home_team_id': 'Columbus Blue Jackets',
            'away_team_id': 'Edmonton Oilers',
            'home_score': 5,
            'away_score': 4,
            'elo_home_prob': 0.55,
        },
    ]
    out = N._dedupe_nhl_game_rows(rows)
    assert len(out) == 1
    assert out[0]['game_id'] == 'NHL_2025020274'


def test_dedupe_nhl_game_rows_excludes_international_teams():
    import NHL77FINAL as N

    rows = [
        {
            'game_id': 'INTL_1',
            'game_date': '2026-02-10',
            'home_team_id': 'USA',
            'away_team_id': 'CAN',
            'home_score': 3,
            'away_score': 2,
        },
        {
            'game_id': 'NHL_1',
            'game_date': '2026-02-10',
            'home_team_id': 'Boston Bruins',
            'away_team_id': 'Toronto Maple Leafs',
            'home_score': 4,
            'away_score': 3,
            'elo_home_prob': 0.52,
        },
    ]
    out = N._dedupe_nhl_game_rows(rows)
    assert len(out) == 1
    assert out[0]['game_id'] == 'NHL_1'


def test_nhl_results_games_in_scope_never_exceeds_league_max():
    import NHL77FINAL as N

    daily = defaultdict(lambda: {'games': []})
    for i in range(1400):
        daily['2026-03-01']['games'].append({'game_id': f'g{i}'})
    assert N._nhl_results_games_in_scope(daily) == 1312


def test_nhl_sport_results_season_perf_uses_regular_season_scope(monkeypatch):
    import NHL77FINAL as N

    season_start = datetime(2025, 10, 1)
    season_end = datetime(2026, 4, 30)
    yesterday = datetime(2026, 6, 1)

    season_daily = defaultdict(lambda: {'games': []})
    for i in range(50):
        season_daily['2026-03-01']['games'].append({
            'game_id': f'NHL_{i}',
            'date': '2026-03-01',
            'skip_grading': False,
            'ens_prob': 55.0,
            'ens_correct': True,
            'elo_prob': 52.0,
            'elo_correct': True,
            'xgb_prob': 48.0,
            'xgb_correct': False,
            'glicko2_prob': 50.0,
            'glicko2_correct': True,
            'trueskill_prob': 51.0,
            'trueskill_correct': True,
        })

    display_daily = defaultdict(lambda: {'games': []})
    display_daily['2026-03-01'] = {'games': season_daily['2026-03-01']['games'][:3]}

    monkeypatch.setattr(N, 'update_nhl_scores', lambda: None)
    monkeypatch.setattr(
        N,
        '_results_season_bounds',
        lambda sport, ref_dt=None: (season_start, season_end),
    )
    monkeypatch.setattr(
        N,
        '_banner_daily_results_for_range',
        lambda sport, start_dt, end_dt: season_daily,
    )
    monkeypatch.setattr(
        N,
        '_subset_daily_results',
        lambda _season, start_dt, end_dt: display_daily,
    )
    monkeypatch.setattr(N, '_attach_book_odds_to_daily_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_cache_market_lines_for_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_attach_engine_odds_to_daily_results', lambda *_a, **_k: None)
    monkeypatch.setattr(
        N,
        '_compute_spread_total_for_daily',
        lambda *_a, **_k: {'spread_graded': 10, 'total_graded': 8, 'spread_pct': 60.0, 'total_pct': 50.0},
    )
    monkeypatch.setattr(N, '_finalize_daily_result_cards', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_SPORT_RESULTS_CACHE', {})
    monkeypatch.setattr(N, '_time', MagicMock(time=MagicMock(return_value=0)))

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(N, 'render_template_string', _fake_render)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 2, 12, 0, 0)

    monkeypatch.setattr(N, 'datetime', _FixedDatetime)

    out = N.sport_results('NHL')
    assert out == "ok"
    sp = captured['season_perf']
    assert sp['scope_label'] == 'NHL regular season (Oct–Apr)'
    assert sp['games_expected'] == 1312
    assert sp['games_in_scope'] == 50
    assert sp['games_in_scope'] <= 1312
    assert captured['overall_stats']['ensemble']['total'] == 50
    assert captured['overall_stats']['glicko2']['total'] == 50
    assert captured['overall_stats']['trueskill']['total'] == 50
    assert len(captured['daily_results']['2026-03-01']['games']) == 3


def test_nhl_banner_daily_wires_glicko_trueskill_from_db(monkeypatch):
    import NHL77FINAL as N

    row = {
        'game_id': 'NHL_TEST_1',
        'game_date': '2026-01-15',
        'home_team_id': 'Boston Bruins',
        'away_team_id': 'Toronto Maple Leafs',
        'home_score': 4,
        'away_score': 3,
        'league': 'NHL',
        'elo_home_prob': 0.54,
        'xgboost_home_prob': 0.57,
        'logistic_home_prob': 0.56,
        'win_probability': 0.58,
        'meta_home_prob': 0.58,
        'catboost_home_prob': 0.61,
        'glicko_home_prob': 0.61,
        'trueskill_home_prob': 0.56,
    }

    class _Cursor:
        def fetchall(self):
            return [row]

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return _Cursor()

        def close(self):
            return None

    monkeypatch.setattr(N, 'get_db_connection', lambda: _Conn())
    monkeypatch.setattr(N, 'get_v2_prediction', lambda *_a, **_k: None)

    start = datetime(2025, 10, 1)
    end = datetime(2026, 4, 30)
    daily = N._banner_daily_results_for_range('NHL', start, end)
    game = daily['2026-01-15']['games'][0]
    assert game['glicko2_prob'] == 61.0
    assert game['trueskill_prob'] == 56.0
    assert game['glicko2_correct'] is True
    assert game['trueskill_correct'] is True
