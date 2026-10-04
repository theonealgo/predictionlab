#!/usr/bin/env python3
"""
Daily Schedule Updater
Fetches upcoming games from ESPN and updates database.
Run this daily to keep schedule current.
"""

import requests
from datetime import datetime, timedelta
import sqlite3

DB_PATH = "sports_predictions_original.db"

ESPN_APIS = {
    'NBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
    'NHL': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard',
    'NFL': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
    'NCAAM': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
    'NCAAW': 'https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard',
    'WNBA': 'https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
    'MLS': 'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard',
    'NCAAMF': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
    'SOCCER': 'https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard'
}


# Automatically use the current year
current_year = datetime.now().year
def get_season(sport, game_date):
    year = game_date.year

    # College basketball seasons span two years
    if sport in ('NCAAM', 'NCAAW'):
        return year - 1 if game_date.month >= 11 else year

    # College football uses calendar year
    if sport == 'NCAAMF':
        return year

    # NBA, NHL, NFL, WNBA, Soccer
    return year


def update_schedule_for_sport(sport, days_ahead=14):
    """Update schedule for a specific sport"""
    print(f"\nUpdating {sport} schedule...")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    total_added = 0

    for days in range(-1, days_ahead):  # include yesterday
        date_to_fetch = datetime.now() + timedelta(days=days)
        date_str = date_to_fetch.strftime('%Y%m%d')
        url = f"{ESPN_APIS[sport]}?dates={date_str}"

        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            events = data.get('events', [])

            for event in events:
                competition = event.get('competitions', [{}])[0]
                competitors = competition.get('competitors', [])

                if len(competitors) != 2:
                    continue

                home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                away = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                if not home or not away:
                    continue

                home_team = home.get('team', {}).get('displayName', '')
                away_team = away.get('team', {}).get('displayName', '')
                event_id = event.get('id', '')
                game_date_str = event.get('date', '')

                # Parse game date
                try:
                    game_date_utc = datetime.strptime(game_date_str, '%Y-%m-%dT%H:%M%SZ')
                    game_date_local = game_date_utc - timedelta(hours=5)  # Adjust timezone if needed
                    game_date = game_date_local.strftime('%Y-%m-%d')
                except:
                    game_date = date_to_fetch.strftime('%Y-%m-%d')

                # Get status
                status_name = event.get('status', {}).get('type', {}).get('name', 'scheduled')
                status = 'scheduled'
                if status_name in ['STATUS_IN_PROGRESS', 'STATUS_HALFTIME']:
                    status = 'in_progress'
                elif status_name in ['STATUS_FINAL', 'STATUS_FINAL_OT']:
                    status = 'final'

                # Get scores if final
                home_score = home.get('score') if status == 'final' else None
                away_score = away.get('score') if status == 'final' else None

                game_id = f"{sport}_{event_id}"
                season = get_season(sport, datetime.strptime(game_date, "%Y-%m-%d"))

                # Insert/update game
                try:
                    cursor.execute("""
                        INSERT OR REPLACE INTO games 
                        (game_id, sport, league, season, game_date, home_team_id, away_team_id, 
                         home_score, away_score, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (game_id, sport, sport, season, game_date, home_team, away_team, home_score, away_score, status))
                    total_added += 1
                except Exception as e:
                    print(f"Error inserting game {game_id}: {e}")

        except Exception as e:
            print(f"Error fetching {sport} for {date_str}: {e}")

    conn.commit()
    conn.close()
    print(f"  ✓ {total_added} games updated")
    return total_added


def main():
    print("=" * 60)
    print(f"Daily Schedule Update - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    for sport in ESPN_APIS.keys():
        update_schedule_for_sport(sport, days_ahead=14)

    print("\n" + "=" * 60)
    print("✓ Schedules updated")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()