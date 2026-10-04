# Daily Tracking Setup

## Automated Daily Tasks

Run these scripts daily to keep the system updated:

### 1. Morning Update (9 AM)
```bash
cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\)

# Fetch latest odds and schedules
python3 fetch_espn_odds.py
python3 update_all_schedules.py

# Update injury reports
python3 fetch_injuries.py

# Update weather for outdoor games
python3 fetch_weather.py
```

### 2. Evening Results Tracking (11 PM)
```bash
cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\)

# Grade yesterday's picks
python3 track_results.py

# Update team records with latest games
python3 calculate_team_records.py
```

## Setup Cron Jobs (Automated)

Add to crontab (`crontab -e`):

```cron
# Morning updates at 9 AM
0 9 * * * cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\) && /usr/local/bin/python3 fetch_espn_odds.py >> /tmp/morning_update.log 2>&1
5 9 * * * cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\) && /usr/local/bin/python3 update_all_schedules.py >> /tmp/morning_update.log 2>&1
10 9 * * * cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\) && /usr/local/bin/python3 fetch_injuries.py >> /tmp/morning_update.log 2>&1
15 9 * * * cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\) && /usr/local/bin/python3 fetch_weather.py >> /tmp/morning_update.log 2>&1

# Evening tracking at 11 PM
0 23 * * * cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\) && /usr/local/bin/python3 track_results.py >> /tmp/evening_track.log 2>&1
10 23 * * * cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\) && /usr/local/bin/python3 calculate_team_records.py >> /tmp/evening_track.log 2>&1
```

## View Results

### Check Daily Results:
```bash
sqlite3 sports_predictions_original.db "
SELECT pick_date, sport, pick_type, result, COUNT(*) as picks, SUM(units_won) as units
FROM pick_results
GROUP BY pick_date, sport, pick_type, result
ORDER BY pick_date DESC
LIMIT 20;
"
```

### Check Weekly Summary:
```bash
sqlite3 sports_predictions_original.db "
SELECT sport, week_number, week_start_date, 
       ml_wins || '-' || ml_losses as ML,
       ats_wins || '-' || ats_losses as ATS,
       ou_wins || '-' || ou_losses as OU,
       printf('%.2f', total_units) as Units,
       printf('%.1f%%', roi) as ROI
FROM weekly_summary
ORDER BY week_number DESC, sport;
"
```

### Check Overall Record:
```bash
sqlite3 sports_predictions_original.db "
SELECT sport,
       SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
       SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) as losses,
       SUM(CASE WHEN result='PUSH' THEN 1 ELSE 0 END) as pushes,
       printf('%.2f', SUM(units_won)) as total_units
FROM pick_results
GROUP BY sport;
"
```

## Web Interface

Visit: **http://localhost:8001/results/weekly**

This shows:
- Weekly summaries for all sports
- Daily records
- Unit tracking
- ROI calculation

## Next Week Review

Run this command to get your week's performance:

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('sports_predictions_original.db')
cursor = conn.cursor()
cursor.execute('SELECT * FROM weekly_summary ORDER BY week_number DESC LIMIT 3')
for row in cursor.fetchall():
    print(row)
conn.close()
"
```
