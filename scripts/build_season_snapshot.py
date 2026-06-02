#!/usr/bin/env python3
"""Build a frozen season snapshot JSON for results pages.

Example:
  python scripts/build_season_snapshot.py --sport NHL --season 2025-26

Snapshot builds run full v2 ML + XGB spread/total grading (PL_SNAPSHOT_BUILD=1).
Use PL_SKIP_V2_FOR_RESULTS=1 only for live results pages, not snapshot builds.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Full-season snapshot: grade every completed game (XGB spread/total + v2 ML).
os.environ['PL_SNAPSHOT_BUILD'] = '1'
os.environ.pop('PL_SKIP_V2_FOR_RESULTS', None)


def _parse_season(season: str) -> datetime:
    """Return a reference date after the regular season for label '2025-26'."""
    start_year = int(season.split('-')[0])
    return datetime(start_year + 1, 6, 1)


def build_snapshot(sport: str, season: str, phase: str) -> Path:
    import NHL77FINAL as N
    from src.season_snapshots import save_season_snapshot, season_label_from_bounds

    ref_dt = _parse_season(season)
    if sport != 'NHL':
        raise SystemExit('Only NHL is wired for snapshot builds in this release.')

    if phase == 'regular':
        start_dt, end_dt = N._nhl_results_regular_season_bounds(ref_dt)
        daily = N._banner_daily_results_for_range(
            sport, start_dt, end_dt, playoffs=False, skip_v2=False,
        )
        scope_label = 'NHL regular season (Oct–Apr)'
        games_expected = N.SPORT_REGULAR_SEASON_LEAGUE_GAMES.get('NHL')
        games_in_scope = N._nhl_results_games_in_scope(daily) if daily else 0
    elif phase == 'playoffs':
        start_dt, end_dt = N._nhl_playoff_window(ref_dt)
        daily = N._banner_daily_results_for_range(
            sport, start_dt, end_dt, playoffs=True, skip_v2=False,
        )
        scope_label = 'NHL playoffs'
        games_expected = None
        games_in_scope = N._daily_results_game_count(daily) if daily else 0
    else:
        raise SystemExit(f'Unknown phase: {phase}')

    if not daily or not N._daily_results_game_count(daily):
        raise SystemExit(f'No graded {sport} games for {season} {phase} — check DB and date window.')

    N._attach_book_odds_to_daily_results(sport, daily, api_limit=300)
    N._cache_market_lines_for_results(sport, daily, limit=80)
    N._attach_engine_odds_to_daily_results(sport, daily, limit=40)
    st_stats = N._compute_spread_total_for_daily(sport, daily)
    N._finalize_daily_result_cards(sport, daily)

    overall_stats = N.compute_overall_stats_from_daily(daily)
    total_over, total_under, total_games_ou, avg_total, ou_bench = N._ou_stats(daily, sport)
    season_perf = N._build_season_performance_summary(
        overall_stats,
        st_stats,
        scope_label=scope_label,
        games_expected=games_expected,
        games_in_scope=games_in_scope,
    )
    roi_total = N.compute_roi_for_range(daily, None, None)

    label = season_label_from_bounds(N._nhl_results_regular_season_bounds(ref_dt)[0])
    if label != season:
        print(f'Warning: --season {season} vs computed label {label}', file=sys.stderr)

    payload = {
        'schema_version': 1,
        'sport': sport,
        'season': season,
        'phase': phase,
        'window': {
            'start': start_dt.strftime('%Y-%m-%d'),
            'end': end_dt.strftime('%Y-%m-%d'),
        },
        'games_expected': games_expected,
        'games_in_scope': games_in_scope,
        'overall_stats': overall_stats,
        'spread_total_stats': st_stats,
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
    path = save_season_snapshot(payload, sport, season, phase)
    sp_gr = season_perf.get('spread_graded', 0)
    ou_gr = season_perf.get('ou_graded', 0)
    ml_total = season_perf.get('ml_total', 0)
    print(
        f'Wrote {path} ({games_in_scope} in scope, '
        f'ML {ml_total}, spread {sp_gr}, O/U {ou_gr} graded)'
    )
    return path


def main():
    parser = argparse.ArgumentParser(description='Build frozen season results snapshot JSON.')
    parser.add_argument('--sport', required=True, help='Sport code (NHL)')
    parser.add_argument('--season', required=True, help='Season label e.g. 2025-26')
    parser.add_argument('--phase', default='regular', choices=('regular', 'playoffs'))
    args = parser.parse_args()
    build_snapshot(args.sport.upper(), args.season, args.phase)


if __name__ == '__main__':
    main()
