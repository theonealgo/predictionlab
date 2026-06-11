"""MLB moneyline grading uses 0–1 probs consistently; weak season record is model not a tally bug."""
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_mlb_ml_grading_uses_fraction_threshold_not_percent(nhl):
    """ens_prob stored as 0–100 on cards but grading compares raw 0–1 fraction to 0.5."""
    gi = {
        'home_win': True,
        'ens_prob': 58.0,
        'glicko2_prob': 61.0,
        'trueskill_prob': 56.0,
        'elo_prob': 54.0,
        'xgb_prob': 52.0,
    }
    nhl._apply_soccer_ml_grading(
        gi,
        draw_dec=None,
        glicko2_prob=0.61,
        trueskill_prob=0.56,
        elo_prob=0.54,
        xgb_prob=0.52,
        ens_prob=0.58,
        home_won=True,
        is_draw=False,
    )
    assert gi['ens_correct'] is True
    assert gi['glicko2_correct'] is True


def test_mlb_season_consensus_accuracy_near_fifty_two_percent(nhl):
    """Full-season ensemble ~52% — genuine model weakness, not inverted grading."""
    conn = nhl.get_db_connection()
    prob_sql = nhl._predictions_prob_select_sql(conn)
    start, end = nhl._results_season_bounds('MLB', datetime.now())
    rows = conn.execute(
        f'''
            SELECT g.*, {prob_sql}
            FROM games g
            LEFT JOIN predictions p ON g.game_id = p.game_id AND p.sport = 'MLB'
            WHERE g.sport = 'MLB' AND g.home_score IS NOT NULL
              AND date(g.game_date) >= ? AND date(g.game_date) <= ?
        ''',
        (start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')),
    ).fetchall()
    conn.close()
    if len(rows) < 50:
        pytest.skip('not enough MLB games in DB')

    daily = defaultdict(lambda: {'games': []})
    for game in rows:
        hs = nhl._to_float_safe(game['home_score'])
        aw = nhl._to_float_safe(game['away_score'])
        if hs is None or aw is None:
            continue
        home_won = hs > aw
        gd = nhl._normalize_game_date_key(game['game_date'])
        g2, ts, el, xg, ens = nhl._model_probs_for_grading(
            'MLB', game, game['home_team_id'], game['away_team_id'], gd,
        )
        gi = {
            'date': gd,
            'home': game['home_team_id'],
            'away': game['away_team_id'],
            'home_score': int(hs),
            'away_score': int(aw),
            'home_win': home_won,
            'glicko2_prob': round(g2 * 100, 1) if g2 else None,
            'trueskill_prob': round(ts * 100, 1) if ts else None,
            'elo_prob': round(el * 100, 1) if el else None,
            'xgb_prob': round(xg * 100, 1) if xg else None,
            'ens_prob': round(ens * 100, 1) if ens else None,
        }
        nhl._apply_soccer_ml_grading(
            gi,
            draw_dec=None,
            glicko2_prob=g2,
            trueskill_prob=ts,
            elo_prob=el,
            xgb_prob=xg,
            ens_prob=ens,
            home_won=home_won,
            is_draw=False,
        )
        daily[gd]['games'].append(gi)

    stats = nhl.compute_overall_stats_from_daily(daily)
    ens = stats['ensemble']
    assert ens['total'] >= 50
    assert 48.0 <= ens['accuracy'] <= 56.0
