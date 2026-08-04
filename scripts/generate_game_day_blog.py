#!/usr/bin/env python3
"""Generate game-day Prediction Lab blog previews (LOCAL).

Pulls today's ESPN scoreboards for gated sports (default: MLB, WNBA, UFC on
event days) and writes one preview post per game into data/blog_posts.json.

Google Trends blogging is permanently disabled — this script never touches Trends.

Usage (from predictionlabfix_work):
  python3 scripts/generate_game_day_blog.py
  python3 scripts/generate_game_day_blog.py --dry-run

Live site: owner Manual Deploy after reviewing local output. Agents never push.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print titles only; do not write data/blog_posts.json',
    )
    parser.add_argument(
        '--sports',
        default='',
        help='Comma list override (must still be in _BLOG_GAME_DAY_SPORTS), e.g. MLB,WNBA',
    )
    args = parser.parse_args()

    import NHL77FINAL as N  # noqa: WPS433

    sports = None
    if args.sports.strip():
        sports = [s.strip().upper() for s in args.sports.split(',') if s.strip()]

    if args.dry_run:
        posts = N._generate_game_day_blog_posts(sports=sports)
        print(f'dry-run: {len(posts)} game-day preview(s)')
        for p in posts:
            print(f"  [{p.get('sport_tag')}] {p.get('title')}")
        return 0

    # Clear blog cache so rebuild reads disk fresh
    if hasattr(N, '_BLOG_CACHE'):
        N._BLOG_CACHE.update({'ts': 0, 'posts': []})

    if sports:
        today = N._blog_today_et()
        today_str = today.strftime('%Y-%m-%d')
        existing = [p for p in N._load_blog_posts_from_json() if not N._is_google_trends_blog_spam(p)]
        by_slug = {p['slug']: p for p in existing}
        for post in N._generate_game_day_blog_posts(today=today, sports=sports):
            by_slug[post['slug']] = post
        by_slug = N._prune_stale_auto_blog_posts(by_slug, keep_date=today_str)
        posts = list(by_slug.values())
        posts.sort(key=N._blog_date_key, reverse=True)
        N._persist_blog_posts_to_json(posts)
    else:
        posts = N._rebuild_game_day_blog_archive(persist=True)

    print(f'wrote {len(posts)} post(s) → data/blog_posts.json')
    for p in posts[:20]:
        print(f"  [{p.get('sport_tag')}] {p.get('title')}")
    if len(posts) > 20:
        print(f'  … +{len(posts) - 20} more')
    print('Trends blogging remains OFF. Live needs owner Manual Deploy — do not git push.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
