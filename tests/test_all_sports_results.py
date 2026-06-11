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
                'efficiency': {'correct': 52, 'total': 95, 'accuracy': 54.7},
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
    assert rows[0]['ml']['efficiency']['pct'] == 54.7
    assert rows[0]['ml']['efficiency']['n'] == 95
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
    assert '<td>—</td>' not in html
    assert 'Not tracked' in html or 'No games yet' in html


def test_all_sports_dashboard_includes_twelve_sports():
    import NHL77FINAL as N

    assert len(N.ALL_SPORTS_DASHBOARD_SPORTS) == 12
    assert {'TENNIS', 'UFC', 'GOLF'}.issubset(set(N.ALL_SPORTS_DASHBOARD_SPORTS))


def test_all_sports_dashboard_marks_missing_cells_explicitly():
    import NHL77FINAL as N

    rows = N._build_all_sports_dashboard_rows([{
        'sport': 'TENNIS',
        'season': '2026-27',
        'games_in_scope': 42,
        'overall_stats': {
            'glicko2': {'correct': 24, 'total': 42, 'accuracy': 57.1},
        },
        'spread_total_stats': {},
    }])
    assert rows[0]['ml']['xgboost']['status'] == 'not_tracked'
    assert rows[0]['spread_xsharp']['status'] == 'not_tracked'


def test_indexing_routes_are_registered():
    import NHL77FINAL as N

    with N.app.test_client() as client:
        robots = client.get('/robots.txt')
        llms = client.get('/llms.txt')
        ai = client.get('/ai.txt')
        sitemap = client.get('/sitemap.xml')
    assert robots.status_code == 200
    assert 'Sitemap: https://predictionlab.io/sitemap.xml' in robots.get_data(as_text=True)
    assert llms.status_code == 200
    assert 'predictionlab.io' in llms.get_data(as_text=True)
    assert ai.status_code == 200
    assert 'LLMs: https://predictionlab.io/llms.txt' in ai.get_data(as_text=True)
    assert sitemap.status_code == 200
    assert '/llms.txt' in sitemap.get_data(as_text=True)


def test_individual_sport_grading_has_elo_and_consensus():
    import NHL77FINAL as N
    from sports._individual_sport import build_graded_daily_results, individual_sport_season_bounds

    start_dt, end_dt = individual_sport_season_bounds()
    daily = build_graded_daily_results('UFC', start_dt, end_dt)
    if not daily or not N._daily_results_game_count(daily):
        import pytest
        pytest.skip('No completed UFC events in current ESPN window')
    overall = N.compute_overall_stats_from_daily(daily)
    assert overall['elo']['total'] > 0
    assert overall['ensemble']['total'] > 0
    assert overall['elo']['accuracy'] is not None


def test_load_all_sports_snapshots_skips_missing_without_placeholder(tmp_path, monkeypatch):
    import NHL77FINAL as N

    snap_dir = tmp_path / 'season_snapshots'
    snap_dir.mkdir()
    (snap_dir / 'NHL_2025-26_regular.json').write_text(
        '{"sport": "NHL", "season": "2025-26", "overall_stats": {}, "spread_total_stats": {}}',
        encoding='utf-8',
    )
    (snap_dir / 'UFC_2026_regular.json').write_text(
        '{"sport": "UFC", "season": "2026", "overall_stats": {"elo": {"correct": 7, "total": 13, "accuracy": 53.8}, "ensemble": {"correct": 7, "total": 13, "accuracy": 53.8}}, "spread_total_stats": {}}',
        encoding='utf-8',
    )
    monkeypatch.setattr(N, '_all_sports_snapshot_dir', lambda: str(snap_dir))
    rows = N._load_all_sports_season_snapshots()
    sports = {r['sport'] for r in rows}
    assert 'NHL' in sports
    assert 'UFC' in sports
    assert all(not r.get('_placeholder') for r in rows)
    ufc_row = next(r for r in N._build_all_sports_dashboard_rows(rows) if r['sport'] == 'UFC')
    assert ufc_row['ml']['elo']['n'] > 0
    assert ufc_row['ml']['ensemble']['n'] > 0


def test_all_sports_results_survives_snapshot_import_failure(monkeypatch):
    import NHL77FINAL as N

    def _boom():
        raise ImportError('simulated missing src.season_snapshots')

    monkeypatch.setattr(N, '_load_all_sports_season_snapshots', _boom)
    with N.app.test_client() as client:
        resp = client.get('/all-sports-results')
    assert resp.status_code == 200
    assert 'All Sports Results' in resp.get_data(as_text=True)
