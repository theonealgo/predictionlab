"""New sports-page layout: the sports list on the left, the sport's cards in the middle, and that
sport's full results on the right. Bubble links to every sport sit on top, with GAMES and PLAYERS
tabs under them. GAMES has All / East / West and team bubbles; PLAYERS shows the player props
(members) inside the page, so the props no longer need their own page.

Applied to the finished page HTML, so the cards, caches and paywall stay as they are.
The classic view keeps the old page: a data attribute on <html> hides the new parts.
"""
import json
import re
from html import escape

_HEAD_END = re.compile(r'</head\s*>', re.I)
_BODY_START = re.compile(r'<body\b[^>]*>', re.I)
_HEADER_END = re.compile(r'</header\s*>', re.I)
_FOOTER_START = re.compile(r'<footer\b[^>]*site-directory-footer', re.I)
_RESULTS_START = re.compile(r'<section\b[^>]*\bid="picks-recent-results"', re.I)
_SECTION_TAG = re.compile(r'<(/?)section\b', re.I)

# East and West lists by full team name, exactly as the cards print them.
CONFERENCES = {
    'NBA': (
        ['Atlanta Hawks', 'Boston Celtics', 'Brooklyn Nets', 'Charlotte Hornets', 'Chicago Bulls',
         'Cleveland Cavaliers', 'Detroit Pistons', 'Indiana Pacers', 'Miami Heat', 'Milwaukee Bucks',
         'New York Knicks', 'Orlando Magic', 'Philadelphia 76ers', 'Toronto Raptors', 'Washington Wizards'],
        ['Dallas Mavericks', 'Denver Nuggets', 'Golden State Warriors', 'Houston Rockets', 'LA Clippers',
         'Los Angeles Lakers', 'Memphis Grizzlies', 'Minnesota Timberwolves', 'New Orleans Pelicans',
         'Oklahoma City Thunder', 'Phoenix Suns', 'Portland Trail Blazers', 'Sacramento Kings',
         'San Antonio Spurs', 'Utah Jazz'],
    ),
    'NHL': (
        ['Boston Bruins', 'Buffalo Sabres', 'Carolina Hurricanes', 'Columbus Blue Jackets',
         'Detroit Red Wings', 'Florida Panthers', 'Montreal Canadiens', 'New Jersey Devils',
         'New York Islanders', 'New York Rangers', 'Ottawa Senators', 'Philadelphia Flyers',
         'Pittsburgh Penguins', 'Tampa Bay Lightning', 'Toronto Maple Leafs', 'Washington Capitals'],
        ['Anaheim Ducks', 'Arizona Coyotes', 'Utah Mammoth', 'Utah Hockey Club', 'Calgary Flames',
         'Chicago Blackhawks', 'Colorado Avalanche', 'Dallas Stars', 'Edmonton Oilers',
         'Los Angeles Kings', 'Minnesota Wild', 'Nashville Predators', 'San Jose Sharks',
         'Seattle Kraken', 'St. Louis Blues', 'Vancouver Canucks', 'Vegas Golden Knights',
         'Winnipeg Jets'],
    ),
    'WNBA': (
        ['Atlanta Dream', 'Chicago Sky', 'Connecticut Sun', 'Indiana Fever', 'New York Liberty',
         'Washington Mystics', 'Toronto Tempo'],
        ['Dallas Wings', 'Golden State Valkyries', 'Las Vegas Aces', 'Los Angeles Sparks',
         'Minnesota Lynx', 'Phoenix Mercury', 'Portland Fire', 'Seattle Storm'],
    ),
}

VIEW_SCRIPT = """<script>
(function(){
  var q = null, v = null;
  try { q = new URLSearchParams(location.search).get('view'); } catch (e) {}
  try {
    if (q === 'classic' || q === 'kalshi') { localStorage.setItem('pl_view', q); }
    v = localStorage.getItem('pl_view');
  } catch (e) {}
  document.documentElement.setAttribute('data-view', (q || v) === 'classic' ? 'classic' : 'kalshi');
})();
</script>"""

STYLE = """<style>
html[data-view="classic"] .kalshi-only{display:none!important}
html[data-view="kalshi"] .classic-only{display:none!important}
.pl-view-bar{max-width:1320px;margin:0 auto;padding:10px 20px 0;display:flex;justify-content:flex-end;font:700 13px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
.pl-view-bar a{color:#1d4ed8;text-decoration:none}
.pl-view-bar a:hover{text-decoration:underline}
html[data-view="kalshi"] .pl-sport-grid{display:grid;grid-template-columns:230px minmax(0,1fr) 340px;gap:24px;align-items:start;max-width:1320px;margin:0 auto;padding:12px 20px 48px;background:#fff}
html[data-view="kalshi"] .pl-sport-side{grid-column:1;grid-row:1;position:sticky;top:84px}
html[data-view="kalshi"] .pl-sport-main{grid-column:2;grid-row:1;min-width:0}
html[data-view="kalshi"] .pl-sport-results{grid-column:3;grid-row:1;position:sticky;top:84px;max-height:calc(100vh - 100px);overflow:auto;padding:16px;border:1px solid #e5e7eb;border-radius:14px;background:#fff}
html[data-view="kalshi"] .pl-side-head{margin:0 0 10px;font:700 12px/1 ui-monospace,Menlo,monospace;letter-spacing:.12em;text-transform:uppercase;color:#6b7280}
html[data-view="kalshi"] .pl-side-link{display:flex;align-items:center;gap:10px;padding:9px 10px;border-radius:10px;color:#111827;text-decoration:none;font:600 14px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
html[data-view="kalshi"] .pl-side-link:hover{background:#f3f4f6}
html[data-view="kalshi"] .pl-side-link.is-active{background:#e8f8ee;color:#065f46}
html[data-view="kalshi"] .pl-side-icon{width:22px;text-align:center}
html[data-view="kalshi"] .pl-side-live{margin-left:auto;font:700 10px/1 ui-monospace,Menlo,monospace;letter-spacing:.06em;text-transform:uppercase;color:#15803d}
html[data-view="kalshi"] .pl-bubbles{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}
html[data-view="kalshi"] .pl-bubble{padding:8px 14px;border:1px solid #d1d5db;border-radius:999px;background:#fff;color:#111827;text-decoration:none;white-space:nowrap;font:600 14px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
html[data-view="kalshi"] .pl-bubble:hover{background:#f3f4f6}
html[data-view="kalshi"] .pl-bubble.is-active{background:#111827;border-color:#111827;color:#fff}
html[data-view="kalshi"] .pl-tabs{display:flex;gap:30px;margin:6px 0 14px;border-bottom:1px solid #e5e7eb}
html[data-view="kalshi"] .pl-tab{padding:12px 0;margin-bottom:-1px;border:0;border-bottom:2px solid transparent;background:none;color:#9ca3af;cursor:pointer;font:700 14px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;letter-spacing:.08em;text-transform:uppercase}
html[data-view="kalshi"] .pl-tab.is-active{color:#111827;border-bottom-color:#111827}
html[data-view="kalshi"] .pl-conf-row{display:flex;gap:20px;margin:4px 0 10px}
html[data-view="kalshi"] .pl-conf{padding:4px 0;border:0;background:none;color:#9ca3af;cursor:pointer;font:700 24px/1.1 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
html[data-view="kalshi"] .pl-conf.is-active{color:#111827}
html[data-view="kalshi"] .pl-team-pills{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 16px}
html[data-view="kalshi"] .pl-team-pill{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border:1px solid #d1d5db;border-radius:999px;background:#fff;color:#111827;cursor:pointer;font:700 14px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
html[data-view="kalshi"] .pl-team-pill img{width:22px;height:22px;object-fit:contain}
html[data-view="kalshi"] .pl-team-pill.is-active{border-color:#111827;box-shadow:inset 0 0 0 1px #111827}
html[data-view="kalshi"] .pl-players-panel{min-height:520px}
html[data-view="kalshi"] .pl-props-frame{display:block;width:100%;height:900px;border:0;background:#fff}
html[data-view="kalshi"] .pl-login-prompt{margin-top:24px;padding:36px 24px;border:1px solid #e5e7eb;border-radius:14px;text-align:center;color:#374151;font:500 16px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
html[data-view="kalshi"] .pl-login-prompt a{color:#1d4ed8;font-weight:700}
html[data-view="kalshi"] .pl-results-head{margin:0 0 4px;font:700 12px/1 ui-monospace,Menlo,monospace;letter-spacing:.12em;text-transform:uppercase;color:#6b7280}
html[data-view="kalshi"] .pl-results-title{margin:0 0 12px;font:800 20px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#111827}
html[data-view="kalshi"] .pl-results-more{display:inline-block;margin-top:12px;color:#1d4ed8;text-decoration:none;font:600 14px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
html[data-view="kalshi"] .pl-sport-results .picks-recent-grid{grid-template-columns:1fr}
@media(max-width:1100px){html[data-view="kalshi"] .pl-sport-grid{grid-template-columns:200px minmax(0,1fr)}html[data-view="kalshi"] .pl-sport-results{grid-column:1/-1;grid-row:auto;position:static;max-height:none}}
@media(max-width:760px){html[data-view="kalshi"] .pl-sport-grid{grid-template-columns:minmax(0,1fr)}html[data-view="kalshi"] .pl-sport-side{display:none}html[data-view="kalshi"] .pl-sport-main,html[data-view="kalshi"] .pl-sport-results{grid-column:1;grid-row:auto}}
</style>"""

LAYOUT_SCRIPT = r"""<script>
(function(){
  var conf = window.PL_SPORT_CONF || null;
  var east = {}, west = {};
  if (conf) {
    (conf.east || []).forEach(function(n){ east[n] = true; });
    (conf.west || []).forEach(function(n){ west[n] = true; });
  }
  var panel = document.querySelector('.pl-games-panel');
  var pillBox = document.querySelector('.pl-team-pills');
  var players = document.querySelector('.pl-players-panel');
  var frame = document.querySelector('.pl-props-frame');
  var cards = panel ? Array.prototype.slice.call(panel.querySelectorAll('[data-home][data-away]')) : [];
  var state = { conf: 'all', team: null };
  var teams = {}, order = [];

  function logoAbbr(src, name) {
    var m = /\/([a-z0-9]+)\.png/i.exec(src || '');
    return m ? m[1].toUpperCase() : name.split(' ').pop().slice(0, 3).toUpperCase();
  }

  cards.forEach(function(card){
    var imgs = Array.prototype.slice.call(card.querySelectorAll('img')).filter(function(i){
      return (i.getAttribute('src') || '').indexOf('/teamlogos/') !== -1;
    });
    [[card.dataset.away, imgs[0]], [card.dataset.home, imgs[1]]].forEach(function(pair){
      var name = pair[0], img = pair[1];
      if (!name) return;
      if (!teams[name]) { teams[name] = { name: name, src: img ? img.getAttribute('src') : '' }; order.push(name); }
      else if (!teams[name].src && img) { teams[name].src = img.getAttribute('src'); }
    });
  });

  if (pillBox && order.length) {
    order.sort(function(a, b){
      var x = logoAbbr(teams[a].src, a), y = logoAbbr(teams[b].src, b);
      return x < y ? -1 : (x > y ? 1 : 0);
    });
    order.forEach(function(name){
      var t = teams[name];
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'pl-team-pill';
      b.setAttribute('data-team', name);
      if (t.src) { var im = document.createElement('img'); im.src = t.src; im.alt = ''; b.appendChild(im); }
      var s = document.createElement('span');
      s.textContent = logoAbbr(t.src, name);
      b.appendChild(s);
      pillBox.appendChild(b);
    });
  }

  function apply() {
    cards.forEach(function(card){
      var h = card.dataset.home, a = card.dataset.away;
      var okConf = state.conf === 'all' || (state.conf === 'east' ? (east[h] || east[a]) : (west[h] || west[a]));
      var okTeam = !state.team || h === state.team || a === state.team;
      card.style.display = (okConf && okTeam) ? '' : 'none';
    });
    document.querySelectorAll('.pl-conf').forEach(function(b){
      b.classList.toggle('is-active', b.getAttribute('data-pl-conf') === state.conf);
    });
    document.querySelectorAll('.pl-team-pill').forEach(function(b){
      b.classList.toggle('is-active', b.getAttribute('data-team') === state.team);
    });
  }

  document.querySelectorAll('.pl-conf').forEach(function(b){
    b.addEventListener('click', function(){ state.conf = b.getAttribute('data-pl-conf'); apply(); });
  });
  if (pillBox) {
    pillBox.addEventListener('click', function(e){
      var b = e.target.closest ? e.target.closest('.pl-team-pill') : null;
      if (!b) return;
      var t = b.getAttribute('data-team');
      state.team = state.team === t ? null : t;
      apply();
    });
  }

  var tabs = document.querySelectorAll('.pl-tab');
  tabs.forEach(function(t){
    t.addEventListener('click', function(){
      var mode = t.getAttribute('data-pl-tab');
      tabs.forEach(function(x){ x.classList.toggle('is-active', x === t); });
      if (panel) panel.hidden = mode !== 'games';
      if (players) players.hidden = mode !== 'players';
      if (mode === 'players' && frame && !frame.getAttribute('src')) {
        frame.setAttribute('src', frame.getAttribute('data-src'));
      }
    });
  });

  if (frame) {
    frame.addEventListener('load', function(){
      try {
        var d = frame.contentDocument;
        if (!d) return;
        ['header', 'footer', '.pl2-header', '.site-directory-footer', '.join-premium-bar', '.share-strip', '.pl-view-bar']
          .forEach(function(sel){
            d.querySelectorAll(sel).forEach(function(el){ el.style.display = 'none'; });
          });
        frame.style.height = Math.max(640, d.documentElement.scrollHeight) + 'px';
      } catch (err) {}
    });
  }

  apply();
})();
</script>"""


def _take_results(html):
    """Lift the page's own picks-recent-results section out, keeping nested sections intact."""
    m = _RESULTS_START.search(html)
    if not m:
        return html, ''
    depth = 0
    for tok in _SECTION_TAG.finditer(html, m.start()):
        depth += -1 if tok.group(1) else 1
        if depth == 0:
            end = html.find('>', tok.end())
            if end < 0:
                break
            return html[:m.start()] + html[end + 1:], html[m.start():end + 1]
    return html, ''


def _open(sports, active, conf):
    bubbles = ''.join(
        '<a class="pl-bubble%s" href="/%s">%s</a>' % (
            ' is-active' if s['key'] == active else '', escape(s['slug']), escape(s['short']))
        for s in sports)
    side = ''.join(
        '<a class="pl-side-link%s" href="/%s"><span class="pl-side-icon">%s</span><span>%s</span>%s</a>' % (
            ' is-active' if s['key'] == active else '', escape(s['slug']), escape(s['icon']),
            escape(s['short']), '<span class="pl-side-live">Live</span>' if s['live'] else '')
        for s in sports)
    conf_row = ''
    if conf:
        conf_row = ('<div class="pl-conf-row kalshi-only">'
                    '<button type="button" class="pl-conf is-active" data-pl-conf="all">All</button>'
                    '<button type="button" class="pl-conf" data-pl-conf="east">East</button>'
                    '<button type="button" class="pl-conf" data-pl-conf="west">West</button>'
                    '</div>')
    return (
        '<script>window.PL_SPORT_CONF=' + json.dumps(conf) + ';</script>'
        '<div class="pl-view-bar"><a class="kalshi-only" href="?view=classic">Classic view</a>'
        '<a class="classic-only" href="?view=kalshi">New layout</a></div>'
        '<section class="pl-sport-grid">'
        '<aside class="pl-sport-side kalshi-only"><div class="pl-side-head">Sports</div>' + side + '</aside>'
        '<section class="pl-sport-main">'
        '<nav class="pl-bubbles kalshi-only" aria-label="Sports">' + bubbles + '</nav>'
        '<div class="pl-tabs kalshi-only" role="tablist">'
        '<button type="button" class="pl-tab is-active" data-pl-tab="games">Games</button>'
        '<button type="button" class="pl-tab" data-pl-tab="players">Players</button>'
        '</div>'
        '<section class="pl-games-panel">'
        + conf_row +
        '<div class="pl-team-pills kalshi-only"></div>'
    )


def _players(sport_key, logged_in):
    if logged_in:
        body = ('<iframe class="pl-props-frame" title="Player props" data-src="/player-props?sport='
                + escape(sport_key) + '"></iframe>')
    else:
        body = ('<div class="pl-login-prompt">Player props are for members. '
                '<a href="/login">Log in</a> to see them, or <a href="/plans">see plans</a>.</div>')
    return '<section class="pl-players-panel kalshi-only" hidden>' + body + '</section>'


def _close(results, name, slug, players):
    return (
        '</section>'
        + players +
        '</section>'
        '<aside class="pl-sport-results kalshi-only">'
        '<div class="pl-results-head">Results</div>'
        '<div class="pl-results-title">' + escape(name) + ' results</div>'
        + results +
        '<a class="pl-results-more" href="/' + escape(slug) + '">Full ' + escape(name) + ' results &rarr;</a>'
        '</aside></section>'
        + LAYOUT_SCRIPT
    )


def apply(html, *, sport_key, sports, results_block, results_name, results_slug, logged_in=False):
    """Return the page with the new sports layout around its own content."""
    if not html or not results_name:
        return html
    html, taken = _take_results(html)
    results = taken or results_block or ''
    if not results:
        results = '<p class="pl-results-empty">Results appear here once games are graded.</p>'
    head = _HEAD_END.search(html)
    if head:
        html = html[:head.start()] + STYLE + VIEW_SCRIPT + html[head.start():]
    body = _BODY_START.search(html)
    header = _HEADER_END.search(html, body.end() if body else 0)
    footer = _FOOTER_START.search(html)
    if not header or not footer or footer.start() < header.end():
        return html
    pair = CONFERENCES.get(sport_key)
    conf = {'east': pair[0], 'west': pair[1]} if pair else None
    return (
        html[:header.end()]
        + _open(sports, sport_key, conf)
        + html[header.end():footer.start()]
        + _close(results, results_name, results_slug, _players(sport_key, logged_in))
        + html[footer.start():]
    )
