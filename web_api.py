"""JSON API for the React front end in web/.

Cards are read from the same sport page the server renders today, fetched in-process with the
visitor's host and cookies, so the numbers and the paywall match the current site. The data
helpers (sports list, live boxes, results) live in NHL77FINAL and are imported when called.
"""
import gzip
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from html import unescape
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, current_app, jsonify, render_template, request

api = Blueprint('pl_web_api', __name__)


@api.route('/api/web/blog')
def api_blog():
    from blog_feed import feed, PAGE_SIZE, MAX_PAGE_SIZE
    import NHL77FINAL as site
    try:
        offset = int(request.args.get('offset', '0'))
        limit = int(request.args.get('limit', str(PAGE_SIZE)))
        section = request.args.get('section') or None
        if section not in (None, 'news', 'latest') or offset < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify(error='Choose a valid article page.'), 400
    try:
        payload = feed(site._blog_template_posts(), section, offset, limit)
    except Exception:
        current_app.logger.exception('Published blog articles unavailable')
        return jsonify(error='Articles could not be loaded. Please retry.'), 503
    response = jsonify(payload)
    response.headers['Cache-Control'] = 'public, max-age=30'
    return response


@api.record_once
def _start_prop_workers(state):
    if state.app.testing:
        return
    import NHL77FINAL as site
    if os.getenv('PL_PROP_SETTLEMENT', '1') == '1':
        from props_service import start_settlement
        start_settlement(site)
    if os.getenv('PL_PROP_PUBLICATION', '1') == '1':
        from props_service import start_daily
        start_daily(site)


def _props_hub_access():
    from flask_login import current_user
    from auth_system import is_premium_user
    if not current_user.is_authenticated or not is_premium_user():
        response = jsonify(locked=True, loggedIn=bool(current_user.is_authenticated),
                           detail='An active premium membership is required for Sports Props.')
        response.status_code = 403 if current_user.is_authenticated else 401
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Cookie')
        return response
    return None


@api.route('/api/web/props-navigation')
def api_props_navigation():
    from props_navigation import navigation
    from sports_props_hub import LEAGUES
    import NHL77FINAL as site
    day = request.args.get('date') or datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    try:
        if datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day:
            raise ValueError
    except ValueError:
        return jsonify(error='Invalid date'), 400
    from props_service import entries as prop_entries
    entries = prop_entries(site, day)
    try:
        data = navigation(site.DATABASE, day, entries)
    except Exception:
        current_app.logger.exception('Props availability metadata unavailable')
        return jsonify(date=day, sports=[], known_total=None, complete=False), 503
    response = jsonify(data)
    response.headers['Cache-Control'] = 'public, max-age=30'
    return response


@api.route('/api/web/props-hub')
def api_props_hub():
    denied = _props_hub_access()
    if denied is not None:
        return denied
    from sports_props_hub import LEAGUES, preview, historical_catalog
    import NHL77FINAL as site
    selected = (request.args.get('league') or '').upper()
    if selected and selected not in LEAGUES:
        return jsonify(error='Unsupported sport'), 400
    day = request.args.get('date') or datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    try:
        if datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day:
            raise ValueError
    except ValueError:
        return jsonify(error='Choose a valid YYYY-MM-DD props date.'), 400
    if day < datetime.now(ZoneInfo('America/New_York')).date().isoformat():
        from props_navigation import game_catalog, bind_games
        data = historical_catalog(site.DATABASE, day, selected, limit=None if selected else 3)
        catalog = game_catalog(site.DATABASE, day)
        for section in data['sections']:
            bind_games(section, catalog)
        response = jsonify(data)
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Cookie')
        return response
    sections = []
    from props_navigation import game_catalog, bind_games
    from props_service import entries as prop_entries, ensure, building as prop_building
    if not current_app.testing:
        ensure(site, day, selected or None)
    entries = prop_entries(site, day)
    catalog = game_catalog(site.DATABASE, day)
    counts = []
    for league in LEAGUES:
        entry = entries.get(league)
        building = prop_building(site, league, day)
        section = preview(league, entry, limit=None if selected else 3, day=day)
        bind_games(section, catalog, entry)
        section['building'] = building
        counts.append({'league': league, 'count': section['total'], 'kind': 'available',
                       'status': section.get('status'), 'reason': section.get('reason')})
        if not selected or selected == league:
            sections.append(section)
    from prop_forecast_store import capture
    recorded = 0
    try:
        from prop_forecast_store import _aware
        now = datetime.now(ZoneInfo('UTC'))
        expected = sum(bool(forecast.get('side')) for section in sections for row in section.get('items') or []
                       if _aware(row.get('event_start')) and _aware(row['event_start']) > now
                       for forecast in row.get('model_forecasts') or [])
        published = bool(sections) and all(
            ((entries.get(section['league']) or {}).get('payload') or {}).get('publication_captured')
            for section in sections if section.get('items'))
        recorded = expected if published else capture(site.DATABASE, sections)
        if recorded and not current_app.testing:
            from props_service import start_settlement
            start_settlement(site)
        tracking_available = recorded >= expected
    except Exception:
        current_app.logger.exception('Prospective prop capture failed')
        tracking_available = False
    for section in sections:
        for row in section.get('items') or []:
            row.pop('input_values', None)
            row.pop('input_hash', None)
    response = jsonify(leagues=list(LEAGUES), date=day, counts=counts, sections=sections, captured_forecasts=recorded,
                       tracking_available=tracking_available)
    response.headers['Cache-Control'] = 'private, no-store'
    response.vary.add('Cookie')
    return response


@api.route('/api/web/props-results')
def api_props_results():
    denied = _props_hub_access()
    if denied is not None:
        return denied
    from sports_props_hub import read_results
    from NHL77FINAL import DATABASE
    try:
        data = read_results(DATABASE, (request.args.get('league') or '').upper() or None,
                            request.args.get('date') or None, window=request.args.get('window') or 'night')
    except ValueError:
        return jsonify(error='Choose a supported sport and a valid YYYY-MM-DD date.'), 400
    except Exception:
        current_app.logger.exception('Read-only props results unavailable')
        return jsonify(error='Recorded prop results could not be loaded. Please retry.'), 503
    response = jsonify(data)
    response.headers['Cache-Control'] = 'private, no-store'
    response.vary.add('Cookie')
    return response


@api.after_request
def _gzip_json(response):
    """Compress the JSON API: the sport cards payloads are large and very repetitive."""
    if response.status_code != 200 or response.direct_passthrough or response.headers.get('Content-Encoding'):
        return response
    if 'gzip' not in request.headers.get('Accept-Encoding', ''):
        return response
    body = response.get_data()
    if len(body) < 1024:
        return response
    response.set_data(gzip.compress(body, compresslevel=5))
    response.headers['Content-Encoding'] = 'gzip'
    response.vary.add('Accept-Encoding')
    return response


# Small feeds whose text the page shows as is (header / footer HTML, golf notes, the board's share text).
# Registered after _gzip_json, so it runs before it. The card feed is reworded once when it is built.
_WORDED_FEEDS = ('/api/web/chrome', '/api/web/golf', '/api/web/all-sports', '/api/web/live', '/api/web/season',
                 '/api/web/search')


@api.after_request
def _visitor_words(response):
    if response.status_code != 200 or response.direct_passthrough or response.headers.get('Content-Encoding'):
        return response
    if request.path not in _WORDED_FEEDS or not response.is_json:
        return response
    try:
        from site_words import rewrite_json_strings
        response.set_data(json.dumps(rewrite_json_strings(response.get_json()), separators=(',', ':')))
    except Exception:
        logging.getLogger(__name__).debug('visitor words skipped for %s', request.path, exc_info=True)
    return response


def _reword_cards(payload):
    """Card HTML with visitor wording (site_words), done once per built or loaded feed."""
    from site_words import WORDING_VERSION
    if payload.get('words') == WORDING_VERSION:
        return payload
    try:
        from site_words import rewrite_html
        for card in payload.get('cards') or []:
            card['html'] = rewrite_html(card.get('html') or '')
        if payload.get('shareImage'):
            payload['shareImage'] = rewrite_html(payload['shareImage'])
        payload['words'] = WORDING_VERSION
    except Exception:
        logging.getLogger(__name__).debug('card wording skipped', exc_info=True)
    return payload

_GAME_ID = re.compile(r'\sdata-game-id="([^"]*)"')
_CARD_TIME_DATE = re.compile(r'class="game-time"[^>]*>\s*(\d{4}-\d{2}-\d{2})\b')
_GAME_DATES = {}  # sport -> (loaded at, {game id: 'YYYY-MM-DD'})
_EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿⭐️‍]+')


def _strip_emoji(text):
    return ' '.join(_EMOJI.sub('', text or '').split())

_CARD_OPEN = re.compile(r'<div class="game-card', re.I)
_DIV_TOKEN = re.compile(r'<(/?)div\b', re.I)
_STYLE_BLOCK = re.compile(r'<style\b[^>]*>[\s\S]*?</style>', re.I)
_ATTR = {
    'data-home': re.compile(r'\sdata-home="([^"]*)"'),
    'data-away': re.compile(r'\sdata-away="([^"]*)"'),
    'data-date': re.compile(r'\sdata-date="([^"]*)"'),
}


def _page_html(path, app=None, host=None, cookie=None):
    """A page of this site, rendered in-process for the visitor (their host and cookies, so the
    paywall matches). A background refresh passes the app, host and cookie it captured."""
    app = app or current_app
    # X-PL-Internal: card SSR must not share the visitor 2.5s ESPN page budget
    # (that budget was causing "page budget spent" storms and worker hangs).
    headers = {
        'Host': host or request.host,
        'X-Forwarded-Proto': 'https',
        'X-PL-Internal': '1',
    }
    cookie = cookie if host else request.headers.get('Cookie')
    if cookie:
        headers['Cookie'] = cookie
    # use_cookies=False: otherwise the test client drops the visitor's Cookie header and a member's page builds signed out.
    from paywall import page_source
    with page_source():
        resp = app.test_client(use_cookies=False).get(path, headers=headers)
    return resp.get_data(as_text=True) if resp.status_code == 200 else ''


def _balanced_end(html, start):
    depth = 0
    for tok in _DIV_TOKEN.finditer(html, start):
        depth += -1 if tok.group(1) else 1
        if depth == 0:
            gt = html.find('>', tok.end())
            return gt + 1 if gt >= 0 else -1
    return -1


def _cards(html):
    out = []
    pos = 0
    while True:
        m = _CARD_OPEN.search(html, pos)
        if not m:
            break
        end = _balanced_end(html, m.start())
        if end < 0:
            break
        block = html[m.start():end]
        head = block[:block.find('>') + 1]
        values = {}
        for key, rx in _ATTR.items():
            found = rx.search(head)
            values[key] = found.group(1) if found else ''
        out.append({
            'html': block,
            'home': values['data-home'],
            'away': values['data-away'],
            'date': values['data-date'],
        })
        pos = end
    return out


_SHARE_OPEN = re.compile(r'<div class="social-export-wrap', re.I)


def _share_image(html):
    """The page's Predictions Image block (image, download and fullscreen links), or ''."""
    m = _SHARE_OPEN.search(html)
    if not m:
        return ''
    end = _balanced_end(html, m.start())
    if end > 0:
        return html[m.start():end]
    stop = html.find('</main>', m.start())  # the UFC page leaves the block open
    if stop < 0:
        return ''
    block = html[m.start():stop]
    opened = len(re.findall(r'<div\b', block, re.I)) - len(re.findall(r'</div>', block, re.I))
    return block + '</div>' * max(opened, 0)


_SHARE_TOKEN_IN_HTML = re.compile(r'/share/predictions/([0-9a-f]{32})\.jpg', re.I)


def _ensure_live_share_image(payload, slug):
    """Re-mint the Predictions Image when the web_cards feed still names an expired token."""
    if not isinstance(payload, dict):
        return payload
    share = payload.get('shareImage') or ''
    m = _SHARE_TOKEN_IN_HTML.search(share)
    if not m:
        return payload
    token = m.group(1)
    try:
        from NHL77FINAL import _get_share_cache_entry
        if _get_share_cache_entry(token):
            return payload
    except Exception:
        return payload
    sport = _sport_for(slug) or (slug or 'Picks').replace('-picks', '').upper()
    parts = []
    for card in payload.get('cards') or []:
        if not isinstance(card, dict):
            continue
        date = (card.get('date') or '').strip()
        if date:
            parts.append(f'<div id="date-{date}"></div>')
        parts.append(card.get('html') or '')
    if not parts:
        return payload
    try:
        from isolate_checker_fixes import refresh_predictions_share_image
        rebuilt = refresh_predictions_share_image(
            '<main>' + ''.join(parts) + share + '</main>', sport
        )
        new_share = _share_image(rebuilt)
        if not new_share or new_share == share:
            return payload
        out = dict(payload)
        out['shareImage'] = new_share
        return out
    except Exception:
        logging.getLogger(__name__).exception(
            '[web cards] share image revive failed for %s', slug
        )
        return payload


_CARD_SHEETS = ('mlb-pick-cards.css',)


def _card_styles(html, app=None):
    """The card styles a page carries: its own style blocks, plus the shared card sheet when it links
    one instead (tennis links mlb-pick-cards.css rather than carrying the card styles in the page)."""
    keys = ('game-card', 'team-slot', 'picks-recent', 'social-export', 'social-image-link')
    sheets = []
    for name in _CARD_SHEETS:
        if re.search(r'<link[^>]+href="/static/css/' + re.escape(name), html):
            try:
                with open(f'{(app or current_app).static_folder}/css/{name}', encoding='utf-8') as sheet:
                    sheets.append(sheet.read())
            except OSError:
                pass
    return ''.join(sheets) + ''.join(b for b in _STYLE_BLOCK.findall(html) if any(k in b for k in keys))


def _sport_for(slug):
    from NHL77FINAL import _SEO_SLUG_TO_SPORT
    return _SEO_SLUG_TO_SPORT.get(slug.replace('-projections', '-picks'))


def _game_dates(sport):
    """Each game's date for a sport, read from the predictions table (cached 5 minutes)."""
    cached = _GAME_DATES.get(sport)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    dates = {}
    try:
        from NHL77FINAL import get_db_connection
        conn = get_db_connection()
        rows = conn.execute(
            'SELECT game_id, date(game_date) FROM predictions WHERE sport = ? GROUP BY game_id',
            (sport,),
        ).fetchall()
        conn.close()
        dates = {row[0]: row[1] for row in rows if row[1]}
    except Exception:
        dates = {}
    _GAME_DATES[sport] = (time.time(), dates)
    return dates


_SEARCH_TEAMS = {'at': 0, 'rows': []}  # [(sport, team name)] from recent and upcoming games
_PRO_SPORTS = ('NFL', 'NBA', 'MLB', 'NHL', 'WNBA', 'CFL')
_SPORT_ORDER = _PRO_SPORTS + ('SOCCER', 'NCAAF', 'NCAAB', 'NCAAW', 'TENNIS', 'UFC', 'GOLF')


def _search_teams():
    if _SEARCH_TEAMS['rows'] and time.time() - _SEARCH_TEAMS['at'] < 600:
        return _SEARCH_TEAMS['rows']
    rows = set()
    try:
        from NHL77FINAL import get_db_connection
        conn = get_db_connection()
        for sport, home, away in conn.execute(
            "SELECT sport, home_team_id, away_team_id FROM predictions "
            "WHERE date(game_date) >= date('now', '-150 days')"
        ).fetchall():
            sport = str(sport or '').upper()
            if sport not in _SPORT_ORDER:
                continue
            for name in (home, away):
                name = str(name or '').strip()
                if name and not name.isdigit():
                    rows.add((sport, name))
        conn.close()
    except Exception:
        pass
    _SEARCH_TEAMS.update(at=time.time(), rows=sorted(rows))
    return _SEARCH_TEAMS['rows']


@api.route('/api/web/search')
def api_search():
    """Public names across teams and players, independent of today's props slate."""
    q = ' '.join((request.args.get('q') or '').lower().split())
    if len(q) < 2:
        return jsonify({'results': []})
    from NHL77FINAL import SPORT_SEO_SLUGS as slug_of
    results = [
        {'label': f'{sport.title() if sport in ("SOCCER", "TENNIS", "GOLF") else sport} Predictions',
         'sport': sport, 'href': '/' + slug_of[sport].replace('-picks', '-predictions')}
        for sport in _SPORT_ORDER if sport in slug_of and q in sport.lower()
    ]
    if 'props' in q or 'player' in q:
        results.append({'label': 'Player Props', 'sport': 'PROPS', 'href': '/player-props'})

    from NHL77FINAL import get_db_connection
    from public_name_search import find_names, local_entities
    connection = get_db_connection()
    try:
        names = local_entities(connection)
    finally:
        connection.close()
    matched, partial = find_names(q, names, slug_of, _SPORT_ORDER)
    return jsonify({'results': (results + matched)[:20], 'partial': partial})


@api.route('/api/web/all-sports')
def api_all_sports():
    """The All Sports Picks board (same data and same members-only rule as the classic page)."""
    from ladder_picks.views import _viewer
    premium, logged_in, _admin = _viewer()
    if not premium:
        resp = jsonify({'locked': True, 'loggedIn': logged_in})
    else:
        from ladder_picks import board
        resp = jsonify({'locked': False, 'board': board.current()})
    resp.headers['Cache-Control'] = 'private, no-store'
    return resp


@api.route('/api/web/sports')
def api_sports():
    from NHL77FINAL import _web_sports_payload
    return jsonify({'sports': _web_sports_payload()})


@api.route('/api/web/soccer-leagues')
def api_soccer_leagues():
    """Read-only, date-separated league counts from the existing soccer board."""
    from soccer_nav_counts import counts
    today = datetime.now(_ET).date().isoformat()
    payload = None
    for tier in ('member', 'free', 'guest'):
        saved = _saved_cards('soccer-picks', tier)
        if not saved:
            continue
        payload = saved[1]
        break
    return jsonify(counts(payload, today))


_CARD_DAY = re.compile(r'\b(\d{4}-\d{2}-\d{2}) ·')
_TENNIS_EVENT = re.compile(r'Event</span>\s*<span class="sf-val">([^<]*)')


@api.route('/api/web/tennis-events')
def api_tennis_events():
    """Tennis tournaments still to play, from the tennis DB: matches today and in all, most matches first (the menu list)."""
    import sqlite3
    _tennis_sync_in_background()
    today = datetime.now(_ET).date().isoformat()
    try:
        con = sqlite3.connect(f'file:{_TENNIS_DB}?mode=ro', uri=True, timeout=2)
        rows = con.execute(
            "SELECT tournament, COUNT(*), SUM(substr(match_date, 1, 10) = ?) FROM matches "
            "WHERE status != 'final' AND substr(match_date, 1, 10) >= ? AND COALESCE(tournament, '') != '' "
            "GROUP BY tournament",
            (today, today),
        ).fetchall()
        con.close()
    except Exception:
        rows = []
    events = [{'name': name, 'today': int(n_today or 0), 'total': int(total)} for name, total, n_today in rows]
    return jsonify({'events': sorted(events, key=lambda r: (-r['total'], r['name']))})


@api.route('/api/web/live')
def api_live():
    from NHL77FINAL import _web_live_payload
    from live_scores import enrich_live_items
    items = enrich_live_items(_web_live_payload() + _tennis_live_items())
    response = jsonify({'items': items})
    response.headers['Cache-Control'] = 'no-store'
    return response

def _results_summary_cell(cell):
    if not isinstance(cell, dict):
        return {'status': 'unavailable', 'record': 'N/A', 'pct': None, 'reason': ''}
    return {
        'status': cell.get('status'),
        'record': cell.get('record'),
        'pct': cell.get('pct'),
        'reason': cell.get('reason') or '',
    }


def _results_summary_row(row):
    return {
        'sport': row['sport'],
        'name': row['name'],
        'results_url': row['results_url'],
        'through': row.get('through') or '',
        'available': bool(row.get('available')),
        'ml': {key: _results_summary_cell(val) for key, val in (row.get('ml') or {}).items()},
        'spread': {key: _results_summary_cell(val) for key, val in (row.get('spread') or {}).items()},
        'ou': {key: _results_summary_cell(val) for key, val in (row.get('ou') or {}).items()},
    }


@api.route('/api/web/results-summary')
def api_results_summary():
    """Homepage results rail.

    Default (no sport=): full HTML fragment — matches the live React board.
    ?sport=NBA: one-sport JSON (lighter; for a future picker client).
    """
    from all_sports_summary import MODELS, SPORTS, render_fragment, summary_rows, warm_missing
    sport = (request.args.get('sport') or '').upper()
    catalog = [{'key': key, 'name': name, 'results_url': f'/{key.lower()}-results'}
               for key, name in SPORTS]
    models = [{'id': key, 'label': label} for key, label in MODELS]
    if sport and sport in dict(SPORTS):
        rows = [row for row in summary_rows() if row['sport'] == sport]
        warm_missing(current_app._get_current_object(), rows)
        pending = any(not row['available'] and row['sport'] != 'GOLF' for row in rows)
        response = jsonify({
            'sports': catalog,
            'models': models,
            'rows': [_results_summary_row(row) for row in rows],
            'pending': pending,
        })
        response.headers['Cache-Control'] = 'public, max-age=30'
        return response
    # Live homepage + all-sports-results: full HTML board (same as before).
    rows = summary_rows()
    warm_missing(current_app._get_current_object(), rows)
    return jsonify({
        'html': render_fragment(rows),
        'sports': catalog,
        'models': models,
        'pending': any(not row['available'] and row['sport'] != 'GOLF' for row in rows),
    })


_TENNIS_DB = Path(__file__).resolve().parent / 'tennis' / 'database' / 'tennis.sqlite'
_TENNIS_SYNC = {'at': 0.0, 'running': False}
# Render: one worker — do not sync tennis every 10 minutes under load.
_TENNIS_SYNC_EVERY = int(os.environ.get('PL_TENNIS_SYNC_S', str(30 * 60)) or (30 * 60))
_PROCESS_START = time.time()
_BOOT_GRACE_S = float(os.environ.get('PL_BOOT_GRACE_S', '180') or 180)


def _in_boot_grace() -> bool:
    return (time.time() - _PROCESS_START) < _BOOT_GRACE_S


def _tennis_sync_in_background():
    """Keep the tennis DB's live / scheduled matches fresh (ESPN, ATP + WTA) for the live row."""
    if _in_boot_grace():
        return
    with _CARDS_LOCK:
        if _TENNIS_SYNC['running'] or time.time() - _TENNIS_SYNC['at'] < _TENNIS_SYNC_EVERY:
            return
        _TENNIS_SYNC.update(running=True, at=time.time())

    def run():
        try:
            from tennis.espn_sync import sync_tennis_db
            sync_tennis_db(force=True)
            slate = _tennis_slate_ids()
            if slate and slate != _TENNIS_SYNC.get('slate'):
                _TENNIS_SYNC['slate'] = slate
                _drop_saved_tennis_pages()
        except Exception as exc:
            logging.getLogger(__name__).info('[web] tennis sync failed: %s', exc)
        finally:
            _TENNIS_SYNC['running'] = False

    threading.Thread(target=run, name='web-tennis-sync', daemon=True).start()


def _tennis_slate_ids():
    import sqlite3
    try:
        con = sqlite3.connect(f'file:{_TENNIS_DB}?mode=ro', uri=True, timeout=2)
        ids = frozenset(r[0] for r in con.execute("SELECT match_id FROM matches WHERE status != 'final'"))
        con.close()
        return ids
    except Exception:
        return frozenset()


def _drop_saved_tennis_pages():
    """New matches in the tennis DB: the saved tennis pages and card feeds are rebuilt on the next visit."""
    with _CARDS_LOCK:
        for key in [k for k in _CARDS if k[0].startswith('tennis-')]:
            _CARDS.pop(key, None)
    for path in list(_CARDS_DIR.glob('tennis-*.json.gz')) + [
        _CARDS_DIR.parent / 'served_picks_TENNIS.html',
        _CARDS_DIR.parent / 'served_TENNIS_.html',
    ]:
        try:
            path.unlink()
        except OSError:
            pass


def _tennis_live_items():
    """Today's tennis matches that are live or still to play, from the tennis DB (its own database)."""
    import sqlite3
    _tennis_sync_in_background()
    today = datetime.now(_ET).date().isoformat()
    try:
        con = sqlite3.connect(f'file:{_TENNIS_DB}?mode=ro', uri=True, timeout=2)
        rows = con.execute(
            "SELECT m.player_a, m.player_b, m.status, p.win_prob_a, m.tournament FROM matches m "
            "LEFT JOIN predictions p ON p.match_id = m.match_id "
            "WHERE m.status IN ('live', 'scheduled') AND substr(m.match_date, 1, 10) = ? "
            "AND p.win_prob_a IS NOT NULL ORDER BY m.match_date",
            (today,),
        ).fetchall()
        con.close()
    except Exception as exc:
        logging.getLogger(__name__).debug('[web] tennis live rows failed: %s', exc)
        return []
    return [{
        'sport': 'Tennis', 'group': tournament or 'Tennis', 'away': a, 'home': b,
        'awayPct': round(float(prob) * 100, 1), 'homePct': round((1 - float(prob)) * 100, 1),
        'href': '/tennis-predictions', 'live': status == 'live',
    } for a, b, status, prob, tournament in rows]


_ET = ZoneInfo('America/New_York')
_LIVE_FOR = timedelta(hours=4)
# How long a game runs after its start time; scores can sync later than the final whistle.
_LIVE_FOR_SPORT = {
    'Soccer': timedelta(hours=2, minutes=5),
    'NBA': timedelta(hours=2, minutes=45),
    'WNBA': timedelta(hours=2, minutes=30),
    'NCAAB': timedelta(hours=2, minutes=30),
    'NCAAW': timedelta(hours=2, minutes=30),
    'NHL': timedelta(hours=3),
    'MLB': timedelta(hours=3, minutes=30),
}
_CARD_TIME = re.compile(r'\b(\d{1,2}):(\d{2})\s*([AP]M)\s*ET\b')


def _team_key(name):
    return re.sub(r'[^a-z0-9]', '', (name or '').lower())


def _card_start_times():
    """Today's start time (Eastern) per team, read from the saved sport cards; the games table has dates only."""
    today = datetime.now(_ET).date().isoformat()
    starts = {}
    try:
        for path in _CARDS_DIR.glob('*.v2.json.gz'):
            slug, tier = path.name.split('.')[:2]
            _saved_cards(slug, tier)
    except OSError:
        pass
    for (_slug, _tier), (_saved_at, payload) in list(_CARDS.items()):
        for card in payload.get('cards') or []:
            if card.get('date') != today:
                continue
            m = _CARD_TIME.search(card.get('html') or '')
            if not m:
                continue
            hour = int(m.group(1)) % 12 + (12 if m.group(3) == 'PM' else 0)
            start = datetime.fromisoformat(today).replace(hour=hour, minute=int(m.group(2)), tzinfo=_ET)
            for team in (card.get('home'), card.get('away')):
                if team:
                    starts.setdefault(_team_key(team), start)
    return starts


@api.route('/api/web/chrome')
def api_chrome():
    """The shared header and footer, rendered for this visitor (login state comes from the session)."""
    return jsonify({
        'header': render_template('partials/research_header.html'),
        'footer': render_template('partials/site_directory_footer.html'),
    })


@api.route('/api/web/me')
def api_me():
    """Membership for the React shell. Always JSON + no-store so a signed-in
    props page never paints a cached guest answer or a non-JSON error page."""
    try:
        from flask_login import current_user
        from auth_system import is_premium_user
        logged_in = bool(getattr(current_user, 'is_authenticated', False))
        premium = bool(is_premium_user()) if logged_in else False
    except Exception:
        current_app.logger.exception('api/web/me failed')
        logged_in, premium = False, False
    response = jsonify({'loggedIn': logged_in, 'premium': premium})
    response.headers['Cache-Control'] = 'private, no-store'
    response.vary.add('Cookie')
    return response


@api.route('/api/web/season')
def api_season():
    from NHL77FINAL import _get_sport_ml_units_banner
    items = [dict(item, label=_strip_emoji(item.get('label'))) for item in _get_sport_ml_units_banner()]
    return jsonify({'items': items})


_MEMBER_CHIP = re.compile(
    r'(<div class="line-chip (?:consensus-hist-chip|pl-vs-books-chip)[^"]*">\s*'
    r'<div class="line-chip-label">[^<]*</div>\s*<div class="line-chip-val">).*?(</div>)',
    re.S,
)
_CHIP_LOCK = '<span class="pl-chip-locked">Members only · <a href="/plans">Join Premium</a></span>'


def _lock_member_chips(html):
    """Free visitors get the Consensus Historical Record and PL vs Books boxes locked."""
    return _MEMBER_CHIP.sub(lambda m: m.group(1) + _CHIP_LOCK + m.group(2), html)


def _page_order(card):
    """The order the sport page shows: upcoming days earliest first, finished games last."""
    if 'FINAL' in card['html']:
        return (1, '')
    return (0, card['date'])


# Saved card feeds: one per sport and visitor kind (member / free signed in / signed out), so a
# free visitor is never handed a member's cards. A saved feed is served at once; one older than
# _CARDS_FRESH is refreshed in the background. Saved to disk too, so a restart starts warm.
# Keep FRESH aligned with the warmer so homepage traffic does not stampede full SSR rebuilds.
_WARM_EVERY = 10 * 60
_CARDS_FRESH = _WARM_EVERY
_CARDS_KEEP = 7 * 24 * 3600
_CARDS = {}  # (slug, tier) -> (saved at, payload)
_CARDS_BUILDING = set()
_CARDS_LOCK = threading.Lock()
# Full classic-page card builds are heavy; only one runs at a time so visitors stay responsive.
_CARD_BUILD_SLOTS = threading.Semaphore(1)
try:
    from served_cache import cache_dir as _cache_dir
    _CARDS_DIR = _cache_dir() / 'web_cards'  # the persistent disk on the live server, so a deploy starts warm
except Exception:
    _CARDS_DIR = Path(__file__).resolve().parent / '.cache' / 'web_cards'
_WARM_HOST = 'predictionlab.io'


def _build_cards(slug, tier, app, host, cookie, offset=0, limit=None):
    sport = _sport_for(slug)
    path = '/' + slug.replace('-projections', '-picks')
    if sport == 'SOCCER':
        path += '?region=all'  # the soccer page redirects to its region view without this
    # Member feeds must be full paid HTML. Background rebuilds often have no
    # visitor cookie; without this the free/locked slate is stored as "member"
    # and premium NHL/NCAAB pages look empty or missing lines.
    from contextlib import ExitStack
    from unittest.mock import patch
    with ExitStack() as stack:
        if tier == 'member':
            stack.enter_context(patch('auth_system.is_premium_user', return_value=True))
            stack.enter_context(patch('NHL77FINAL.is_premium_user', return_value=True))
        if limit is None:
            html = _page_html(path, app, host, cookie)
            page = None
        else:
            from card_pagination import window
            with window(offset, limit) as page:
                html = _page_html(path, app, host, cookie)
    if not html or 'upstream data/model dependency failed' in html.lower():
        raise RuntimeError(f'Original sport renderer returned no page for {slug}')
    cards = _cards(html)
    if tier != 'member':
        for card in cards:
            card['html'] = _lock_member_chips(card['html'])
    dates = _game_dates(sport) if any(not card['date'] for card in cards) else {}
    for card in cards:
        if not card['date']:
            found = _GAME_ID.search(card['html'])
            card['date'] = dates.get(found.group(1), '') if found else ''
        if not card['date']:
            shown = _CARD_TIME_DATE.search(card['html'])
            card['date'] = shown.group(1) if shown else ''
    cards.sort(key=_page_order)
    total = len(cards)
    next_offset = None
    if limit is not None:
        if page['total'] is None:  # Existing specialized renderers retain their original path.
            cards = cards[offset:offset+limit]
            next_offset = offset+len(cards) if offset+len(cards) < total else None
        else:
            total, next_offset = page['total'], page['nextOffset']
    return _reword_cards({'cards': cards, 'total': total, 'css': _card_styles(html, app),
                          'shareImage': _share_image(html), 'offset': offset,
                          'nextOffset': next_offset, 'hasMore': next_offset is not None})


def _cards_file(slug, tier):
    return _CARDS_DIR / f'{slug}.{tier}.v2.json.gz'


def _read_cards_file(path):
    raw = json.loads(gzip.decompress(path.read_bytes()))
    # Older writers wrapped the feed; accept both shapes.
    if isinstance(raw, dict) and 'cards' not in raw and isinstance(raw.get('payload'), dict):
        raw = raw['payload']
    return _reword_cards(raw)


def _usable_cards(payload):
    return bool(payload and (payload.get('cards') or payload.get('picks')))


def _saved_cards(slug, tier):
    saved = _CARDS.get((slug, tier))
    if saved and _usable_cards(saved[1]):
        return saved
    if saved and not _usable_cards(saved[1]):
        _CARDS.pop((slug, tier), None)
    try:
        candidates = [
            _cards_file(slug, tier),
            # Homepage only needs the first page; older warmers saved that slice alone.
            _CARDS_DIR / f'{slug}.{tier}-page-0-5.v2.json.gz',
        ]
        for path in candidates:
            if not path.is_file():
                continue
            payload = _read_cards_file(path)
            if not _usable_cards(payload):
                continue
            # Stamp "now" so a restart still serves multi-day-old disk feeds immediately
            # (stale-while-revalidate); the warmer owns rebuilds.
            saved = (time.time(), payload)
            _CARDS[(slug, tier)] = saved
            return saved
    except (OSError, ValueError, TypeError):
        return None
    return None


def _save_cards(slug, tier, payload, allow_empty=False):
    from slate_freshness import current_payload
    payload = current_payload(payload)
    if not payload['cards'] and not allow_empty:
        return  # an empty page (a failed build) never replaces a good saved feed
    _CARDS[(slug, tier)] = (time.time(), payload)
    try:
        _CARDS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _cards_file(slug, tier).with_suffix('.tmp')
        tmp.write_bytes(gzip.compress(json.dumps(payload).encode(), compresslevel=5))
        tmp.replace(_cards_file(slug, tier))
    except OSError:
        pass


def _refresh_cards(slug, tier, app, host, cookie):
    with _CARD_BUILD_SLOTS:
        try:
            with app.app_context():
                payload = _build_cards(slug, tier, app, host, cookie)
                _save_cards(slug, tier, payload)
                if tier == 'guest':
                    _save_cards(slug, 'free', payload, allow_empty=True)
        except Exception:
            logging.getLogger(__name__).exception('[web cards] refresh failed for %s', slug)
        finally:
            with _CARDS_LOCK:
                _CARDS_BUILDING.discard((slug, tier))


def _locked_from_member(payload):
    """Member feed with Consensus / PL-vs-Books chips locked for guest/free visitors."""
    import copy
    out = copy.deepcopy(payload)
    for card in out.get('cards') or []:
        card['html'] = _lock_member_chips(card.get('html') or '')
    return out


def _seed_cards_from_disk():
    """Load every saved feed into memory so the first visitor does not pay gzip I/O."""
    try:
        for path in _CARDS_DIR.glob('*.v2.json.gz'):
            parts = path.name.split('.')
            if len(parts) >= 3 and parts[1] in ('guest', 'free', 'member'):
                _saved_cards(parts[0], parts[1])
        # Fill missing guest/free from member so the homepage never waits on a cold SSR.
        for path in _CARDS_DIR.glob('*.member.v2.json.gz'):
            slug = path.name.split('.')[0]
            if _saved_cards(slug, 'guest'):
                continue
            member = _saved_cards(slug, 'member')
            if not member:
                continue
            locked = _locked_from_member(member[1])
            _save_cards(slug, 'guest', locked, allow_empty=True)
            _save_cards(slug, 'free', locked, allow_empty=True)
    except OSError:
        pass


def _warm_cards_loop(app):
    """Keep every sport's signed-out feed built, one sport at a time, so no visitor waits on a build."""
    from NHL77FINAL import SPORT_SEO_SLUGS
    time.sleep(2)
    while True:
        for sport in _SPORT_ORDER:
            slug = SPORT_SEO_SLUGS.get(sport, '').replace('-projections', '-picks')
            if not slug or not _sport_for(slug):
                continue
            saved = _saved_cards(slug, 'guest')
            if saved and time.time() - saved[0] < _WARM_EVERY:
                # Free visitors share the guest feed (chips already locked).
                if not _saved_cards(slug, 'free'):
                    _save_cards(slug, 'free', saved[1], allow_empty=True)
                continue
            with _CARDS_LOCK:
                if (slug, 'guest') in _CARDS_BUILDING:
                    continue
                _CARDS_BUILDING.add((slug, 'guest'))
            _refresh_cards(slug, 'guest', app, _WARM_HOST, None)
            guest = _saved_cards(slug, 'guest')
            if guest:
                _save_cards(slug, 'free', guest[1], allow_empty=True)
        time.sleep(_WARM_EVERY)


_WARM_STARTED = False


def start_cards_warmer(app):
    """Seed disk feeds into memory on boot. Full SSR warm loop only if PL_WEB_WARM=1."""
    global _WARM_STARTED
    if _WARM_STARTED:
        return
    _WARM_STARTED = True
    _seed_cards_from_disk()
    # Default off: background classic-page builds starve the left sports nav.
    if os.environ.get('PL_WEB_WARM', '0') != '1':
        return
    threading.Thread(target=_warm_cards_loop, args=(app,), daemon=True, name='web-cards-warm').start()


_CARD_ERRORS = {}


def _cards_for_tier(slug, tier):
    """Guest/free share the locked feed; never hand a member feed to a free visitor."""
    saved = _saved_cards(slug, tier)
    if saved and _usable_cards(saved[1]):
        return saved
    if tier in ('guest', 'free'):
        saved = _saved_cards(slug, 'free' if tier == 'guest' else 'guest')
        if saved and _usable_cards(saved[1]):
            return saved
        member = _saved_cards(slug, 'member')
        if member and _usable_cards(member[1]):
            locked = _locked_from_member(member[1])
            _save_cards(slug, 'guest', locked, allow_empty=True)
            _save_cards(slug, 'free', locked, allow_empty=True)
            return (time.time(), locked)
    return None


def _start_full_cards(slug, tier, app):
    """One full build per sport and tier; every page of cards is cut from it."""
    # Right after deploy, prefer answering the homepage over starting N classic SSRs.
    if _in_boot_grace():
        return False
    with _CARDS_LOCK:
        if (slug, tier) in _CARDS_BUILDING:
            return True
        _CARDS_BUILDING.add((slug, tier))
    host, cookie = request.host, request.headers.get('Cookie')

    def build():
        with _CARD_BUILD_SLOTS:
            try:
                with app.app_context():
                    payload = _build_cards(slug, tier, app, host, cookie)
                    _save_cards(slug, tier, payload, allow_empty=not _cards_for_tier(slug, tier))
                    if tier == 'guest':
                        _save_cards(slug, 'free', payload, allow_empty=True)
                _CARD_ERRORS.pop((slug, tier), None)
            except Exception:
                _CARD_ERRORS[(slug, tier)] = time.time()
                logging.getLogger(__name__).exception('[web cards] build failed for %s', slug)
            finally:
                with _CARDS_LOCK:
                    _CARDS_BUILDING.discard((slug, tier))

    threading.Thread(target=build, daemon=True, name=f'cards-{slug}-{tier}').start()
    return True


def _card_page(slug, tier, app, offset, limit):
    from slate_freshness import current_payload
    saved = _cards_for_tier(slug, tier)
    if saved and time.time()-saved[0] < _CARDS_KEEP:
        payload = _ensure_live_share_image(current_payload(saved[1]), slug)
        if payload is not saved[1] and payload.get('shareImage') != (saved[1] or {}).get('shareImage'):
            _save_cards(slug, tier, payload, allow_empty=True)
        cards = payload['cards'][offset:offset+limit]
        total = len(payload['cards'])
        next_offset = offset+len(cards) if offset+len(cards) < total else None
        # Never flag refreshing when cards are already on hand — the client skips
        # caching refreshing payloads and the homepage would refetch every sport.
        return {**payload, 'cards': cards, 'total': total, 'offset': offset,
                'nextOffset': next_offset, 'hasMore': next_offset is not None,
                'refreshing': False}, 200
    if time.time()-_CARD_ERRORS.get((slug, tier), 0) < 15:
        return {'error': 'Games could not be loaded. Please retry.', 'cards': []}, 503
    # One cold build at a time (semaphore inside _start_full_cards). Homepage stays
    # serial so this does not stampede; sport pages poll building:true.
    _start_full_cards(slug, tier, app)
    return {'cards': [], 'total': None, 'css': '', 'shareImage': '', 'offset': offset,
            'nextOffset': offset, 'hasMore': True, 'building': True,
            'refreshing': True, 'retryAfter': 1}, 202


@api.route('/api/web/sports/<slug>/cards')
def api_cards(slug):
    if not _sport_for(slug):
        return jsonify({'error': 'unknown sport'}), 404
    from auth_system import is_premium_user
    from flask_login import current_user
    if is_premium_user():
        tier = 'member'
    else:
        tier = 'free' if current_user.is_authenticated else 'guest'
    app = current_app._get_current_object()
    if 'limit' in request.args:
        try:
            limit = int(request.args['limit'])
            offset = int(request.args.get('offset', '0'))
            if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify(error='Choose a valid game page.'), 400
        payload, status = _card_page(slug, tier, app, offset, limit)
        if tier == 'member' and _sport_for(slug) in {'NHL', 'NFL', 'NCAAB', 'NCAAW', 'NBA'}:
            from market_card_refresh import refresh as refresh_market_cards
            from NHL77FINAL import DATABASE
            payload = refresh_market_cards(payload, _sport_for(slug), DATABASE)
            if _sport_for(slug) == 'NHL':
                from pl_line_repair import refresh as refresh_pl_cards
                payload = refresh_pl_cards(payload, DATABASE)
        response = jsonify(payload)
        response.status_code = status
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Cookie')
        return response
    saved = _cards_for_tier(slug, tier)
    age = time.time() - saved[0] if saved else None
    if saved and age < _CARDS_KEEP:
        payload = saved[1]
    else:
        with _CARD_BUILD_SLOTS:
            payload = _build_cards(slug, tier, app, request.host, request.headers.get('Cookie'))
        _save_cards(slug, tier, payload)
        if tier == 'guest':
            _save_cards(slug, 'free', payload, allow_empty=True)
    from slate_freshness import current_payload
    payload = _ensure_live_share_image(current_payload(payload), slug)
    if payload['suppressed_stale_predictions']:
        with _CARDS_LOCK:
            start = (slug, tier) not in _CARDS_BUILDING
            if start:
                _CARDS_BUILDING.add((slug, tier))
        if start:
            threading.Thread(target=_refresh_cards,
                             args=(slug, tier, app, request.host, request.headers.get('Cookie')),
                             daemon=True).start()
    payload['refreshing'] = (slug, tier) in _CARDS_BUILDING
    if tier == 'member' and _sport_for(slug) in {'NHL', 'NFL', 'NCAAB', 'NCAAW', 'NBA'}:
        from market_card_refresh import refresh as refresh_market_cards
        from NHL77FINAL import DATABASE
        payload = refresh_market_cards(payload, _sport_for(slug), DATABASE)
        if _sport_for(slug) == 'NHL':
            from pl_line_repair import refresh as refresh_pl_cards
            payload = refresh_pl_cards(payload, DATABASE)
    limit = request.args.get('limit', type=int)
    if limit:
        payload = dict(payload, cards=payload['cards'][:limit])
    return jsonify(payload)


@api.route('/api/web/sports/<slug>/results')
def api_results(slug):
    sport = _sport_for(slug)
    if not sport:
        return jsonify({'error': 'unknown sport'}), 404
    from NHL77FINAL import _web_results_html
    return jsonify({'html': _web_results_html(sport)})


_GOLF_PICKER_TOKEN = re.compile(
    r'<optgroup label="([^"]*)">|'
    r'<option value="(\d+)"( selected)?>([^<]*?) — (\d{4}-\d{2}-\d{2}) · ([^<]*?)</option>'
)
_GOLF_PICK = re.compile(
    r'<div class="gpm-card" data-market="([^"]*)"><h3>[^<]*</h3><div class="sub">([^<]*)</div><table>(.*?)</table></div>',
    re.S,
)
_GOLF_PICK_ROW = re.compile(r'<tr><td>([^<]*)</td><td>([^<]*)</td></tr>')
_GOLF_FIELD_ROW = re.compile(
    r'<tr><td class="golf-rank">#(\d+)</td>'
    r'<td class="golf-player">(?:<img src="([^"]*)"[^>]*>)?<strong>([^<]*)</strong></td>'
    r'<td class="golf-winpct"><span class="golf-mpct">([^<]*)</span></td>'
    r'<td><span class="golf-mpct">([^<]*)</span></td>'
    r'<td><span class="golf-mpct">([^<]*)</span></td></tr>'
)


def _golf_payload(event):
    """The golf page's tournaments, our picks and the field, read from the same page the site shows."""
    html = _page_html('/golf-picks' + (f'?event={event}' if event else ''))
    picker = re.search(r'<select id="golf-event".*?</select>', html, re.S)
    events = []
    group = ''
    for token in _GOLF_PICKER_TOKEN.finditer(picker.group(0) if picker else ''):
        if token.group(1) is not None:
            group = unescape(token.group(1))
            continue
        events.append({
            'id': token.group(2),
            'name': unescape(token.group(4)),
            'date': token.group(5),
            'status': token.group(6),
            'group': group,
            'selected': bool(token.group(3)),
        })
    picks = [
        {
            'market': unescape(market),
            'note': unescape(note),
            'rows': [{'name': unescape(name), 'chance': chance} for name, chance in _GOLF_PICK_ROW.findall(table)],
        }
        for market, note, table in _GOLF_PICK.findall(html)
    ]
    field = [
        {'rank': rank, 'photo': photo or '', 'player': unescape(name), 'win': win, 'top10': top10, 'top20': top20}
        for rank, photo, name, win, top10, top20 in _GOLF_FIELD_ROW.findall(html)
    ]
    selected = next((item['id'] for item in events if item['selected']), '')
    return {'events': events, 'selected': selected, 'picks': picks, 'field': field}


@api.route('/api/web/golf')
def api_golf():
    """Golf tournaments: the picker, our picks and the field for the chosen tournament (default: this week's)."""
    event = request.args.get('event', '')
    if event and not event.isdigit():
        return jsonify({'error': 'unknown tournament'}), 400
    return jsonify(_golf_payload(event))


@api.after_request
def _restore_college_rating_fields(response):
    if (response.status_code != 200 or not response.is_json
            or not re.fullmatch(r'/api/web/sports/ncaa[bw]-picks/cards', request.path)):
        return response
    from auth_system import is_premium_user
    if not is_premium_user():
        return response
    try:
        import NHL77FINAL as site
        from published_rating_fields import refresh, model_values
        def ratings(sport, home, away, game_date):
            predictor = site.V2_PREDICTORS.get(site._v2_model_sport(sport))
            return model_values(predictor, home, away) if predictor else {}
        payload = refresh(response.get_json(), _sport_for(request.path.split('/')[-2]),
                          site.DATABASE, ratings)
        response.set_data(json.dumps(payload))
    except Exception:
        current_app.logger.exception('College rating field recovery unavailable')
    return response

