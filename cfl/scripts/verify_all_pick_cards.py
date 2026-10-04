#!/usr/bin/env python3
"""Verify every sport picks page: HTTP 200, game-cards, visible default date, no error banners."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPORTS_SLUGS = [
    ('NBA', '/nba-picks'),
    ('MLB', '/mlb-picks'),
    ('NHL', '/nhl-picks'),
    ('NFL', '/nfl-picks'),
    ('NCAAB', '/ncaab-picks'),
    ('NCAAF', '/ncaaf-picks'),
    ('WNBA', '/wnba-picks'),
    ('NCAAW', '/ncaaw-picks'),
    ('SOCCER', '/soccer-picks'),
]


def check(path: str, body: str) -> dict:
    low = body.lower()
    cards = body.count('class="game-card"')
    visible = len(re.findall(r'class="date-section visible"', body))
    hidden_only = cards > 0 and visible == 0 and 'date-section seo-hidden' in body
    m = re.search(r"const defaultPickDate = '([^']*)'", body)
    default_date = m.group(1) if m else None
    cards_on_default = 0
    if default_date and f'id="date-{default_date}"' in body:
        idx = body.find(f'id="date-{default_date}"')
        chunk = body[idx : idx + 80000]
        cards_on_default = chunk.count('class="game-card"')
    return {
        'bytes': len(body),
        'cards': cards,
        'visible_sections': visible,
        'hidden_only': hidden_only,
        'default_date': default_date,
        'cards_on_default': cards_on_default,
        'no_data': 'no predictions available' in low,
        'upstream_err': 'upstream data/model dependency failed' in low,
        'fallback': 'refreshing this page right now' in low,
        'offseason': 'return when the season' in low or 'return when the' in low,
    }


def main():
    from NHL77FINAL import app

    fails = []
    print(f'{"Sport":<8} {"Status":>6} {"Cards":>6} {"Vis":>4} {"DefCards":>8} {"Issue"}')
    print('-' * 72)
    with app.test_client() as client:
        for sport, path in SPORTS_SLUGS:
            r = client.get(path, headers={'Host': '127.0.0.1'})
            body = r.get_data(as_text=True)
            c = check(path, body)
            issue = []
            if r.status_code != 200:
                issue.append(f'HTTP {r.status_code}')
            if c['fallback']:
                issue.append('RENDER_FALLBACK')
            if c['upstream_err']:
                issue.append('UPSTREAM_FAIL')
            if c['no_data'] and not c['offseason']:
                issue.append('NO_DATA')
            if c['cards'] == 0 and not c['offseason']:
                issue.append('ZERO_CARDS')
            if c['hidden_only']:
                issue.append('ALL_HIDDEN')
            if c['cards'] > 0 and c['cards_on_default'] == 0 and not c['offseason']:
                issue.append('DEFAULT_DATE_EMPTY')
            iss = ','.join(issue) or ('OFFSEASON' if c['offseason'] and c['cards'] == 0 else 'OK')
            print(
                f'{sport:<8} {r.status_code:>6} {c["cards"]:>6} {c["visible_sections"]:>4} '
                f'{c["cards_on_default"]:>8} {iss}'
            )
            if issue:
                fails.append((sport, issue))
    print()
    if fails:
        print('FAILED:', fails)
        return 1
    print('All active sports rendered pick cards.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
