#!/usr/bin/env python3
"""
Spot-check PL book odds API against ESPN Core providers and optional manual DK lines.

We do not call DraftKings directly. ESPN often syndicates DraftKings; when you open
the DK app, paste lines into data/manual_dk_lines.csv for true book-vs-API tests.

Usage:
  python3 scripts/compare_pl_book_odds.py --sport NBA --count 10
  python3 scripts/compare_pl_book_odds.py --sport ALL --count 20 --days 3
  python3 scripts/compare_pl_book_odds.py --game-id NBA_401873342
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pl_book_odds_api import (  # noqa: E402
    _espn_event_id,
    build_pl_book_odds,
    diff_book_lines,
    fetch_all_core_providers,
)

DB_PATH = ROOT / 'sports_predictions_original.db'
MANUAL_CSV = ROOT / 'data' / 'manual_dk_lines.csv'


def _load_manual_dk() -> dict[str, dict]:
    if not MANUAL_CSV.is_file():
        return {}
    out = {}
    with MANUAL_CSV.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            gid = (row.get('game_id') or '').strip()
            if not gid:
                continue
            try:
                out[gid] = {
                    'spread': float(row['dk_spread']),
                    'total': float(row['dk_total']),
                    'home_moneyline': int(row['dk_home_ml']),
                    'away_moneyline': int(row['dk_away_ml']),
                    'notes': row.get('notes', ''),
                }
            except (KeyError, TypeError, ValueError):
                continue
    return out


def _sample_games(
    sport: str | None,
    count: int,
    days: int,
    game_id: str | None,
) -> list[tuple]:
    conn = sqlite3.connect(DB_PATH)
    if game_id:
        row = conn.execute(
            """
            SELECT sport, game_id, home_team, away_team, game_date
            FROM betting_lines WHERE game_id = ? LIMIT 1
            """,
            (game_id,),
        ).fetchone()
        conn.close()
        return [row] if row else []

    where = ["game_id LIKE '%_401%'"]
    params: list = []
    if sport and sport.upper() != 'ALL':
        where.append('sport = ?')
        params.append(sport.upper())
    if days:
        where.append("date(game_date) BETWEEN date('now', ?) AND date('now', ?)")
        params.extend([f'-{days} day', f'+{days} day'])

    sql = f"""
        SELECT sport, game_id, home_team, away_team, game_date
        FROM betting_lines
        WHERE {' AND '.join(where)}
        ORDER BY RANDOM()
        LIMIT ?
    """
    params.append(count)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def _fmt_ml(v) -> str:
    if v is None:
        return '—'
    return f'{int(v):+d}'


def _print_game(
    sport: str,
    game_id: str,
    home: str,
    away: str,
    manual: dict,
) -> dict:
    pl = build_pl_book_odds(sport, game_id, home, away)
    eid = _espn_event_id(game_id)
    providers = fetch_all_core_providers(sport, eid) if eid else []

    print(f"\n{'=' * 72}")
    print(f"{sport}  {away} @ {home}")
    print(f"game_id: {game_id}")

    stats = {'has_pl': bool(pl), 'exact_dk': False, 'manual': False}

    if not pl:
        print('  PL API: no ESPN odds')
        return stats

    print(
        f"  PL API ({pl['provider']}):  spread {pl['spread']:g}  total {pl['total']:g}  "
        f"ML { _fmt_ml(pl['away_moneyline']) } / { _fmt_ml(pl['home_moneyline']) }  "
        f"fav: {pl.get('favorite_team')}"
    )

    for p in providers:
        name = p['provider']
        d = diff_book_lines(pl, p)
        flags = []
        for k, lim in (
            ('spread_delta', 0.01),
            ('total_delta', 0.01),
            ('home_ml_delta', 0),
            ('away_ml_delta', 0),
        ):
            v = d.get(k)
            if v is not None and abs(v) > lim:
                flags.append(f"{k}={v:+.2g}")
        tag = 'MATCH' if not flags else 'DIFF ' + ', '.join(flags)
        if name.lower() == 'draftkings' and not flags:
            stats['exact_dk'] = True
        print(
            f"  ESPN {name}: spread {p['spread']:g}  total {p['total']:g}  "
            f"ML {_fmt_ml(p['away_moneyline'])} / {_fmt_ml(p['home_moneyline'])}  [{tag}]"
        )

    m = manual.get(game_id)
    if m:
        stats['manual'] = True
        d = diff_book_lines(pl, m)
        print(
            f"  YOUR DK APP: spread {m['spread']:g}  total {m['total']:g}  "
            f"ML {_fmt_ml(m['away_moneyline'])} / {_fmt_ml(m['home_moneyline'])}"
        )
        print(
            f"    vs PL API: spread Δ{d.get('spread_delta')}  total Δ{d.get('total_delta')}  "
            f"home ML Δ{d.get('home_ml_delta')}  away ML Δ{d.get('away_ml_delta')}"
            + (f"  ({m.get('notes')})" if m.get('notes') else '')
        )

    if not providers and pl:
        print('  (only one ESPN provider returned for this event)')

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description='Compare PL book odds to ESPN / manual DK')
    ap.add_argument('--sport', default='NBA', help='NBA, MLB, NHL, NFL, NCAAB, NCAAF, or ALL')
    ap.add_argument('--count', type=int, default=10, help='Random games to sample')
    ap.add_argument('--days', type=int, default=14, help='Date window around today')
    ap.add_argument('--game-id', help='Single game_id (e.g. NBA_401873342)')
    args = ap.parse_args()

    manual = _load_manual_dk()
    if not MANUAL_CSV.is_file():
        print(f"Tip: paste DraftKings app lines into {MANUAL_CSV} for real book checks.")
        print('      Copy data/manual_dk_lines.csv.example → manual_dk_lines.csv\n')

    rows = _sample_games(args.sport, args.count, args.days, args.game_id)
    if not rows:
        print('No games found for sample.')
        return 1

    n = exact = manual_n = 0
    for sport, gid, home, away, _gd in rows:
        st = _print_game(sport, gid, home, away, manual)
        n += 1
        if st.get('exact_dk'):
            exact += 1
        if st.get('manual'):
            manual_n += 1

    print(f"\n{'=' * 72}")
    print(f"Sampled {n} games with ESPN event IDs.")
    print(f"Exact match vs ESPN DraftKings (pregame): {exact}/{n}")
    if manual:
        print(f"Games with your manual DK file: {manual_n}/{len(manual)} rows in CSV")
    print(
        '\nInterpretation: PL API uses ESPN Core (DraftKings when listed). '
        'Differences vs your DK app = line moved or ESPN lag; use manual CSV for ground truth.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
