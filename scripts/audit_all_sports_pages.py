#!/usr/bin/env python3
"""Deep audit: all 9 sports picks + results pages (HTTP + card pipeline)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPORTS = ['NBA', 'MLB', 'NHL', 'NFL', 'NCAAB', 'NCAAF', 'WNBA', 'NCAAW', 'SOCCER']

# After _prepare_pred_card_display — keys the pick card template expects when data exists.
PICK_CARD_KEYS = {
    'all': ['face_home_prob', 'face_away_prob', 'pl_model_home_ml', 'pl_model_away_ml'],
    'books': ['disp_book_spread', 'disp_book_total'],
    'pl': ['disp_pl_spread', 'disp_pl_total'],
    'xs': ['disp_xs_spread', 'disp_xs_total'],
}
SOCCER_EXTRA = ['face_draw_prob', 'pl_model_draw_ml']
MLB_EXTRA = ['face_edge_pct']


def _has_pick_cards(html: str) -> bool:
    return (
        'class="game-card pick-card"' in html
        or ('class="matchup-row"' in html and 'face-pl-ml' in html)
    )


def _count_pick_cards(html: str) -> int:
    return html.count('class="game-card pick-card"')


def _spread_consistent(card: dict) -> bool:
    """PL moneyline pick must agree with home-centric spread sign."""
    sp = card.get('our_spread')
    if sp is None:
        sp = card.get('disp_pl_spread')
    if sp is None:
        return True
    try:
        sp = float(sp)
    except (TypeError, ValueError):
        return True
    pw = card.get('predicted_winner') or card.get('face_pick_team')
    home = card.get('home_team_id')
    away = card.get('away_team_id')
    if pw and home and away:
        if pw == home and sp < 0:
            return False
        if pw == away and sp > 0:
            return False
    return True


def main():
    import NHL77FINAL as N
    from NHL77FINAL import app

    N._SPORT_PREDICTIONS_PAGE_CACHE.clear()

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

            pr = c.get(f'/{picks_slug}', headers={'Host': '127.0.0.1'})
            pb = pr.get_data(as_text=True)
            row['picks_status'] = pr.status_code
            row['picks_cards'] = _count_pick_cards(pb)
            row['picks_has_cards'] = _has_pick_cards(pb)
            row['picks_join'] = 'Join Premium' in pb or '/plans' in pb
            row['picks_books_col'] = (
                'ml-src books' in pb or 'Books · DK' in pb or 'Books run line' in pb
            ) and 'Prediction Lab' in pb
            row['picks_pl_ml'] = 'Prediction Lab' in pb and ('face-pl-ml' in pb or 'ml-src pl' in pb)
            row['picks_edge_chip'] = (
                sport != 'MLB' or 'edge-chip' in pb or 'line-chip-label">Edge' in pb
            )
            row['picks_soccer_draw'] = (
                sport != 'SOCCER' or 'soccer-draw-row' in pb or row['picks_cards'] == 0
            )
            row['picks_soccer_all'] = (
                sport != 'SOCCER' or 'All Leagues' in pb or row['picks_cards'] == 0
            )
            row['picks_offseason'] = row['picks_cards'] == 0 and (
                'return when' in pb.lower()
                or 'no upcoming' in pb.lower()
                or 'offseason' in pb.lower()
                or 'no predictions' in pb.lower()
                or 'prediction_error' in pb.lower()
            )
            row['picks_error'] = pr.status_code >= 500 or 'failed to render' in pb.lower()

            if row['picks_error']:
                issues.append(f'{sport} picks: HTTP {pr.status_code} or render error')

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
                or 'will appear once' in rb.lower()
            )
            row['results_grinder'] = (
                'Glicko' in rb or 'Grinder' in rb or 'glicko2' in rb.lower()
                or row['results_graded'] is False
            )
            row['results_offseason'] = 'will appear once' in rb.lower() or 'no results data' in rb.lower()
            row['results_error'] = rr.status_code >= 500 or 'processing error' in rb.lower()
            row['results_soccer_leagues'] = (
                sport != 'SOCCER'
                or 'league-slider' in rb
                or 'English Premier League' in rb
                or row['results_offseason']
            )

            if row['results_error']:
                issues.append(f'{sport} results: HTTP {rr.status_code} or processing error')
            elif not row['results_join']:
                issues.append(f'{sport} results: missing Join Premium link')
            elif not row['results_tabs']:
                issues.append(f'{sport} results: missing Predictions/Results tabs')
            elif sport == 'SOCCER' and not row['results_soccer_leagues']:
                issues.append(f'{sport} results: missing league filter UI')

            try:
                preds = N.get_upcoming_predictions(sport, days=7) or []
                upcoming = [p for p in preds if p.get('home_score') is None]
                row['upcoming_n'] = len(upcoming)
                row['spread_ok'] = True
                row['field_rates'] = {}
                if upcoming:
                    N._refresh_books_on_predictions(sport, upcoming)
                    for p in upcoming[:8]:
                        N._finalize_prediction_odds(p)
                        N._enforce_pick_spread_consistency(p, sport=sport)
                        N._prepare_pred_card_display(p, sport=sport)
                    keys = list(PICK_CARD_KEYS['all'])
                    if sport == 'SOCCER':
                        keys += SOCCER_EXTRA
                    if sport == 'MLB':
                        keys += MLB_EXTRA
                    n = min(len(upcoming), 8)
                    for k in keys:
                        row['field_rates'][k] = sum(
                            1 for p in upcoming[:n] if p.get(k) is not None
                        )
                    row['spread_ok'] = all(
                        _spread_consistent(p) for p in upcoming[:n]
                    )
                    if not row['spread_ok']:
                        issues.append(f'{sport} picks data: PL spread contradicts face pick')
                    sample = upcoming[0]
                    row['sample_book_ml'] = sample.get('book_home_moneyline') is not None
                    row['sample_pl_spread'] = sample.get('disp_pl_spread') is not None
                    if sport == 'MLB' and sample.get('edge_pct') is not None:
                        if sample.get('face_edge_pct') is None:
                            issues.append(f'{sport}: edge_pct set but face_edge_pct missing after prepare')
                else:
                    row['sample_book_ml'] = None
                    row['sample_pl_spread'] = None
            except Exception as e:
                row['upcoming_n'] = f'ERR:{e}'
                issues.append(f'{sport} get_upcoming_predictions: {e}')

            if not row.get('picks_error'):
                if (
                    not row['picks_has_cards']
                    and isinstance(row.get('upcoming_n'), int)
                    and row['upcoming_n'] > 0
                ):
                    issues.append(f'{sport} picks: upcoming games in DB but no cards rendered')
                elif not row['picks_offseason'] and not row['picks_has_cards']:
                    issues.append(f'{sport} picks: 200 but no pick cards in HTML')
                elif not row['picks_offseason'] and not row['picks_join']:
                    issues.append(f'{sport} picks: missing Join Premium nav')
                elif not row['picks_offseason'] and not row['picks_books_col']:
                    issues.append(f'{sport} picks: missing Books/PL labels')
                elif not row['picks_offseason'] and not row['picks_pl_ml']:
                    issues.append(f'{sport} picks: missing Prediction Lab ML on card face')
                elif not row['picks_offseason'] and not row['picks_edge_chip']:
                    issues.append(f'{sport} picks: MLB Edge % chip missing')
                elif not row['picks_offseason'] and not row['picks_soccer_draw']:
                    issues.append(f'{sport} picks: soccer draw row missing on 3-way slate')
                elif not row['picks_offseason'] and not row['picks_soccer_all']:
                    issues.append(f'{sport} picks: All Leagues default missing')

            rows.append(row)

        sr = c.get('/soccer-results?league=eng.1', headers={'Host': '127.0.0.1'})
        sb = sr.get_data(as_text=True)
        soccer_league_ok = (
            sr.status_code == 200
            and ('league-slider' in sb or 'English Premier League' in sb or 'eng.1' in sb)
        )
        print('\n## SOCCER results league URL')
        print(f'  soccer-results?league=eng.1 -> {sr.status_code} leagues_ui={"Y" if soccer_league_ok else "N"}')
        if not soccer_league_ok:
            issues.append('SOCCER results league query param page unhealthy')

    print('\n## PICKS PAGES (HTTP)')
    print(f'{"Sport":<8} {"Status":<6} {"Cards":<6} {"PL ML":<6} {"Edge":<5} {"Off":<4}')
    for r in rows:
        edge_s = (
            'Y' if r.get('picks_edge_chip') else ('—' if r['sport'] != 'MLB' else 'N')
        )
        print(
            f'{r["sport"]:<8} {r["picks_status"]:<6} {r["picks_cards"]:<6} '
            f'{"Y" if r.get("picks_pl_ml") else "N":<6} {edge_s:<5} '
            f'{"Y" if r.get("picks_offseason") else "N":<4}'
        )

    print('\n## RESULTS PAGES (HTTP)')
    print(f'{"Sport":<8} {"Status":<6} {"Join":<5} {"Tabs":<5} {"Glicko":<7}')
    for r in rows:
        print(
            f'{r["sport"]:<8} {r["results_status"]:<6} '
            f'{"Y" if r["results_join"] else "N":<5} {"Y" if r["results_tabs"] else "N":<5} '
            f'{"Y" if r.get("results_grinder") else "N":<7}'
        )

    print('\n## UPCOMING DATA (pipeline)')
    for r in rows:
        n = r.get('upcoming_n')
        if isinstance(n, int) and n > 0:
            fr = r.get('field_rates') or {}
            pl_ml = fr.get('pl_model_home_ml', 0)
            print(
                f'  {r["sport"]:<8} n={n} book_ml={"Y" if r.get("sample_book_ml") else "N"} '
                f'pl_spread={"Y" if r.get("sample_pl_spread") else "N"} '
                f'pl_ml={pl_ml}/{min(n, 8)} spread_ok={"Y" if r.get("spread_ok") else "N"}'
            )
        elif isinstance(n, int):
            print(f'  {r["sport"]:<8} n=0 (no upcoming in 7d window)')

    print('\n' + '=' * 72)
    if issues:
        print('ISSUES FOUND (%d):' % len(issues))
        for i in issues:
            print(f'  - {i}')
        print('VERDICT: FIX ISSUES ABOVE')
        print('=' * 72)
        return 1
    print('VERDICT: ALL CHECKS PASSED')
    print('=' * 72)
    return 0


if __name__ == '__main__':
    sys.exit(main())
