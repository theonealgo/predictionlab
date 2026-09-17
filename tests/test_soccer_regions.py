"""Soccer region catalog + region filter (15-league slate)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from soccer_league_catalog import (
    SOCCER_LEAGUE_ENDPOINTS,
    SOCCER_LEAGUE_NUMERIC_IDS,
    SOCCER_LEAGUE_ORDER,
    soccer_leagues_for_region,
    soccer_region_from_slug,
)


def test_catalog_has_official_slugs_and_ids():
    assert len(SOCCER_LEAGUE_ORDER) == 15
    assert SOCCER_LEAGUE_ENDPOINTS['English Premier League'] == 'eng.1'
    assert SOCCER_LEAGUE_ENDPOINTS['Major League Soccer'] == 'usa.1'
    assert SOCCER_LEAGUE_ENDPOINTS['Brazilian Serie A'] == 'bra.1'
    assert SOCCER_LEAGUE_ENDPOINTS['Swiss Super League'] == 'sui.1'
    assert SOCCER_LEAGUE_NUMERIC_IDS['700'] == 'English Premier League'
    assert SOCCER_LEAGUE_NUMERIC_IDS['740'] == 'Spanish LaLiga'
    assert SOCCER_LEAGUE_NUMERIC_IDS['3944'] == 'Swiss Super League'


def test_every_catalog_league_has_espn_slug_and_numeric_id():
    for name in SOCCER_LEAGUE_ORDER:
        slug = SOCCER_LEAGUE_ENDPOINTS.get(name)
        assert slug, f'missing slug for {name}'
        assert any(v == name for v in SOCCER_LEAGUE_NUMERIC_IDS.values()), name


def test_region_buckets_match_labels():
    assert soccer_region_from_slug('europe') == 'europe'
    assert soccer_region_from_slug('top') == 'top'
    europe = soccer_leagues_for_region('europe')
    assert 'English Premier League' in europe
    assert 'Swiss Super League' in europe
    assert 'Brazilian Serie A' not in europe
    south = soccer_leagues_for_region('south-america')
    assert 'Brazilian Serie A' in south
    concacaf = soccer_leagues_for_region('concacaf')
    assert 'Major League Soccer' in concacaf
    assert soccer_leagues_for_region('asia') == list(SOCCER_LEAGUE_ORDER)


def test_filter_soccer_picks_by_region(nhl_mod=None):
    import NHL77FINAL as nhl

    preds = [
        {'home_team_id': 'A', 'away_team_id': 'B', 'game_date': '2026-08-20',
         'league': 'English Premier League', 'home_score': None},
        {'home_team_id': 'C', 'away_team_id': 'D', 'game_date': '2026-08-20',
         'league': 'Brazilian Serie A', 'home_score': None},
        {'home_team_id': 'E', 'away_team_id': 'F', 'game_date': '2026-08-20',
         'league': 'Major League Soccer', 'home_score': None},
    ]
    filtered, leagues_ui, selected = nhl._filter_soccer_picks(
        preds, None, 'south-america'
    )
    assert selected is None
    assert {p['league'] for p in filtered} == {'Brazilian Serie A'}
    names = [lg['name'] for lg in leagues_ui]
    assert names[0] == 'All Leagues'
    assert 'Brazilian Serie A' in names
    assert 'English Premier League' not in names


def test_filter_soccer_picks_all_still_shows_every_region():
    import NHL77FINAL as nhl

    preds = [
        {'home_team_id': 'A', 'away_team_id': 'B', 'game_date': '2026-08-20',
         'league': 'English Premier League', 'home_score': None},
        {'home_team_id': 'C', 'away_team_id': 'D', 'game_date': '2026-08-20',
         'league': 'Major League Soccer', 'home_score': None},
    ]
    filtered, leagues_ui, selected = nhl._filter_soccer_picks(preds, None, None)
    assert selected is None
    assert len(filtered) == 2
    assert leagues_ui[0]['active'] is True
    assert [lg['name'] for lg in leagues_ui[1:]] == list(nhl.SOCCER_LEAGUE_ORDER)


def test_dropdown_has_continent_and_all():
    from soccer_ui_fixup import soccer_league_dropdown_html, _curated_soccer_league_options

    opts = _curated_soccer_league_options(kind='picks')
    html = soccer_league_dropdown_html(opts, kind='picks')
    assert 'id="soccer-region"' in html
    assert 'Top Competitions' in html
    assert 'USA &amp; Canada' in html or 'USA & Canada' in html
    assert 'Europe' in html
    assert 'South America' in html
    assert '>Live</option>' in html or 'value="live"' in html
    assert 'id="league"' in html
    assert 'All leagues' in html
    assert 'optgroup' in html
    assert 'Swiss Super League' in html
    assert 'Japanese J.League' not in html
    assert 'data-region="top,europe"' in html


def test_live_region_is_a_continent_slug():
    assert soccer_region_from_slug('live') == 'live'
    assert soccer_leagues_for_region('live') == []
    assert soccer_leagues_for_region(
        'live', live_names=['English Premier League', 'Swiss Super League'],
    ) == ['English Premier League', 'Swiss Super League']


def test_filter_soccer_picks_live_skips_idle_historical():
    import NHL77FINAL as nhl

    today = nhl._soccer_et_today_str()
    preds = [
        {'home_team_id': 'Arsenal', 'away_team_id': 'Chelsea',
         'game_date': today, 'league': 'English Premier League',
         'home_score': None},
        {'home_team_id': 'Flamengo', 'away_team_id': 'Palmeiras',
         'game_date': '2025-12-22', 'league': 'Brazilian Serie A',
         'home_score': 1, 'away_score': 0},
    ]
    filtered, leagues_ui, selected = nhl._filter_soccer_picks(preds, None, 'live')
    assert selected is None
    assert {p['league'] for p in filtered} == {'English Premier League'}
    names = [lg['name'] for lg in leagues_ui]
    assert names[0] == 'All Leagues'
    assert 'English Premier League' in names
    assert 'Brazilian Serie A' not in names
