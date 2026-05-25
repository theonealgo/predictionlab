#!/usr/bin/env python3
"""Deep audit: all 9 sports picks + results pages (HTTP + data pipeline)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPORTS = ['NBA', 'MLB', 'NHL', 'NFL', 'NCAAB', 'NCAAF', 'WNBA', 'NCAAW', 'SOCCER']


def main():
    import NHL77FINAL as N
    from NHL77FINAL import app

    print('=' * 72)
    print('ALL-SPORTS PREDICTIONS + RESULTS AUDIT')
    print('=' * 72)

    issues = []
    rows = []

    with app.test_client() as c:
        for sport in SPORTS:
            picks_slug = N.SPORT_SEO_SLUGS.get(sport)
            results_slug = N._SPORT_RESULTS_SLUGS.get(sport)
            row = {'sport': sport, 'picks': picks_slug, 'results': results_slug}

            # --- Picks HTTP ---
            pr = c.get(f'/{picks_slug}', headers={'Host': '127.0.0.1'})
            pb = pr.get_data(as_text=True)
            row['picks_status'] = pr.status_code
            row['picks_cards'] = pb.count('class="game-card"')
            row['picks_join'] = 'Join Premium' in pb or '/plans' in pb
            row['picks_books_col'] = 'Books' in pb and 'Prediction Lab' in pb
            row['picks_hero_ml'] = bool(
                re.search(r'book_home_moneyline|book_away_moneyline|>-?\d{2,4}<', pb)
                or 'disp_book' in pb
            )
            row['picks_locked'] = 'unlock premium' in pb.lower() or 'Lines &amp; projections locked' in pb
            row['picks_offseason'] = (
                row['picks_cards'] == 0
                and ('return when' in pb.lower() or 'no upcoming' in pb.lower() or 'offseason' in pb.lower())
            )
            row['picks_error'] = pr.status_code >= 500 or 'failed to render' in pb.lower()

            if row['picks_error']:
                issues.append(f'{sport} picks: HTTP {pr.status_code} or render error')
            elif not row['picks_offseason'] and row['picks_cards'] == 0:
                issues.append(f'{sport} picks: 200 but zero game cards (not offseason msg)')
            elif not row['picks_offseason'] and not row['picks_join']:
                issues.append(f'{sport} picks: missing Join Premium nav')
            elif not row['picks_offseason'] and not row['picks_books_col']:
                issues.append(f'{sport} picks: missing Books/PL table labels')

            # --- Results HTTP ---
            rr = c.get(f'/{results_slug}', headers={'Host': '127.0.0.1'})
            rb = rr.get_data(as_text=True)
            row['results_status'] = rr.status_code
            row['results_join'] = 'Join Premium' in rb or '/plans' in rb
            row['results_tabs'] = 'Predictions' in rb and 'Results' in rb
            row['results_graded'] = (
                'FINAL' in rb
                or 'daily-acc' in rb
                or 'Season Performance' in rb
                or 'Moneyline Accuracy' in rb
                or 'no graded games' in rb.lower()
                or 'no results' in rb.lower()
            )
            row['results_offseason'] = 'will appear once' in rb.lower() or 'no results data' in rb.lower()
            row['results_error'] = rr.status_code >= 500 or 'processing error' in rb.lower()
            row['results_soccer_leagues'] = (
                sport != 'SOCCER' or 'English Premier League' in rb or 'league-slider' in rb or row['results_offseason']
            )

            if row['results_error']:
                issues.append(f'{sport} results: HTTP {rr.status_code} or processing error')
            elif not row['results_join']:
                issues.append(f'{sport} results: missing Join Premium link')
            elif not row['results_tabs']:
                issues.append(f'{sport} results: missing Predictions/Results tabs')
            elif sport == 'SOCCER' and not row['results_soccer_leagues'] and not row['results_offseason']:
                issues.append(f'{sport} results: missing league filter UI')

            # --- Data: upcoming predictions ---
            try:
                preds = N.get_upcoming_predictions(sport, days=7) or []
                upcoming = [p for p in preds if p.get('home_score') is None]
                row['upcoming_n'] = len(upcoming)
                if upcoming:
                    N._refresh_books_on_predictions(sport, upcoming)
                    for p in upcoming[:5]:
                        N._finalize_prediction_odds(p)
                        N._prepare_pred_card_display(p)
                    sample = upcoming[0]
                    row['sample_book_ml'] = sample.get('book_home_moneyline') is not None
                    row['sample_pl_spread'] = sample.get('disp_pl_spread') is not None
                    row['sample_logo'] = (
                        N.team_logo_url(sport, sample.get('home_team_id') or '')
                        != '/static/pl-logo.svg'
                        if sport == 'SOCCER'
                        else True
                    )
                else:
                    row['sample_book_ml'] = None
                    row['sample_pl_spread'] = None
                    row['sample_logo'] = None
            except Exception as e:
                row['upcoming_n'] = f'ERR:{e}'
                issues.append(f'{sport} get_upcoming_predictions: {e}')

            rows.append(row)

    print('\n## PICKS PAGES (HTTP)')
    print(f'{"Sport":<8} {"Status":<6} {"Cards":<6} {"Join":<5} {"Books":<6} {"Off":<4} {"Err":<4}')
    for r in rows:
        print(
            f'{r["sport"]:<8} {r["picks_status"]:<6} {r["picks_cards"]:<6} '
            f'{"Y" if r["picks_join"] else "N":<5} {"Y" if r["picks_books_col"] else "N":<6} '
            f'{"Y" if r.get("picks_offseason") else "N":<4} {"Y" if r.get("picks_error") else "N":<4}'
        )

    print('\n## RESULTS PAGES (HTTP)')
    print(f'{"Sport":<8} {"Status":<6} {"Join":<5} {"Tabs":<5} {"Data":<5} {"Err":<4}')
    for r in rows:
        print(
            f'{r["sport"]:<8} {r["results_status"]:<6} '
            f'{"Y" if r["results_join"] else "N":<5} {"Y" if r["results_tabs"] else "N":<5} '
            f'{"Y" if r["results_graded"] else "N":<5} {"Y" if r.get("results_error") else "N":<4}'
        )

    print('\n## UPCOMING DATA (sample when games exist)')
    for r in rows:
        n = r.get('upcoming_n')
        if isinstance(n, int) and n > 0:
            logo = r.get('sample_logo')
            logo_s = 'crest' if logo else ('PL-logo' if logo is False else 'n/a')
            print(
                f'  {r["sport"]:<8} n={n} book_ml={"Y" if r.get("sample_book_ml") else "N"} '
                f'pl_spread={"Y" if r.get("sample_pl_spread") else "N"} logo={logo_s}'
            )
            if r['sport'] == 'SOCCER' and logo is False:
                issues.append('SOCCER picks: sample team still using PL logo (cache miss)')
        elif isinstance(n, int):
            print(f'  {r["sport"]:<8} n=0 (no upcoming in 7d window)')

    print('\n## SOCCER results league URL')
    with app.test_client() as c:
        sr = c.get('/soccer-results?league=eng.1', headers={'Host': '127.0.0.1'})
        sb = sr.get_data(as_text=True)
        ok = sr.status_code == 200 and ('English Premier League' in sb or 'no graded' in sb.lower())
        print(f'  soccer-results?league=eng.1 -> {sr.status_code} leagues_ui={"Y" if ok else "N"}')
        if not ok:
            issues.append('SOCCER results league query param page unhealthy')

    print('\n' + '=' * 72)
    if issues:
        print('ISSUES FOUND (%d):' % len(issues))
        for i in issues:
            print(f'  - {i}')
        print('VERDICT: NOT READY TO PUSH (fix issues above)')
        print('=' * 72)
        return 1
    print('VERDICT: ALL CHECKS PASSED — safe to push from audit perspective')
    print('=' * 72)
    return 0


if __name__ == '__main__':
    sys.exit(main())
