#!/usr/bin/env python3
"""Exit 0 if picks pages render (not fallback). Run before/after Render deploy."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from NHL77FINAL import app

MIN_BYTES = 50_000
PATHS = ('/nba-picks', '/nhl-picks', '/mlb-picks', '/healthz/picks-template')
FAIL_SNIPPET = 'refreshing this page right now'


def main() -> int:
    ok = True
    with app.test_client() as client:
        for path in PATHS:
            r = client.get(path, headers={'Host': '127.0.0.1'})
            body = r.get_data(as_text=True)
            if path == '/healthz/picks-template':
                try:
                    good = r.status_code == 200 and bool(r.get_json().get('ok'))
                except Exception:
                    good = False
            else:
                good = (
                    r.status_code == 200
                    and len(body) >= MIN_BYTES
                    and FAIL_SNIPPET not in body.lower()
                )
            status = 'OK' if good else 'FAIL'
            print(f'{status} {path} status={r.status_code} bytes={len(body)}')
            if not good:
                ok = False
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
