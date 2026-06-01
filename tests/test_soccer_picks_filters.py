"""Soccer picks page: all-leagues default and optional league filter."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def _sample_soccer_preds():
    return [
        {
            'home_team_id': 'Arsenal',
            'away_team_id': 'Chelsea',
            'game_date': '2026-06-02',
            'league': 'English Premier League',
            'home_score': None,
        },
        {
            'home_team_id': 'Real Madrid',
            'away_team_id': 'Barcelona',
            'game_date': '2026-06-03',
            'league': 'Spanish LaLiga',
            'home_score': None,
        },
        {
            'home_team_id': 'Team A',
            'away_team_id': 'Team B',
            'game_date': '2026-06-02',
            'league': 'Spanish Segunda División',
            'home_score': None,
        },
    ]


def test_soccer_all_leagues_default_shows_every_league_and_date(nhl):
    filtered, leagues_ui, selected = nhl._filter_soccer_picks(_sample_soccer_preds(), None)

    assert selected is None
    assert len(filtered) == 3
    assert {p['league'] for p in filtered} == {
        'English Premier League',
        'Spanish LaLiga',
        'Spanish Segunda División',
    }
    assert leagues_ui[0]['name'] == 'All Leagues'
    assert leagues_ui[0]['active'] is True
    assert any(lg['name'] == 'English Premier League' and not lg['active'] for lg in leagues_ui[1:])


def test_soccer_league_slug_filters_to_one_league(nhl):
    filtered, leagues_ui, selected = nhl._filter_soccer_picks(
        _sample_soccer_preds(),
        'english-premier-league',
    )

    assert selected == 'English Premier League'
    assert len(filtered) == 1
    assert filtered[0]['league'] == 'English Premier League'
    assert leagues_ui[0]['active'] is False
    assert any(lg['name'] == 'English Premier League' and lg['active'] for lg in leagues_ui[1:])


def test_soccer_picks_page_passes_multi_date_sorted_dates(nhl, monkeypatch):
    import NHL77FINAL as N

    preds = _sample_soccer_preds()

    def _fake_upcoming(_sport):
        return preds

    captured = {}

    def _fake_render(**kwargs):
        captured.update(kwargs)
        return 'ok'

    monkeypatch.setattr(N, 'get_upcoming_predictions', _fake_upcoming)
    monkeypatch.setattr(N, '_refresh_books_on_predictions', lambda *_a, **_k: None)
    monkeypatch.setattr(N, '_render_espn_picks_page', _fake_render)
    monkeypatch.setattr(N, 'log_site_visit', lambda *_a, **_k: None)
    monkeypatch.setattr(N, 'is_premium_user', lambda: False)

    class _User:
        is_authenticated = True

    monkeypatch.setattr(N, 'current_user', _User(), raising=False)

    with N.app.test_request_context('/soccer-picks'):
        out = N.sport_predictions('SOCCER')

    assert out == 'ok'
    assert captured['soccer_leagues'][0]['active'] is True
    assert len(captured['sorted_dates']) == 2
    assert '2026-06-02' in captured['sorted_dates']
    assert '2026-06-03' in captured['sorted_dates']
    assert sum(len(g) for g in captured['grouped_predictions'].values()) == 3
