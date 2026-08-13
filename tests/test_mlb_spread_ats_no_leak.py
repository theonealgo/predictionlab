"""MLB ATS: xs/xt must not leak across games when heavy predict is skipped."""
from collections import defaultdict
from unittest.mock import MagicMock


def test_mlb_spread_xs_not_leaked_when_heavy_predict_skipped(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(N, '_attach_h2h_projection_to_daily_results', lambda *a, **k: None)
    monkeypatch.setattr(N, '_attach_nba_efficiency_to_daily_results', lambda *a, **k: None)
    monkeypatch.setattr(N, '_apply_fades_to_daily_results', lambda *a, **k: None)
    monkeypatch.setattr(
        N,
        'get_db_connection',
        lambda: MagicMock(
            execute=MagicMock(return_value=MagicMock(fetchall=MagicMock(return_value=[]))),
            close=MagicMock(),
        ),
    )
    monkeypatch.setattr(N, '_get_xgb_spread_model', MagicMock())

    daily = defaultdict(lambda: {'games': []})
    # >500 games triggers skip_heavy_predict; distinct our_spread per game.
    for i in range(501):
        # Alternate strong home / strong away H2H margins so leaked xs would
        # force every pick HOME -1.5, while per-game our_spread picks both sides.
        our = 3.0 if i == 0 else (-3.0 if i % 2 else 0.5)
        daily['2026-06-01']['games'].append({
            'game_id': f'MLB_{i}',
            'date': '2026-06-01',
            'home': f'Home{i}',
            'away': f'Away{i}',
            'home_score': 5 if i % 2 == 0 else 1,
            'away_score': 1 if i % 2 == 0 else 5,
            'our_spread': our,
            'our_total': 9.0,
        })

    stats = N._compute_spread_total_for_daily('MLB', daily, skip_efficiency=True)
    spreads = [g.get('xgb_spread') for g in daily['2026-06-01']['games']]
    assert len(set(spreads)) > 1, f'expected distinct per-game spreads, got {set(spreads)}'
    assert spreads[0] == 3.0
    assert spreads[1] == -3.0
    # With leak, every pick is HOME -1.5 from first our_spread=3.0.
    away_fav_labels = [
        g.get('spread_pick_label')
        for g in daily['2026-06-01']['games']
        if g.get('spread_pick_label') and g['spread_pick_label'].startswith('Away')
    ]
    assert away_fav_labels, 'expected some Away -1.5 picks from our_spread=-3.0'
    assert stats.get('spread_graded', 0) == 501
