"""Tennis, UFC, Golf sport modules — imports, routing, and card pages."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.mark.parametrize('mod_name,sport,picks_slug,results_slug,shortcut', [
    ('TENNIS', 'TENNIS', 'tennis-picks', 'tennis-results', '/tennis'),
    ('UFC', 'UFC', 'ufc-picks', 'ufc-results', '/ufc'),
    ('GOLF', 'GOLF', 'golf-picks', 'golf-results', '/golf'),
])
def test_individual_sport_module_exports(mod_name, sport, picks_slug, results_slug, shortcut):
    import importlib
    mod = importlib.import_module(f'sports.{mod_name}')
    assert mod.SPORT == sport
    assert mod.PICKS_SLUG == picks_slug
    assert mod.RESULTS_SLUG == results_slug
    assert callable(mod.register_routes)
    assert callable(mod.fetch_api_games)
    assert callable(mod.load_upcoming_games)
    assert callable(mod.render_sport_results_page)
    assert mod.render_sport_results_page('OTHER') is None
    hint = getattr(mod, 'OFFSEASON_HINT', '')
    assert hint
    assert 'ESPN' not in hint


def test_sports_package_exports_individual_sports():
    from sports import TENNIS, UFC, GOLF
    assert TENNIS.PICKS_SLUG == 'tennis-picks'
    assert UFC.PICKS_SLUG == 'ufc-picks'
    assert GOLF.PICKS_SLUG == 'golf-picks'


def test_individual_sport_loaders_registered():
    import NHL77FINAL as N
    assert 'TENNIS' in N._INDIVIDUAL_SPORT_LOADERS
    assert 'UFC' in N._INDIVIDUAL_SPORT_LOADERS
    assert 'GOLF' in N._INDIVIDUAL_SPORT_LOADERS
    assert N._SPORT_RESULTS_RENDERERS['TENNIS'] is not None
    assert N._SPORT_RESULTS_RENDERERS['UFC'] is not None
    assert N._SPORT_RESULTS_RENDERERS['GOLF'] is not None


@pytest.mark.parametrize('path', [
    '/tennis-picks', '/ufc-picks', '/golf-picks',
    '/tennis-results', '/ufc-results', '/golf-results',
])
def test_individual_sport_routes_http_200(path):
    import NHL77FINAL as N
    with N.app.test_client() as client:
        resp = client.get(path)
    assert resp.status_code == 200, f'{path} returned {resp.status_code}'


def test_parse_match_scores_individual_sports():
    from sports._individual_sport import _parse_match_scores

    assert _parse_match_scores('STATUS_SCHEDULED', {'score': None}, {'score': None}) == (None, None)
    assert _parse_match_scores('STATUS_FINAL', {'score': None}, {'score': None}) == (None, None)
    assert _parse_match_scores(
        'STATUS_FINAL',
        {'linescores': [{'winner': True}, {'winner': True}]},
        {'linescores': [{'winner': False}, {'winner': False}]},
    ) == (2, 0)
    assert _parse_match_scores('STATUS_FINAL', {'winner': True}, {'winner': False}) == (1, 0)


@pytest.mark.parametrize('path,sport', [
    ('/tennis-picks', 'TENNIS'),
    ('/ufc-picks', 'UFC'),
    ('/golf-picks', 'GOLF'),
])
def test_individual_sport_picks_pages_render_game_cards(path, sport, monkeypatch):
    import NHL77FINAL as N
    from datetime import datetime, timedelta

    monkeypatch.setattr(N, '_PREDICTIONS_CACHE', {})
    N._SPORT_PREDICTIONS_PAGE_CACHE.clear()
    game_day = (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d')
    sample = {
        'game_id': f'{sport}_test_1',
        'home_team_id': 'Player Alpha',
        'away_team_id': 'Player Beta',
        'game_date': game_day,
        'event_date': f'{game_day}T18:00Z',
        'home_score': None,
        'away_score': None,
        'league': 'Test Event',
    }
    dated = [(N.parse_date(sample['game_date']), sample)]

    monkeypatch.setitem(N._INDIVIDUAL_SPORT_LOADERS, sport, lambda: dated)
    with N.app.test_client() as client:
        resp = client.get(path)
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert html.count('class="game-card pick-card"') >= 1
    assert 'ESPN' not in html
    assert 'no predictions available' not in html.lower()


@pytest.mark.parametrize('shortcut,expected', [
    ('/tennis', '/tennis-picks'),
    ('/ufc', '/ufc-picks'),
    ('/golf', '/golf-picks'),
])
def test_individual_sport_shortcuts_redirect(shortcut, expected):
    import NHL77FINAL as N
    with N.app.test_client() as client:
        resp = client.get(shortcut, follow_redirects=False)
    assert resp.status_code == 301
    assert expected in (resp.location or '')


def test_get_upcoming_predictions_delegates_to_sport_modules(monkeypatch):
    import NHL77FINAL as N

    calls = []

    def _fake_tennis():
        calls.append('TENNIS')
        return []

    monkeypatch.setitem(N._INDIVIDUAL_SPORT_LOADERS, 'TENNIS', _fake_tennis)
    monkeypatch.setattr(N, '_PREDICTIONS_CACHE', {})

    try:
        N.get_upcoming_predictions('TENNIS')
    except Exception:
        pass
    assert 'TENNIS' in calls


@pytest.mark.parametrize('path', ['/tennis-picks', '/ufc-picks', '/golf-picks'])
def test_individual_sport_live_picks_have_cards(path):
    """Live ESPN fetch — skipped in CI when no events are scheduled."""
    import NHL77FINAL as N

    N._PREDICTIONS_CACHE.clear()
    N._SPORT_PREDICTIONS_PAGE_CACHE.clear()
    with N.app.test_client() as client:
        resp = client.get(path)
    html = resp.get_data(as_text=True)
    cards = len(re.findall(r'class="game-card pick-card"', html))
    if cards == 0:
        pytest.skip(f'No live events for {path}')
    assert cards > 0
    assert 'ESPN' not in html
