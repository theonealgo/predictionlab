#!/usr/bin/env python3
"""Audit upcoming prediction cards: render pipeline + required display fields."""
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPORTS = ['NBA', 'MLB', 'NHL', 'NFL', 'NCAAB', 'NCAAF', 'WNBA', 'NCAAW', 'SOCCER']

# Fields the wireframe / game_card_body expect after _prepare_pred_card_display
UPCOMING_FIELDS = {
    'identity': ['home_team_id', 'away_team_id', 'game_date'],
    'ml_pick': ['ensemble_prob', 'predicted_winner'],
    'pl_model': ['disp_pl_spread', 'disp_pl_total', 'pl_model_home_ml', 'pl_model_away_ml'],
    'xsharp': ['disp_xs_spread', 'disp_xs_total'],
    'books': ['disp_book_spread', 'disp_book_total', 'book_home_moneyline'],
    'proj_scores': ['pl_proj_home_pts', 'pl_proj_away_pts', 'xs_proj_home_pts', 'xs_proj_away_pts'],
}


def audit_pred(p):
    missing = defaultdict(list)
    for group, keys in UPCOMING_FIELDS.items():
        for k in keys:
            v = p.get(k)
            if v is None or v == '':
                missing[group].append(k)
    return dict(missing)


def main():
    import NHL77FINAL as N

    print('=' * 70)
    print('PREDICTION CARD FIELD AUDIT (upcoming games only)')
    print('=' * 70)

    all_ok = True
    for sport in SPORTS:
        print(f'\n## {sport}')
        try:
            preds = N.get_upcoming_predictions(sport, days=7)
        except Exception as e:
            print(f'  LOAD FAILED: {e}')
            all_ok = False
            continue
        upcoming = [p for p in (preds or []) if p.get('home_score') is None]
        if not upcoming:
            print('  No upcoming games in 7-day window')
            continue
        print(f'  Upcoming games: {len(upcoming)}')
        try:
            N._refresh_books_on_predictions(sport, upcoming)
        except Exception:
            pass
        for p in upcoming:
            N._finalize_prediction_odds(p)
            N._prepare_pred_card_display(p)
        # Count field presence
        stats = {g: {k: 0 for k in keys} for g, keys in UPCOMING_FIELDS.items()}
        games_with_gaps = []
        for i, p in enumerate(upcoming):
            miss = audit_pred(p)
            if miss:
                games_with_gaps.append((p.get('home_team_id'), p.get('away_team_id'), p.get('game_date'), miss))
            for g, keys in UPCOMING_FIELDS.items():
                for k in keys:
                    v = p.get(k)
                    if v is not None and v != '':
                        stats[g][k] += 1
        n = len(upcoming)
        print(f'  {"Group":<14} {"Field":<22} {"Have":>5}/{n}')
        for g, keys in UPCOMING_FIELDS.items():
            for k in keys:
                c = stats[g][k]
                flag = '' if c == n else (' PARTIAL' if c > 0 else ' MISSING')
                if c < n:
                    all_ok = False
                print(f'  {g:<14} {k:<22} {c:>5}/{n}{flag}')
        if games_with_gaps:
            print(f'  Sample gaps (first 3 of {len(games_with_gaps)}):')
            for home, away, gd, miss in games_with_gaps[:3]:
                print(f'    {gd} {away} @ {home}: {miss}')

    # HTTP render check
    print('\n' + '=' * 70)
    print('HTTP RENDER (game-card + key labels in HTML)')
    print('=' * 70)
    from NHL77FINAL import app
    labels = ['Books', 'Prediction Lab', 'XSharp', 'class="game-card"', 'odds-pricing-table']
    with app.test_client() as c:
        for sport in SPORTS:
            slug = N.SPORT_SEO_SLUGS.get(sport, sport.lower() + '-picks')
            r = c.get(f'/{slug}', headers={'Host': '127.0.0.1'})
            body = r.get_data(as_text=True)
            cards = body.count('class="game-card"')
            row = {'cards': cards}
            for lb in labels:
                row[lb] = lb in body
            miss = [k for k, v in row.items() if k != 'cards' and not v]
            status = 'OK' if cards > 0 and not miss else ('OFFSEASON' if cards == 0 and 'return when' in body.lower() else 'GAP')
            if status not in ('OK', 'OFFSEASON'):
                all_ok = False
            print(f'  {sport:<8} cards={cards:<4} html_labels_missing={miss or "none"} -> {status}')

    print('\n' + '=' * 70)
    print('RESULT:', 'PASS' if all_ok else 'GAPS REMAIN (see PARTIAL/MISSING above)')
    print('=' * 70)
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
