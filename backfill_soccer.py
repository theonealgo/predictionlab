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

# League code → display name mapping (kept in sync with SOCCER_LEAGUE_ENDPOINTS)
LEAGUES = {
    'eng.1': 'English Premier League',
    'esp.1': 'Spanish LaLiga',
    'esp.2': 'Spanish Segunda División',
    'ger.1': 'German Bundesliga',
    'ita.1': 'Italian Serie A',
    'fra.1': 'French Ligue 1',
    'ned.1': 'Dutch Eredivisie',
    'por.1': 'Portuguese Primeira Liga',
    'eng.2': 'EFL Championship',
    'eng.fa': 'FA Cup',
    'eng.league_cup': 'EFL Cup',
    'uefa.champions': 'UEFA Champions League',
    'uefa.europa': 'UEFA Europa League',
    'uefa.europa.conf': 'UEFA Europa Conference League',
    'usa.1': 'Major League Soccer',
    'mex.1': 'Liga MX',
    'conmebol.libertadores': 'Copa Libertadores',
    'concacaf.champions': 'CONCACAF Champions Cup',
    'concacaf.leagues.cup': 'Leagues Cup',
    'fifa.world': 'FIFA World Cup',
    'fifa.worldq.uefa': 'FIFA World Cup Qualifiers (UEFA)',
    'fifa.worldq.conmebol': 'FIFA World Cup Qualifiers (CONMEBOL)',
    'fifa.worldq.caf': 'FIFA World Cup Qualifiers (CAF)',
    'fifa.worldq.concacaf': 'FIFA World Cup Qualifiers (CONCACAF)',
}

def _parse_event_date(event):
    """Extract game date from ESPN event."""
    try:
        dt_str = event.get('date', '')
        if dt_str:
            return dt_str[:10]  # '2026-01-15T...' -> '2026-01-15'
    except Exception:
        pass
    return datetime.now().strftime('%Y-%m-%d')


def backfill():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    total_inserted = 0
    total_skipped = 0

    # Use date range queries to get full season data in fewer API calls
    now = datetime.now()
    # Fetch 2 windows: current year and last few months of previous year
    date_ranges = [
        (f'{now.year}0101', now.strftime('%Y%m%d')),           # Jan 1 to today
        (f'{now.year - 1}0801', f'{now.year - 1}1231'),       # Aug-Dec last year
    ]

    for league_code, league_name in LEAGUES.items():
        logger.info(f"Fetching {league_name} ({league_code})...")
        league_inserted = 0

        for start_date, end_date in date_ranges:
            page = 1
            while page <= 5:  # max 5 pages per range
                try:
                    url = (f'https://site.api.espn.com/apis/site/v2/sports/soccer/'
                           f'{league_code}/scoreboard?dates={start_date}-{end_date}&limit=100&page={page}')
                    resp = requests.get(url, timeout=12)
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                except Exception as e:
                    logger.debug(f"  Fetch error: {e}")
                    break

                events = data.get('events', [])
                if not events:
                    break

                for event in events:
                    comp = event.get('competitions', [{}])[0]
                    competitors = comp.get('competitors', [])
                    if len(competitors) != 2:
                        continue

                    status = event.get('status', {}).get('type', {}).get('name', '')
                    if 'FINAL' not in status and 'FULL_TIME' not in status:
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
                    game_date = _parse_event_date(event)

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

                # If we got fewer than 100, no more pages
                if len(events) < 100:
                    break
                page += 1

        conn.commit()  # commit per league
        logger.info(f"  {league_name}: {league_inserted} games inserted")

    conn.close()

    logger.info(f"\nDone. Inserted {total_inserted} games, skipped {total_skipped} duplicates.")
    logger.info("Soccer models will retrain automatically on next page load.")


if __name__ == '__main__':
    backfill()
