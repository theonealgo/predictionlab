"""Soccer results query must expose all model prob columns used in sport_results."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def nhl():
    import NHL77FINAL as N
    return N


def test_fetch_soccer_completed_games_includes_model_prob_columns(nhl):
    conn = nhl.get_db_connection()
    try:
        rows = nhl._fetch_soccer_completed_games(
            conn, selected_league='English Premier League', limit=3,
        )
        if not rows:
            pytest.skip('no completed EPL games in DB')
        row = rows[0]
        for col in (
            'glicko_home_prob',
            'trueskill_home_prob',
            'catboost_home_prob',
            'meta_home_prob',
            'elo_home_prob',
            'xgboost_home_prob',
        ):
            assert col in row.keys(), f'missing column {col}'
            _ = row[col]
    finally:
        conn.close()
