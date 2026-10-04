# Automated Sports Picks Tracking

Your sports betting picks system is now running automatically! ✅

## What Runs Automatically

### 🌅 Morning Job (9:00 AM Daily)
Saves today's picks before games start:
- Saves ML, ATS, and Totals picks to database
- Fetches latest betting lines from ESPN
- Updates game schedules
- Fetches injury reports and weather data

**Log files:**
- `daily_picks.log` - Main log
- `morning_job.log` - Job output
- `morning_job_error.log` - Any errors

### 🌙 Evening Job (11:00 PM Daily)
Grades yesterday's picks and updates records:
- Grades all completed picks (WIN/LOSS/PUSH)
- Updates weekly summaries
- Recalculates team records from completed games

**Log files:**
- `daily_results.log` - Main log
- `evening_job.log` - Job output
- `evening_job_error.log` - Any errors

## Manual Controls

### Check Status
```bash
launchctl list | grep sportspicks
```

### Stop Jobs
```bash
launchctl unload ~/Library/LaunchAgents/com.sportspicks.morning.plist
launchctl unload ~/Library/LaunchAgents/com.sportspicks.evening.plist
```

### Start Jobs
```bash
launchctl load ~/Library/LaunchAgents/com.sportspicks.morning.plist
launchctl load ~/Library/LaunchAgents/com.sportspicks.evening.plist
```

### Run Manually (Testing)
```bash
# Morning picks
cd "/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)"
./daily_morning_picks.sh

# Evening results
./daily_evening_results.sh
```

### View Recent Logs
```bash
# Morning logs
tail -50 daily_picks.log

# Evening logs
tail -50 daily_results.log
```

## Files Created

**Scripts:**
- `daily_morning_picks.sh` - Morning automation script
- `daily_evening_results.sh` - Evening automation script

**Launchd Jobs:**
- `~/Library/LaunchAgents/com.sportspicks.morning.plist`
- `~/Library/LaunchAgents/com.sportspicks.evening.plist`

**Log Files:**
- `daily_picks.log` - Morning execution log
- `daily_results.log` - Evening execution log
- `morning_job.log` - Launchd stdout
- `morning_job_error.log` - Launchd stderr
- `evening_job.log` - Launchd stdout
- `evening_job_error.log` - Launchd stderr

## How It Works

1. **9 AM**: System saves picks for today's games using threshold-based system
   - ML picks: Teams with >61% win rate or fade teams <31%
   - ATS picks: Teams with >61% ATS rate or fade teams <31%
   - Totals picks: Games where combined O/U rate ≥60%

2. **11 PM**: System grades yesterday's picks
   - Compares picks against final scores
   - Calculates WIN/LOSS/PUSH for each pick
   - Updates weekly summaries with W-L records and units

3. **Results Page**: Visit http://localhost:8001/results/nfl (or /nba, /nhl)
   - View last 7 days of results
   - See daily W-L records
   - Track performance over time

## Troubleshooting

**Jobs not running?**
```bash
# Check if jobs are loaded
launchctl list | grep sportspicks

# Reload if needed
launchctl unload ~/Library/LaunchAgents/com.sportspicks.*.plist
launchctl load ~/Library/LaunchAgents/com.sportspicks.*.plist
```

**Check for errors:**
```bash
cat morning_job_error.log
cat evening_job_error.log
```

**Test manually:**
```bash
./daily_morning_picks.sh
./daily_evening_results.sh
```

## Notes

- Jobs will run every day at 9 AM and 11 PM
- Logs are appended (not overwritten) so you can track history
- Jobs persist across reboots
- If your Mac is asleep at the scheduled time, jobs will run when it wakes up
