#!/usr/bin/env python3
"""Full 9-sport test: picks card pipeline, results grading, ESPN book API."""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPORTS = ['NBA', 'MLB', 'NHL', 'NFL', 'NCAAB', 'NCAAF', 'WNBA', 'NCAAW', 'SOCCER']
ESPN_BOOK = {'NBA', 'WNBA', 'NHL', 'NFL', 'MLB', 'NCAAB', 'NCAAF', 'NCAAW', 'SOCCER'}


def audit_pred_after_pipeline(p):
    issues = []
    bk, bt = p.get('book_total'), p.get('book_spread')
    xs = p.get('disp_xs_total')
    pl_t = p.get('disp_pl_total')
    mkt_t, mkt_s = p.get('market_total'), p.get('market_spread')

    if bk is not None and xs is not None:
        try:
            if abs(float(bk) - float(xs)) < 0.01:
                issues.append('XSHARP_TOTAL=BOOK')
        except (TypeError, ValueError):
            pass
    if bk is not None and pl_t is not None:
        try:
            if abs(float(bk) - float(pl_t)) < 0.01:
                issues.append('PL_TOTAL=BOOK')
        except (TypeError, ValueError):
            pass
    if bt is not None and mkt_s is not None and p.get('book_odds_source') == 'pl_book_odds_api':
        try:
            if abs(float(bt) - float(mkt_s)) < 0.01 and bk is None:
                issues.append('MARKET_SPREAD=BOOK_ONLY')
        except (TypeError, ValueError):
            pass
    if bk is not None and mkt_t is not None and p.get('book_odds_source') == 'pl_book_odds_api':
        try:
            if abs(float(bk) - float(mkt_t)) < 0.01:
                issues.append('MARKET_TOTAL=BOOK_BLEED')
        except (TypeError, ValueError):
            pass
    if not (p.get('book_spread') or p.get('book_total') or p.get('book_home_moneyline')):
        issues.append('NO_BOOK_LINES')
    if p.get('disp_book_spread') is None and p.get('disp_book_total') is None:
        if 'NO_BOOK_LINES' not in issues:
            issues.append('NO_DISP_BOOK')
    return issues


def test_pl_api_direct():
    from pl_book_odds_api import CORE_API_SPORT_PATHS, build_pl_book_odds, _soccer_slug_from_league_name
    rows = {}
    rows['NCAAW_in_paths'] = 'NCAAW' in CORE_API_SPORT_PATHS
    rows['SOCCER_in_paths'] = 'SOCCER' in CORE_API_SPORT_PATHS
    rows['soccer_afc_slug'] = _soccer_slug_from_league_name('AFC Champions League Elite')
    return rows


def main():
    import NHL77FINAL as NHL
    from datetime import datetime, timedelta
    from collections import defaultdict

    print('=' * 60)
    print('STATIC / pl_book_odds_api')
    print('=' * 60)
    api = test_pl_api_direct()
    for k, v in api.items():
        print(f'  {k}: {v}')

    src = (ROOT / 'NHL77FINAL.py').read_text()
    static = {
        'xsharp_copies_market': "pred['xgb_total'] = pred['market_total']" in src,
        'book_copies_to_market': "pred['market_spread'] = row['spread']" in src,
        'soccer_in_attach': "'SOCCER'" in src and "('NBA', 'MLB', 'NHL', 'NFL', 'NCAAB', 'NCAAF', 'WNBA', 'NCAAW', 'SOCCER')" in src,
    }
    for k, bad in static.items():
        print(f'  {k}: {"FAIL" if bad else "OK"}')

    print('\n' + '=' * 60)
    print('PICKS (get_upcoming_predictions + card pipeline)')
    print('=' * 60)
    picks = {}
    for sport in SPORTS:
        print(f'  {sport}...', end=' ', flush=True)
        row = {'espn_api': sport in ESPN_BOOK}
        try:
            preds = NHL.get_upcoming_predictions(sport, days=5)
        except Exception as e:
            row['error'] = str(e)[:120]
            picks[sport] = row
            print('ERROR')
            continue
        upcoming = [p for p in (preds or []) if p.get('home_score') is None]
        row['upcoming_total'] = len(upcoming)
        sample = upcoming[:6]
        if not sample:
            picks[sport] = row
            print('no upcoming')
            continue
        issue_counts = defaultdict(int)
        with_book = 0
        for p in sample:
            NHL._finalize_prediction_odds(p)
            NHL._prepare_pred_card_display(p)
            for code in audit_pred_after_pipeline(p):
                issue_counts[code] += 1
            if p.get('book_spread') or p.get('book_total') or p.get('book_home_moneyline'):
                with_book += 1
        row['sample'] = len(sample)
        row['with_book'] = with_book
        row['issues'] = dict(issue_counts) if issue_counts else None
        picks[sport] = row
        ib = sum(issue_counts.values())
        print(f'n={len(sample)} book={with_book} flags={ib}')

    print('\n' + '=' * 60)
    print('RESULTS (book before compute)')
    print('=' * 60)
    results = {}
    y = datetime.now() - timedelta(days=1)
    start = y - timedelta(days=45)
    for sport in SPORTS:
        print(f'  {sport}...', end=' ', flush=True)
        row = {}
        try:
            if sport == 'NBA':
                weekly = NHL.calculate_nba_weekly_performance()
                daily = NHL._daily_results_from_weekly(weekly) if weekly else None
            elif sport == 'NFL':
                weekly = NHL.calculate_nfl_weekly_performance()
                daily = NHL._daily_results_from_weekly(weekly) if weekly else None
                row['nfl_weekly_path'] = bool(weekly)
            else:
                daily = NHL._banner_daily_results_for_range(sport, start, y)
            if not daily:
                row['skip'] = 'no completed games in range'
                results[sport] = row
                print(row['skip'])
                continue
            games_n = sum(len(dd.get('games', [])) for dd in daily.values())
            row['games'] = games_n
            NHL._attach_book_odds_to_daily_results(sport, daily, api_limit=40)
            with_book = sum(
                1 for dd in daily.values() for g in dd.get('games', [])
                if g.get('book_total') or g.get('book_spread')
            )
            row['games_with_book'] = with_book
            st = NHL._compute_spread_total_for_daily(sport, daily)
            row['spread_graded'] = (st or {}).get('spread_graded', 0)
            row['total_graded'] = (st or {}).get('total_graded', 0)
            ou_g = sum(
                1 for dd in daily.values() for g in dd.get('games', [])
                if g.get('total_correct') is not None
            )
            sp_g = sum(
                1 for dd in daily.values() for g in dd.get('games', [])
                if g.get('spread_correct') is not None
            )
            row['per_game_ou'] = ou_g
            row['per_game_sp'] = sp_g
            if games_n and with_book == 0:
                row['gap'] = 'NO_BOOK_ON_RESULTS'
            if games_n and row['total_graded'] == 0 and with_book > 0:
                row['gap'] = 'BOOK_PRESENT_BUT_OU_NOT_GRADED'
            elif games_n and row['total_graded'] == 0:
                row['gap'] = 'NO_OU_GRADED'
            results[sport] = row
            print(f"g={games_n} book={with_book} sp={row['spread_graded']} ou={row['total_graded']}")
        except Exception as e:
            row['error'] = str(e)[:120]
            results[sport] = row
            print('ERROR')

    print('\n' + '=' * 60)
    print('ROUTE GAPS (code inspection)')
    print('=' * 60)
    routes = []
    if 'NFL_WEEKLY_RESULTS_TEMPLATE' in src and '_compute_spread_total_for_daily(sport, daily_results)' not in src[src.find("if sport == 'NFL'"):src.find("if sport == 'NHL'")]:
        routes.append('NFL primary results: weekly template, no spread/O/U compute')
    print('  ' + (routes[0] if routes else 'Daily results routes call book→compute for non-NFL'))

    print('\n' + '=' * 60)
    print('MISSING / GAPS SUMMARY')
    print('=' * 60)
    missing = []

    for sport in SPORTS:
        p = picks.get(sport, {})
        r = results.get(sport, {})
        if p.get('error'):
            missing.append(f'{sport} PICKS: error — {p["error"]}')
        elif p.get('upcoming_total', 0) == 0:
            missing.append(f'{sport} PICKS: no upcoming games in DB/API (5-day window)')
        elif p.get('with_book', 0) == 0 and p.get('sample', 0) > 0:
            missing.append(f'{sport} PICKS: {p["sample"]} games, 0 with ESPN book lines')
        if p.get('issues'):
            for code, cnt in p['issues'].items():
                if code in ('MARKET_TOTAL=BOOK_BLEED', 'XSHARP_TOTAL=BOOK'):
                    missing.append(f'{sport} PICKS: {cnt} games — {code}')
        if r.get('error'):
            missing.append(f'{sport} RESULTS: error — {r["error"]}')
        elif r.get('skip'):
            missing.append(f'{sport} RESULTS: {r["skip"]}')
        elif r.get('gap'):
            missing.append(f'{sport} RESULTS: {r["gap"]} (games={r.get("games")}, book={r.get("games_with_book")})')
        elif r.get('games') and r.get('total_graded', 0) == 0:
            missing.append(f'{sport} RESULTS: 0 O/U graded ({r.get("games")} games)')

    for line in routes:
        missing.append(line)

    if not missing:
        print('  No critical gaps detected in this run.')
    else:
        for i, m in enumerate(missing, 1):
            print(f'  {i}. {m}')

    print('\n' + '=' * 60)
    print('DETAIL TABLE')
    print('=' * 60)
    print(f'{"Sport":<8} {"Upcoming":>8} {"Book":>6} {"Issues":<30} {"Res G":>6} {"Bk":>5} {"SpGr":>6} {"OuGr":>6}')
    for sport in SPORTS:
        p = picks.get(sport, {})
        r = results.get(sport, {})
        iss = ','.join(f'{k}:{v}' for k, v in (p.get('issues') or {}).items()) or '-'
        print(
            f'{sport:<8} {p.get("sample", p.get("upcoming_total", "-"))!s:>8} '
            f'{p.get("with_book", "-")!s:>6} {iss:<30} '
            f'{r.get("games", r.get("skip", "-"))!s:>6} {r.get("games_with_book", "-")!s:>5} '
            f'{r.get("spread_graded", "-")!s:>6} {r.get("total_graded", "-")!s:>6}'
        )


if __name__ == '__main__':
    main()
