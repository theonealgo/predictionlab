#!/usr/bin/env python3
"""
Results Updater - All Sports
Hard-limited to yesterday (UTC).
Self-heals bad dates (NBA Oct 21 issue).
"""

import sqlite3
import logging
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd
import nfl_data_py as nfl
from nba_api.stats.endpoints import leaguegamefinder

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

DATABASE = 'sports_predictions_original.db'

NOW_UTC = datetime.now(timezone.utc)
YESTERDAY = (NOW_UTC - timedelta(days=1)).date()


def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def grade_predictions():

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE predictions
        SET
            actual_home_score = (
                SELECT home_score 
                FROM games 
                WHERE games.game_id = predictions.game_id
            ),
            actual_away_score = (
                SELECT away_score 
                FROM games 
                WHERE games.game_id = predictions.game_id
            ),
            actual_winner = (
                SELECT 
                    CASE 
                    WHEN home_score > away_score THEN home_team_id
                    ELSE away_team_id
                    END
                FROM games
                WHERE games.game_id = predictions.game_id
            )
        WHERE EXISTS (
            SELECT 1
            FROM games
            WHERE games.game_id = predictions.game_id
            AND games.status='final'
        )
    """)


    cursor.execute("""
        UPDATE predictions
        SET win_prediction_correct =
            CASE
                WHEN predicted_winner = actual_winner THEN 1
                ELSE 0
            END,
            result_updated_at=CURRENT_TIMESTAMP
        WHERE actual_winner IS NOT NULL
    """)

    conn.commit()
    print(f"✓ Graded {cursor.rowcount} predictions")

    conn.close()
# ============================================================
# NHL
# ============================================================

def update_nhl_scores():
    logger.info("=" * 60)
    logger.info("NHL SCORE UPDATE")
    logger.info("=" * 60)

    start_date = YESTERDAY - timedelta(days=30)

    nhl_team_map = {
        'ANA': 'Anaheim Ducks', 'BOS': 'Boston Bruins', 'BUF': 'Buffalo Sabres',
        'CGY': 'Calgary Flames', 'CAR': 'Carolina Hurricanes', 'CHI': 'Chicago Blackhawks',
        'COL': 'Colorado Avalanche', 'CBJ': 'Columbus Blue Jackets', 'DAL': 'Dallas Stars',
        'DET': 'Detroit Red Wings', 'EDM': 'Edmonton Oilers', 'FLA': 'Florida Panthers',
        'LAK': 'Los Angeles Kings', 'MIN': 'Minnesota Wild', 'MTL': 'Montreal Canadiens',
        'NSH': 'Nashville Predators', 'NJD': 'New Jersey Devils', 'NYI': 'New York Islanders',
        'NYR': 'New York Rangers', 'OTT': 'Ottawa Senators', 'PHI': 'Philadelphia Flyers',
        'PIT': 'Pittsburgh Penguins', 'SJS': 'San Jose Sharks', 'SEA': 'Seattle Kraken',
        'STL': 'St. Louis Blues', 'TBL': 'Tampa Bay Lightning', 'TOR': 'Toronto Maple Leafs',
        'VAN': 'Vancouver Canucks', 'VGK': 'Vegas Golden Knights', 'WSH': 'Washington Capitals',
        'WPG': 'Winnipeg Jets', 'UTA': 'Utah Hockey Club'
    }

    conn = get_db_connection()
    cursor = conn.cursor()
    updates = 0

    d = start_date
    while d <= YESTERDAY:
        date_str = d.isoformat()

        try:
            r = requests.get(f"https://api-web.nhle.com/v1/score/{date_str}", timeout=5)
            if r.status_code != 200:
                d += timedelta(days=1)
                continue

            for g in r.json().get("games", []):
                if g.get("gameState") not in ("OFF", "FINAL"):
                    continue

                # HARD BLOCK future / preseason NHL games
                if g.get("gameDate") != date_str:
                    continue

                home = nhl_team_map.get(g["homeTeam"]["abbrev"])
                away = nhl_team_map.get(g["awayTeam"]["abbrev"])
                hs = g["homeTeam"].get("score", 0)
                as_ = g["awayTeam"].get("score", 0)
                gid = f"NHL_{g['id']}"

                cursor.execute("""
                    UPDATE games
                    SET home_score = ?, away_score = ?, status = 'final'
                    WHERE sport = 'NHL'
                      AND game_id = ?
                      AND (
                          home_score IS NULL OR away_score IS NULL
                          OR home_score != ? OR away_score != ?
                      )
                """, (hs, as_, gid, hs, as_))

                updates += cursor.rowcount

        except Exception as e:
            logger.warning(f"NHL {date_str}: {e}")

        d += timedelta(days=1)

    conn.commit()
    conn.close()
    logger.info(f"✓ NHL updated {updates} games (through {YESTERDAY})")
    return updates


# ============================================================
# NFL
# ============================================================

def update_nfl_scores():
    logger.info("=" * 60)
    logger.info("NFL SCORE UPDATE")
    logger.info("=" * 60)

    years = [NOW_UTC.year - 1, NOW_UTC.year]
    schedule = nfl.import_schedules(years)

    if schedule.empty:
        return 0

    schedule["gameday"] = pd.to_datetime(schedule["gameday"]).dt.date
    finished = schedule[
        (schedule["result"].notna()) &
        (schedule["gameday"] <= YESTERDAY)
    ]

    conn = get_db_connection()
    cursor = conn.cursor()
    updates = 0

    for _, g in finished.iterrows():
        cursor.execute("""
            UPDATE games
            SET home_score = ?, away_score = ?, status = 'final'
            WHERE sport = 'NFL'
              AND game_id = ?
              AND (
                  home_score IS NULL OR away_score IS NULL
                  OR home_score != ? OR away_score != ?
              )
        """, (
            g["home_score"], g["away_score"], g["game_id"],
            g["home_score"], g["away_score"]
        ))

        updates += cursor.rowcount

    conn.commit()
    conn.close()
    logger.info(f"✓ NFL updated {updates} games (through {YESTERDAY})")
    return updates


# ============================================================
# NBA (DATE SELF-HEALING)
# ============================================================

def update_nba_scores():
    logger.info("=" * 60)
    logger.info("NBA SCORE UPDATE")
    logger.info("=" * 60)

    season = (
        f"{NOW_UTC.year}-{str(NOW_UTC.year + 1)[-2:]}"
        if NOW_UTC.month >= 10
        else f"{NOW_UTC.year - 1}-{str(NOW_UTC.year)[-2:]}"
    )

    nba_team_map = {
        'ATL': 'Atlanta Hawks', 'BOS': 'Boston Celtics', 'BKN': 'Brooklyn Nets',
        'CHA': 'Charlotte Hornets', 'CHI': 'Chicago Bulls', 'CLE': 'Cleveland Cavaliers',
        'DAL': 'Dallas Mavericks', 'DEN': 'Denver Nuggets', 'DET': 'Detroit Pistons',
        'GSW': 'Golden State Warriors', 'HOU': 'Houston Rockets', 'IND': 'Indiana Pacers',
        'LAC': 'LA Clippers', 'LAL': 'Los Angeles Lakers', 'MEM': 'Memphis Grizzlies',
        'MIA': 'Miami Heat', 'MIL': 'Milwaukee Bucks', 'MIN': 'Minnesota Timberwolves',
        'NOP': 'New Orleans Pelicans', 'NYK': 'New York Knicks', 'OKC': 'Oklahoma City Thunder',
        'ORL': 'Orlando Magic', 'PHI': 'Philadelphia 76ers', 'PHX': 'Phoenix Suns',
        'POR': 'Portland Trail Blazers', 'SAC': 'Sacramento Kings', 'SAS': 'San Antonio Spurs',
        'TOR': 'Toronto Raptors', 'UTA': 'Utah Jazz', 'WAS': 'Washington Wizards'
    }

    gf = leaguegamefinder.LeagueGameFinder(season_nullable=season, timeout=10)
    df = gf.get_data_frames()[0]

    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"]).dt.date
    df = df[df["GAME_DATE"] <= YESTERDAY]

    conn = get_db_connection()
    cursor = conn.cursor()
    updates = 0
    seen = set()

    for _, r in df.iterrows():
        gid = r["GAME_ID"]
        if gid in seen:
            continue
        seen.add(gid)

        opp = df[(df["GAME_ID"] == gid) & (df["TEAM_ID"] != r["TEAM_ID"])]
        if opp.empty:
            continue

        matchup = r["MATCHUP"]
        if " @ " in matchup:
            away, home = matchup.split(" @ ")
            away_score, home_score = r["PTS"], opp.iloc[0]["PTS"]
        else:
            home, away = matchup.split(" vs. ")
            home_score, away_score = r["PTS"], opp.iloc[0]["PTS"]

        home_team = nba_team_map.get(home, home)
        away_team = nba_team_map.get(away, away)
        game_date = r["GAME_DATE"]

        cursor.execute("""
            UPDATE games
            SET
                game_date = ?,
                home_score = ?,
                away_score = ?,
                status = 'final'
            WHERE sport = 'NBA'
              AND home_team_id = ?
              AND away_team_id = ?
              AND (
                    DATE(game_date) != DATE(?)
                    OR home_score IS NULL
                    OR away_score IS NULL
                    OR home_score != ?
                    OR away_score != ?
              )
        """, (
            game_date,
            int(home_score),
            int(away_score),
            home_team,
            away_team,
            game_date,
            int(home_score),
            int(away_score)
        ))

        updates += cursor.rowcount

    conn.commit()
    conn.close()
    logger.info(f"✓ NBA updated {updates} games (through {YESTERDAY})")
    return updates


# ============================================================
# RUN ALL
# ============================================================

def update_all_scores():
    print("\n" + "=" * 60)
    print("RESULTS UPDATER - ALL SPORTS")
    print(f"Updating through {YESTERDAY} (UTC)")
    print("=" * 60 + "\n")

    total = 0
    total += update_nhl_scores()
    total += update_nfl_scores()
    total += update_nba_scores()

    print("\n" + "=" * 60)
    print(f"COMPLETE: {total} games updated")
    print("=" * 60 + "\n")
    return total


if __name__ == "__main__":
    update_all_scores()