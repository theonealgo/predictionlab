"""Team Model Performance uses full-season daily_results grading, not last-N picked-team ML."""
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _game(home, away, *, g2=60.0, ts=55.0, elo=58.0, xgb=62.0, ens=61.0,
          home_win=True, spread_ok=True, total_ok=True, pl_spread_ok=True, pl_total_ok=True):
    return {
        'home': home,
        'away': away,
        'home_win': home_win,
        'is_draw': False,
        'skip_grading': False,
        'glicko2_prob': g2,
        'trueskill_prob': ts,
        'elo_prob': elo,
        'xgb_prob': xgb,
        'ens_prob': ens,
        'spread_pick': 'HOME',
        'spread_correct': spread_ok,
        'total_pick': 'OVER',
        'total_correct': total_ok,
        'pl_spread_correct': pl_spread_ok,
        'pl_total_correct': pl_total_ok,
    }


def _rows_for_team(rows, team):
    return next(r for r in rows if r['team'] == team)


def test_team_performance_counts_all_games_and_bet_types(monkeypatch):
    import NHL77FINAL as N

    daily = {
        '2026-04-01': {
            'games': [
                _game('Los Angeles Dodgers', 'San Francisco Giants', home_win=True),
                _game('New York Yankees', 'Boston Red Sox', home_win=False),
            ],
        },
    }

    monkeypatch.setattr(N, '_results_season_bounds', lambda sport, ref_dt=None: (datetime(2026, 3, 20), datetime(2026, 11, 5)))
    monkeypatch.setattr(N, '_banner_daily_results_for_range', lambda sport, start_dt, end_dt: daily)
    monkeypatch.setattr(N, '_compute_spread_total_for_daily', lambda sport, dr: {})

    rows = N._build_team_performance_rows(sport_filter='MLB')
    dodgers = _rows_for_team(rows, 'Los Angeles Dodgers')
    yankees = _rows_for_team(rows, 'New York Yankees')

    assert dodgers['models']['Grinder2']['n'] == 1
    assert dodgers['models']['XSharp']['n'] == 3  # ML + spread + total
    assert dodgers['models']['Efficiency']['n'] == 2  # PL spread + total
    assert yankees['models']['Consensus']['n'] == 1
    assert yankees['models']['XSharp']['n'] == 3


def test_team_performance_ignores_last_n_picked_team_logic(monkeypatch):
    import NHL77FINAL as N

    daily = {
        '2026-04-02': {
            'games': [_game('Los Angeles Dodgers', 'San Francisco Giants', home_win=False)],
        },
    }

    monkeypatch.setattr(N, '_results_season_bounds', lambda sport, ref_dt=None: (datetime(2026, 3, 20), datetime(2026, 11, 5)))
    monkeypatch.setattr(N, '_banner_daily_results_for_range', lambda sport, start_dt, end_dt: daily)
    monkeypatch.setattr(N, '_compute_spread_total_for_daily', lambda sport, dr: {})

    rows = N._build_team_performance_rows(sport_filter='MLB')
    dodgers = _rows_for_team(rows, 'Los Angeles Dodgers')

    # Home lost; high home probs mean model picked Dodgers and lost.
    assert dodgers['models']['Grinder2']['wins'] == 0
    assert dodgers['models']['Grinder2']['losses'] == 1


def test_team_ml_grading_from_team_perspective():
    import NHL77FINAL as N

    away_win = {
        'home': 'Los Angeles Dodgers',
        'away': 'San Francisco Giants',
        'home_win': False,
        'is_draw': False,
        'xgb_prob': 40.0,
    }
    assert N._team_perf_ml_correct_for_team(away_win, 'San Francisco Giants', 'xgb_prob') is True
    # Model faded Dodgers (40% home); Dodgers lost — correct fade.
    assert N._team_perf_ml_correct_for_team(away_win, 'Los Angeles Dodgers', 'xgb_prob') is True


def test_performance_page_team_rows_use_separate_builder(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(
        N,
        '_build_performance_page_data',
        lambda sport_filter='', last_n=None: ({}, {}),
    )
    monkeypatch.setattr(
        N,
        '_build_team_performance_rows',
        lambda sport_filter='': [{'sport': 'MLB', 'team': 'Los Angeles Dodgers', 'models': {}, 'total_n': 99}],
    )
    monkeypatch.setattr(N, 'render_template', lambda *args, **kwargs: kwargs)
    monkeypatch.setattr(N, 'current_user', MagicMock(is_authenticated=True))
    monkeypatch.setattr(N, 'is_premium_user', lambda: True)

    with N.app.test_request_context('/performance?sport=MLB&last_n=50'):
        out = N.performance_page()

    assert out['team_chart_rows'][0]['total_n'] == 99
    assert out['team_model_order'][-1] == 'Efficiency'
