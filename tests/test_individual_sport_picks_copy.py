"""Individual sport picks pages: no data-source leaks; UFC cards render when bouts exist."""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_FORBIDDEN = re.compile(
    r'espn|pull live|data source|live bout data|live draw data|live field data',
    re.I,
)

_INDIVIDUAL_SPORTS = ('TENNIS', 'UFC', 'GOLF')
_PICKS_PATHS = {
    'TENNIS': '/tennis-picks',
    'UFC': '/ufc-picks',
    'GOLF': '/golf-picks',
}


@pytest.mark.parametrize('mod_name', _INDIVIDUAL_SPORTS)
def test_offseason_hint_has_no_data_source_leak(mod_name):
    import importlib
    mod = importlib.import_module(f'sports.{mod_name}')
    hint = getattr(mod, 'OFFSEASON_HINT', '')
    assert hint, f'{mod_name} missing OFFSEASON_HINT'
    assert not _FORBIDDEN.search(hint), f'{mod_name} OFFSEASON_HINT leaks source: {hint!r}'


@pytest.mark.parametrize('sport,path', list(_PICKS_PATHS.items()))
def test_picks_page_has_no_data_source_leak(sport, path):
    import NHL77FINAL as N
    N._PREDICTIONS_CACHE.clear()
    N._SPORT_PREDICTIONS_PAGE_CACHE.clear()
    with N.app.test_client() as client:
        resp = client.get(path)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Ignore JSON keys in search script (espn_results API field name).
    visible = re.sub(r'<script[\s\S]*?</script>', '', html, flags=re.I)
    assert not _FORBIDDEN.search(visible), f'{path} exposes data source in visible HTML'


def test_ufc_picks_renders_fight_cards_when_bouts_exist(monkeypatch):
    import NHL77FINAL as N
    from sports import UFC

    fight_date = (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d')

    def _fake_load():
        return [
            (datetime.strptime(fight_date, '%Y-%m-%d'), {
                'game_id': 'UFC_test_401',
                'home_team_id': 'Fighter Alpha',
                'away_team_id': 'Fighter Beta',
                'game_date': fight_date,
                'event_date': f'{fight_date}T21:00:00Z',
                'home_score': None,
                'away_score': None,
                'league': 'UFC Test Card',
            }),
        ]

    monkeypatch.setitem(N._INDIVIDUAL_SPORT_LOADERS, 'UFC', _fake_load)
    N._PREDICTIONS_CACHE.clear()
    N._SPORT_PREDICTIONS_PAGE_CACHE.clear()

    preds = N.get_upcoming_predictions('UFC')
    assert len(preds) >= 1
    assert preds[0]['home_team_id'] == 'Fighter Alpha'

    with N.app.test_client() as client:
        resp = client.get('/ufc-picks')
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert 'game-card' in html
    assert 'Fighter Alpha' in html
    assert 'Fighter Beta' in html
    assert 'No predictions available' not in html


def test_ufc_fetch_api_games_returns_bouts_from_scoreboard():
    """Integration: live scoreboard should yield bout rows when cards are scheduled."""
    from sports.UFC import fetch_api_games

    games = fetch_api_games()
    if not games:
        pytest.skip('No UFC bouts in current ESPN window')
    sample = games[0]
    assert sample.get('home_team_id') and sample.get('away_team_id')
    assert sample['home_team_id'] != 'TBD'
    assert sample['away_team_id'] != 'TBD'
    assert sample.get('game_id', '').startswith('UFC_')
