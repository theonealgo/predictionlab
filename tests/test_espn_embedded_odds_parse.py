"""ESPN scoreboard odds: legacy flat fields + nested DraftKings widgets."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_parse_legacy_flat_odds(nhl):
    item = {
        'provider': {'name': 'DraftKings'},
        'spread': -0.5,
        'overUnder': 2.5,
        'homeTeamOdds': {'moneyLine': -160},
        'awayTeamOdds': {'moneyLine': 380},
    }
    parsed = nhl._parse_espn_embedded_odds_item(item)
    assert parsed['spread'] == pytest.approx(-0.5)
    assert parsed['total'] == pytest.approx(2.5)
    assert parsed['home_ml'] == -160
    assert parsed['away_ml'] == 380


def test_parse_nested_scoreboard_widget(nhl):
    item = {
        'provider': {'name': 'DraftKings'},
        'overUnder': 2.5,
        'pointSpread': {
            'home': {'close': {'line': '-0.5', 'odds': '-165'}},
            'away': {'close': {'line': '+0.5', 'odds': '+120'}},
        },
        'moneyline': {
            'home': {'close': {'odds': '-160'}},
            'away': {'close': {'odds': '+380'}},
        },
    }
    parsed = nhl._parse_espn_embedded_odds_item(item)
    assert parsed is not None
    assert parsed['spread'] == pytest.approx(-0.5)
    assert parsed['total'] == pytest.approx(2.5)
    assert parsed['home_ml'] == -160
    assert parsed['away_ml'] == 380


def test_parse_skips_live_provider_and_null_items(nhl):
    assert nhl._parse_espn_embedded_odds_item(None) is None
    assert nhl._parse_espn_embedded_odds_item({'provider': {'name': 'ESPN BET Live'}}) is None


def test_scoreboard_fetch_attaches_nested_books(nhl, monkeypatch):
    payload = {
        'events': [{
            'id': '761695',
            'date': '2026-07-31T23:30Z',
            'status': {'type': {'name': 'STATUS_SCHEDULED'}},
            'competitions': [{
                'uid': 's:600~l:770~e:761695~c:761695',
                'competitors': [
                    {'homeAway': 'home', 'team': {'displayName': 'New York City FC'}, 'score': '0'},
                    {'homeAway': 'away', 'team': {'displayName': 'Toronto FC'}, 'score': '0'},
                ],
                'odds': [{
                    'provider': {'name': 'DraftKings'},
                    'overUnder': 2.5,
                    'pointSpread': {
                        'home': {'close': {'line': '-0.5', 'odds': '-165'}},
                    },
                    'moneyline': {
                        'home': {'close': {'odds': '-160'}},
                        'away': {'close': {'odds': '+380'}},
                    },
                }],
            }],
        }],
    }

    monkeypatch.setattr(nhl, '_cached_get', lambda *_a, **_k: payload)
    monkeypatch.setattr(
        nhl,
        '_espn_soccer_league_id_map',
        lambda: {'770': 'Major League Soccer'},
    )
    monkeypatch.setattr(nhl, '_register_soccer_from_competitor', lambda *_a, **_k: None)

    games = nhl._fetch_soccer_scoreboard_api_games(days_back=0, days_forward=0)
    assert len(games) == 1
    g = games[0]
    assert g['book_spread'] == pytest.approx(-0.5)
    assert g['book_total'] == pytest.approx(2.5)
    assert g['book_home_moneyline'] == -160
    assert g['book_away_moneyline'] == 380
