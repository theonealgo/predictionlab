#!/usr/bin/env python3
"""Logged-in admin audit for AUDIT_LOGGED_IN.md generation."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    for _p in (ROOT / '.env.local', ROOT / '.env'):
        if _p.exists():
            load_dotenv(_p, override=True)
            break
except ImportError:
    pass

SPORTS = ['NHL', 'NBA', 'NFL', 'MLB', 'NCAAB', 'NCAAF', 'WNBA', 'NCAAW', 'SOCCER']
ADMIN_EMAIL = os.environ.get('AUDIT_ADMIN_EMAIL', 'nmesghali@gmail.com').strip().lower()
ADMIN_PASSWORD = (os.environ.get('ADMIN_PASSWORD') or '').strip()

PICKS_EXTRA = {
    'NBA': '/nba-picks', 'NHL': '/nhl-picks', 'NFL': '/nfl-picks', 'MLB': '/mlb-picks',
    'NCAAB': '/ncaab-picks', 'NCAAF': '/ncaaf-picks', 'WNBA': '/wnba-picks',
    'NCAAW': '/ncaaw-picks', 'SOCCER': '/soccer-picks',
}
CROSS = [
    ('/', 'home'),
    ('/all-sports-results', 'all-sports-results'),
    ('/team-efficiency-results', 'team-efficiency-results'),
    ('/plans', 'plans'),
    ('/login', 'login-get'),
]
FOOTER_LINKS = [
    '/nba-picks', '/nba-results', '/all-sports-results', '/performance', '/tutorial',
]
MODEL_LABELS = ['Grinder2', 'Takedown', 'Edge', 'XSharp', 'Sharp Consensus', 'Team Efficiency']
DEBUG_SNIPPETS = [
    'DEBUG', 'traceback', 'TODO: remove', 'fixture only', 'lorem ipsum',
]


def _login(client) -> dict:
    if not ADMIN_PASSWORD:
        return {'ok': False, 'reason': 'ADMIN_PASSWORD not set'}
    r = client.post(
        '/login',
        data={'email': ADMIN_EMAIL, 'password': ADMIN_PASSWORD},
        headers={'Host': '127.0.0.1'},
        follow_redirects=False,
    )
    ok = r.status_code in (302, 303)
    probe = client.get('/', headers={'Host': '127.0.0.1'}).get_data(as_text=True)
    signed_in = 'Sign Out' in probe or 'Log out' in probe
    return {
        'ok': ok and signed_in,
        'status': r.status_code,
        'signed_in_nav': signed_in,
        'redirect': r.headers.get('Location'),
    }


def _title(html: str) -> str:
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    return (m.group(1).strip() if m else '')


def _count_cards(html: str) -> int:
    return html.count('class="game-card pick-card"')


def _check_picks(html: str, sport: str) -> list[str]:
    issues = []
    if 'premium-locked' in html or 'Unlock Premium' in html:
        issues.append('premium-gated content visible')
    if '>TBD<' in html or ' pick-tbd' in html:
        issues.append('TBD on card')
    if _count_cards(html) > 0:
        for lbl in MODEL_LABELS:
            if lbl not in html and 'efficiency' not in lbl.lower():
                if lbl == 'Team Efficiency' and 'efficiency_prob' not in html and 'Team Efficiency' not in html:
                    issues.append(f'missing model row: {lbl}')
                elif lbl != 'Team Efficiency' and lbl not in html:
                    issues.append(f'missing model label: {lbl}')
        if 'Team Efficiency' not in html and 'efficiency' not in html.lower():
            issues.append('missing Efficiency model row')
    return issues


def _check_results(html: str, sport: str) -> list[str]:
    issues = []
    title = _title(html)
    if title and 'Results' not in title and 'Predictions' in title:
        issues.append(f'page title says Predictions not Results: {title!r}')
    if 'debug' in html.lower() and 'debug-banner' not in html:
        if re.search(r'\bdebug\b.*\b(copy|mode|dump)\b', html, re.I):
            issues.append('debug copy in body')
    for snip in DEBUG_SNIPPETS:
        if snip in html:
            issues.append(f'debug snippet: {snip}')
    if sport == 'SOCCER':
        for league, min_games in (('EFL', 2), ('UCL', 2), ('eng.1', 2)):
            if league in html:
                # weekly tally: look for "1 game" near league name
                if re.search(rf'{re.escape(league)}[^<]{{0,200}}1\s+game', html, re.I):
                    issues.append(f'{league} weekly tally shows only 1 game')
    # Grinder2 full season — expect graded count > 20 when season block present
    if 'Season Performance' in html or 'season-block' in html:
        m = re.search(r'Grinder2[^0-9]*(\d+)\s*[-–]\s*(\d+)', html)
        if m:
            w, l = int(m.group(1)), int(m.group(2))
            if w + l < 15:
                issues.append(f'Grinder2 season count low: {w}-{l}')
    # ROI headline = win rate
    if 'ROI' in html and 'win rate' not in html.lower() and 'Win Rate' not in html:
        if re.search(r'class="[^"]*roi[^"]*"', html, re.I) and 'accuracy' not in html.lower():
            issues.append('ROI card may lack win-rate headline')
    return issues


def main() -> int:
    import NHL77FINAL as N
    from NHL77FINAL import app

    N._SPORT_PREDICTIONS_PAGE_CACHE.clear()
    results = {'auth': {}, 'routes': [], 'issues_fixed': [], 'issues_open': []}

    with app.test_client() as c:
        auth = _login(c)
        results['auth'] = {**auth, 'email': ADMIN_EMAIL}
        if not auth.get('ok'):
            print(json.dumps(results, indent=2))
            return 1

        # premium flag via session page
        pr = c.get('/plans', headers={'Host': '127.0.0.1'})
        pb = pr.get_data(as_text=True)
        results['auth']['plans_status'] = pr.status_code
        results['auth']['plans_premium_hint'] = (
            'premium' in pb.lower() or 'subscription' in pb.lower() or pr.status_code == 200
        )

        for path, name in CROSS:
            r = c.get(path, headers={'Host': '127.0.0.1'})
            html = r.get_data(as_text=True)
            issues = []
            if path == '/' and '>TBD<' in html:
                issues.append('TBD on home value picks')
            if name.endswith('results') or 'results' in path:
                issues.extend(_check_results(html, ''))
            row = {
                'url': path, 'name': name, 'status': r.status_code,
                'pass': r.status_code == 200 and not issues, 'issues': issues,
            }
            results['routes'].append(row)

        for link in FOOTER_LINKS:
            r = c.get(link, headers={'Host': '127.0.0.1'})
            results['routes'].append({
                'url': link, 'name': f'footer:{link}',
                'status': r.status_code,
                'pass': r.status_code == 200,
                'issues': [] if r.status_code == 200 else [f'HTTP {r.status_code}'],
            })

        for sport in SPORTS:
            picks_slug = N.SPORT_SEO_SLUGS.get(sport)
            results_slug = N._SPORT_RESULTS_SLUGS.get(sport)
            for kind, slug in (('picks', picks_slug), ('results', results_slug)):
                url = f'/{slug}'
                r = c.get(url, headers={'Host': '127.0.0.1'})
                html = r.get_data(as_text=True)
                issues = []
                if r.status_code != 200:
                    issues.append(f'HTTP {r.status_code}')
                if kind == 'picks':
                    issues.extend(_check_picks(html, sport))
                    cards = _count_cards(html)
                else:
                    issues.extend(_check_results(html, sport))
                    cards = None
                results['routes'].append({
                    'url': url, 'name': f'{sport}-{kind}', 'status': r.status_code,
                    'pass': r.status_code == 200 and not issues,
                    'issues': issues, 'cards': cards,
                    'title': _title(html) if kind == 'results' else None,
                })

        # Soccer league pages
        for q in ('?league=eng.1', '?league=uefa.champions'):
            r = c.get(f'/soccer-results{q}', headers={'Host': '127.0.0.1'})
            html = r.get_data(as_text=True)
            issues = _check_results(html, 'SOCCER')
            results['routes'].append({
                'url': f'/soccer-results{q}', 'name': f'soccer-results{q}',
                'status': r.status_code,
                'pass': r.status_code == 200 and not issues,
                'issues': issues,
            })

        # Spread consistency on upcoming data
        try:
            for sport in SPORTS:
                preds = N.get_upcoming_predictions(sport, days=7) or []
                upcoming = [p for p in preds if p.get('home_score') is None]
                for p in upcoming[:5]:
                    N._refresh_books_on_predictions(sport, [p])
                    N._finalize_prediction_odds(p)
                    N._enforce_pick_spread_consistency(p, sport=sport)
                    N._prepare_pred_card_display(p, sport=sport)
                    sp = p.get('our_spread') or p.get('disp_pl_spread')
                    pw = p.get('predicted_winner') or p.get('face_pick_team')
                    home, away = p.get('home_team_id'), p.get('away_team_id')
                    if sp is not None and pw and home and away:
                        spf = float(sp)
                        if pw == home and spf < 0:
                            results['issues_open'].append(
                                f'{sport} PL spread flipped vs home pick (data pipeline)'
                            )
                        if pw == away and spf > 0:
                            results['issues_open'].append(
                                f'{sport} PL spread flipped vs away pick (data pipeline)'
                            )
        except Exception as e:
            results['issues_open'].append(f'pipeline check error: {e}')

    pass_n = sum(1 for r in results['routes'] if r['pass'])
    fail_n = sum(1 for r in results['routes'] if not r['pass'])
    results['summary'] = {'pass': pass_n, 'fail': fail_n, 'total': pass_n + fail_n}
    out_path = ROOT / 'audit_results_logged_in.json'
    out_path.write_text(json.dumps(results, indent=2), encoding='utf-8')
    print(f'Wrote {out_path} pass={pass_n} fail={fail_n}', file=sys.stderr)
    return 0 if fail_n == 0 and results['auth'].get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
