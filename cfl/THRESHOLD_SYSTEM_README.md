# Threshold-Based ATS Betting System

## Overview
The system has been upgraded from a static list-based approach to a dynamic threshold-based system that automatically generates picks based on current team performance metrics.

## Pick Generation Rules

### Moneyline (ML)
- **Pick**: Teams with win% > 61%
- **Fade**: Teams with win% < 31% (pick their opponent)
- **Skip**: Teams between 31% and 61%

### Spread (ATS)
- **Pick**: Teams with ATS% > 61%
- **Fade**: Teams with ATS% < 31% (pick their opponent)
- **Skip**: Teams between 31% and 61%

### Totals (O/U)
- **Combined Calculation**: Add both teams' over/under records
- **Pick OVER**: If combined over% ≥ 60%
- **Pick UNDER**: If combined under% ≥ 60%
- **Skip**: If neither threshold met

Example: Lakers (9-5 over) vs Bucks (8-6 over) = 17-11 combined (60.7%) → Pick OVER

## Data Sources

### Primary: Web Scraping
- **Source**: sportsbettingdime.com
- **Script**: `update_team_records.py`
- **Schedule**: Run every 3 days
- **Command**: `python3 update_team_records.py`
- **Note**: Currently disabled due to JavaScript rendering issues

### Alternative: CSV Import
- **Script**: `import_team_records.py`
- **Usage**: `python3 import_team_records.py NBA nba_records.csv`
- **CSV Format**:
  ```
  Team,ML_Record,ATS_Record,O/U_Record
  Philadelphia 76ers,9-3,9-3,5-9
  ```

## Database Schema

```sql
CREATE TABLE team_records (
    sport TEXT,
    team_name TEXT,
    wins INT,
    losses INT,
    win_pct REAL,
    ats_wins INT,
    ats_losses INT,
    ats_pct REAL,
    over_wins INT,
    over_losses INT,
    over_pct REAL,
    under_wins INT,
    under_losses INT,
    under_pct REAL,
    last_updated TIMESTAMP,
    UNIQUE(sport, team_name)
);
```

## Files Modified

### Core System
- **`ats_system.py`**: Refactored pick generation methods
  - `generate_moneyline_picks()`: Lines 509-613 (threshold-based)
  - `generate_spread_picks()`: Lines 413-517 (threshold-based)
  - `generate_total_picks()`: Lines 604-698 (combined threshold)
  - `get_team_records()`: Lines 115-148 (helper method)

### Data Management
- **`update_team_records.py`**: Scraper for team records (needs fixing)
- **`import_team_records.py`**: Manual CSV import utility
- **`nba_records_sample.csv`**: Sample NBA data

### Testing
- **`test_threshold_picks.py`**: Test script for pick generation

## Backups Created
- `ats_system_backup_YYYYMMDD_HHMMSS.py` (before refactor)

## Testing

Run test script:
```bash
python3 test_threshold_picks.py
```

Expected output:
- **ML picks**: Only teams with >61% or opponents of teams with <31%
- **Spread picks**: Only teams with ATS% >61% or opponents of teams with <31%
- **Total picks**: Only games with combined O/U% ≥60%

## Maintenance Tasks

### Weekly (or every 3 days)
1. Get latest team records from sportsbettingdime.com
2. Create CSV file with format above
3. Import: `python3 import_team_records.py NBA nba_records.csv`
4. Repeat for NHL, NFL as needed

### Future Improvements
1. **Fix web scraper**: Add selenium/playwright for JavaScript rendering
2. **Automated scheduling**: Setup cron job
   ```bash
   0 0 */3 * * cd /path/to/project && python3 update_team_records.py
   ```
3. **API Integration**: Find alternative data source with API access
4. **Dashboard**: Show team records directly in web interface

## Known Issues

1. **Web Scraper**: sportsbettingdime.com uses JavaScript to render tables
   - **Workaround**: Manual CSV import
   - **Solution**: Add headless browser (selenium/playwright)

2. **Duplicate Teams**: Some teams have multiple entries (e.g., "PHO" and "Phoenix Suns")
   - **Solution**: Add team name normalization

3. **Missing Teams**: Teams not in CSV show 0% for all stats
   - **Solution**: System correctly filters out teams with no data

## Examples

### Example 1: Lakers vs Bucks (ATS Pick)
- Lakers: 10-4 ATS (71.4%) → **Pick Lakers ATS**
- Bucks: 7-7 ATS (50.0%) → No pick

### Example 2: Lakers vs Bucks (Total Pick)
- Lakers: 9-5 over (64.3%)
- Bucks: 6-8 over (42.9%)
- Combined: 15-13 over (53.6%) → **No pick** (under 60%)

### Example 3: Washington vs Brooklyn (ML Pick)
- Washington: 5-9 (35.7%) → No pick
- Brooklyn: 1-11 (8.3%) → **Fade Brooklyn, pick Washington**

## Contact

For questions about the threshold system, refer to:
- Conversation summary in Warp
- This README
- Code comments in `ats_system.py`
