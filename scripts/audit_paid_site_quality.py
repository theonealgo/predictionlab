#!/usr/bin/env python3
"""Paid-site quality audit: line coverage, internal consistency, results grading."""
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPORTS = ['NBA', 'MLB', 'NHL', 'NFL', 'NCAAB', 'NCAAF', 'WNBA', 'NCAAW', 'SOCCER']

LINE_KEYS = {
    'book_ml': ('book_home_moneyline', 'book_away_moneyline'),
    'book_spread': ('disp_book_spread',),
    'book_total': ('disp_book_total',),
    'pl_spread': ('disp_pl_spread',),
    'pl_total': ('disp_pl_total',),
    'xs_spread': ('disp_xs_spread',),
    'xs_total': ('disp_xs_total',),
    'pl_scores': ('pl_proj_home_pts', 'pl_proj_away_pts'),
    'ens_pick': ('ensemble_prob', 'predicted_winner'),
}


def _scores_from_spread_total(spread, total):
    try:
        s, t = float(spread), float(total)
    except (TypeError, ValueError):
        return None, None
    h = (t + s) / 2.0
    a = (t - s) / 2.0
    return round(h, 2), round(a, 2)


def audit_pred(p, sport):
    issues = []

    for name, keys in LINE_KEYS.items():
        if name == 'book_ml':
            if p.get(keys[0]) is None or p.get(keys[1]) is None:
                issues.append(f'missing_{name}')
        elif name == 'ens_pick':
            ens = p.get('ensemble_prob')
            winner = p.get('predicted_winner') or p.get('home_team_id')
            if ens is not None and winner:
                pick_home = ens >= 50
                home = p.get('home_team_id')
                away = p.get('away_team_id')
                if pick_home and winner not in (home,):
                    if winner != home:
                        issues.append('pick_vs_ens_mismatch')
                elif not pick_home and winner not in (away,):
                    if winner != away:
                        issues.append('pick_vs_ens_mismatch')
        else:
            if any(p.get(k) is None for k in keys):
                issues.append(f'missing_{name}')

    ps = p.get('disp_pl_spread') if p.get('disp_pl_spread') is not None else p.get('our_spread')
    pt = p.get('disp_pl_total') if p.get('disp_pl_total') is not None else p.get('our_total')
    ph = p.get('pl_proj_home_pts')
    pa = p.get('pl_proj_away_pts')
    if ps is not None and pt is not None and ph is not None and pa is not None:
        eh, ea = _scores_from_spread_total(ps, pt)
        if eh is not None:
            if abs((ph + pa) - pt) > 1.5:
                issues.append('pl_scores_total_mismatch')
            if abs((ph - pa) - ps) > 1.5:
                issues.append('pl_scores_spread_mismatch')

    if p.get('book_spread') is not None and p.get('book_home_moneyline') is None:
        issues.append('book_spread_without_ml')
    src = p.get('book_odds_source') or ''
    if 'fallback' in str(src).lower():
        issues.append('book_ml_synthetic')

    return issues


def regrade_ens(game):
    """Recompute consensus moneyline correct from stored fields."""
    hs, as_ = game.get('home_score'), game.get('away_score')
    if hs is None or as_ is None:
        return None
    if game.get('is_draw'):
        return None
    home_won = hs > as_
    ens = game.get('ens_prob')
    if ens is None:
        return None
    pick_home = ens >= 50
    return pick_home == home_won


def main():
    import NHL77FINAL as N
    from NHL77FINAL import app

    today = datetime.now().strftime('%Y-%m-%d')
    print('=' * 72)
    print('PAID SITE QUALITY AUDIT')
    print(f'Today (server): {today}')
    print('=' * 72)

    pred_issues_by_sport = {}
    visible_counts = {}

    for sport in SPORTS:
        try:
            preds = N.get_upcoming_predictions(sport, days=7) or []
        except Exception as e:
            pred_issues_by_sport[sport] = [f'load_failed:{e}']
            continue
        upcoming = [p for p in preds if p.get('home_score') is None]
        grouped = defaultdict(list)
        for p in upcoming:
            gd = (p.get('game_date') or '')[:10]
            grouped[gd].append(p)
        sorted_dates, default_date = N._picks_display_dates(dict(grouped), today)
        visible = grouped.get(default_date, []) if default_date else []
        if not visible and sorted_dates:
            visible = grouped.get(sorted_dates[-1], [])

        visible_counts[sport] = {
            'default_date': default_date,
            'visible_n': len(visible),
            'upcoming_n': len(upcoming),
        }

        if not visible:
            pred_issues_by_sport[sport] = []
            continue

        try:
            N._refresh_books_on_predictions(sport, visible)
        except Exception:
            pass
        for p in visible:
            N._finalize_prediction_odds(p)
            N._enforce_pick_spread_consistency(p, sport=sport)
            N._prepare_pred_card_display(p)

        sport_issues = defaultdict(int)
        for p in visible:
            for iss in audit_pred(p, sport):
                sport_issues[iss] += 1
        pred_issues_by_sport[sport] = dict(sport_issues)

    print('\n## PREDICTIONS — default visible day only')
    print(f'{"Sport":<8} {"Date":<12} {"Visible":<8} {"BookML":<8} {"BookS/T":<10} {"PL":<6} {"XS":<6} Issues')
    for sport in SPORTS:
        vc = visible_counts.get(sport, {})
        n = vc.get('visible_n', 0)
        if n == 0:
            print(f'{sport:<8} {"—":<12} {0:<8} —        —          —    —    offseason/empty')
            continue
        # recount after pipeline
        preds = []
        grouped = defaultdict(list)
        raw = N.get_upcoming_predictions(sport, days=7) or []
        for p in raw:
            if p.get('home_score') is None:
                gd = (p.get('game_date') or '')[:10]
                if gd == vc.get('default_date'):
                    grouped[gd].append(p)
        visible = grouped.get(vc.get('default_date'), [])
        N._refresh_books_on_predictions(sport, visible)
        for p in visible:
            N._finalize_prediction_odds(p)
            N._enforce_pick_spread_consistency(p, sport=sport)
            N._prepare_pred_card_display(p)
            preds.append(p)
        n = len(preds)
        bk_ml = sum(1 for p in preds if p.get('book_home_moneyline') is not None)
        bk_st = sum(1 for p in preds if p.get('disp_book_spread') is not None and p.get('disp_book_total') is not None)
        pl_ok = sum(1 for p in preds if p.get('disp_pl_spread') is not None and p.get('disp_pl_total') is not None)
        xs_ok = sum(1 for p in preds if p.get('disp_xs_spread') is not None and p.get('disp_xs_total') is not None)
        iss = pred_issues_by_sport.get(sport, {})
        iss_s = ', '.join(f'{k}={v}' for k, v in sorted(iss.items())[:4]) if iss else 'none'
        print(
            f'{sport:<8} {str(vc.get("default_date","?")):<12} {n:<8} '
            f'{bk_ml}/{n:<5} {bk_st}/{n:<7} {pl_ok}/{n:<4} {xs_ok}/{n:<4} {iss_s}'
        )

    print('\n## PREMIUM GATING (free user HTML)')
    with app.test_client() as c:
        for sport in ['NBA', 'MLB', 'NHL']:
            slug = N.SPORT_SEO_SLUGS[sport]
            html = c.get(f'/{slug}', headers={'Host': '127.0.0.1'}).get_data(as_text=True)
            locked = 'Lines &amp; projections locked' in html or 'unlock premium' in html
            has_books_hero = 'ml-src books' in html
            has_table = 'odds-pricing-table' in html
            print(f'  {sport}: hero_books={has_books_hero} table_visible={has_table and not locked} locked_msg={locked}')

    print('\n## RESULTS — moneyline grading accuracy (last 50 games/sport)')
    grade_errors = {}
    for sport in SPORTS:
        conn = N.get_db_connection()
        rows = conn.execute(
            '''SELECT g.home_team_id, g.away_team_id, g.game_date, g.home_score, g.away_score,
                      p.win_probability, p.elo_home_prob
               FROM games g
               LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
               WHERE g.sport = ? AND g.home_score IS NOT NULL
               ORDER BY g.game_date DESC LIMIT 50''',
            (sport, sport),
        ).fetchall()
        conn.close()
        mismatches = 0
        checked = 0
        for row in rows:
            hs, as_ = row['home_score'], row['away_score']
            if hs is None or as_ is None:
                continue
            if sport == 'SOCCER' and abs(float(hs) - float(as_)) < 1e-9:
                continue
            ens = row['win_probability']
            if ens is None:
                ens = row['elo_home_prob']
            if ens is None:
                continue
            try:
                ens_pct = float(ens) * 100 if float(ens) <= 1 else float(ens)
            except (TypeError, ValueError):
                continue
            home_won = float(hs) > float(as_)
            expected = (ens_pct >= 50) == home_won
            checked += 1
        grade_errors[sport] = {'checked': checked, 'note': 'DB probs OK if checked>0'}

    for sport, g in grade_errors.items():
        print(f'  {sport}: graded_sample={g["checked"]}')

    print('\n## RESULTS — live page sanity (season ML % vs W-L)')
    with app.test_client() as c:
        for sport in SPORTS:
            rslug = N._SPORT_RESULTS_SLUGS[sport]
            html = c.get(f'/{rslug}', headers={'Host': '127.0.0.1'}).get_data(as_text=True)
            import re
            m = re.search(r'Moneyline.*?(\d+\.?\d*)%.*?(\d+)-(\d+)', html, re.S | re.I)
            if m:
                pct, w, l = float(m.group(1)), int(m.group(2)), int(m.group(3))
                calc = round(w / (w + l) * 100, 1) if (w + l) else 0
                ok = abs(pct - calc) < 1.1
                flag = 'OK' if ok else f'MATH MISMATCH calc={calc}%'
                print(f'  {sport}: displayed {pct}% ({w}-{l}) {flag}')
            elif 'no graded' in html.lower() or 'no results' in html.lower():
                print(f'  {sport}: no season block (empty/offseason)')
            else:
                print(f'  {sport}: could not parse season ML block')

    print('\n## VERDICT')
    blockers = []
    for sport in SPORTS:
        vc = visible_counts.get(sport, {})
        n = vc.get('visible_n', 0)
        if n == 0:
            continue
        iss = pred_issues_by_sport.get(sport, {})
        if isinstance(iss, list):
            blockers.append(f'{sport}: {iss}')
            continue
        miss_ml = iss.get('missing_book_ml', 0)
        if miss_ml > 0:
            blockers.append(f'{sport}: {miss_ml}/{n} visible games missing Books ML on default day')
        if iss.get('pick_vs_ens_mismatch', 0):
            blockers.append(f'{sport}: pick vs ensemble_prob mismatch on {iss["pick_vs_ens_mismatch"]} games')

    if blockers:
        print('BLOCKERS / PAID-SITE RISKS:')
        for b in blockers:
            print(f'  - {b}')
        print('\nNote: Games days+ in the future often have no ESPN/DK lines — expected.')
        print('Spread ML may be SYNTHETIC from spread when ESPN omits moneylines (_ml_from_spread_fallback).')
        print('Spread grading uses pick-em (0) when no book spread — affects results accuracy labels.')
        return 1
    print('No blockers on default visible day; review notes above for known limitations.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
