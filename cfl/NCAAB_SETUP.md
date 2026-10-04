# NCAA Men's Basketball (NCAAB) Setup Guide

## Overview
NCAAB has been successfully integrated into the sports prediction platform. All core components are now configured to support college basketball.

## ✅ Completed Integration

### 1. Schedule Fetching
- **File**: `update_all_schedules.py`
- **API**: ESPN Scoreboard API for Men's College Basketball
- **Endpoint**: `https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard`
- **Status**: ✅ Working - Successfully fetched 14 games

### 2. Odds Fetching
- **File**: `fetch_espn_odds.py`
- **API**: ESPN Scoreboard API (same as schedule)
- **Status**: ✅ Working - Successfully fetched 5 betting lines with spreads and totals

### 3. ATS System Configuration
- **File**: `ats_system.py`
- **Configuration**: 
  - Added NCAAB to `SYSTEM_TEAMS` dictionary (empty placeholders)
  - Added NCAAB to `home_field_adv` (3.0 points, same as NBA)
  - Updated `normalize_team_name()` to handle college mascots
- **Status**: ✅ Configured

### 4. Team Records Tracking
- **File**: `update_team_records.py`
- **Scraper URL**: `https://www.sportsbettingdime.com/ncaab/team-trends/`
- **Status**: ✅ Configured (scraper has JS rendering issues, use CSV import)

### 5. Web App Routes
- **File**: `NHL77FINAL.py`
- **Route**: `/sport/NCAAB/ats` (already exists generically)
- **Status**: ✅ Ready - Will display picks once team records are populated

## Database Verification

```sql
-- Games added
SELECT COUNT(*) FROM games WHERE sport='NCAAB';
-- Result: 14 games

-- Betting lines added
SELECT COUNT(*) FROM betting_lines WHERE sport='NCAAB';
-- Result: 5 lines

-- Sample game
SELECT game_id, game_date, home_team_id, away_team_id 
FROM games WHERE sport='NCAAB' LIMIT 1;
-- Result: NCAAB_1: Stonehill Skyhawks @ Iowa State Cyclones on 2025-11-17

-- Sample betting line
SELECT game_id, spread, total FROM betting_lines WHERE sport='NCAAB' LIMIT 1;
-- Result: NCAAB_1: Spread=-36.5, Total=146.5
```

## How to Populate Team Records

Since the web scraper has JavaScript rendering issues, use manual CSV import:

### Step 1: Get Data from SportsBettingDime
Visit: https://www.sportsbettingdime.com/ncaab/team-trends/

### Step 2: Create CSV File
Format:
```csv
Team,ML_Record,ATS_Record,O/U_Record
Duke Blue Devils,5-0,4-1,3-2
UConn Huskies,4-1,3-2,2-3
Kansas Jayhawks,4-0,3-1,2-2
```

**Note**: ESPN uses full team names with mascots (e.g., "Duke Blue Devils"). The `normalize_team_name()` function will strip mascots automatically.

### Step 3: Import to Database
```bash
python3 import_team_records.py NCAAB ncaab_records.csv
```

## Running Daily Updates

### Full Update (Schedule + Odds)
```bash
# Update schedule
python3 update_all_schedules.py

# Fetch odds
python3 fetch_espn_odds.py
```

### Team Records (Weekly/Every 3 days)
```bash
# Option 1: Scraper (when fixed)
python3 update_team_records.py

# Option 2: Manual CSV (current workaround)
python3 import_team_records.py NCAAB ncaab_records.csv
```

## Testing

### Test Schedule Fetch
```python
from update_all_schedules import fetch_espn_schedule
added = fetch_espn_schedule('NCAAB', days_ahead=7)
print(f"Added {added} NCAAB games")
```

### Test Odds Fetch
```python
from fetch_espn_odds import fetch_espn_odds
added = fetch_espn_odds('NCAAB', days_ahead=7)
print(f"Added {added} NCAAB betting lines")
```

### Test Pick Generation
```python
from ats_system import ATSSystem
ats = ATSSystem()

ml_picks = ats.generate_moneyline_picks('NCAAB', days_ahead=7)
spread_picks = ats.generate_spread_picks('NCAAB', days_ahead=7)
total_picks = ats.generate_total_picks('NCAAB', days_ahead=7)

print(f"ML: {len(ml_picks)}, Spread: {len(spread_picks)}, Totals: {len(total_picks)}")
```

## Threshold-Based Picking System

NCAAB uses the same threshold logic as other sports:

### Moneyline
- **Pick**: Teams with win% > 61%
- **Fade**: Teams with win% < 31%

### Spread (ATS)
- **Pick**: Teams with ATS% > 61%
- **Fade**: Teams with ATS% < 31%

### Totals (O/U)
- **Pick OVER**: If combined over% ≥ 60%
- **Pick UNDER**: If combined under% ≥ 60%

## Web Interface Access

Once the Flask app is running:
- **Schedule**: `http://localhost:5000/sport/NCAAB/schedule`
- **ATS Picks**: `http://localhost:5000/sport/NCAAB/ats`
- **Results**: `http://localhost:5000/sport/NCAAB/results`

## Season Information

- **Season Span**: November - April (spans calendar year like NBA/NHL)
- **Season Year Logic**: If current month ≤ June, use previous year's season
- **Games Per Day**: Varies (typically 50-200 games/day during peak season)

## Known Limitations

1. **Web Scraper**: SportsBettingDime uses JavaScript, so automated scraping doesn't work
   - **Workaround**: Manual CSV import weekly
   - **Fix**: Add Selenium/Playwright for headless browsing

2. **Team Name Normalization**: ESPN uses full names with mascots
   - **Solution**: `normalize_team_name()` function strips common mascots
   - **May need updates**: If new mascot names cause mismatches

3. **No Moneylines in Odds**: ESPN API returns spread/total but not moneylines for NCAAB
   - **Impact**: Moneyline picks will be generated but without actual odds displayed

## Files Modified

1. `ats_system.py` - Added NCAAB configuration
2. `update_all_schedules.py` - Added NCAAB ESPN API endpoint
3. `fetch_espn_odds.py` - Added NCAAB odds fetching
4. `update_team_records.py` - Added NCAAB scraper URL
5. `NHL77FINAL.py` - Already had NCAAB in SPORTS dictionary

## Next Steps

1. ✅ Integration complete
2. ⏳ Populate initial team records via CSV import
3. ⏳ Monitor first picks generation
4. ⏳ Track results as season progresses
5. ⏳ Fine-tune thresholds based on NCAAB-specific performance

## Support

For issues or questions:
- Check `THRESHOLD_SYSTEM_README.md` for pick generation logic
- Check database with: `sqlite3 sports_predictions_original.db`
- Test scripts are in the main directory
