"""JSON API for the React front end in web/.

Cards are read from the same sport page the server renders today, fetched in-process with the
visitor's host and cookies, so the numbers and the paywall match the current site. The data
helpers (sports list, live boxes, results) live in NHL77FINAL and are imported when called.
"""
import gzip
import json
import logging
import re
import threading
import time
from html import unescape
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, current_app, jsonify, render_template, request

api = Blueprint('pl_web_api', __name__)


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
    response.headers['Vary'] = 'Accept-Encoding'
    return response

_GAME_ID = re.compile(r'\sdata-game-id="([^"]*)"')
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
    headers = {'Host': host or request.host, 'X-Forwarded-Proto': 'https'}
    cookie = cookie if host else request.headers.get('Cookie')
    if cookie:
        headers['Cookie'] = cookie
    resp = app.test_client().get(path, headers=headers)
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
    """Teams and sports whose name matches the query; each result opens that sport's page."""
    q = ' '.join((request.args.get('q') or '').lower().split())
    if len(q) < 2:
        return jsonify({'results': []})
    from NHL77FINAL import SPORT_SEO_SLUGS as slug_of
    results = [
        {'label': f'{sport.title() if sport in ("SOCCER", "TENNIS", "GOLF") else sport} Predictions',
         'sport': sport, 'href': '/' + slug_of[sport]}
        for sport in _SPORT_ORDER if sport in slug_of and q in sport.lower()
    ]
    if 'props' in q or 'player' in q:
        results.append({'label': 'Player Props', 'sport': 'PROPS', 'href': '/player-props'})

    def _rank(row):
        sport, name = row
        words = name.lower().split()
        starts = any(' '.join(words[i:]).startswith(q) for i in range(len(words)))
        return (0 if starts else 1, _SPORT_ORDER.index(sport), name)

    teams = sorted((r for r in _search_teams() if q in r[1].lower() and r[0] in slug_of), key=_rank)
    for sport, name in teams[:12]:
        results.append({
            'label': name, 'sport': sport,
            'href': f"/{slug_of[sport]}?team={quote(name)}",
        })
    return jsonify({'results': results[:14]})


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


@api.route('/api/web/live')
def api_live():
    from NHL77FINAL import _web_live_payload
    return jsonify({'items': _web_live_payload()})


@api.route('/api/web/chrome')
def api_chrome():
    """The shared header and footer, rendered for this visitor (login state comes from the session)."""
    return jsonify({
        'header': render_template('partials/research_header.html'),
        'footer': render_template('partials/site_directory_footer.html'),
    })


@api.route('/api/web/me')
def api_me():
    from flask_login import current_user
    from auth_system import is_premium_user
    return jsonify({'loggedIn': bool(current_user.is_authenticated), 'premium': bool(is_premium_user())})


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
_CARDS_FRESH = 60
_CARDS_KEEP = 6 * 3600
_CARDS = {}  # (slug, tier) -> (saved at, payload)
_CARDS_BUILDING = set()
_CARDS_LOCK = threading.Lock()
_CARDS_DIR = Path(__file__).resolve().parent / '.cache' / 'web_cards'


def _build_cards(slug, tier, app, host, cookie):
    sport = _sport_for(slug)
    path = '/' + slug.replace('-projections', '-picks')
    if sport == 'SOCCER':
        path += '?region=all'  # the soccer page redirects to its region view without this
    html = _page_html(path, app, host, cookie)
    cards = _cards(html)
    if tier != 'member':
        for card in cards:
            card['html'] = _lock_member_chips(card['html'])
    dates = _game_dates(sport)
    for card in cards:
        if not card['date']:
            found = _GAME_ID.search(card['html'])
            card['date'] = dates.get(found.group(1), '') if found else ''
    cards.sort(key=_page_order)
    return {'cards': cards, 'total': len(cards), 'css': _card_styles(html, app), 'shareImage': _share_image(html)}


def _cards_file(slug, tier):
    return _CARDS_DIR / f'{slug}.{tier}.json.gz'


def _saved_cards(slug, tier):
    saved = _CARDS.get((slug, tier))
    if saved:
        return saved
    try:
        path = _cards_file(slug, tier)
        payload = json.loads(gzip.decompress(path.read_bytes()))
        saved = (path.stat().st_mtime, payload)
        _CARDS[(slug, tier)] = saved
        return saved
    except (OSError, ValueError):
        return None


def _save_cards(slug, tier, payload):
    if not payload['cards']:
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
    try:
        with app.app_context():
            _save_cards(slug, tier, _build_cards(slug, tier, app, host, cookie))
    except Exception:
        logging.getLogger(__name__).exception('[web cards] refresh failed for %s', slug)
    finally:
        with _CARDS_LOCK:
            _CARDS_BUILDING.discard((slug, tier))


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
    saved = _saved_cards(slug, tier)
    age = time.time() - saved[0] if saved else None
    if saved and age < _CARDS_KEEP:
        payload = saved[1]
        if age >= _CARDS_FRESH:
            with _CARDS_LOCK:
                start = (slug, tier) not in _CARDS_BUILDING
                _CARDS_BUILDING.add((slug, tier))
            if start:
                threading.Thread(
                    target=_refresh_cards,
                    args=(slug, tier, app, request.host, request.headers.get('Cookie')),
                    daemon=True,
                ).start()
    else:
        payload = _build_cards(slug, tier, app, request.host, request.headers.get('Cookie'))
        _save_cards(slug, tier, payload)
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
