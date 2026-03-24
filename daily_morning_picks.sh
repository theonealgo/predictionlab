#!/bin/bash
# Daily Morning Picks - Runs at 9 AM
# Saves today's picks before games start

cd "/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)"

echo "========================================" >> daily_picks.log
echo "Morning Picks - $(date)" >> daily_picks.log
echo "========================================" >> daily_picks.log

# Backup database first
./backup_database.sh >> daily_picks.log 2>&1

# Save today's picks
/usr/local/bin/python3 track_picks_results.py >> daily_picks.log 2>&1

# Fetch latest odds
/usr/local/bin/python3 fetch_espn_odds.py >> daily_picks.log 2>&1
/usr/local/bin/python3 fetch_core_api_odds.py >> daily_picks.log 2>&1

# Update schedules
/usr/local/bin/python3 update_all_schedules.py >> daily_picks.log 2>&1

# Import baseline from CSV and update with recent games
/usr/local/bin/python3 import_and_update_team_records.py >> daily_picks.log 2>&1

# Fetch injuries and weather
/usr/local/bin/python3 fetch_injuries.py >> daily_picks.log 2>&1
/usr/local/bin/python3 fetch_weather.py >> daily_picks.log 2>&1

echo "" >> daily_picks.log
