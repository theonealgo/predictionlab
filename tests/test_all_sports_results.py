"""All-sports season dashboard (frozen JSON snapshots)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_all_sports_dashboard_builds_rows():
    import NHL77FINAL as N

    snapshots = [
        {
            'sport': 'NHL',
            'season': '2025-26',
            'games_in_scope': 100,
            'overall_stats': {
                'glicko2': {'correct': 55, 'total': 100, 'accuracy': 55.0},
                'trueskill': {'correct': 54, 'total': 100, 'accuracy': 54.0},
                'elo': {'correct': 53, 'total': 100, 'accuracy': 53.0},
                'xgboost': {'correct': 52, 'total': 100, 'accuracy': 52.0},
                'ensemble': {'correct': 56, 'total': 100, 'accuracy': 56.0},
            },
            'spread_total_stats': {
                'spread_graded': 90,
                'spread_covered': 48,
                'spread_pct': 53.3,
                'spread_pushes': 2,
                'pl_spread_graded': 88,
                'pl_spread_covered': 46,
                'pl_spread_pct': 52.3,
                'pl_spread_pushes': 1,
                'total_graded': 85,
                'total_correct': 44,
                'total_pct': 51.8,
                'total_pushes': 3,
                'pl_total_graded': 80,
                'pl_total_correct': 42,
                'pl_total_pct': 52.5,
                'pl_total_pushes': 2,
            },
        },
    ]
    rows = N._build_all_sports_dashboard_rows(snapshots)
    assert len(rows) == 1
    assert rows[0]['sport'] == 'NHL'
    assert rows[0]['ml']['ensemble']['pct'] == 56.0
    assert rows[0]['spread_xsharp']['n'] == 90
    assert rows[0]['spread_pl']['n'] == 88
    assert rows[0]['ou_pl']['n'] == 80


def test_all_sports_results_route(monkeypatch):
    import NHL77FINAL as N

    monkeypatch.setattr(N, '_load_all_sports_season_snapshots', lambda: [])
    captured = {}

    def _fake_render(_template, **kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(N, 'render_template_string', _fake_render)
    out = N.all_sports_results_page()
    assert out == 'ok'
    assert captured['page'] == 'all-sports-results'
    assert captured['dashboard_rows'] == []


def test_all_sports_results_http_ok():
    import NHL77FINAL as N

    with N.app.test_client() as client:
        resp = client.get('/all-sports-results')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'All Sports Results' in html


def test_all_sports_results_survives_snapshot_import_failure(monkeypatch):
    import NHL77FINAL as N

    def _boom():
        raise ImportError('simulated missing src.season_snapshots')

    monkeypatch.setattr(N, '_load_all_sports_season_snapshots', _boom)
    with N.app.test_client() as client:
        resp = client.get('/all-sports-results')
    assert resp.status_code == 200
    assert 'All Sports Results' in resp.get_data(as_text=True)
