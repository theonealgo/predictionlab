#!/usr/bin/env python3
"""Compute season results stats for all 12 sports (for review before deploy).

Usage:
  PL_SNAPSHOT_BUILD=1 python3 scripts/compute_all_sport_season_stats.py
  PL_SNAPSHOT_BUILD=1 python3 scripts/compute_all_sport_season_stats.py --sport MLB
  PL_SNAPSHOT_BUILD=1 python3 scripts/compute_all_sport_season_stats.py --write-snapshots
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('PL_SNAPSHOT_BUILD', '1')
os.environ.pop('PL_SKIP_V2_FOR_RESULTS', None)

SPORTS = [
    'NHL', 'NBA', 'MLB', 'NFL', 'NCAAB', 'NCAAW', 'NCAAF', 'WNBA', 'SOCCER',
    'TENNIS', 'UFC', 'GOLF',
]
INDIVIDUAL_SPORTS = {'TENNIS', 'UFC', 'GOLF'}


def _season_label(N, sport, ref_dt):
    if sport == 'NHL':
        return N._nhl_season_label(ref_dt)
    start, _ = N._results_season_bounds(sport, ref_dt)
    if not start:
        return str(ref_dt.year)
    return f'{start.year}-{str(start.year + 1)[-2:]}'


def compute_sport(N, sport: str) -> dict:
    ref_dt = datetime.now()
    yesterday = ref_dt - timedelta(days=1)
    start_dt, end_dt = N._results_season_bounds(sport, yesterday)
    end_eff = min(end_dt, yesterday) if end_dt else yesterday

    if sport in INDIVIDUAL_SPORTS:
        daily = N._banner_daily_results_for_range(sport, start_dt, end_eff)
        if not daily or not N._daily_results_game_count(daily):
            season = _season_label(N, sport, ref_dt)
            empty_overall = N._normalize_overall_stats({})
            return {
                'sport': sport,
                'season': season,
                'phase': 'regular',
                'window': {
                    'start': start_dt.strftime('%Y-%m-%d') if start_dt else None,
                    'end': end_eff.strftime('%Y-%m-%d') if end_eff else None,
                },
                'games_in_scope': 0,
                'games_expected': None,
                'overall_stats': empty_overall,
                'spread_total_stats': {},
                'season_perf': N._build_season_performance_summary(
                    empty_overall, {},
                    scope_label=f'{N.SPORTS[sport]["name"]} season',
                    games_expected=None,
                    games_in_scope=0,
                ),
                'ou_summary': {},
                'roi_total': None,
            }
        overall = N.compute_overall_stats_from_daily(daily)
        st = N._compute_spread_total_for_daily(sport, daily, skip_efficiency=True)
        games_in_scope = N._daily_results_game_count(daily)
        season = _season_label(N, sport, ref_dt)
        season_perf = N._build_season_performance_summary(
            overall, st,
            scope_label=f'{N.SPORTS[sport]["name"]} season',
            games_expected=None,
            games_in_scope=games_in_scope,
        )
        return {
            'sport': sport,
            'season': season,
            'phase': 'regular',
            'window': {
                'start': start_dt.strftime('%Y-%m-%d') if start_dt else None,
                'end': end_eff.strftime('%Y-%m-%d') if end_eff else None,
            },
            'games_in_scope': games_in_scope,
            'games_expected': None,
            'overall_stats': overall,
            'spread_total_stats': st,
            'season_perf': season_perf,
            'ou_summary': {},
            'roi_total': None,
        }

    if sport == 'SOCCER':
        conn = N.get_db_connection()
        games = N._fetch_soccer_completed_games(conn, None, limit=5000)
        conn.close()
        if not games:
            return {'sport': sport, 'error': 'no soccer games'}
        from collections import defaultdict
        daily = defaultdict(lambda: {'games': []})
        bundle = N._get_soccer_model_bundle(games, None)
        for game in N._sort_game_rows_by_date_desc(games):
            gd = N._normalize_game_date_key(game['game_date'])
            if start_dt and end_eff and not N._date_in_range(gd, start_dt, end_eff):
                continue
            try:
                hs = N._to_float_safe(game['home_score'])
                aw = N._to_float_safe(game['away_score'])
                if hs is None or aw is None:
                    continue
                home_won = hs > aw
                is_draw = abs(hs - aw) < 1e-9
                if is_draw:
                    home_won = None
                ht, at = game['home_team_id'], game['away_team_id']
                gd = N._normalize_game_date_key(game['game_date'])
                lg = N._canonical_soccer_league_name(N._row_field(game, 'league')) or N._row_field(game, 'league')
                if not lg or lg not in N.SOCCER_LEAGUE_ORDER:
                    continue
                g2 = ts = el = xg = ens = None
                sp = bundle.predict(ht, at) if bundle and getattr(bundle, 'ready', False) else None
                if sp:
                    g2 = sp.get('poisson_xg_prob')
                    ts = sp.get('markov_prob')
                    el = sp.get('elo_prob')
                    xg = sp.get('poisson_reg_prob')
                    ens = sp.get('ensemble_prob')
                gi = {
                    'game_id': game['game_id'], 'date': gd, 'home': ht, 'away': at,
                    'league': lg, 'home_score': hs, 'away_score': aw,
                    'home_win': home_won, 'is_draw': is_draw,
                    'glicko2_prob': round(g2 * 100, 1) if g2 else None,
                    'trueskill_prob': round(ts * 100, 1) if ts else None,
                    'elo_prob': round(el * 100, 1) if el else None,
                    'xgb_prob': round(xg * 100, 1) if xg else None,
                    'ens_prob': round(ens * 100, 1) if ens else None,
                }
                N._apply_soccer_ml_grading(
                    gi, draw_dec=sp.get('draw_prob') if sp else None,
                    glicko2_prob=g2, trueskill_prob=ts, elo_prob=el,
                    xgb_prob=xg, ens_prob=ens, home_won=home_won, is_draw=is_draw,
                )
                daily[gd]['games'].append(gi)
            except Exception:
                continue
    else:
        daily = N._banner_daily_results_for_range(
            sport, start_dt, end_eff, playoffs=False, skip_v2=False,
        )

    if not daily or not N._daily_results_game_count(daily):
        return {'sport': sport, 'error': 'no games in season window', 'window': (
            start_dt.strftime('%Y-%m-%d') if start_dt else None,
            end_eff.strftime('%Y-%m-%d') if end_eff else None,
        )}

    N._attach_book_odds_to_daily_results(sport, daily, api_limit=400)
    N._cache_market_lines_for_results(sport, daily, limit=120)
    N._attach_engine_odds_to_daily_results(sport, daily, limit=80)
    st = N._compute_spread_total_for_daily(sport, daily)
    overall = N.compute_overall_stats_from_daily(daily)
    N._finalize_daily_result_cards(sport, daily)
    games_in_scope = (
        N._nhl_results_games_in_scope(daily) if sport == 'NHL'
        else N._daily_results_game_count(daily)
    )
    season_perf = N._build_season_performance_summary(
        overall, st,
        scope_label=f'{N.SPORTS[sport]["name"]} season',
        games_expected=N.SPORT_REGULAR_SEASON_LEAGUE_GAMES.get(sport),
        games_in_scope=games_in_scope,
    )
    total_over, total_under, total_games_ou, avg_total, ou_bench = N._ou_stats(daily, sport)
    roi_total = None
    try:
        roi_total = N.compute_roi_for_range(daily, None, None)
    except Exception:
        pass
    season = _season_label(N, sport, ref_dt)
    return {
        'sport': sport,
        'season': season,
        'phase': 'regular',
        'window': {
            'start': start_dt.strftime('%Y-%m-%d') if start_dt else None,
            'end': end_eff.strftime('%Y-%m-%d') if end_eff else None,
        },
        'games_in_scope': games_in_scope,
        'games_expected': N.SPORT_REGULAR_SEASON_LEAGUE_GAMES.get(sport),
        'overall_stats': overall,
        'spread_total_stats': st,
        'season_perf': season_perf,
        'ou_summary': {
            'total_over': total_over,
            'total_under': total_under,
            'total_games_ou': total_games_ou,
            'avg_total': avg_total,
            'ou_bench': ou_bench,
        },
        'roi_total': roi_total,
    }


def _fmt_model(overall, key):
    m = overall.get(key) or {}
    t, c = int(m.get('total') or 0), int(m.get('correct') or 0)
    if t == 0:
        return '—'
    return f'{m.get("accuracy", 0)}% ({c}-{t - c})'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sport', action='append', dest='sports')
    parser.add_argument('--json-out', type=Path, default=ROOT / 'data' / 'season_snapshots' / '_computed_preview.json')
    parser.add_argument('--write-snapshots', action='store_true', help='Write per-sport JSON to data/season_snapshots/')
    args = parser.parse_args()
    import NHL77FINAL as N

    if args.write_snapshots:
        from src.season_snapshots import save_season_snapshot

    targets = args.sports or SPORTS
    results = []
    written = []
    print(f'{"Sport":<8} {"Games":>6} {"Exp":>6}  {"Edge":>18} {"XSharp":>18} {"Cons":>18} {"Eff":>18}  {"Sprd":>8} {"O/U":>8}')
    print('-' * 100)
    for sport in targets:
        print(f'Computing {sport}...', file=sys.stderr, flush=True)
        row = compute_sport(N, sport)
        results.append(row)
        if row.get('error'):
            print(f'{sport:<8} ERROR: {row["error"]}')
            continue
        if args.write_snapshots:
            path = save_season_snapshot(row, sport, row['season'], 'regular')
            written.append(path)
            print(f'  wrote {path}', file=sys.stderr)
        o = row['overall_stats']
        sp = row['season_perf']
        print(
            f'{sport:<8} {row["games_in_scope"]:>6} '
            f'{row.get("games_expected") or "—":>6}  '
            f'{_fmt_model(o, "elo"):>18} '
            f'{_fmt_model(o, "xgboost"):>18} '
            f'{_fmt_model(o, "ensemble"):>18} '
            f'{_fmt_model(o, "efficiency"):>18}  '
            f'{sp.get("spread_graded", 0):>8} '
            f'{sp.get("ou_graded", 0):>8}'
        )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open('w', encoding='utf-8') as fh:
        json.dump(results, fh, indent=2)
    print(f'\nWrote detail JSON to {args.json_out}', file=sys.stderr)
    if written:
        print(f'Committed-style snapshots: {len(written)} files', file=sys.stderr)


if __name__ == '__main__':
    main()
