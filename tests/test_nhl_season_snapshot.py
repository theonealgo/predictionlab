"""NHL frozen season snapshot + results page integration tests."""
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


MOCK_SNAPSHOT = {
    'schema_version': 1,
    'sport': 'NHL',
    'season': '2025-26',
    'phase': 'regular',
    'games_expected': 1312,
    'games_in_scope': 1300,
    'overall_stats': {
        'ensemble': {'correct': 700, 'total': 1300, 'accuracy': 53.8},
        'glicko2': {'correct': 690, 'total': 1300, 'accuracy': 53.1},
        'trueskill': {'correct': 680, 'total': 1300, 'accuracy': 52.3},
        'elo': {'correct': 650, 'total': 1300, 'accuracy': 50.0},
        'xgboost': {'correct': 660, 'total': 1300, 'accuracy': 50.8},
    },
    'spread_total_stats': {
        'spread_graded': 900,
        'spread_covered': 470,
        'spread_pct': 52.2,
        'total_graded': 850,
        'total_correct': 440,
        'total_pct': 51.8,
    },
    'season_perf': {
        'ml_total': 1300,
        'ml_correct': 700,
        'ml_accuracy': 53.8,
        'spread_graded': 900,
        'spread_covered': 470,
        'spread_pct': 52.2,
        'ou_graded': 850,
        'ou_correct': 440,
        'ou_pct': 51.8,
        'scope_label': 'NHL regular season (Oct–Apr)',
        'games_expected': 1312,
        'games_in_scope': 1300,
    },
    'ou_summary': {
        'total_over': 600,
        'total_under': 700,
        'total_games_ou': 1300,
        'avg_total': 6.1,
        'ou_bench': 6.0,
    },
    'roi_total': {
        'moneyline': {'graded': 1300, 'wins': 700, 'losses': 600, 'units_won': 100.0, 'units_risked': 1300},
        'spread': {'graded': 900, 'wins': 470, 'losses': 430, 'units_won': 40.0, 'units_risked': 900},
        'total': {'graded': 850, 'wins': 440, 'losses': 410, 'units_won': 30.0, 'units_risked': 850},
    },
}


def _patch_nhl_results_common(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(N, 'update_nhl_scores', lambda: None)
    monkeypatch.setattr(N, '_attach_nhl_display_grading', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_attach_book_odds_to_daily_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_cache_market_lines_for_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_attach_engine_odds_to_daily_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_compute_spread_total_for_daily', lambda *_a, **_k: MOCK_SNAPSHOT['spread_total_stats'])
    monkeypatch.setattr(N, '_finalize_daily_result_cards', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_SPORT_RESULTS_CACHE', {})
    monkeypatch.setattr(N, '_time', MagicMock(time=MagicMock(return_value=0)))
    return N


def test_nhl_results_uses_snapshot_when_regular_season_complete(monkeypatch):
    N = _patch_nhl_results_common(monkeypatch)
    monkeypatch.setattr(N, '_load_nhl_season_snapshot', lambda *_a, **_k: MOCK_SNAPSHOT)
    monkeypatch.setattr(N, '_stats_from_nhl_snapshot', N._stats_from_nhl_snapshot)

    playoff_daily = defaultdict(lambda: {'games': []})
    playoff_daily['2026-05-15'] = {'games': [{'game_id': 'p1', 'date': '2026-05-15', 'skip_grading': False}]}

    card_daily = defaultdict(lambda: {'games': []})
    card_daily['2026-04-20'] = {'games': [{'game_id': 'r1', 'date': '2026-04-20', 'skip_grading': False}]}

    def _banner(sport, start, end, *, playoffs=False, skip_v2=False):
        if playoffs:
            return playoff_daily
        return card_daily

    monkeypatch.setattr(N, '_banner_daily_results_for_range', _banner)
    monkeypatch.setattr(N, '_daily_results_game_count', lambda d: sum(len(b.get('games') or []) for b in (d or {}).values()))

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(N, 'render_template_string', _fake_render)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 2, 12, 0, 0)

    monkeypatch.setattr(N, 'datetime', _FixedDatetime)

    out = N.sport_results('NHL')
    assert out == 'ok'
    assert captured['season_perf']['games_in_scope'] == 1300
    assert captured['season_perf']['ml_total'] == 1300
    assert captured['overall_stats']['ensemble']['total'] == 1300
    assert captured['playoff_perf'] is not None
    assert captured['playoff_perf']['scope_label'] == 'NHL playoffs (live)'


def test_nhl_playoff_games_graded_live(monkeypatch):
    N = _patch_nhl_results_common(monkeypatch)
    monkeypatch.setattr(N, '_load_nhl_season_snapshot', lambda *_a, **_k: MOCK_SNAPSHOT)

    playoff_game = {
        'game_id': 'NHL_PO_1',
        'date': '2026-05-20',
        'skip_grading': False,
        'ens_prob': 58.0,
        'ens_correct': True,
        'glicko2_prob': 55.0,
        'glicko2_correct': True,
        'trueskill_prob': 54.0,
        'trueskill_correct': True,
        'elo_prob': 52.0,
        'elo_correct': True,
        'xgb_prob': 56.0,
        'xgb_correct': True,
        'home_score': 3,
        'away_score': 2,
    }
    playoff_daily = defaultdict(lambda: {'games': []})
    playoff_daily['2026-05-20'] = {'games': [playoff_game]}

    attach_calls = []

    def _attach(sport, daily):
        attach_calls.append(sport)
        return MOCK_SNAPSHOT['spread_total_stats']

    monkeypatch.setattr(N, '_attach_nhl_display_grading', _attach)
    monkeypatch.setattr(
        N,
        '_banner_daily_results_for_range',
        lambda sport, start, end, *, playoffs=False, skip_v2=False: playoff_daily if playoffs else defaultdict(lambda: {'games': []}),
    )
    monkeypatch.setattr(N, '_daily_results_game_count', lambda d: sum(len(b.get('games') or []) for b in (d or {}).values()))

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(N, 'render_template_string', _fake_render)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 2, 12, 0, 0)

    monkeypatch.setattr(N, 'datetime', _FixedDatetime)

    out = N.sport_results('NHL')
    assert out == 'ok'
    assert attach_calls == ['NHL']
    assert captured['daily_results']['2026-05-20']['games'][0]['game_id'] == 'NHL_PO_1'
    assert captured['playoff_perf'] is not None


def test_nhl_empty_page_regression_snapshot_when_banner_empty(monkeypatch):
    """After dedupe/timeout, banner can be empty — snapshot must still render."""
    N = _patch_nhl_results_common(monkeypatch)
    monkeypatch.setattr(N, '_load_nhl_season_snapshot', lambda *_a, **_k: MOCK_SNAPSHOT)
    monkeypatch.setattr(
        N,
        '_banner_daily_results_for_range',
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(N, '_daily_results_game_count', lambda d: 0 if d is None else sum(
        len(b.get('games') or []) for b in d.values()
    ))

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(N, 'render_template_string', _fake_render)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 2, 12, 0, 0)

    monkeypatch.setattr(N, 'datetime', _FixedDatetime)

    out = N.sport_results('NHL')
    assert out == 'ok'
    assert captured['season_perf']['ml_total'] == 1300


def test_nhl_post_regular_missing_snapshot_renders_not_fallback(monkeypatch):
    """Production regression: post-regular + no JSON + empty DB must not hard-fallback."""
    N = _patch_nhl_results_common(monkeypatch)
    monkeypatch.setattr(N, '_load_nhl_season_snapshot', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_nhl_snapshot_json_path', lambda *_a, **_k: '/tmp/no_nhl_snapshot.json')
    monkeypatch.setattr(
        N,
        '_banner_daily_results_for_range',
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(N, '_daily_results_game_count', lambda d: 0 if d is None else sum(
        len(b.get('games') or []) for b in d.values()
    ))

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(N, 'render_template_string', _fake_render)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 2, 12, 0, 0)

    monkeypatch.setattr(N, 'datetime', _FixedDatetime)

    out = N.sport_results('NHL')
    assert out == 'ok'
    assert captured.get('results_snapshot_notice')


def test_load_season_snapshot_from_repo():
    from src.season_snapshots import load_season_snapshot

    snap = load_season_snapshot('NHL', '2025-26', 'regular')
    if snap is None:
        pytest.skip('NHL_2025-26_regular.json not built yet')
    assert snap['sport'] == 'NHL'
    scope = snap.get('games_in_scope') or 0
    assert scope <= 1312
    assert scope > 1000
    perf = snap['season_perf']
    assert perf['ml_total'] > 1000
    st = snap.get('spread_total_stats') or {}
    assert st.get('spread_graded', 0) > 1000
    assert st.get('total_graded', 0) > 1000
