"""Serve the React front end (built into static/web/) at the site's main addresses.

Visitors opening /, /<sport>-picks, /<sport>-projections, /player-props or /all-sports-picks get the
React page; the same pages stay at /classic/... (see _ClassicPrefix in NHL77FINAL). Only real visitor
requests get React: the site's own in-process page builds (test client, saved-page rebuilds) keep
getting the classic HTML they read. PL_REACT=0 turns React off and serves the classic pages again.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SHELL_FILE = ROOT / 'static' / 'web' / 'index.html'
CANONICAL = 'https://predictionlab.io'

SPORT_NAMES = {
    'nhl': 'NHL', 'nba': 'NBA', 'mlb': 'MLB', 'nfl': 'NFL', 'ncaab': 'NCAAB', 'ncaaf': 'NCAAF',
    'ncaaw': 'NCAAW', 'wnba': 'WNBA', 'cfl': 'CFL', 'soccer': 'Soccer', 'tennis': 'Tennis', 'ufc': 'UFC',
    'golf': 'Golf',
}
_SPORT_PAGE = re.compile(r'^/(%s)-(picks|projections)/?$' % '|'.join(SPORT_NAMES))
_OTHER_PAGES = {
    '/': ('Prediction Lab | AI Sports Predictions for Every Sport',
          "Today's AI predictions and win probabilities for the NFL, NBA, NHL, MLB, college sports, "
          'soccer, tennis, UFC and golf, with every model graded in public.'),
    '/player-props': ('Player Props Predictions | Prediction Lab',
                      'AI player prop projections with each model graded in public.'),
    '/all-sports-picks': ('All Sports Picks Today | Prediction Lab',
                          "Every sport's AI picks on one page, with each pick type's recent record."),
    '/all-sports-projections': ('All Sports Projections Today | Prediction Lab',
                                "Every sport's AI projections on one page, with each pick type's recent record."),
}


def _page_meta(path):
    """(title, description) for a React page, or None when the path is not one."""
    clean = path.rstrip('/') or '/'
    if clean in _OTHER_PAGES:
        return _OTHER_PAGES[clean]
    match = _SPORT_PAGE.match(path)
    if not match:
        return None
    name = SPORT_NAMES[match.group(1)]
    return (f'{name} Predictions Today: AI Picks and Win Probabilities | Prediction Lab',
            f"Today's {name} predictions from Prediction Lab: win probabilities from six models, "
            'model lines, and every result graded in public.')


def _esc(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


class ReactShell:
    """WSGI wrapper: hands real visitors the React page for the main addresses."""

    def __init__(self, wsgi_app, add_site_tags=None):
        self.wsgi_app = wsgi_app
        self.add_site_tags = add_site_tags
        self._shell = None
        self._shell_mtime = None

    def _shell_html(self):
        try:
            mtime = SHELL_FILE.stat().st_mtime
        except OSError:
            return None
        if self._shell is None or mtime != self._shell_mtime:
            self._shell = SHELL_FILE.read_text(encoding='utf-8')
            self._shell_mtime = mtime
        return self._shell

    @staticmethod
    def _wants_react(environ):
        if os.environ.get('PL_REACT', '1') == '0' or environ.get('pl.classic'):
            return False
        if environ.get('REQUEST_METHOD') not in ('GET', 'HEAD'):
            return False
        # A real visitor arrives through the server; the site's own page builds use the test client.
        if 'gunicorn.socket' not in environ and 'werkzeug.socket' not in environ:
            return False
        if environ.get('HTTP_X_PL_RESULTS_REBUILD'):
            return False
        host = (environ.get('HTTP_HOST') or '').split(':')[0].lower()
        if host.endswith('underdogs.bet') or host.startswith('www.'):
            return False  # the app redirects these to the canonical address first
        if host.endswith('predictionlab.io') and environ.get('HTTP_X_FORWARDED_PROTO', 'https') != 'https':
            return False
        return True

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        meta = _page_meta(path) if self._wants_react(environ) else None
        shell = self._shell_html() if meta else None
        if not shell:
            return self.wsgi_app(environ, start_response)
        title, description = meta
        head = (
            f'<title>{_esc(title)}</title>\n'
            f'    <meta name="description" content="{_esc(description)}" />\n'
            f'    <link rel="canonical" href="{CANONICAL}{_esc(path.rstrip("/") or "/")}" />\n'
            f'    <meta property="og:title" content="{_esc(title)}" />\n'
            f'    <meta property="og:description" content="{_esc(description)}" />\n'
            f'    <meta property="og:url" content="{CANONICAL}{_esc(path.rstrip("/") or "/")}" />'
        )
        html = re.sub(r'<title>.*?</title>', head, shell, count=1, flags=re.S)
        if self.add_site_tags:
            try:
                html = self.add_site_tags(html)
            except Exception:
                pass
        body = html.encode('utf-8')
        start_response('200 OK', [
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Content-Length', str(len(body))),
            ('Cache-Control', 'no-cache'),
        ])
        return [b''] if environ.get('REQUEST_METHOD') == 'HEAD' else [body]
