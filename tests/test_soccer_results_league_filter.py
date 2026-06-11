"""Soccer results must scope tallies and season stats to the selected league only."""
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_soccer_results_league_slugs_cover_curated_order(nhl):
    for lg in nhl.SOCCER_LEAGUE_ORDER:
        slug = nhl._soccer_league_slug(lg)
        assert slug
        assert nhl._soccer_league_from_slug(slug) == lg


def test_build_soccer_results_leagues_ui_lists_every_curated_league(nhl):
    counts = {lg: 10 if lg == 'EFL Championship' else 0 for lg in nhl.SOCCER_LEAGUE_ORDER}
    ui = nhl._build_soccer_results_leagues_ui('EFL Championship', counts)
    assert len(ui) == len(nhl.SOCCER_LEAGUE_ORDER)
    efl = next(row for row in ui if row['name'] == 'EFL Championship')
    assert efl['active'] is True
    assert efl['url'] == '/soccer-results?league=eng.2'
    assert efl['count'] == 10
    wc = next(row for row in ui if row['name'] == 'FIFA World Cup')
    assert wc['url'] == '/soccer-results?league=fifa.world'


def test_fetch_soccer_completed_games_scoped_to_one_league(nhl):
    conn = nhl.get_db_connection()
    try:
        rows = nhl._fetch_soccer_completed_games(
            conn, 'EFL Championship', limit=500,
        )
        if not rows:
            pytest.skip('no EFL games in DB')
        leagues = {
            nhl._canonical_soccer_league_name(r['league']) or r['league']
            for r in rows
        }
        assert leagues == {'EFL Championship'}
    finally:
        conn.close()


def test_efl_tally_game_counts_match_league_scope(nhl):
    conn = nhl.get_db_connection()
    try:
        rows = nhl._fetch_soccer_completed_games(
            conn, 'EFL Championship', limit=500,
        )
        if not rows:
            pytest.skip('no EFL games in DB')
    finally:
        conn.close()

    daily = defaultdict(lambda: {'games': []})
    for game in rows:
        hs = nhl._to_float_safe(game['home_score'])
        aw = nhl._to_float_safe(game['away_score'])
        if hs is None or aw is None:
            continue
        dk = nhl._normalize_game_date_key(game['game_date'])
        daily[dk]['games'].append({
            'skip_grading': False,
            'ens_prob': 55.0,
            'ens_correct': True,
            'league': 'EFL Championship',
        })

    yesterday_dt = datetime.now() - timedelta(days=1)
    bundle = nhl._compute_results_tally_bundle(
        daily, yesterday_dt, sport='SOCCER', league_scoped=True,
    )
    season_games = sum(len(v['games']) for v in daily.values())
    cal_start = yesterday_dt - timedelta(days=6)
    cal_games = sum(
        len(bucket['games'])
        for dk, bucket in daily.items()
        if nhl.parse_date(dk) and cal_start <= nhl.parse_date(dk) <= yesterday_dt
    )
    assert season_games == len(rows)
    assert bundle['weekly_tally_games'] == cal_games
    assert bundle['weekly_tally_games'] <= season_games
    assert bundle['daily_tally_games'] <= season_games
    if cal_games:
        assert bundle['weekly_tally']['ensemble']['total'] == bundle['weekly_tally_games']


def test_soccer_results_redirects_bare_url_to_league_slug(nhl, monkeypatch):
    conn_calls = {'n': 0}

    class _Cursor:
        def fetchall(self):
            return []

    class _Conn:
        def execute(self, *_args, **_kwargs):
            return _Cursor()

        def close(self):
            conn_calls['n'] += 1

    monkeypatch.setattr(nhl, 'get_db_connection', lambda: _Conn())
    monkeypatch.setattr(
        nhl,
        '_soccer_curated_league_game_counts',
        lambda _c: {'EFL Championship': 5, 'English Premier League': 12},
    )
    monkeypatch.setattr(nhl, '_resolve_soccer_results_league', nhl._resolve_soccer_results_league)

    with nhl.app.test_request_context('/soccer-results'):
        resp = nhl._render_daily_sport_results_page('SOCCER')

    assert resp.status_code == 302
    assert 'league=eng.1' in resp.location


def test_team_short_soccer_keeps_multi_word_country_names():
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(ROOT / 'templates' / 'includes')))
    tmpl = env.from_string(
        "{% from 'score_macros.html' import team_short %}"
        "{{ team_short('South Africa', 'SOCCER') }}"
    )
    assert tmpl.render().strip() == 'South Africa'
    tmpl_mlb = env.from_string(
        "{% from 'score_macros.html' import team_short %}"
        "{{ team_short('South Africa', 'MLB') }}"
    )
    assert tmpl_mlb.render().strip() == 'Africa'


def test_pick_card_template_passes_sport_to_both_team_short_calls():
    """Pick-card face row must pass sport into team_short for home and away."""
    src = (ROOT / 'templates' / 'includes' / 'game_card_body.html').read_text()
    assert "team_short(away_id, sport)" in src
    assert "team_short(home_id, sport)" in src
    assert "team_short(home_id)" not in src.replace("team_short(home_id, sport)", "")
