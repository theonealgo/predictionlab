#!/usr/bin/env python3
"""
Update NBA Scores
Runs the NBA score updater to fetch and populate scores from the API.
"""

import sqlite3
import logging
from datetime import datetime
from nbaschedules import get_nba_schedule
from nba_api.stats.endpoints import leaguegamefinder
from nbaschedules_DO_NOT_CODE import get_nba_schedule

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE = 'sports_predictions_original.db'


def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def update_nba_scores():
    """
    Fetches and updates NBA scores using official schedule as source of truth.
    """
    try:
        logger.info("Fetching NBA scores using official schedule...")

        # Get the official NBA schedule
        official_schedule = get_nba_schedule()

        # NBA team abbreviation to full name mapping
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

        # Reverse mapping
        nba_team_map['PHO'] = 'Phoenix Suns'
        nba_team_map['NOP'] = 'New Orleans Pelicans'
        nba_team_map['BRK'] = 'Brooklyn Nets'

        conn = get_db_connection()
        cursor = conn.cursor()

        updates_count = 0

        # Get games from NBA API
        today = datetime.now()
        if today.month >= 10:
            season = f"{today.year}-{str(today.year + 1)[-2:]}"
        else:
            season = f"{today.year - 1}-{str(today.year)[-2:]}"

        logger.info("Fetching games from NBA API...")
        gamefinder = leaguegamefinder.LeagueGameFinder(
            season_nullable=season,
            league_id_nullable='00',
            timeout=10
        )
        games_df = gamefinder.get_data_frames()[0]

        # Filter for current season games from start through today only
        today_str = today.strftime('%Y-%m-%d')
        games_df = games_df[
            (games_df['GAME_DATE'] >= '2024-10-21') &
            (games_df['GAME_DATE'] <= today_str)
        ]

        logger.info(f"Found {len(games_df)} game records from API")

        # Build a lookup of API games
        api_games = {}
        processed_game_ids = set()

        for _, row in games_df.iterrows():
            game_id = row['GAME_ID']
            if game_id in processed_game_ids:
                continue
            processed_game_ids.add(game_id)

            team_abbr = row['TEAM_ABBREVIATION']
            matchup = row['MATCHUP']
            pts = row['PTS']

            # Parse matchup
            if ' @ ' in matchup:
                away_abbr, home_abbr = matchup.split(' @ ')
            elif ' vs. ' in matchup:
                home_abbr, away_abbr = matchup.split(' vs. ')
            else:
                continue

            # Get opponent score
            opponent_row = games_df[
                (games_df['GAME_ID'] == game_id) &
                (games_df['TEAM_ABBREVIATION'] != team_abbr)
            ]

            if opponent_row.empty:
                continue

            opponent_pts = opponent_row.iloc[0]['PTS']

            # Determine home/away scores
            if ' @ ' in matchup:
                away_score = pts
                home_score = opponent_pts
            else:
                home_score = pts
                away_score = opponent_pts

            # Convert to full team names
            home_team = nba_team_map.get(home_abbr, home_abbr)
            away_team = nba_team_map.get(away_abbr, away_abbr)

            # Store in API lookup
            game_date_api = row['GAME_DATE']
            game_key = (game_date_api, home_team, away_team)
            api_games[game_key] = (int(home_score), int(away_score))

        logger.info(f"Processed {len(api_games)} unique games from API")

        # Match API games to official schedule and update database
        for match in official_schedule:
            home_team = match['home_team']
            away_team = match['away_team']

            date_str = match['date']
            try:
                date_parts = date_str.split(', ')
                if len(date_parts) >= 3:
                    month_day_year = date_parts[1] + ' ' + date_parts[2].split(' ')[0]
                    parsed_date = datetime.strptime(month_day_year, '%b %d %Y')

                    api_date = parsed_date.strftime('%Y-%m-%d')
                    db_date = parsed_date.strftime('%Y-%m-%d')

                    game_key = (api_date, home_team, away_team)
                    if game_key in api_games:
                        home_score, away_score = api_games[game_key]

                        cursor.execute("""
                            UPDATE games
                            SET home_score = ?, away_score = ?, status = 'final'
                            WHERE sport = 'NBA'
                              AND home_team_id = ?
                              AND away_team_id = ?
                              AND game_date LIKE ?
                              AND (home_score IS NULL OR home_score != ?)
                        """, (
                            home_score,
                            away_score,
                            home_team,
                            away_team,
                            f"{db_date}%",
                            home_score
                        ))

                        if cursor.rowcount > 0:
                            updates_count += 1
                            logger.info(
                                f"✓ Updated {home_team} vs {away_team} on {db_date}: "
                                f"{home_score}-{away_score}"
                            )

            except Exception as date_error:
                continue

        conn.commit()
        conn.close()
        logger.info(f"\n✓ Successfully updated {updates_count} NBA game scores")

    except Exception as e:
        logger.error(f"Error updating NBA scores: {e}")
        raise


if __name__ == '__main__':
    print("=" * 60)
    print("NBA Score Updater")
    print("=" * 60)
    update_nba_scores()
    print("\n✓ Complete!")