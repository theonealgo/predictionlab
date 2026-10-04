#!/bin/bash
# Daily Evening Results - Runs at 11 PM
# Grades yesterday's picks and updates weekly summaries

cd "/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)"

echo "========================================" >> daily_results.log
echo "Evening Results - $(date)" >> daily_results.log
echo "========================================" >> daily_results.log

# Grade yesterday's picks
/usr/local/bin/python3 track_results.py >> daily_results.log 2>&1

# Recalculate team records from completed games
/usr/local/bin/python3 calculate_team_records.py >> daily_results.log 2>&1

echo "" >> daily_results.log
