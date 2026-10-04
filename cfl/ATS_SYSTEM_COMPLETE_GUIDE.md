# Complete ATS Betting System Guide

Automated system that calculates **real ATS records** from actual betting lines and game results, then generates picks based on proven performers.

## 🎯 System Overview

### What This Does
1. **Fetches schedules** from ESPN/CFBD APIs daily
2. **Calculates ATS rankings** from real betting lines + game results  
3. **Auto-updates system teams** weekly based on performance
4. **Generates picks** for moneyline, spread, and over/under

### Key Metrics
- **ATS (Against The Spread)**: Teams that cover >60%
- **Moneyline**: Teams winning >58% straight-up
- **Over**: Teams hitting over >60% of the time
- **Under**: Teams hitting under >60% (inverse - <40% over rate)

---

## 📋 Daily Workflow

### Step 1: Update Schedules (9 AM daily)
```bash
python3 update_all_schedules.py
```

**What it does:**
- Fetches next 7 days of games from APIs
- Updates: NBA, NHL, NFL, MLB, WNBA, NCAAF
- Clears old scheduled games
- Adds fresh games to database

**Sources:**
- ESPN API: NBA, NHL, NFL, MLB, WNBA
- CFBD API: NCAAF (College Football Data)

### Step 2: Fetch Betting Lines (10 AM daily)
```bash
python3 daily_ats_updater.py
```

**What it does:**
- Fetches real Vegas lines from APIs
- Stores spreads, totals, moneylines
- Updates `betting_lines` table

**Current Status:**
- ✅ NBA: Working (SportsData.io)
- ⚠️ NFL: Needs API setup
- ⚠️ NHL: ESPN doesn't provide lines
- ⚠️ NCAAF: Needs odds API

### Step 3: Calculate ATS Rankings (Weekly - Mondays)
```bash
python3 calculate_ats_rankings.py
```

**What it does:**
- Analyzes last 90 days of completed games
- Calculates ATS records from real betting lines
- Determines over/under trends
- Calculates moneyline win %
- Outputs rankings to `ats_rankings_output.txt`

**Example Output (NFL):**
```
Top 10 ATS Covers:
  LA Rams                        7-2-0     77.8%  +5.7
  Seattle                        7-2-0     77.8%  +9.3
  New England                    7-3-1     70.0%  +5.2
  Indianapolis                   6-3-1     66.7%  +7.5
  Philadelphia                   6-3-0     66.7%  -1.3
  ...
```

### Step 4: View Picks (Anytime)
```bash
# Start web app
python3 ats_app.py

# Or generate CSV
python3 get_ats_picks.py --csv today.csv
```

**Web Interface:**
- Visit: http://localhost:8000
- Shows picks for each sport
- Filters by system teams only
- Displays moneyline, spread, over/under

---

## 🤖 Automation Setup

### Option 1: LaunchAgent (macOS - Recommended)

**Schedule Updater (9 AM daily):**
```bash
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
        <string>/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)/update_all_schedules.py</string>
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

launchctl load ~/Library/LaunchAgents/com.sportspicks.schedules.plist
```

**ATS Rankings Calculator (Mondays 8 AM):**
```bash
cat > ~/Library/LaunchAgents/com.sportspicks.ats.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.sportspicks.ats</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/Users/nimamesghali/Documents/2025sports/SportStatsAPI (v2)/calculate_ats_rankings.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>8</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/ats_rankings.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ats_rankings_error.log</string>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.sportspicks.ats.plist
```

### Option 2: Cron Jobs

```bash
crontab -e
```

Add these lines:
```cron
# Update schedules daily at 9 AM
0 9 * * * cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\) && /usr/local/bin/python3 update_all_schedules.py >> /tmp/schedules_update.log 2>&1

# Calculate ATS rankings every Monday at 8 AM
0 8 * * 1 cd /Users/nimamesghali/Documents/2025sports/SportStatsAPI\ \(v2\) && /usr/local/bin/python3 calculate_ats_rankings.py >> /tmp/ats_rankings.log 2>&1
```

---

## 📊 System Teams Criteria

### Spread (ATS Coverage)
- **Minimum**: 5 games played
- **Threshold**: >60% cover rate
- **Example**: LA Rams 7-2 (77.8%) means 7 covers, 2 non-covers

### Moneyline (Win %)
- **Minimum**: 5 games played
- **Threshold**: >58% win rate
- **Example**: Thunder 12-3 (80%) means 12 wins, 3 losses

### Over (High Scoring)
- **Minimum**: 5 games played
- **Threshold**: >60% over rate
- **Example**: Timberwolves going over 60% of the time

### Under (Low Scoring)
- **Minimum**: 5 games played
- **Threshold**: <40% over rate (= >60% under rate)
- **Example**: Mavericks staying under 60% of the time

---

## 🗂️ File Structure

### Core Scripts
- `update_all_schedules.py` - Fetch schedules from APIs
- `calculate_ats_rankings.py` - Calculate real ATS records
- `ats_system.py` - Core ATS logic & system teams
- `ats_app.py` - Web interface (Flask)
- `get_ats_picks.py` - CLI picks generator

### Data Files
- `sports_predictions_original.db` - SQLite database
  - `games` table - All games & results
  - `betting_lines` table - Real Vegas lines
  - `predictions` table - Model predictions
- `ats_rankings_output.txt` - Weekly rankings report

### Configuration
- `SYSTEM_TEAMS` - Hardcoded in `ats_system.py` (lines 33-85)
- Update manually or with `calculate_ats_rankings.py` output

---

## 🔧 Manual Updates

### Update System Teams Manually
Edit `ats_system.py` lines 33-85:

```python
SYSTEM_TEAMS = {
    'NBA': {
        'spread': ['Philadelphia 76ers', 'Chicago Bulls', ...],
        'moneyline': ['Oklahoma City Thunder', ...],
        'over': ['Minnesota Timberwolves', ...],
        'under': ['Indiana Pacers', ...]
    },
    'NFL': {
        ...
    }
}
```

After editing, restart the app:
```bash
pkill -f ats_app.py
python3 ats_app.py &
```

---

## 📈 Current Status (Nov 14, 2025)

### Schedules
- ✅ NBA: 207 games (ESPN API)
- ✅ NHL: 103 games (ESPN API)
- ✅ NFL: 96 games (ESPN API)
- ✅ NCAAF: 194 games (CFBD API)
- ⚠️ MLB: Offseason
- ⚠️ WNBA: Offseason

### Betting Lines Data
- ✅ NBA: 4 games with lines
- ⚠️ NFL: 0 games (need to populate)
- ⚠️ NHL: 0 games (ESPN doesn't provide)
- ⚠️ NCAAF: 0 games (need odds API)

### ATS Rankings
- NBA: 1 ML team (Boston 60%)
- NHL: 8 ML teams (New Jersey, Carolina, Anaheim, etc.)
- NFL: No data yet
- NCAAF: No data yet

**Next Steps:**
1. Populate betting lines for completed games
2. Run `calculate_ats_rankings.py` to get real ATS records
3. Update SYSTEM_TEAMS with calculated rankings
4. System will then generate picks based on proven performers

---

## 🎓 Understanding the System

### Why 90 Days Lookback?
- Recent performance matters more
- Teams change over a season
- Balances sample size vs recency

### Why These Thresholds?
- **60% ATS**: Industry standard for "sharp" performance
- **58% ML**: Above breakeven after -110 juice
- **60% O/U**: Clear trend indicator

### Betting Line Format
- **Spread**: Negative = favorite (e.g., -7.5)
- **Total**: Combined score over/under (e.g., 215.5)
- **Moneyline**: Negative = favorite (e.g., -150)

---

## 🚀 Quick Start Commands

```bash
# 1. Update everything
python3 update_all_schedules.py

# 2. Calculate rankings (weekly)
python3 calculate_ats_rankings.py

# 3. Start web app
python3 ats_app.py

# 4. View picks
open http://localhost:8000

# 5. Export to CSV
python3 get_ats_picks.py --csv picks.csv
```

---

## 📝 Logs & Debugging

**Check logs:**
```bash
# Schedule updates
tail -f /tmp/schedules_update.log

# ATS rankings
tail -f /tmp/ats_rankings.log

# Web app
ps aux | grep ats_app.py
```

**Verify database:**
```bash
sqlite3 sports_predictions_original.db

# Check schedules
SELECT COUNT(*), sport FROM games WHERE status='scheduled' GROUP BY sport;

# Check betting lines
SELECT COUNT(*), sport FROM betting_lines GROUP BY sport;

# Check completed games
SELECT COUNT(*), sport FROM games WHERE status='final' GROUP BY sport;
```

---

## 🆘 Troubleshooting

**Problem**: No ATS rankings showing
**Solution**: Need betting lines data first - run `daily_ats_updater.py` or manually populate

**Problem**: Schedule not updating
**Solution**: Check API status, verify cron/LaunchAgent is running

**Problem**: Wrong teams in system teams
**Solution**: Run `calculate_ats_rankings.py`, review output, manually update `ats_system.py`

**Problem**: App not showing picks
**Solution**: Verify games exist, check that teams match system teams exactly (case-sensitive)
