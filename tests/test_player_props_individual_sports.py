"""Player props for Tennis, UFC, and Golf."""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _ensure_props_modules():
    from NHL77FINAL import _load_props_modules
    _load_props_modules()


def test_supported_leagues_include_individual_sports():
    from NHL77FINAL import _load_props_modules

    _, cfg = _load_props_modules()
    leagues = set(getattr(cfg, 'SUPPORTED_LEAGUES', []))
    assert {'TENNIS', 'UFC', 'GOLF'}.issubset(leagues)


def test_nba_next_game_tracks_home_away_venue(monkeypatch):
    from NHL77FINAL import _load_props_modules

    engine, _ = _load_props_modules()
    data = engine.get_league_data('NBA')
    rows = data.get('props') or []
    if not rows:
        pytest.skip('No NBA prop rows available in local live-data window')
    venues = {r.get('venue') for r in rows if r.get('venue')}
    assert venues <= {'home', 'away'}
    assert venues, 'NBA prop rows should expose home/away venue'


def test_individual_sports_return_prop_boards():
    from NHL77FINAL import _load_props_modules

    engine, _ = _load_props_modules()
    for league in ('TENNIS', 'UFC', 'GOLF'):
        data = engine.get_league_data(league)
        props = data.get('props') or []
        players = data.get('players') or []
        assert players, f'{league} should expose athletes from ESPN schedule'
        assert props, f'{league} should expose model prop rows'
        sample = props[0]
        assert sample.get('player_name')
        assert sample.get('prop_type') in engine._SPORT_PROJ_CAPS.get(league, {})
        assert sample.get('picked_side') in ('OVER', 'UNDER')
        assert sample.get('projection') is not None


def test_tennis_schedule_merges_atp_and_wta():
    _ensure_props_modules()
    from _standalone_player_props.data_sources import fetch_schedule_and_teams

    rows = fetch_schedule_and_teams('TENNIS')
    names = {r.get('home_team', '').lower() for r in rows} | {r.get('away_team', '').lower() for r in rows}
    assert names, 'Tennis schedule should not be empty when draws are live'
    assert len(rows) >= 4


def test_individual_results_payload_has_rows():
    from NHL77FINAL import _load_props_modules

    engine, _ = _load_props_modules()
    for league in ('TENNIS', 'UFC', 'GOLF'):
        payload = engine.get_league_results(league)
        assert payload.get('count', 0) > 0
        assert payload.get('items')
        assert payload.get('summary', {}).get('overall') is not None


def test_golf_actual_stats_parser():
    _ensure_props_modules()
    from _standalone_player_props.data_sources import _golf_round_stats, _birdies_bogeys_from_holes

    competitor = {
        'linescores': [{
            'period': 1,
            'statistics': {'categories': [{'stats': [
                {'value': 5.0}, {'value': 2.0},
            ]}]},
        }],
    }
    stats = _golf_round_stats(competitor)
    assert stats['birdies'] == 5.0
    assert stats['bogeys'] == 2.0

    hole_competitor = {
        'linescores': [{
            'period': 3,
            'linescores': [
                {'scoreType': {'displayValue': '-1'}},
                {'scoreType': {'displayValue': 'E'}},
                {'scoreType': {'displayValue': '+1'}},
                {'scoreType': {'displayValue': '-1'}},
            ],
        }],
    }
    hole_stats = _golf_round_stats(hole_competitor)
    assert hole_stats['birdies'] == 2.0
    assert hole_stats['bogeys'] == 1.0
    assert _birdies_bogeys_from_holes(hole_competitor['linescores'][0]['linescores']) == hole_stats


def test_tennis_games_parser():
    _ensure_props_modules()
    from _standalone_player_props.data_sources import _tennis_games_won

    competitor = {
        'linescores': [{'value': 7.0, 'winner': True}, {'value': 6.0, 'winner': True}],
    }
    assert _tennis_games_won(competitor) == 13.0


def test_ufc_core_fight_stats_parser():
    from _standalone_player_props.data_sources import _ufc_core_fight_stats

    stats = _ufc_core_fight_stats('600058949', '401864366', '5211223')
    assert stats.get('significant_strikes', 0) > 0
    assert stats.get('takedowns') is not None


def test_individual_actuals_from_box_scores():
    _ensure_props_modules()
    from datetime import date

    from _standalone_player_props.data_sources import fetch_individual_prop_actuals

    ufc = fetch_individual_prop_actuals('UFC', date(2026, 6, 6))
    assert any(v.get('significant_strikes') is not None for v in ufc.values())

    tennis = fetch_individual_prop_actuals('TENNIS', date(2026, 6, 6))
    assert any(v.get('games') is not None for v in tennis.values())

    golf = fetch_individual_prop_actuals('GOLF', date(2026, 6, 6))
    assert any(v.get('birdies') is not None for v in golf.values())
