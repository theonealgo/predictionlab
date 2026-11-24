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
    # Default: full slate. ?limit=N still caps per prop type for progressive loads.
    raw_limit = (request.args.get('limit') or 'all').strip().lower()
    if raw_limit == 'all':
        item_limit = None
    else:
        try:
            item_limit = max(1, min(500, int(raw_limit)))
        except ValueError:
            item_limit = None
    if day < datetime.now(ZoneInfo('America/New_York')).date().isoformat():
        from props_navigation import game_catalog, bind_games
        data = historical_catalog(site.DATABASE, day, selected, limit=item_limit)
        catalog = game_catalog(site.DATABASE, day)
        for section in data['sections']:
            bind_games(section, catalog)
        response = jsonify(data)
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Cookie')
        return response
    sections = []
    from props_navigation import game_catalog, bind_games
    from props_service import entries as prop_entries, ensure_selected, building as prop_building
    # A visit only reads the saved props. Load=1 publishes the one sport they chose.
    if not current_app.testing and request.args.get('load') == '1' and selected:
        ensure_selected(site, selected, day)
    entries = prop_entries(site, day)
    catalog = game_catalog(site.DATABASE, day)
    counts = []
    for league in LEAGUES:
        entry = entries.get(league)
        building = prop_building(site, league, day)
        section = preview(league, entry, limit=item_limit, day=day)
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


@api.route('/api/web/props-streaks')
def api_props_streaks():
    denied = _props_hub_access()
    if denied is not None:
        return denied
    from NHL77FINAL import _query_prop_streaks
    league = (request.args.get('league') or '').strip().upper()
    leagues = [league] if league else ['NBA', 'NHL', 'NFL', 'MLB', 'WNBA', 'NCAAB', 'NCAAW', 'NCAAF']
    items = []
    for name in leagues:
        try:
            data = _query_prop_streaks(name, limit=20 if league else 6)
        except Exception:
            current_app.logger.exception('Prop streaks unavailable for %s', name)
            continue
        for item in data.get('items') or []:
            item['league'] = name
            items.append(item)
    response = jsonify(league=league or None, items=items)
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
    league = (request.args.get('league') or '').upper() or None
    day = request.args.get('date') or None
    # Read saved results only. Settling on this request broke every sport when
    # DATABASE was a relative path (result_dates) and when ESPN SSL stalled.
    try:
        data = read_results(DATABASE, league, day, window=request.args.get('window') or 'night')
    except ValueError:
        return jsonify(error='Choose a supported sport and a valid YYYY-MM-DD date.'), 400
    except Exception:
        current_app.logger.exception('Read-only props results unavailable')
        return jsonify(error='Recorded prop results could not be loaded. Please retry.'), 503
    # items=0 → model tables only. The per-player card list is huge; the React page
    # loads it later so the worker is not holding that JSON on every props visit.
    if str(request.args.get('items') or '1').strip() in ('0', 'false', 'no'):
        data = dict(data)
        data['items'] = []
        data['items_deferred'] = True
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


# Face chips are only these two. Books/Market spread and total stay in the details table.
_FACE_LINE_ONLY = re.compile(
    r'<div class="line-chip">\s*'
    r'<div class="line-chip-label">\s*(?:Books|Market)\s+'
    r'(?:spread|total|run line|puck line)\s*</div>'
    r'[\s\S]*?</div>\s*</div>\s*',
    re.I,
)


def _face_chips_only(html):
    if not html or 'line-chip-label' not in html:
        return html
    return _FACE_LINE_ONLY.sub('', html)


def _reword_cards(payload):
    """Card HTML with visitor wording (site_words), done once per built or loaded feed."""
    for card in payload.get('cards') or []:
        if card.get('html'):
            card['html'] = _face_chips_only(card['html'])
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


def _share_images_enabled() -> bool:
    # Off by default on constrained hosts — reminting burns CPU/RAM; React no longer shows the block.
    return (os.environ.get('PL_SHARE_IMAGES', '0') or '0') == '1'


def _ensure_live_share_image(payload, slug):
    """Re-mint the Predictions Image when the web_cards feed still names an expired token."""
    if not isinstance(payload, dict):
        return payload
    if not _share_images_enabled():
        if payload.get('shareImage'):
            out = dict(payload)
            out['shareImage'] = ''
            return out
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
        return jsonify({'results': [], 'partial': False})
    try:
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
        out = (results + matched)[:20]
        # Matches already answer the box — never show "directory unavailable".
        return jsonify({'results': out, 'partial': bool(partial) and not out})
    except Exception:
        logging.getLogger(__name__).exception('[search] failed for %r', q)
        return jsonify({'results': [], 'partial': True})


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
_BOOT_STAMP = Path(os.environ.get('PL_BOOT_STAMP', '/data/.cache/gunicorn_boot_at'))


def _boot_started_at() -> float:
    """Container/master start — not worker import (recycles must not re-arm grace)."""
    try:
        return float(_BOOT_STAMP.read_text().strip())
    except (OSError, ValueError, TypeError):
        return _PROCESS_START


def _in_boot_grace() -> bool:
    if _BOOT_GRACE_S <= 0:
        return False
    return (time.time() - _boot_started_at()) < _BOOT_GRACE_S


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
            previous = _TENNIS_SYNC.get('slate')
            if slate:
                _TENNIS_SYNC['slate'] = slate
            # A fresh process has no remembered slate. Drop only after a real change.
            if slate and previous and slate != previous:
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


def _stamp_tennis_events(cards):
    """Put data-event on tennis cards so the React filter works when the free face strips Event."""
    import html as _html
    import sqlite3
    if not cards:
        return cards
    try:
        con = sqlite3.connect(f'file:{_TENNIS_DB}?mode=ro', uri=True, timeout=2)
        rows = con.execute(
            "SELECT player_a, player_b, tournament FROM matches "
            "WHERE status != 'final' AND COALESCE(tournament, '') != ''"
        ).fetchall()
        con.close()
    except Exception:
        return cards
    by_pair = {}
    for a, b, tourney in rows:
        if not tourney:
            continue
        by_pair[(a or '').strip().lower(), (b or '').strip().lower()] = tourney
        by_pair[(b or '').strip().lower(), (a or '').strip().lower()] = tourney
    for card in cards:
        block = card.get('html') or ''
        if 'data-event="' in block:
            continue
        away = (card.get('away') or '').strip().lower()
        home = (card.get('home') or '').strip().lower()
        tourney = by_pair.get((away, home))
        if not tourney:
            found = _TENNIS_EVENT.search(block)
            tourney = found.group(1).strip() if found else ''
        if not tourney:
            continue
        safe = _html.escape(tourney, quote=True)
        card['html'] = block.replace('<div ', f'<div data-event="{safe}" ', 1)
    return cards


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
    # The homepage no longer asks for this. The old query scanned every graded
    # game on the request and held the only web worker, so the sports list
    # stayed on Loading. Return at once. Do not run that scan here.
    market = (request.args.get('market') or 'moneyline').strip().lower()
    if market in ('su', 'straight', 'straight_up', 'straight-up'):
        market = 'moneyline'
    elif market in ('ou', 'over_under', 'over-under', 'totals'):
        market = 'total'
    elif market not in ('moneyline', 'spread', 'total'):
        market = 'moneyline'
    response = jsonify({'items': [], 'market': market})
    response.headers['Cache-Control'] = 'private, max-age=60'
    return response


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
    # Card extraction needs classic SSR HTML. Plain /<sport>-picks is the React
    # shell (no data-pick-card), which produced empty or truncated web_cards feeds
    # and hid Load more on large slates (NCAAB).
    path = '/classic/' + slug.replace('-projections', '-picks')
    if sport == 'SOCCER':
        path += '?region=all'  # the soccer page redirects to its region view without this
    # Member feeds must be full paid HTML. Background rebuilds often have no
    # visitor cookie; without this the free/locked slate is stored as "member"
    # and premium NHL/NCAAB pages look empty or missing lines.
    from contextlib import ExitStack
    from unittest.mock import patch
    with ExitStack() as stack:
        if tier == 'member':
            # paywall imports is_premium_user by name at load time — patch that
            # binding too or member feeds bake "Members only · Join Premium".
            stack.enter_context(patch('auth_system.is_premium_user', return_value=True))
            stack.enter_context(patch('NHL77FINAL.is_premium_user', return_value=True))
            try:
                stack.enter_context(patch('paywall.is_premium_user', return_value=True))
            except Exception:
                pass
        if limit is None:
            html = _page_html(path, app, host, cookie)
            page = None
        else:
            from card_pagination import window
            with window(offset, limit) as page:
                html = _page_html(path, app, host, cookie)
    if not html or 'upstream data/model dependency failed' in html.lower():
        raise RuntimeError(f'Original sport renderer returned no page for {slug}')
    # Stamp Consensus Historical Record on classic HTML before extracting React
    # card feeds. NBA past30 lives on the results table — without this the
    # web_cards JSON keeps baked "Unanimous: 0-0" forever.
    if sport in ('NCAAB', 'NCAAW', 'NBA', 'WNBA', 'NHL', 'NFL', 'NCAAF', 'CFL') and 'data-pick-card' in html:
        try:
            from card_face import _add_results_chips
            html = _add_results_chips(html, sport) or html
        except Exception:
            try:
                from team_results_charts import (
                    _inject_consensus_hist_chips,
                    _NCAAF_CONSENSUS_MODELS,
                    _WNBA_CONSENSUS_MODELS,
                )
                models = (
                    _WNBA_CONSENSUS_MODELS if sport == 'WNBA' else _NCAAF_CONSENSUS_MODELS
                )
                html = _inject_consensus_hist_chips(
                    html, sport=sport, models=models
                ) or html
            except Exception:
                pass
    elif sport == 'TENNIS' and 'data-pick-card' in html:
        # Same finals-backed history as tennis_live — without this, web_cards
        # rebuilds keep baking Unanimous: 0-0 when served_TENNIS_.html is empty.
        try:
            from tennis_live import _inject_tennis_consensus_hist
            html = _inject_tennis_consensus_hist(html) or html
        except Exception:
            pass
    # Free SSR strips the face chips; guest/free feeds lock the member cards.
    if (
        sport in ('NCAAB', 'NCAAW', 'NBA', 'WNBA')
        and tier in ('guest', 'free')
        and 'Consensus Historical Record' not in html
    ):
        member = _saved_cards(slug, 'member')
        if member and _usable_cards(member[1]):
            return _locked_from_member(member[1])
    cards = _cards(html)
    if tier != 'member':
        for card in cards:
            card['html'] = _lock_member_chips(card['html'])
    if sport == 'TENNIS':
        cards = _stamp_tennis_events(cards)
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
                          'shareImage': _share_image(html) if _share_images_enabled() else '',
                          'offset': offset,
                          'nextOffset': next_offset, 'hasMore': next_offset is not None})

def _cards_file(slug, tier):
    return _CARDS_DIR / f'{slug}.{tier}.v2.json.gz'


def _cards_ok_file(slug):
    """Last known-good unlocked member feed (safeguard against poisoned rebuilds)."""
    return _CARDS_DIR / f'{slug}.member.ok.v2.json.gz'


def _cards_head_file(slug, tier):
    """First-10 slice for instant paint while a full rebuild runs."""
    return _CARDS_DIR / f'{slug}.{tier}.head10.v2.json.gz'


def _read_cards_file(path):
    raw = json.loads(gzip.decompress(path.read_bytes()))
    # Older writers wrapped the feed; accept both shapes.
    if isinstance(raw, dict) and 'cards' not in raw and isinstance(raw.get('payload'), dict):
        raw = raw['payload']
    return _reword_cards(raw)


def _usable_cards(payload):
    if not payload or not isinstance(payload, dict):
        return False
    if payload.get('cards') or payload.get('picks'):
        return True
    # Off-season / empty slate after a finished build — do not rebuild forever.
    return 'cards' in payload and payload.get('total') == 0


def _saved_cards(slug, tier):
    saved = _CARDS.get((slug, tier))
    path = _cards_file(slug, tier)
    # Disk rewritten after a fix must beat the in-memory feed without a restart.
    if saved and _usable_cards(saved[1]):
        try:
            if path.is_file() and path.stat().st_mtime > (saved[0] + 0.5):
                _CARDS.pop((slug, tier), None)
            else:
                return saved
        except OSError:
            return saved
    if saved and not _usable_cards(saved[1]):
        _CARDS.pop((slug, tier), None)
    try:
        candidates = [
            path,
            # Homepage only needs the first page; older warmers saved that slice alone.
            _CARDS_DIR / f'{slug}.{tier}-page-0-5.v2.json.gz',
        ]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            payload = _read_cards_file(candidate)
            if not _usable_cards(payload):
                continue
            # Stamp "now" so a restart still serves multi-day-old disk feeds immediately
            # (stale-while-revalidate); the warmer owns rebuilds.
            # Use max(now, mtime) so a just-written file is not treated as newer than memory.
            try:
                stamp = max(time.time(), candidate.stat().st_mtime)
            except OSError:
                stamp = time.time()
            saved = (stamp, payload)
            _CARDS[(slug, tier)] = saved
            return saved
    except (OSError, ValueError, TypeError):
        return None
    return None


def _consensus_chip_val(html: str) -> str:
    m = re.search(
        r'consensus-hist[\s\S]{0,220}?line-chip-val[^>]*>([^<]*)',
        html or '',
    )
    return (m.group(1).strip() if m else '')


def _consensus_chip_is_bare_dash(val: str) -> bool:
    v = (val or '').strip()
    return v in (
        'Consensus Record: —',
        'Consensus Record: –',
        'Consensus Record: -',
        '—',
        '–',
        '-',
    )


def _consensus_chip_needs_restamp(val: str, sport: str) -> bool:
    """Bare dashes / empty 0-0 stubs, including baked NCAAB blank-model 0-0 chips."""
    v = (val or '').strip()
    if not v:
        return False
    if sport in ('NCAAB', 'NCAAW') and _consensus_chip_is_bare_dash(v):
        return True
    # NCAAB/NCAAW blank G2/TD invent "0-0 (—) — Season · …" — restamp those.
    if sport in ('NCAAB', 'NCAAW') and (
        '0-0 (—)' in v or '0-0 (–)' in v or '0-0 (-)' in v
    ):
        return True
    # Already patterned with a real W-L — leave alone.
    if 'Season ·' in v or ' · ' in v:
        return False
    if ': 0-0' in v or 'Unanimous: 0-0' in v or v.endswith('0-0'):
        return True
    return False


def _restamp_consensus_hist_on_payload(payload, sport):
    """Re-run Consensus Historical inject on each card HTML in a web_cards payload."""
    if not payload or not isinstance(payload, dict) or not sport:
        return payload
    cards = payload.get('cards') or []
    if not cards:
        return payload
    if not any(
        _consensus_chip_needs_restamp(_consensus_chip_val(c.get('html') or ''), sport)
        for c in cards
    ):
        return payload
    tennis_inject = None
    models = None
    try:
        if sport == 'TENNIS':
            from tennis_live import _inject_tennis_consensus_hist as tennis_inject
        else:
            from team_results_charts import (
                _inject_consensus_hist_chips,
                _inject_nhl_consensus_hist_chips,
                _NCAAF_CONSENSUS_MODELS,
                _WNBA_CONSENSUS_MODELS,
            )
            models = _WNBA_CONSENSUS_MODELS if sport == 'WNBA' else _NCAAF_CONSENSUS_MODELS
    except Exception:
        return payload
    out_cards = []
    changed = False
    for card in cards:
        block = card.get('html') or ''
        before_val = _consensus_chip_val(block)
        if not _consensus_chip_needs_restamp(before_val, sport):
            out_cards.append(card)
            continue
        try:
            if sport == 'TENNIS':
                fixed = tennis_inject(block) or block
            elif sport == 'NHL':
                fixed = _inject_nhl_consensus_hist_chips(block) or block
            else:
                fixed = _inject_consensus_hist_chips(block, sport=sport, models=models) or block
        except Exception:
            fixed = block
        after_val = _consensus_chip_val(fixed)
        # Never replace a patterned chip with a bare dash (stale process / failed inject).
        if _consensus_chip_is_bare_dash(after_val) and not _consensus_chip_is_bare_dash(before_val):
            fixed = block
        if fixed != block:
            changed = True
        out_cards.append(dict(card, html=fixed))
    if not changed:
        return payload
    return dict(payload, cards=out_cards)


def _payload_has_locked_member_chips(payload) -> bool:
    """True when Consensus / PL-vs-Books chips still say Members only."""
    cards = (payload or {}).get('cards') or []
    if not cards:
        return False
    locked = sum(1 for c in cards if 'Members only' in ((c or {}).get('html') or ''))
    return locked >= max(1, len(cards) // 2)


def _save_cards(slug, tier, payload, allow_empty=False):
    from slate_freshness import current_payload
    payload = current_payload(payload)
    if not payload['cards'] and not allow_empty:
        return  # an empty page (a failed build) never replaces a good saved feed
    # Never persist a "member" feed that still shows Join Premium on the chips.
    if tier == 'member' and _payload_has_locked_member_chips(payload):
        logging.getLogger(__name__).warning(
            '[web cards] refusing to save locked member feed for %s', slug,
        )
        return
    # Never replace a feed that already has upcoming cards with a past-only rebuild.
    if slug.endswith('-picks') and payload.get('cards') and not _feed_has_upcoming(payload):
        existing = _CARDS.get((slug, tier)) or _saved_cards(slug, tier)
        if existing and _feed_has_upcoming(existing[1]):
            return
    # Never shrink a warm picks feed to a short page (usually a limit=10 response
    # that was restamped and saved). Load-more needs the full slate on disk.
    if slug.endswith('-picks') and payload.get('cards') is not None:
        existing = _CARDS.get((slug, tier)) or _saved_cards(slug, tier)
        if existing and _feed_has_upcoming(existing[1]):
            old_n = len((existing[1] or {}).get('cards') or [])
            new_n = len(payload.get('cards') or [])
            if old_n > 10 and 0 < new_n < old_n and new_n <= 10:
                return
    _CARDS[(slug, tier)] = (time.time(), payload)
    try:
        _CARDS_DIR.mkdir(parents=True, exist_ok=True)
        blob = gzip.compress(json.dumps(payload).encode(), compresslevel=5)
        tmp = _cards_file(slug, tier).with_suffix('.tmp')
        tmp.write_bytes(blob)
        tmp.replace(_cards_file(slug, tier))
        # First 10 cards always on disk so NCAAB (and large slates) paint instantly.
        if slug.endswith('-picks') and payload.get('cards'):
            head = dict(
                payload,
                cards=list(payload.get('cards') or [])[:10],
                total=len(payload.get('cards') or []),
                offset=0,
                nextOffset=10 if len(payload.get('cards') or []) > 10 else None,
                hasMore=len(payload.get('cards') or []) > 10,
            )
            head_tmp = _cards_head_file(slug, tier).with_suffix('.tmp')
            head_tmp.write_bytes(gzip.compress(json.dumps(head).encode(), compresslevel=5))
            head_tmp.replace(_cards_head_file(slug, tier))
        # Keep an unlocked member backup — never lose a good feed to a bad rebuild.
        if tier == 'member' and not _payload_has_locked_member_chips(payload):
            ok_tmp = _cards_ok_file(slug).with_suffix('.tmp')
            ok_tmp.write_bytes(blob)
            ok_tmp.replace(_cards_ok_file(slug))
        if tier == 'member':
            _fill_missing_locked_feeds(slug, payload)
            # Refresh guest/free from member when the member feed has upcoming games,
            # so a past-only locked feed cannot outlive a successful rebuild.
            if _feed_has_upcoming(payload):
                locked = _locked_from_member(payload)
                for locked_tier in ('guest', 'free'):
                    _CARDS[(slug, locked_tier)] = (time.time(), locked)
                    try:
                        tmp = _cards_file(slug, locked_tier).with_suffix('.tmp')
                        tmp.write_bytes(gzip.compress(json.dumps(locked).encode(), compresslevel=5))
                        tmp.replace(_cards_file(slug, locked_tier))
                    except OSError:
                        pass
    except OSError:
        pass


def _fill_missing_locked_feeds(slug, payload):
    """Guest and free are the member cards with the two face chips locked.
    Write them when a member feed exists and those files were never saved.
    """
    if not payload.get('cards'):
        return
    for locked_tier in ('guest', 'free'):
        if _cards_file(slug, locked_tier).is_file():
            continue
        _save_cards(slug, locked_tier, _locked_from_member(payload), allow_empty=True)


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
    # A member feed that still says "Join Premium" is unusable — restore .ok or rebuild.
    if (
        tier == 'member'
        and saved
        and _usable_cards(saved[1])
        and _payload_has_locked_member_chips(saved[1])
    ):
        _CARDS.pop((slug, 'member'), None)
        restored = None
        try:
            ok = _cards_ok_file(slug)
            if ok.is_file():
                payload = _read_cards_file(ok)
                if _usable_cards(payload) and not _payload_has_locked_member_chips(payload):
                    restored = (time.time(), payload)
                    _CARDS[(slug, 'member')] = restored
                    # Put the good feed back on the live path.
                    tmp = _cards_file(slug, 'member').with_suffix('.tmp')
                    tmp.write_bytes(ok.read_bytes())
                    tmp.replace(_cards_file(slug, 'member'))
            path = _cards_file(slug, 'member')
            if restored is None and path.is_file():
                path.unlink()
        except OSError:
            pass
        saved = restored
    if saved and _usable_cards(saved[1]):
        return saved
    # Instant first paint from head10 while a full rebuild is still running.
    if slug.endswith('-picks'):
        try:
            head_path = _cards_head_file(slug, tier)
            if head_path.is_file():
                head = _read_cards_file(head_path)
                if _usable_cards(head) and (
                    tier != 'member' or not _payload_has_locked_member_chips(head)
                ):
                    return (time.time(), head)
        except (OSError, ValueError, TypeError):
            pass
    if tier in ('guest', 'free'):
        saved = _saved_cards(slug, 'free' if tier == 'guest' else 'guest')
        if saved and _usable_cards(saved[1]):
            return saved
        member = _saved_cards(slug, 'member')
        if member and _usable_cards(member[1]) and not _payload_has_locked_member_chips(member[1]):
            locked = _locked_from_member(member[1])
            _save_cards(slug, 'guest', locked, allow_empty=True)
            _save_cards(slug, 'free', locked, allow_empty=True)
            return (time.time(), locked)
    return None


def _start_full_cards(slug, tier, app):
    """One full build per sport and tier; every page of cards is cut from it."""
    # Boot grace only blocks *extra* SSR when a disk feed already exists.
    # A cold miss must start building or the React page stays on "Loading games…"
    # for the whole grace window (and Results never enable).
    if _in_boot_grace() and _cards_for_tier(slug, tier):
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


def _card_in_span(card, span):
    """Today, and `span` calendar days after today. Blank dates stay (undated upcoming bouts)."""
    today = datetime.now(ZoneInfo("America/New_York")).date()
    end = today + timedelta(days=span)
    raw = str((card or {}).get("date") or "")[:10]
    if not raw:
        return True
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return True
    return today <= day <= end


_NCAAW_ODDS_TIP = "odds currently unavailable"
_NCAAW_ODDS_BTN = (
    '<button type="button" class="pl-info-btn" '
    f'data-tip="{_NCAAW_ODDS_TIP}" data-pl-info-tip="{_NCAAW_ODDS_TIP}" '
    f'title="{_NCAAW_ODDS_TIP}" aria-label="{_NCAAW_ODDS_TIP}" '
    'aria-expanded="false" aria-haspopup="true">ⓘ</button>'
)


def _stamp_ncaaw_unavailable_odds(payload):
    """NCAAW-only: Books N/A uses ⓘ hover 'odds currently unavailable'."""
    if not payload or not isinstance(payload, dict):
        return payload
    cards = payload.get('cards') or []
    if not cards:
        return payload
    out = []
    changed = False
    old_tip = (
        "No Market odds for this game. Market post lines the day before or "
        "the day of the game, and often post none for games against "
        "non-Division I schools."
    )
    for card in cards:
        html = card.get('html') or ''
        if 'data-pick-card' not in html:
            out.append(card)
            continue
        fixed = html
        if old_tip in fixed:
            fixed = fixed.replace(old_tip, _NCAAW_ODDS_TIP)
        # Rewrite any existing Market-odds info button tip to the owner copy.
        fixed = re.sub(
            r'((?:title|data-tip|data-pl-info-tip|aria-label)=")No Market odds[^"]*(")',
            rf'\1{_NCAAW_ODDS_TIP}\2',
            fixed,
        )
        fixed = re.sub(
            r'(<div class="ml-line face-books-ml">\s*<span class="ml-src books">[^<]*</span>\s*)'
            r'<span class="ml-num[^"]*">\s*(?:—|–|-|N/A)\s*</span>(?!\s*<button)',
            lambda m: m.group(1) + '<span class="ml-num ">N/A</span> ' + _NCAAW_ODDS_BTN,
            fixed,
        )
        fixed = re.sub(
            r'<td class="val-books">\s*(?:—|–|-|N/A)\s*</td>',
            '<td class="val-books">N/A ' + _NCAAW_ODDS_BTN + '</td>',
            fixed,
        )
        if fixed != html:
            changed = True
            out.append(dict(card, html=fixed))
        else:
            out.append(card)
    return dict(payload, cards=out) if changed else payload


def _ncaab_feed_has_today(payload) -> bool:
    """True when the NCAAB card feed includes at least one card dated today (ET)."""
    return _feed_has_upcoming(payload, today_only=True)


def _feed_has_upcoming(payload, today_only: bool = False) -> bool:
    """True when the feed has a non-final card on/after today (ET)."""
    today = datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    for card in (payload or {}).get('cards') or []:
        day = str((card or {}).get('date') or '')[:10]
        if not day:
            continue
        if today_only and day != today:
            continue
        if day < today:
            continue
        if (card or {}).get('isFinal') is True:
            continue
        return True
    return False


def _feed_max_upcoming_date(payload):
    """Latest non-final card date on/after today (ET), or None."""
    today = datetime.now(ZoneInfo('America/New_York')).date()
    latest = None
    for card in (payload or {}).get('cards') or []:
        if (card or {}).get('isFinal') is True:
            continue
        raw = str((card or {}).get('date') or '')[:10]
        if not raw:
            continue
        try:
            day = datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            continue
        if day < today:
            continue
        if latest is None or day > latest:
            latest = day
    return latest


def _feed_missing_near_slate(payload, span: int = 2) -> bool:
    """True when the feed stops before today+span — 'Show next two days' would no-op.

    NHL (and other team sports) often publish tomorrow's slate while today's
    feed still only holds tonight. Force a rebuild so the button can reveal them.
    """
    if span is None or int(span) <= 0:
        return False
    latest = _feed_max_upcoming_date(payload)
    if latest is None:
        return False
    today = datetime.now(ZoneInfo('America/New_York')).date()
    return latest < today + timedelta(days=int(span))


def _sport_in_season_for_cards(sport: str) -> bool:
    if not sport:
        return False
    try:
        from NHL77FINAL import get_season_phase
        return get_season_phase(sport)[0] != 'offseason'
    except Exception:
        return False


def _invalidate_sport_card_feeds(slug: str):
    """Drop one sport's web_cards only (memory + disk)."""
    if not slug or not slug.endswith('-picks'):
        return
    for locked_tier in ('guest', 'free', 'member'):
        _CARDS.pop((slug, locked_tier), None)
        try:
            path = _cards_file(slug, locked_tier)
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def _invalidate_ncaab_card_feeds():
    """Drop NCAAB web_cards only (memory + disk)."""
    _invalidate_sport_card_feeds('ncaab-picks')


def _next_available_cards(cards, span: int):
    """When today..today+span is empty, return the next upcoming day (plus span days)."""
    today = datetime.now(ZoneInfo('America/New_York')).date()
    upcoming = []
    for card in cards or []:
        if (card or {}).get('isFinal') is True:
            continue
        raw = str((card or {}).get('date') or '')[:10]
        if not raw:
            continue
        try:
            day = datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            continue
        if day >= today:
            upcoming.append((day, card))
    if not upcoming:
        return []
    first = min(d for d, _ in upcoming)
    end = first + timedelta(days=max(int(span or 0), 1))
    return [card for day, card in upcoming if first <= day <= end]


def _league_slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', (text or '').strip().lower()).strip('-')


def _card_league_label(card: dict) -> str:
    html = (card or {}).get('html') or ''
    m = re.search(r'\bdata-league="([^"]*)"', html, flags=re.I)
    return (m.group(1) if m else '').strip()


def _filter_cards_by_league(cards, league: str | None):
    """Soccer (and similar): page within one league so the first 10 are that league."""
    raw = (league or '').strip()
    if not raw or raw.lower() in {'all', '*'}:
        return list(cards or [])
    want = _league_slug(raw)
    out = []
    for card in cards or []:
        label = _card_league_label(card)
        if not label:
            continue
        if label == raw or _league_slug(label) == want:
            out.append(card)
    return out


def _card_page(slug, tier, app, offset, limit, span=None, league=None):
    from slate_freshness import current_payload
    saved = _cards_for_tier(slug, tier)
    if saved and time.time()-saved[0] < _CARDS_KEEP:
        sport = _sport_for(slug)
        # In-season sports: a past-only feed stays "fresh" for days and the React
        # span filter then shows an empty slate. Force a rebuild when nothing
        # upcoming remains (NCAAB today-miss, UFC next-card miss, etc.).
        needs_rebuild = (
            slug.endswith('-picks')
            and _sport_in_season_for_cards(sport)
            and (
                not _feed_has_upcoming(saved[1])
                or (span is not None and int(span) >= 2 and _feed_missing_near_slate(saved[1], span))
            )
        )
        if needs_rebuild:
            # Keep serving current cards; rebuild in the background so "Show the
            # next two days" can reveal tomorrow once the classic slate is ready.
            _start_full_cards(slug, tier, app)
        payload = _ensure_live_share_image(current_payload(saved[1]), slug)
        if payload is not saved[1] and payload.get('shareImage') != (saved[1] or {}).get('shareImage'):
            _save_cards(slug, tier, payload, allow_empty=True)
        source = payload['cards']
        used_next = False
        if span is not None:
            spanned = [card for card in source if _card_in_span(card, span)]
            if spanned:
                source = spanned
            elif _sport_in_season_for_cards(sport):
                # No games in today..today+span — show the next available slate.
                nxt = _next_available_cards(source, span)
                if nxt:
                    source = nxt
                    used_next = True
                else:
                    source = spanned
            else:
                source = spanned
        if league:
            source = _filter_cards_by_league(source, league)
        total = len(source)
        cards = source[offset:offset+limit]
        next_offset = offset+len(cards) if offset+len(cards) < total else None
        # Never flag refreshing when cards are already on hand — the client skips
        # caching refreshing payloads and the homepage would refetch every sport.
        return {**payload, 'cards': cards, 'total': total, 'offset': offset,
                'nextOffset': next_offset, 'hasMore': next_offset is not None,
                'refreshing': bool(needs_rebuild), 'nextAvailable': used_next}, 200
    if time.time()-_CARD_ERRORS.get((slug, tier), 0) < 15:
        return {'error': 'Games could not be loaded. Please retry.', 'cards': []}, 503
    # One cold build at a time (semaphore inside _start_full_cards). Homepage stays
    # serial so this does not stampede; sport pages poll building:true.
    started = _start_full_cards(slug, tier, app)
    # Cap poll delay — a multi-minute retryAfter freezes "Loading games…" on the
    # sport page even when the client already caps pauses at 30s.
    retry = 2 if started or not _in_boot_grace() else 3
    return {'cards': [], 'total': None, 'css': '', 'shareImage': '', 'offset': offset,
            'nextOffset': offset, 'hasMore': True, 'building': True,
            'refreshing': True, 'retryAfter': retry}, 202


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
            span = request.args.get('span')
            span = None if span is None else int(span)
            if span not in (None, 0, 2):
                raise ValueError
            league = (request.args.get('league') or '').strip() or None
            if league and len(league) > 80:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify(error='Choose a valid game page.'), 400
        if request.args.get('cached') == '1':
            # Homepage fast path: disk/memory only. Never SSR and never queue a
            # background build from the homepage — those builds monopolize the
            # only worker and make results / sport pages hang for minutes.
            # Sports without a warm feed stay off the homepage (client hides them).
            saved = _cards_for_tier(slug, tier)
            if not saved:
                payload = {
                    'cards': [], 'total': 0, 'css': '', 'shareImage': '',
                    'offset': 0, 'nextOffset': None, 'hasMore': False,
                    'building': False, 'refreshing': False,
                }
                response = jsonify(payload)
                response.headers['Cache-Control'] = 'private, max-age=15'
                response.vary.add('Cookie')
                return response
            full = saved[1] or {}
            cards = list(full.get('cards') or [])[:limit]
            payload = dict(
                full, cards=cards, total=len(full.get('cards') or []),
                offset=0, nextOffset=None, hasMore=False,
                building=False, refreshing=False,
            )
            response = jsonify(payload)
            response.headers['Cache-Control'] = 'private, max-age=30'
            response.vary.add('Cookie')
            return response
        payload, status = _card_page(
            slug, tier, app, offset, limit, span, league=league,
        )
        sport = _sport_for(slug)
        # limit/offset responses are one page of the saved feed. Never write that
        # slice back to disk — that shrank NCAAB/NBA feeds to 10 cards and hid
        # "Load more" even when 40+ games were on the slate.
        paged = 'limit' in request.args
        if tier == 'member' and sport in {'NHL', 'NFL', 'NCAAB', 'NCAAW', 'NCAAF', 'NBA', 'WNBA'}:
            from market_card_refresh import refresh as refresh_market_cards
            from NHL77FINAL import DATABASE
            payload = refresh_market_cards(payload, sport, DATABASE)
            if sport == 'NHL':
                from pl_line_repair import refresh as refresh_pl_cards
                payload = refresh_pl_cards(payload, DATABASE)
            fixed = _restamp_consensus_hist_on_payload(payload, sport)
            if fixed is not payload:
                payload = fixed
                if not paged:
                    try:
                        _save_cards(slug, 'member', payload)
                    except Exception:
                        pass
        elif sport == 'TENNIS':
            fixed = _restamp_consensus_hist_on_payload(payload, sport)
            if fixed is not payload:
                payload = fixed
                if not paged:
                    try:
                        _save_cards(slug, tier, payload)
                    except Exception:
                        pass
        if sport == 'NCAAW':
            payload = _stamp_ncaaw_unavailable_odds(payload)
        response = jsonify(payload)
        response.status_code = status
        response.headers['Cache-Control'] = 'private, no-store'
        response.vary.add('Cookie')
        return response
    # No-limit path: never SSR on the visitor thread (workers=1 — a sync NCAAB
    # build wedged every API including /live). Same stale-while-revalidate as _card_page.
    saved = _cards_for_tier(slug, tier)
    age = time.time() - saved[0] if saved else None
    if saved and age < _CARDS_KEEP:
        payload = saved[1]
    else:
        _start_full_cards(slug, tier, app)
        if saved:
            payload = saved[1]
        else:
            response = jsonify({
                'cards': [], 'total': None, 'css': '', 'shareImage': '',
                'building': True, 'refreshing': True, 'retryAfter': 2,
            })
            response.status_code = 202
            response.headers['Cache-Control'] = 'private, no-store'
            response.vary.add('Cookie')
            return response
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
    sport = _sport_for(slug)
    if tier == 'member' and sport in {'NHL', 'NFL', 'NCAAB', 'NCAAW', 'NCAAF', 'NBA', 'WNBA'}:
        from market_card_refresh import refresh as refresh_market_cards
        from NHL77FINAL import DATABASE
        payload = refresh_market_cards(payload, sport, DATABASE)
        if sport == 'NHL':
            from pl_line_repair import refresh as refresh_pl_cards
            payload = refresh_pl_cards(payload, DATABASE)
        fixed = _restamp_consensus_hist_on_payload(payload, sport)
        if fixed is not payload:
            payload = fixed
            try:
                _save_cards(slug, 'member', payload)
            except Exception:
                pass
    elif sport == 'TENNIS':
        fixed = _restamp_consensus_hist_on_payload(payload, sport)
        if fixed is not payload:
            payload = fixed
            try:
                _save_cards(slug, tier, payload)
            except Exception:
                pass
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

