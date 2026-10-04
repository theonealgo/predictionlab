#!/usr/bin/env python3
"""
Backfill missing betting_lines.total (and spread) for completed games via ESPN Core.

Run on Render (one-off job or locally against /data DB):
  python3 scripts/backfill_betting_line_totals.py --sport NBA --limit 200
  python3 scripts/backfill_betting_line_totals.py --sport ALL --limit 500 --sleep 0.25

Safe to re-run: only fetches games with no non-null total in betting_lines (by game_id).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPORTS = ('NBA', 'NHL', 'MLB', 'NFL', 'NCAAB', 'NCAAF', 'WNBA')


def _db_path() -> str:
    import os

    data = '/data/sports_predictions_original.db'
    if os.path.isfile(data):
        return data
    return str(ROOT / 'sports_predictions_original.db')


def _games_missing_totals(conn: sqlite3.Connection, sport: str, limit: int):
    sql = """
        SELECT g.sport, g.game_id, g.game_date, g.home_team_id, g.away_team_id
        FROM games g
        WHERE g.sport = ?
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM betting_lines bl
              WHERE bl.sport = g.sport
                AND bl.game_id = g.game_id
                AND bl.total IS NOT NULL
          )
        ORDER BY g.game_date DESC
        LIMIT ?
    """
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, (sport, limit)).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description='Backfill betting_lines totals from ESPN Core')
    ap.add_argument('--sport', default='NBA', help='Sport code or ALL')
    ap.add_argument('--limit', type=int, default=200, help='Max games per sport')
    ap.add_argument('--sleep', type=float, default=0.2, help='Seconds between ESPN calls')
    ap.add_argument('--dry-run', action='store_true', help='Print targets only')
    args = ap.parse_args()

    sports = SPORTS if args.sport.upper() == 'ALL' else (args.sport.upper(),)

    import NHL77FINAL as N
    from pl_book_odds_api import build_pl_book_odds

    db = _db_path()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    total_ok = total_miss = 0

    for sport in sports:
        rows = _games_missing_totals(conn, sport, args.limit)
        print(f'[{sport}] {len(rows)} completed games missing betting_lines.total')
        for row in rows:
            gid = row['game_id']
            gd = (row['game_date'] or '')[:10]
            home = row['home_team_id']
            away = row['away_team_id']
            if args.dry_run:
                print(f'  would fetch {gid} {gd} {away} @ {home}')
                continue
            api_row = build_pl_book_odds(sport, gid, home, away, gd)
            if not api_row or api_row.get('total') is None:
                total_miss += 1
                if args.sleep:
                    time.sleep(args.sleep)
                continue
            g = {
                'game_id': gid,
                'date': gd,
                'home': home,
                'away': away,
            }
            N._persist_pl_book_row(sport, g, api_row)
            total_ok += 1
            print(f'  ok {gid} total={api_row.get("total")} spread={api_row.get("spread")}')
            if args.sleep:
                time.sleep(args.sleep)

    conn.close()
    print(f'Done: persisted={total_ok} no_line={total_miss} dry_run={args.dry_run}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
