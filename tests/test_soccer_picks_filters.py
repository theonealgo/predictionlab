"""Soccer picks page: all-leagues default, ESPN/all fetch, multi-date nav."""
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
            'ensemble_prob': 58.0,
            'elo_prob': 57.0,
        },
        {
            'home_team_id': 'Real Madrid',
            'away_team_id': 'Barcelona',
            'game_date': '2026-06-03',
            'league': 'Spanish LaLiga',
            'home_score': None,
            'ensemble_prob': 54.0,
            'elo_prob': 53.0,
        },
        {
            'home_team_id': 'Team A',
            'away_team_id': 'Team B',
            'game_date': '2026-06-02',
            'league': 'Spanish Segunda División',
            'home_score': None,
            'ensemble_prob': 61.0,
            'elo_prob': 60.0,
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


def test_soccer_league_slider_lists_full_curated_order(nhl):
    _, leagues_ui, _ = nhl._filter_soccer_picks(_sample_soccer_preds(), None)
    pill_names = [lg['name'] for lg in leagues_ui[1:]]
    assert pill_names == list(nhl.SOCCER_LEAGUE_ORDER)


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
    grouped_n = sum(len(g) for g in captured['grouped_predictions'].values())
    assert grouped_n >= 2
    leagues = {p.get('league') for gs in captured['grouped_predictions'].values() for p in gs}
    assert 'English Premier League' in leagues
    assert 'Spanish LaLiga' in leagues


def _mock_soccer_all_payload():
    """Two curated leagues on two dates via ESPN uid league ids."""
    return [
        {
            'events': [
                {
                    'id': '9001',
                    'date': '2026-06-02T19:00Z',
                    'status': {'type': {'name': 'STATUS_SCHEDULED'}},
                    'competitions': [{
                        'uid': 's:600~l:700~e:9001~c:9001',
                        'competitors': [
                            {'homeAway': 'home', 'team': {'displayName': 'Arsenal'}, 'score': '0'},
                            {'homeAway': 'away', 'team': {'displayName': 'Chelsea'}, 'score': '0'},
                        ],
                    }],
                },
                {
                    'id': '9002',
                    'date': '2026-06-03T19:00Z',
                    'status': {'type': {'name': 'STATUS_SCHEDULED'}},
                    'competitions': [{
                        'uid': 's:600~l:740~e:9002~c:9002',
                        'competitors': [
                            {'homeAway': 'home', 'team': {'displayName': 'Real Madrid'}, 'score': '0'},
                            {'homeAway': 'away', 'team': {'displayName': 'Barcelona'}, 'score': '0'},
                        ],
                    }],
                },
            ],
        },
    ]


def test_fetch_soccer_scoreboard_api_games_multi_league_date(nhl, monkeypatch):
    payloads = _mock_soccer_all_payload()
    calls = {'n': 0}

    def _fake_cached_get(url, **kwargs):
        idx = calls['n']
        calls['n'] += 1
        return payloads[min(idx, len(payloads) - 1)]

    monkeypatch.setattr(nhl, '_cached_get', _fake_cached_get)
    monkeypatch.setattr(
        nhl,
        '_espn_soccer_league_id_map',
        lambda: {'700': 'English Premier League', '740': 'Spanish LaLiga'},
    )
    monkeypatch.setattr(nhl, '_register_soccer_from_competitor', lambda *_a, **_k: None)

    games = nhl._fetch_soccer_scoreboard_api_games(days_back=0, days_forward=0)
    upcoming = [g for g in games if g.get('home_score') is None]
    leagues = {g['league'] for g in upcoming}
    dates = {g['game_date'] for g in upcoming}

    assert len(upcoming) == 2
    assert leagues == {'English Premier League', 'Spanish LaLiga'}
    assert dates == {'2026-06-02', '2026-06-03'}


def test_soccer_espn_slug_covers_new_catalog_leagues():
    from soccer_league_catalog import soccer_espn_slug

    assert soccer_espn_slug('Copa do Brasil') == 'bra.copa_do_brazil'
    assert soccer_espn_slug('copa do brazil') == 'bra.copa_do_brazil'
    assert soccer_espn_slug('English Premier League') == 'eng.1'


def test_soccer_model_bundle_falls_back_to_all_soccer(nhl, monkeypatch):
    ready = type('B', (), {'ready': True, 'reason': None})()
    calls = []

    def _fake_build(games, league_name=None, min_games=12):
        calls.append(league_name)
        if league_name == 'Copa do Brasil':
            return type('B', (), {'ready': False, 'reason': 'only 2 games'})()
        return ready

    monkeypatch.setattr(nhl, 'build_soccer_model_bundle', _fake_build)
    nhl._SOCCER_MODEL_CACHE.clear()
    bundle = nhl._get_soccer_model_bundle([], 'Copa do Brasil')
    assert bundle is ready
    assert 'Copa do Brasil' in calls
    assert None in calls


def test_enrich_thin_copa_card_fills_models_and_pl(nhl, monkeypatch):
    pred = {
        'home_team_id': 'Atlético-MG',
        'away_team_id': 'Cruzeiro',
        'league': 'Copa do Brasil',
        'game_date': '2026-08-18',
        'home_score': None,
    }

    class _Bundle:
        ready = True
        reason = None

        def predict(self, home, away):
            return {
                'elo_prob': 0.58,
                'poisson_reg_prob': 0.61,
                'ensemble_prob': 0.60,
                'poisson_xg_prob': 0.57,
                'markov_prob': 0.59,
                'draw_prob': 0.26,
                'expected_home_score': 1.4,
                'expected_away_score': 1.1,
            }

    monkeypatch.setattr(nhl, '_get_soccer_model_bundle', lambda *_a, **_k: _Bundle())
    monkeypatch.setattr(nhl, '_apply_model_fades_batch', lambda *_a, **_k: None)
    n = nhl._enrich_thin_soccer_predictions([pred])
    assert n == 1
    assert pred['xgb_prob'] == 61.0
    assert pred['ensemble_prob'] == 60.0
    assert pred['face_home_prob'] is None or pred['xgb_prob']
    assert pred['pl_model_home_ml'] is None  # filled later by card display
    nhl._prepare_pred_card_display(pred, sport='SOCCER')
    assert pred.get('face_home_prob') is not None
    assert pred.get('pl_model_home_ml') is not None
    assert pred.get('face_home_prob') != 50.0 or pred.get('pl_model_home_ml') != -108
    assert pred.get('disp_pl_spread') is not None
    assert pred.get('disp_pl_total') is not None
    assert pred.get('disp_xs_spread') is not None
    assert pred.get('disp_xs_total') is not None
    assert pred.get('book_spread') is None
    assert pred.get('disp_book_spread') is None


def test_parse_soccer_scoreboard_keeps_book_odds(nhl):
    data = {
        'events': [{
            'id': '9911',
            'date': '2026-08-18T00:00Z',
            'status': {'type': {'name': 'STATUS_SCHEDULED'}},
            'league': {'name': 'Copa do Brasil'},
            'competitions': [{
                'uid': 's:600~l:8306~e:9911~c:9911',
                'odds': [{
                    'provider': {'name': 'DraftKings'},
                    'homeTeamOdds': {'moneyLine': -145},
                    'awayTeamOdds': {'moneyLine': 125},
                    'spread': -0.5,
                    'overUnder': 2.5,
                }],
                'competitors': [
                    {'homeAway': 'home', 'team': {'displayName': 'Cruzeiro'}, 'score': '0'},
                    {'homeAway': 'away', 'team': {'displayName': 'Atlético-MG'}, 'score': '0'},
                ],
            }],
        }],
    }
    from datetime import datetime
    games = nhl._parse_soccer_scoreboard_events(data, datetime(2026, 8, 18))
    assert len(games) == 1
    assert games[0]['league'] == 'Copa do Brasil'
    assert games[0]['book_home_moneyline'] == -145
    assert games[0]['book_away_moneyline'] == 125
    assert games[0]['book_spread'] == -0.5
    assert games[0]['book_total'] == 2.5


def test_soccer_team_alias_resolves_atletico_mg():
    from soccer_models import SoccerEloModel, _resolve_team_key

    elo = SoccerEloModel()
    elo.ratings['Atletico Mineiro'] = 1620.0
    elo.ratings['Cruzeiro'] = 1580.0
    resolved = _resolve_team_key('Atlético-MG', elo.ratings)
    assert resolved == 'Atletico Mineiro'
    home, draw, away = elo.predict('Atlético-MG', 'Cruzeiro')
    assert home is not None and away is not None
    assert abs(home - 0.5) > 0.01 or abs(away - 0.5) > 0.01


def test_soccer_game_id_keeps_underscore_slugs():
    from pl_book_odds_api import (
        _soccer_slug_and_event_from_game_id,
        _soccer_league_slugs_to_try,
        _odds_api_soccer_key,
    )

    slug, event = _soccer_slug_and_event_from_game_id(
        'SOCCER_bra.copa_do_brazil_401991100'
    )
    assert slug == 'bra.copa_do_brazil'
    assert event == '401991100'
    slugs = _soccer_league_slugs_to_try(
        'SOCCER_bra.copa_do_brazil_401991100',
        'Copa do Brasil',
    )
    assert slugs[0] == 'bra.copa_do_brazil'
    assert 'bra.copa' not in slugs[:2]
    # Existing odds-API adapter has no Copa do Brasil market.
    assert _odds_api_soccer_key('Copa do Brasil') is None
    assert _odds_api_soccer_key('Argentine Liga Profesional de Fútbol') == (
        'soccer_argentina_primera_division'
    )
    assert _odds_api_soccer_key('Brazilian Serie A') == 'soccer_brazil_campeonato'


def test_merge_scoreboard_books_onto_empty_copa_card(nhl):
    pred = {
        'game_id': 'SOCCER_bra.copa_do_brazil_401991100',
        'home_team_id': 'Cruzeiro',
        'away_team_id': 'Atlético-MG',
        'league': 'Copa do Brasil',
        'game_date': '2026-08-25',
        'home_score': None,
        'disp_pl_spread': -1.0,
        'disp_xs_spread': 1.0,
        'disp_pl_total': 1.5,
        'disp_xs_total': 1.5,
    }
    board = [{
        'game_id': 'SOCCER_bra.copa_do_brazil_401991100',
        'home_team_id': 'Cruzeiro',
        'away_team_id': 'Atlético-MG',
        'game_date': '2026-08-25',
        'book_home_moneyline': -145,
        'book_away_moneyline': 125,
        'book_spread': -0.5,
        'book_total': 2.5,
        'book_odds_source': 'ESPN Scoreboard',
    }]
    n = nhl._merge_soccer_scoreboard_books([pred], board)
    assert n == 1
    assert pred['book_home_moneyline'] == -145
    assert pred['book_away_moneyline'] == 125
    assert pred['book_spread'] == -0.5
    assert pred['book_total'] == 2.5
    nhl._prepare_pred_card_display(pred, sport='SOCCER')
    assert pred.get('disp_book_spread') is not None
    assert pred.get('disp_book_total') == 2.5
    assert not nhl._soccer_card_dropped_existing_books(pred)


def test_catalog_books_fail_if_lines_exist_but_odds_table_empty(nhl):
    pred = {
        'home_team_id': 'Cruzeiro',
        'away_team_id': 'Atlético-MG',
        'league': 'Copa do Brasil',
        'game_date': '2026-08-25',
        'home_score': None,
        'book_home_moneyline': -120,
        'book_away_moneyline': 100,
        'book_spread': -0.5,
        'book_total': 2.5,
        'disp_pl_spread': -1.0,
        'disp_xs_spread': 1.0,
        'disp_pl_total': 1.5,
        'disp_xs_total': 1.5,
    }
    nhl._set_card_book_lines(pred)
    assert pred.get('disp_book_spread') is not None
    assert pred.get('disp_book_total') == 2.5
    assert not nhl._soccer_card_dropped_existing_books(pred)
    broken = dict(pred)
    broken['disp_book_spread'] = None
    broken['disp_book_total'] = None
    broken['book_home_moneyline'] = None
    broken['book_away_moneyline'] = None
    # Fetch layer still had lines on the sibling row.
    broken['book_spread'] = -0.5
    broken['book_total'] = 2.5
    assert nhl._soccer_card_dropped_existing_books(broken)


def test_soccer_card_with_models_must_have_pl_odds_when_books_missing(nhl):
    """Model %s + empty books must still show PL ML / AH / total (not book-gated)."""
    pred = {
        'home_team_id': 'Cruzeiro',
        'away_team_id': 'Atlético-MG',
        'league': 'Copa do Brasil',
        'game_date': '2026-08-25',
        'home_score': None,
        'ensemble_prob': 60.0,
        'xgb_prob': 61.0,
        'glicko2_prob': 58.0,
        'draw_prob': 26.0,
        'home_win_prob': 47.0,
        'away_win_prob': 27.0,
        'v2_expected_home': 1.4,
        'v2_expected_away': 1.1,
    }
    assert pred.get('book_home_moneyline') is None
    assert pred.get('book_spread') is None
    nhl._apply_soccer_model_market_lines(pred)
    nhl._prepare_pred_card_display(pred, sport='SOCCER')
    assert pred.get('pl_model_home_ml') is not None
    assert pred.get('pl_model_away_ml') is not None
    assert pred.get('pl_model_draw_ml') is not None
    assert pred.get('disp_pl_spread') is not None
    assert pred.get('disp_pl_total') is not None
    assert pred.get('disp_xs_spread') is not None
    assert pred.get('disp_xs_total') is not None
    assert pred.get('pl_proj_home_pts') is not None
    assert pred.get('xs_proj_home_pts') is not None
    # Do not mint the fake coin-flip filler.
    assert pred.get('pl_model_home_ml') != -108 or pred.get('face_home_prob') != 50.0
    assert pred.get('disp_book_spread') is None
    assert pred.get('disp_book_total') is None


def test_soccer_results_proj_score_not_model_unavailable(nhl):
    """Results card with PL projected score must grade spread/total, not print unavailable."""
    g = {
        'home': 'Al Shabab',
        'away': 'Al Qadsiah',
        'home_team_id': 'Al Shabab',
        'away_team_id': 'Al Qadsiah',
        'date': '2026-08-17',
        'game_id': 'SOCCER_sau.1_test1',
        'home_score': 2,
        'away_score': 3,
        'ens_prob': 55.0,
        'ensemble_prob': 55.0,
        'xgb_prob': 58.0,
        'draw_prob': 26.0,
        'home_win_prob': 42.0,
        'away_win_prob': 32.0,
        'v2_expected_home': 1.5,
        'v2_expected_away': 1.0,
        'book_spread': 0.5,
        'book_total': 2.5,
        'league': 'Saudi Pro League',
    }
    nhl._apply_soccer_model_market_lines(g)
    assert g.get('our_spread') is not None
    assert g.get('xgb_spread') is not None
    daily = {'2026-08-17': {'games': [g]}}
    nhl._compute_spread_total_for_daily('SOCCER', daily, skip_efficiency=True)
    nhl._prepare_result_card_display(g, 'SOCCER')
    assert g.get('spread_pick_reason') != 'model score unavailable'
    assert g.get('total_pick_reason') != 'model score unavailable'
    assert g.get('disp_pl_spread') is not None
    assert g.get('disp_pl_total') is not None
    assert g.get('disp_xs_spread') is not None
    assert g.get('disp_xs_total') is not None
    assert g.get('pl_proj_home_pts') is not None
    assert g.get('xs_proj_home_pts') is not None
    # Books API +0.5 displays as away -0.5; PL home-centric +0.5 is home -0.5.
    # Same raw number, opposite convention — PL must not reuse the book sign.
    assert g.get('disp_book_spread') == pytest.approx(-0.5)
    assert g.get('disp_pl_spread') == pytest.approx(0.5)
