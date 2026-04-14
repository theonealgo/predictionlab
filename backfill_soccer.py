#!/usr/bin/env python3
"""
Soccer Historical Backfill Script
===================================
Fetches completed soccer match results from ESPN API and inserts them
into the games table. Run once to populate training data for soccer models.

Usage:
    python backfill_soccer.py

This fetches the last 90 days of results per league. The soccer_models.py
bundle will automatically retrain when it sees enough completed games.
"""

import sqlite3
import requests
import os
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), 'sports_predictions_original.db')
if os.path.isdir('/data'):
    DB_PATH = '/data/sports_predictions_original.db'

# League code → display name mapping
LEAGUES = {
    'eng.1': 'English Premier League',
    'esp.1': 'Spanish LaLiga',
    'ger.1': 'German Bundesliga',
    'ita.1': 'Italian Serie A',
    'fra.1': 'French Ligue 1',
    'ned.1': 'Dutch Eredivisie',
    'por.1': 'Portuguese Primeira Liga',
    'eng.2': 'EFL Championship',
    'uefa.champions': 'UEFA Champions League',
    'uefa.europa': 'UEFA Europa League',
    'uefa.europa.conf': 'UEFA Europa Conference League',
    'usa.1': 'Major League Soccer',
    'mex.1': 'Liga MX',
    'conmebol.libertadores': 'Copa Libertadores',
}

DAYS_BACK = 90  # How far back to fetch


def backfill():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    total_inserted = 0
    total_skipped = 0

    for league_code, league_name in LEAGUES.items():
        logger.info(f"Fetching {league_name} ({league_code})...")
        league_inserted = 0

        for days_offset in range(DAYS_BACK):
            date = datetime.now() - timedelta(days=days_offset)
            date_str = date.strftime('%Y%m%d')

            try:
                url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/{league_code}/scoreboard?dates={date_str}'
                resp = requests.get(url, timeout=8)
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except Exception as e:
                logger.debug(f"  {date_str}: fetch error: {e}")
                continue

            events = data.get('events', [])
            if not events:
                continue

            for event in events:
                comp = event.get('competitions', [{}])[0]
                competitors = comp.get('competitors', [])
                if len(competitors) != 2:
                    continue

                status = event.get('status', {}).get('type', {}).get('name', '')
                if not status.startswith('STATUS_FINAL'):
                    continue

                home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                if not home or not away:
                    continue

                home_team = home.get('team', {}).get('displayName', '')
                away_team = away.get('team', {}).get('displayName', '')
                try:
                    home_score = int(home.get('score', 0))
                    away_score = int(away.get('score', 0))
                except (ValueError, TypeError):
                    continue

                event_id = event.get('id', '')
                game_id = f'SOCCER_{league_code}_{event_id}'
                game_date = date.strftime('%Y-%m-%d')

                # Check if already exists
                existing = cursor.execute(
                    'SELECT 1 FROM games WHERE game_id = ? AND sport = ?',
                    (game_id, 'SOCCER')
                ).fetchone()

                if existing:
                    total_skipped += 1
                    continue

                try:
                    cursor.execute(
                        '''INSERT INTO games (sport, league, game_id, season, game_date,
                           home_team_id, away_team_id, home_score, away_score, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'final')''',
                        ('SOCCER', league_name, game_id, 2025, game_date,
                         home_team, away_team, home_score, away_score)
                    )
                    league_inserted += 1
                    total_inserted += 1
                except Exception as e:
                    logger.debug(f"  Insert error: {e}")

        logger.info(f"  {league_name}: {league_inserted} games inserted")

    conn.commit()
    conn.close()

    logger.info(f"\nDone. Inserted {total_inserted} games, skipped {total_skipped} duplicates.")
    logger.info("Soccer models will retrain automatically on next page load.")


if __name__ == '__main__':
    backfill()
