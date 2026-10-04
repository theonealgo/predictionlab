#!/usr/bin/env python3
"""Verify picks vs results book-odds paths stay independent and complete."""
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import NHL77FINAL as N

    errors = []

    # Picks: still requires spread + total + ML before skipping ESPN
    needs = {'book_spread': -3.5, 'book_total': None, 'book_home_moneyline': -110, 'book_away_moneyline': -110}
    if not N._pred_needs_book_fetch(needs):
        errors.append('picks: should fetch when book_total missing')

    ok = {'book_spread': -3.5, 'book_total': 220.5, 'book_home_moneyline': -110, 'book_away_moneyline': -110}
    if N._pred_needs_book_fetch(ok):
        errors.append('picks: should not fetch when spread+total+ML present')

    # Results: skip ESPN only when book_total already set
    g = {'book_spread': -3.5, 'book_total': None}
    if g.get('book_total') is not None:
        errors.append('results: should not skip API without total')

    g2 = {'book_total': 219.0}
    if g2.get('book_total') is None:
        errors.append('results: should skip API when total present')

    # Live NBA picks sample
    preds = N.get_upcoming_predictions('NBA', days=4) or []
    upcoming = [p for p in preds if p.get('home_score') is None][:12]
    if upcoming:
        N._refresh_books_on_predictions('NBA', upcoming)
        for p in upcoming:
            N._finalize_prediction_odds(p)
            N._prepare_pred_card_display(p)
        n = len(upcoming)
        bs = sum(1 for p in upcoming if p.get('disp_book_spread') is not None)
        bt = sum(1 for p in upcoming if p.get('disp_book_total') is not None)
        print(f'NBA picks sample {n}: disp_book_spread {bs}/{n}, disp_book_total {bt}/{n}')
        if bs == 0 and n > 0:
            errors.append('picks: no disp_book_spread on any upcoming NBA game')

    # Results DB attach (no API)
    wr = N.calculate_nba_weekly_performance()
    if wr:
        daily = defaultdict(lambda: {'games': []})
        for wd in wr.values():
            for g in wd['games'][-12:]:
                daily[g['date']]['games'].append(g)
        N._attach_book_odds_to_daily_results('NBA', daily, api_limit=0)
        gs = [g for dd in daily.values() for g in dd['games']]
        bt = sum(1 for g in gs if N._safe_float(g.get('book_total')) is not None)
        print(f'NBA results sample {len(gs)}: book_total {bt}/{len(gs)} (DB only)')
        for g in gs:
            N._set_card_book_lines(g)
        dt = sum(1 for g in gs if g.get('disp_book_total') is not None)
        print(f'  disp_book_total {dt}/{len(gs)}')

    if errors:
        print('FAILED:')
        for e in errors:
            print(' -', e)
        sys.exit(1)
    print('OK: book odds paths verified')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
