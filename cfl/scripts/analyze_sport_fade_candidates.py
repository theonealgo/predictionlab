#!/usr/bin/env python3
"""Per-sport spread/ML/O-U win rates using production grading (_compute_spread_total_for_daily)."""
from __future__ import annotations

import copy
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import NHL77FINAL as nhl  # noqa: E402

SPORTS = ['NBA', 'NHL', 'MLB', 'NFL', 'NCAAB', 'NCAAF', 'NCAAW', 'WNBA', 'SOCCER']
START = datetime(2020, 1, 1)
END = datetime.now()


def _load_daily_results(sport: str, limit: int = 5000):
    if sport == 'NFL':
        weekly = nhl.calculate_nfl_weekly_performance()
        return nhl._daily_results_from_weekly(weekly) if weekly else None
    if sport == 'NBA':
        weekly = nhl.calculate_nba_weekly_performance()
        return nhl._daily_results_from_weekly(weekly) if weekly else None

    try:
        conn = nhl.get_db_connection()
        rows = conn.execute(
            '''
            SELECT g.*, p.elo_home_prob, p.xgboost_home_prob, p.logistic_home_prob, p.win_probability
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = ?
            WHERE g.sport = ?
              AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL
              AND date(g.game_date) BETWEEN ? AND ?
            ORDER BY g.game_date DESC
            LIMIT ?
            ''',
            (sport, sport, START.strftime('%Y-%m-%d'), END.strftime('%Y-%m-%d'), limit),
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f'  load error {sport}: {e}', file=sys.stderr)
        return None

    if not rows:
        return None

    daily_results = defaultdict(lambda: {'games': []})
    for game in rows:
        home_score = nhl._to_float_safe(game['home_score'])
        away_score = nhl._to_float_safe(game['away_score'])
        if home_score is None or away_score is None:
            continue
        home_won = home_score > away_score
        is_draw = False
        if sport == 'SOCCER' and abs(home_score - away_score) < 1e-9:
            is_draw = True
            home_won = None
        home_team = game['home_team_id']
        away_team = game['away_team_id']
        _raw_date = nhl._to_date_str(game['game_date'])
        game_date = _raw_date[:10] if _raw_date else None
        league_name = game.get('league') if isinstance(game, dict) else game['league']
        if sport == 'SOCCER':
            league_name = nhl._canonical_soccer_league_name(league_name) or league_name
            if not league_name or league_name not in nhl.SOCCER_LEAGUE_ORDER:
                continue

        elo_prob = nhl._to_float_safe(game['elo_home_prob'], 0.5)
        xgb_prob = nhl._to_float_safe(game['xgboost_home_prob'])
        if xgb_prob is None:
            xgb_prob = nhl._to_float_safe(game['elo_home_prob'], 0.5)
        ens_prob = nhl._to_float_safe(game['win_probability'])
        if ens_prob is None:
            ens_prob = nhl._to_float_safe(game['elo_home_prob'], 0.5)

        v2 = nhl.get_v2_prediction(sport, home_team, away_team, game_date) if sport != 'SOCCER' else None
        glicko2_prob = v2.get('glicko2_prob') if v2 else None
        trueskill_prob = v2.get('trueskill_prob') if v2 else None
        if v2:
            xgb_prob = v2.get('xgboost_prob', xgb_prob)
            ens_prob = nhl._compute_ensemble_prob(
                glicko2_prob, trueskill_prob, xgb_prob, elo_prob, fallback=ens_prob,
            )

        game_info = {
            'game_id': game['game_id'],
            'date': game_date or 'Unknown',
            'home': home_team,
            'away': away_team,
            'league': league_name or sport,
            'home_score': int(home_score) if abs(home_score - round(home_score)) < 1e-6 else round(home_score, 1),
            'away_score': int(away_score) if abs(away_score - round(away_score)) < 1e-6 else round(away_score, 1),
            'home_win': home_won,
            'is_draw': is_draw,
            'glicko2_prob': round(glicko2_prob * 100, 1) if glicko2_prob is not None else None,
            'trueskill_prob': round(trueskill_prob * 100, 1) if trueskill_prob is not None else None,
            'elo_prob': round(elo_prob * 100, 1),
            'xgb_prob': round(xgb_prob * 100, 1),
            'ens_prob': round(ens_prob * 100, 1),
            'glicko2_correct': (glicko2_prob >= 0.5) == home_won if glicko2_prob is not None and home_won is not None else None,
            'trueskill_correct': (trueskill_prob >= 0.5) == home_won if trueskill_prob is not None and home_won is not None else None,
            'elo_correct': (elo_prob >= 0.5) == home_won if home_won is not None else None,
            'xgb_correct': (xgb_prob >= 0.5) == home_won if home_won is not None else None,
            'ens_correct': (ens_prob >= 0.5) == home_won if ens_prob is not None and home_won is not None else None,
            'skip_grading': True if home_won is None else False,
        }
        daily_results[game_info['date']]['games'].append(game_info)
    return daily_results


def _grade_sport(sport: str, daily, *, apply_mlb_fade: bool = True):
    """Run production spread/total grading; return tally dict."""
    if sport == 'MLB' and not apply_mlb_fade:
        orig = nhl._apply_mlb_spread_fade
        nhl._apply_mlb_spread_fade = lambda d: None  # noqa: E731
        try:
            nhl._compute_spread_total_for_daily(sport, daily)
        finally:
            nhl._apply_mlb_spread_fade = orig
    else:
        nhl._compute_spread_total_for_daily(sport, daily)

    tally = nhl.compute_model_tally_for_range(daily)
    sp = tally.get('spread') or {}
    ou = tally.get('total_ou') or {}
    ens = tally.get('ensemble') or {}
    return {
        'spread_pct': sp.get('accuracy'),
        'spread_n': sp.get('total', 0),
        'spread_w': sp.get('correct', 0),
        'spread_l': sp.get('total', 0) - sp.get('correct', 0),
        'ou_pct': ou.get('accuracy'),
        'ou_n': ou.get('total', 0),
        'ml_pct': ens.get('accuracy'),
        'ml_n': ens.get('total', 0),
        'games': tally.get('games', 0),
    }


FADE_THRESHOLD = 55.0
STRONG_N = 100


def _flip_rec(pct, n, metric: str):
    """Recommend model-level flip when win rate is below FADE_THRESHOLD."""
    if pct is None or n == 0:
        return 'n/a', f'no {metric} grades'
    if n < 50:
        return 'insufficient N', f'N={n} (<50) — need more {metric} grades'
    if n >= STRONG_N and pct < FADE_THRESHOLD:
        return 'YES', f'{pct:.1f}% with N={n} (<{FADE_THRESHOLD:.0f}%, strong signal)'
    if pct < FADE_THRESHOLD:
        return 'borderline', f'{pct:.1f}% with N={n} (<{FADE_THRESHOLD:.0f}%, N<{STRONG_N})'
    return 'NO', f'{pct:.1f}% with N={n} (>= {FADE_THRESHOLD:.0f}%)'


def main():
    print('Sport fade analysis — production grading (_compute_spread_total_for_daily)')
    print(f'Date window: {START.date()} .. {END.date()}')
    print(f'Flip threshold: win rate < {FADE_THRESHOLD:.0f}% (YES when N>={STRONG_N})')
    print()
    rows = []

    for sport in SPORTS:
        daily_raw = _load_daily_results(sport)
        if not daily_raw:
            print(f'{sport}: no data')
            continue
        n_games = sum(len(d['games']) for d in daily_raw.values())
        print(f'Grading {sport} ({n_games} final games)...', flush=True)

        if sport == 'MLB':
            daily_pre = copy.deepcopy(daily_raw)
            daily_post = copy.deepcopy(daily_raw)
            pre = _grade_sport(sport, daily_pre, apply_mlb_fade=False)
            post = _grade_sport(sport, daily_post, apply_mlb_fade=True)
            sp_rec, sp_why = _flip_rec(pre['spread_pct'], pre['spread_n'], 'spread')
            ml_rec, ml_why = _flip_rec(post['ml_pct'], post['ml_n'], 'ML')
            ou_rec, ou_why = _flip_rec(post['ou_pct'], post['ou_n'], 'O/U')
            rows.append({
                'sport': sport,
                'spread_pct': pre['spread_pct'],
                'spread_n': pre['spread_n'],
                'spread_w': pre['spread_w'],
                'spread_l': pre['spread_l'],
                'spread_flip': 'NO (already faded)',
                'spread_why': (
                    f'Pre-fade {pre["spread_w"]}-{pre["spread_l"]} ({pre["spread_pct"]}%); '
                    f'post-fade spread {post["spread_pct"]}% (N={post["spread_n"]})'
                ),
                'ml_pct': post['ml_pct'],
                'ml_n': post['ml_n'],
                'ml_flip': ml_rec,
                'ml_why': ml_why,
                'ou_pct': post['ou_pct'],
                'ou_n': post['ou_n'],
                'ou_flip': ou_rec,
                'ou_why': ou_why,
            })
            continue

        daily = copy.deepcopy(daily_raw)
        stats = _grade_sport(sport, daily, apply_mlb_fade=True)
        sp_rec, sp_why = _flip_rec(stats['spread_pct'], stats['spread_n'], 'spread')
        ml_rec, ml_why = _flip_rec(stats['ml_pct'], stats['ml_n'], 'ML')
        ou_rec, ou_why = _flip_rec(stats['ou_pct'], stats['ou_n'], 'O/U')
        rows.append({
            'sport': sport,
            'spread_pct': stats['spread_pct'],
            'spread_n': stats['spread_n'],
            'spread_w': stats['spread_w'],
            'spread_l': stats['spread_l'],
            'spread_flip': sp_rec,
            'spread_why': sp_why,
            'ml_pct': stats['ml_pct'],
            'ml_n': stats['ml_n'],
            'ml_flip': ml_rec,
            'ml_why': ml_why,
            'ou_pct': stats['ou_pct'],
            'ou_n': stats['ou_n'],
            'ou_flip': ou_rec,
            'ou_why': ou_why,
        })

    def _pct(v):
        return f'{v:.1f}' if v is not None else 'n/a'

    hdr = (
        f"{'Sport':<8} | {'Spread%':>7} {'N':>5} {'Flip':<12} | "
        f"{'ML%':>6} {'N':>5} {'Flip':<12} | "
        f"{'O/U%':>6} {'N':>5} {'Flip':<12}"
    )
    print()
    print(hdr)
    print('-' * len(hdr))
    flip_spread = []
    flip_ml = []
    flip_ou = []
    for r in rows:
        print(
            f"{r['sport']:<8} | "
            f"{_pct(r['spread_pct']):>7} {r['spread_n']:>5} {r['spread_flip']:<12} | "
            f"{_pct(r['ml_pct']):>6} {r['ml_n']:>5} {r['ml_flip']:<12} | "
            f"{_pct(r['ou_pct']):>6} {r['ou_n']:>5} {r['ou_flip']:<12}"
        )
        for metric, key, bucket in (
            ('spread', 'spread_flip', flip_spread),
            ('ML', 'ml_flip', flip_ml),
            ('O/U', 'ou_flip', flip_ou),
        ):
            rec = r[key]
            if rec == 'YES':
                bucket.append(f"{r['sport']} {metric}")
            elif rec == 'borderline':
                bucket.append(f"{r['sport']} {metric} (borderline N)")

    print()
    print(f'ACTIONABLE flips (win% < {FADE_THRESHOLD:.0f}%, N>={STRONG_N}):')
    print(f"  Spread: {', '.join(flip_spread) if flip_spread else '(none)'}")
    print(f"  ML:     {', '.join(flip_ml) if flip_ml else '(none)'}")
    print(f"  O/U:    {', '.join(flip_ou) if flip_ou else '(none)'}")
    borderline = [
        f"{r['sport']} spread={r['spread_flip']} ml={r['ml_flip']} ou={r['ou_flip']}"
        for r in rows
        if 'borderline' in (r['spread_flip'], r['ml_flip'], r['ou_flip'])
    ]
    if borderline:
        print('  Borderline (below threshold but N<100):')
        for line in borderline:
            print(f'    {line}')


if __name__ == '__main__':
    main()
