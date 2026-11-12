"""Visitor-facing wording: "prediction" instead of "pick", and the renamed addresses.

Applied once, as the last text step before a page leaves the server (and to the card HTML the React
page reads), so every template copy and every page saved before the change reads the same way.
Only words people read change: text between tags, a few reading attributes (title, alt, aria-label,
placeholder, tooltips) and the page's description tags. Class names, ids, data, scripts and styles stay
as they are, except the exact menu labels / links in the site menu script (TV_MENUS).
"""
import hashlib
import re
import threading
from collections import OrderedDict

SITE = 'https://predictionlab.io'
WORDING_VERSION = 2
SPORT_SLUGS = ('nhl', 'nba', 'ncaab', 'ncaaw', 'mlb', 'soccer', 'nfl', 'ncaaf', 'wnba', 'tennis', 'ufc', 'golf',
               'cfl', 'all-sports')

# Old public address -> new public address (old ones answer 301; see react_shell.PublicPaths).
RENAMED = {
    '/ladder-picks': '/ladder',
    '/ai-sports-betting-picks-today': '/ai-sports-predictions-today',
    '/what-are-ai-sports-betting-picks': '/what-are-ai-sports-predictions',
}
# New address -> the route that still builds the page inside the app.
SERVED_BY = {new: old for old, new in RENAMED.items()}

# Whole phrases first (most specific), then single words. A word next to - or / is part of an address or a
# slug and is left alone, as are longer words (Pickford, picker).
_PHRASES = [
    (r'Picks &(?:amp;)? Predictions', 'Predictions'),
    (r"\bPick['\u2019]em\b", 'Even line'),
    (r"\bpick['\u2019]em\b", 'even line'),
    (r'\bAI sports betting picks are predictions generated', 'AI sports predictions are forecasts generated'),
    (r'\bsports betting picks\b', 'sports predictions'),
    (r'\bSports Betting Picks\b', 'Sports Predictions'),
    (r'\bAI picks overview\b', 'AI predictions overview'),
    (r'\bWhat AI picks are\b', 'What AI predictions are'),
    (r'\bdate picker\b', 'date selector'),
    (r'\beach pick a\b', 'each choose a'),
    (r'\bis picking\b', 'is predicting'),
    (r'\bpick monthly or yearly\b', 'choose monthly or yearly'),
    (r'\bPick to win\b', 'Predicted winner'),
    (r'\b(Efficiency|Edge|XSharp|Grinder2|Takedown|Consensus|model|it|that|which) picks (?=the |that |this |a |an |[A-Z])',
     r'\1 predicts '),
]
_WORDS = [
    (r"(?<![-/\w])picks(?![-/\w])", 'predictions'),
    (r"(?<![-/\w])Picks(?![-/\w])", 'Predictions'),
    (r"(?<![-/\w])PICKS(?![-/\w])", 'PREDICTIONS'),
    (r"(?<![-/\w])pick(?![-/\w])", 'prediction'),
    (r"(?<![-/\w])Pick(?![-/\w])", 'Prediction'),
    (r"(?<![-/\w])PICK(?![-/\w])", 'PREDICTION'),
]
_RULES = [(re.compile(p), r) for p, r in _PHRASES + _WORDS]
_HAS_PICK = re.compile(r'pick|books?|sharp consensus|ladder|player (?:props|projections)|odds|prediction lab', re.I)
_DISPLAY_RULES = [
    (re.compile(r'\bPlayer (?:Props|Projections)\b', re.I), 'Players'),
    (re.compile(r'(?<!Performance )(?<![-/\w])Ladder(?![-/\w])', re.I), 'Performance Ladder'),
    (re.compile(r'\b(?:PL|Prediction Lab)(?: Model)? Odds\b', re.I), 'Predictionlab'),
    (re.compile(r'\bOdds\s*&(?:amp;)?\s*Lines\b', re.I), 'Market & Projections'),
    (re.compile(r'\bSharp Consensus\b', re.I), 'Market Consensus'),
    (re.compile(r'(?<![-/\w])Sportsbooks(?![-/\w])', re.I), 'Markets'),
    (re.compile(r'(?<![-/\w])(?:Books|Sportsbook)(?![-/\w])', re.I), 'Market'),
]


def soften(text):
    """One piece of reading text with "pick" worded as "prediction"."""
    if not text or not _HAS_PICK.search(text):
        return text
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text


_SKIP = re.compile(r'(<(script|style|textarea|code|pre|blockquote)\b.*?</\2\s*>|<!--.*?-->|'
                   r'<(div|span|strong|b)\b(?=[^>]*\bclass="[^"]*\b(?:team-name|player-name|pp-player|golf-player|pl-golf-name)\b)[^>]*>.*?</\3\s*>)', re.S | re.I)
_TEXT = re.compile(r'>([^<>]*)<')
_ATTR = re.compile(r'(\s(?:title|alt|aria-label|placeholder|data-tip|data-tooltip|data-info)=")([^"]*)(")')
_META = re.compile(r'(<meta\s+(?:name|property)="(?:description|og:title|og:description|twitter:title|twitter:description)"'
                   r'\s+content=")([^"]*)(")', re.I)
_TITLE_TAG = re.compile(r'(<title>)(.*?)(</title>)', re.S | re.I)

_sports = '|'.join(re.escape(s) for s in SPORT_SLUGS)
# Links to the sport pages and the renamed pages, in href / action / canonical / og:url values.
_LINK = re.compile(r'((?:href|action|content)=["\'])(?:https://predictionlab\.io)?(/(?:%s)-picks|%s)(?=["\'?#])'
                   % (_sports, '|'.join(re.escape(old) for old in RENAMED)))
# The site menu script: exact label strings and links (TV_MENUS items).
_MENU_STRINGS = [
    ("'Picks & Predictions'", "'Predictions'"),
    ('"Picks & Predictions"', '"Predictions"'),
    ("'All Sports Picks'", "'All Sports Predictions'"),
    ("'10-Day Ladder Picks'", "'10-Day Performance Ladder'"),
    ("'The Ladder'", "'Performance Ladder'"),
    ('"The Ladder"', '"Performance Ladder"'),
    ("'Player Props'", "'Players'"),
    ('"Player Props"', '"Players"'),
]
_MENU_LINK = re.compile(r"(h:')(/(?:%s)-picks|%s)(')" % (_sports, '|'.join(re.escape(old) for old in RENAMED)))
_ABS_RENAMED = re.compile('|'.join(re.escape(SITE + old) for old in RENAMED) + r'(?=["\'?#<\s])')


def _new_path(path):
    if path in RENAMED:
        return RENAMED[path]
    if path.endswith('-picks'):
        return path[:-len('-picks')] + '-predictions'
    return path


def _display_words(text):
    if re.search(r'not (?:an? online gambling operator|a sportsbook)|do not (?:take bets|accept wagers)|responsible (?:gaming|gambling)|1-800-GAMBLER', text, re.I):
        return text
    text = soften(text)
    for pattern, replacement in _DISPLAY_RULES:
        text = pattern.sub(replacement, text)
    return text


def _soften_segment(html):
    html = re.sub(r'(<span\b[^>]*class="[^"]*\bml-src\b[^"]*\bpl\b[^"]*"[^>]*>)\s*Prediction Lab\s*(</span>)',
                  r'\1Predictionlab\2', html)
    html = _TEXT.sub(lambda m: '>' + _display_words(m.group(1)) + '<', html)
    html = _ATTR.sub(lambda m: m.group(1) + _display_words(m.group(2)) + m.group(3), html)
    html = _META.sub(lambda m: m.group(1) + _display_words(m.group(2)) + m.group(3), html)
    return html


def _script(block):
    for old, new in _MENU_STRINGS:
        if old in block:
            block = block.replace(old, new)
    return _MENU_LINK.sub(lambda m: m.group(1) + _new_path(m.group(2)) + m.group(3), block)

def rewrite_policy(html):
    """Policy text stays verbatim; only the shared navigation gets new labels."""
    return re.sub(
        r'<(header|footer)\b[^>]*>[\s\S]*?</\1\s*>',
        lambda match: rewrite_html(match.group(0)), html, flags=re.I,
    )


def rewrite_json_strings(value):
    """JSON data whose strings are page HTML or reading text (the React header / footer, card HTML)."""
    if isinstance(value, str):
        return rewrite_html(value) if '<' in value else soften(value)
    if isinstance(value, list):
        return [rewrite_json_strings(v) for v in value]
    if isinstance(value, dict):
        return {k: rewrite_json_strings(v) for k, v in value.items()}
    return value


_DONE = OrderedDict()  # digest of a page as built -> the reworded page (saved pages repeat often)
_DONE_MAX = 48
_DONE_LOCK = threading.Lock()


def _remembered(body, rewrite):
    key = (rewrite.__name__, hashlib.blake2b(body, digest_size=16).digest())
    with _DONE_LOCK:
        if key in _DONE:
            _DONE.move_to_end(key)
            return _DONE[key]
    try:
        out = rewrite(body.decode('utf-8')).encode('utf-8')
    except Exception:
        return body
    with _DONE_LOCK:
        _DONE[key] = out
        while len(_DONE) > _DONE_MAX:
            _DONE.popitem(last=False)
    return out


class VisitorWords:
    """WSGI wrapper (inside gzip): visitors' HTML pages get rewrite_html. The site's own in-process page
    builds read the original HTML, so saved pages and the checks that read them do not change.
    The blog keeps its own wording."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    @staticmethod
    def _wanted(environ):
        if environ.get('REQUEST_METHOD') != 'GET':
            return False
        if 'gunicorn.socket' not in environ and 'werkzeug.socket' not in environ:
            return False
        if environ.get('HTTP_X_PL_RESULTS_REBUILD'):
            return False
        path = environ.get('PATH_INFO', '')
        return not (path.startswith('/static/') or path.startswith('/api/'))

    def __call__(self, environ, start_response):
        if not self._wanted(environ):
            return self.wsgi_app(environ, start_response)
        captured = {}

        def capture(status, headers, exc_info=None):
            captured['status'], captured['headers'], captured['exc_info'] = status, headers, exc_info
            return lambda data: captured.setdefault('written', []).append(data)

        app_iter = self.wsgi_app(environ, capture)
        status = captured.get('status', '500 INTERNAL SERVER ERROR')
        headers = list(captured.get('headers') or [])
        lower = {k.lower(): v for k, v in headers}
        if (not status.startswith('200') or 'text/html' not in (lower.get('content-type') or '').lower()
                or 'content-encoding' in lower):
            start_response(status, headers, captured.get('exc_info'))
            written = captured.get('written')
            return (list(written) + list(app_iter)) if written else app_iter
        try:
            body = b''.join(captured.get('written') or []) + b''.join(app_iter)
        finally:
            close = getattr(app_iter, 'close', None)
            if close:
                close()
        path = environ.get('PATH_INFO', '')
        blog = path == '/blog' or path.startswith('/blog/')
        policy = path.rstrip('/') in ('/terms', '/privacy', '/refund-policy', '/responsible-gaming')
        out = _remembered(body, rewrite_policy if policy else rewrite_menus if blog else rewrite_html)
        headers = [(k, v) for k, v in headers if k.lower() != 'content-length']
        headers.append(('Content-Length', str(len(out))))
        start_response(status, headers, captured.get('exc_info'))
        return [out]


_MENU_TEXT = re.compile(r'Picks &(?:amp;)? Predictions|All Sports Picks|AI picks overview|What AI picks are|Player (?:Props|Projections)|(?<!Performance )\bLadder\b')


def rewrite_menus(html):
    """Only the site menu labels and the renamed links (the blog keeps its own article wording)."""
    if not html or not _HAS_PICK.search(html):
        return html
    parts = []
    last = 0
    for m in _SKIP.finditer(html):
        parts.append(_MENU_TEXT.sub(lambda t: _display_words(t.group(0)), html[last:m.start()]))
        block = m.group(0)
        parts.append(_script(block) if block[:7].lower() == '<script' else block)
        last = m.end()
    parts.append(_MENU_TEXT.sub(lambda t: _display_words(t.group(0)), html[last:]))
    html = _LINK.sub(lambda m: m.group(1) + (SITE if m.group(0)[len(m.group(1)):].startswith('http') else '')
                     + _new_path(m.group(2)), ''.join(parts))
    return _ABS_RENAMED.sub(lambda m: SITE + RENAMED[m.group(0)[len(SITE):]], html)


def rewrite_html(html):
    """A whole page (or a card) with visitor wording and the new addresses."""
    if not html or not _HAS_PICK.search(html):
        return html
    parts = []
    last = 0
    for m in _SKIP.finditer(html):
        parts.append(_soften_segment(html[last:m.start()]))
        block = m.group(0)
        parts.append(_script(block) if block[:7].lower() == '<script' else block)
        last = m.end()
    parts.append(_soften_segment(html[last:]))
    html = ''.join(parts)
    html = _TITLE_TAG.sub(lambda m: m.group(1) + _display_words(m.group(2)) + m.group(3), html)
    html = _LINK.sub(lambda m: m.group(1) + (SITE if m.group(0)[len(m.group(1)):].startswith('http') else '')
                     + _new_path(m.group(2)), html)
    return _ABS_RENAMED.sub(lambda m: SITE + RENAMED[m.group(0)[len(SITE):]], html)
