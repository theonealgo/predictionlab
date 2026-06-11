"""Flat unit O/U tracking: win rate headline must match W-L; ROI must match ±1u math."""
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _game(date_key, *, total_pick, total_correct, home=110, away=105):
    return {
        'date': date_key,
        'home_score': home,
        'away_score': away,
        'ens_prob': 55.0,
        'total_pick': total_pick,
        'total_correct': total_correct,
    }


def test_flat_unit_roi_matches_win_loss_record():
    import NHL77FINAL as N

    daily = defaultdict(lambda: {'games': []})
    daily['2026-05-01']['games'].append(_game('2026-05-01', total_pick='OVER', total_correct=True))
    daily['2026-05-02']['games'].append(_game('2026-05-02', total_pick='UNDER', total_correct=False))
    daily['2026-05-03']['games'].append(_game('2026-05-03', total_pick='PUSH', total_correct=None))

    roi_all = N.compute_roi_for_range(daily, None, None)
    roi = roi_all['total']
    assert roi['wins'] == 1
    assert roi['losses'] == 1
    assert roi['pushes'] == 1
    assert roi['units_won'] == 0.0
    assert roi['units_risked'] == 2
    assert roi['roi_pct'] == 0.0
    assert roi['win_pct'] == 50.0

    card = N.build_roi_cards(None, roi_all, roi_all)['total']['total']
    assert card['roi'] == '50.0%'
    assert '1-1-1' in card['detail']
    assert 'ROI 0.0%' in card['detail']
    assert '+0.00u' in card['detail']


def test_flat_unit_season_style_ou_record():
    """872-348-6 style record: win% ~71.5, flat ROI ~42.95%, units = wins - losses."""
    import NHL77FINAL as N

    entry = N._roi_entry()
    entry['wins'] = 872
    entry['losses'] = 348
    entry['pushes'] = 6
    N._finalize_flat_unit_roi_entry(entry)

    assert entry['units_won'] == 524.0
    assert entry['units_risked'] == 1220
    assert entry['roi_pct'] == 42.95
    assert entry['win_pct'] == pytest.approx(71.48, abs=0.01)


def test_nba_ou_roi_matches_weekly_tally(monkeypatch):
    """NBA results pipeline: O/U W-L in weekly tally == flat-unit total counters."""
    import NHL77FINAL as N
    from sports import NBA

    d1 = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
    d2 = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    weekly = {
        1: {
            'games': [
                {
                    'date': d1,
                    'home': 'A', 'away': 'B',
                    'home_score': 110, 'away_score': 100,
                    'ens_prob': 60.0,
                    'total_pick': 'OVER', 'total_correct': True,
                    'spread_pick': None, 'spread_correct': None,
                },
                {
                    'date': d2,
                    'home': 'C', 'away': 'D',
                    'home_score': 98, 'away_score': 102,
                    'ens_prob': 40.0,
                    'total_pick': 'UNDER', 'total_correct': False,
                    'spread_pick': None, 'spread_correct': None,
                },
            ],
            'glicko2': {'correct': 0, 'total': 0},
            'trueskill': {'correct': 0, 'total': 0},
            'elo': {'correct': 0, 'total': 0},
            'xgboost': {'correct': 0, 'total': 0},
            'ensemble': {'correct': 0, 'total': 0},
        }
    }
    monkeypatch.setattr(NBA, 'calculate_nba_weekly_performance', lambda: weekly)
    monkeypatch.setattr(NBA, 'update_nba_scores', lambda: None)
    monkeypatch.setattr(N, '_attach_book_odds_to_daily_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_cache_market_lines_for_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_attach_engine_odds_to_daily_results', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_compute_spread_total_for_daily', lambda *_a, **_k: {'spread_graded': 0, 'total_graded': 0})
    monkeypatch.setattr(N, '_finalize_daily_result_cards', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_ou_stats', lambda *_a, **_k: (0, 0, 0, 0, 0))

    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(N, 'render_template_string', _fake_render)
    assert NBA.render_sport_results_page('NBA') == 'ok'

    bundle = N._compute_results_tally_bundle(
        captured['daily_results'],
        datetime.now() - timedelta(days=1),
    )
    tally_ou = bundle['weekly_tally']['total_ou']
    roi_ou = N.compute_roi_for_range(
        captured['daily_results'],
        bundle['weekly_start_dt'],
        bundle['weekly_end_dt'],
    )['total']

    assert tally_ou['correct'] == roi_ou['wins']
    assert tally_ou['total'] - tally_ou['correct'] == roi_ou['losses']
    assert tally_ou['pushes'] == roi_ou['pushes']
    assert roi_ou['win_pct'] == tally_ou['accuracy']
