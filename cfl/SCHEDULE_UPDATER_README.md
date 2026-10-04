# Schedule Updater

Automatically keeps your sports schedules current by fetching fresh data from APIs daily.

## Quick Start

**Run manually:**
```bash
python3 update_schedules.py
```

This will:
- ✓ Clear old/scheduled games from database
- ✓ Fetch next 7 days of NBA games (SportsData.io API)
- ✓ Fetch today's NHL games (ESPN API)
- ⚠ NFL & NCAAF need API fixes

## Automated Daily Updates

### Option 1: macOS LaunchAgent (Recommended)

Run daily at 9 AM automatically:

```bash
# Create LaunchAgent plist
cat > ~/Library/LaunchAgents/com.sportspicks.schedules.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.sportspicks.schedules</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)/update_schedules.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/schedules_update.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/schedules_update_error.log</string>
</dict>
</plist>
EOF

# Load it
launchctl load ~/Library/LaunchAgents/com.sportspicks.schedules.plist

# Check status
launchctl list | grep schedules
```

### Option 2: Cron Job

Add to crontab:
```bash
crontab -e
```

Add this line (runs daily at 9 AM):
```
0 9 * * * cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\) && /usr/local/bin/python3 update_schedules.py >> /tmp/schedules_update.log 2>&1
```

## What Gets Updated

| Sport | Status | Source | Coverage |
|-------|--------|--------|----------|
| **NBA** | ✅ Working | SportsData.io | Next 7 days |
| **NHL** | ✅ Working | ESPN API | Today's games |
| **NFL** | ⚠ Needs fix | nfl_data_py | SSL error |
| **NCAAF** | ⚠ Not implemented | - | - |

## Troubleshooting

**Problem:** NFL schedule not updating
**Fix:** SSL certificate issue with nfl_data_py library
```bash
pip install --upgrade certifi
```

**Problem:** Schedules still outdated
**Check logs:**
```bash
tail -f /tmp/schedules_update.log
```

**Verify it's running:**
```bash
# For LaunchAgent
launchctl list | grep schedules

# For cron
crontab -l
```

## Manual NBA Schedule Fix

If you ever need to manually fix NBA schedule:

```bash
# Open database
sqlite3 sports_predictions_original.db

# Check current schedule
SELECT game_date, matchup FROM games WHERE sport='NBA' AND status='scheduled' ORDER BY game_date;

# Delete all future NBA games
DELETE FROM games WHERE sport='NBA' AND game_date >= date('now') AND status != 'final';

# Exit
.quit

# Run schedule updater
python3 update_schedules.py
```

## Integration with ATS System

The schedule updater runs independently and updates the `games` table in `sports_predictions_original.db`.

Your ATS app (`ats_app.py`) reads from this same database, so updates are automatically reflected.

**Recommended workflow:**
1. Run `update_schedules.py` every morning (9 AM via LaunchAgent)
2. Run `daily_ats_updater.py` after schedules update (10 AM) to fetch betting lines
3. Your ATS app shows fresh data automatically

## Files

- `update_schedules.py` - Main schedule updater script
- `daily_ats_updater.py` - Fetches betting lines (run after schedules update)
- `ats_app.py` - Web interface (reads updated data automatically)
- `sports_predictions_original.db` - Database with schedules
